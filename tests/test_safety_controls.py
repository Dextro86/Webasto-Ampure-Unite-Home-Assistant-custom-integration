from types import SimpleNamespace
import asyncio
import importlib
from datetime import datetime, timedelta, timezone
from time import monotonic
from unittest.mock import AsyncMock, Mock
import pytest

from homeassistant.config_entries import ConfigEntryNotReady
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError

from custom_components.webasto_unite.config_flow import (
    WebastoUniteOptionsFlow,
    _bounded_float,
    _bounded_int,
    _validate_dlb_options,
    _validate_init_options,
    _validate_pv_options,
)
from custom_components.webasto_unite import async_setup as integration_async_setup
from custom_components.webasto_unite.button import (
    WebastoRequestPhase1PButton,
    WebastoRequestPhase3PButton,
    WebastoRestoreDefaultPhaseButton,
    WebastoResetPhaseSwitchStateButton,
    WebastoSoftResetChargerButton,
    async_setup_entry as button_async_setup_entry,
)
from custom_components.webasto_unite.control.inputs import ControlInputReader
from custom_components.webasto_unite.core.capabilities import build_capabilities, build_capability_summary
from custom_components.webasto_unite.core.status import build_operating_state
from custom_components.webasto_unite.controller import WallboxController
from custom_components.webasto_unite.coordinator import WebastoUniteCoordinator
from custom_components.webasto_unite.diagnostics import async_get_config_entry_diagnostics
from custom_components.webasto_unite.evcc import build_evcc_status
from custom_components.webasto_unite.const import (
    DOMAIN,
    PHASE_SWITCHING_MODE_AUTOMATIC_SOLAR,
    PHASE_SWITCHING_MODE_MANUAL_ONLY,
    PHASE_SWITCHING_MODE_OFF,
    SERVICE_RESTORE_DEFAULT_PHASE,
    SERVICE_SOFT_RESET_CHARGER,
)
from custom_components.webasto_unite.models import ChargeMode, ControlConfig, ControlMode, ControlReason, DlbInputModel, HaSensorSnapshot, PhaseCurrents, SolarControlStrategy, SolarInputModel, RuntimeSnapshot, WallboxState
from custom_components.webasto_unite.number import WebastoMaximumCurrentNumber, WebastoRequestedCurrentNumber, WebastoFixedCurrentNumber
from custom_components.webasto_unite.number import async_setup_entry as number_async_setup_entry
from custom_components.webasto_unite.features.phase_policy import AUTO_PHASE_STABLE_TO_1P_S, PhasePolicyDecision
from custom_components.webasto_unite.features.phase_runtime import PhaseRuntimeState
from custom_components.webasto_unite.registers import PHASE_SWITCH_MODE, SET_CHARGE_CURRENT_A
from custom_components.webasto_unite.control.runtime_guards import RuntimeGuards, RuntimeGuardState
from custom_components.webasto_unite.sensor import SENSORS, WebastoSensor
from custom_components.webasto_unite.sensor_adapter import HaSensorAdapter
from custom_components.webasto_unite.select import WebastoModeSelect, WebastoPhaseSwitchSelect
from custom_components.webasto_unite.select import async_setup_entry as select_async_setup_entry
from custom_components.webasto_unite.switch import WebastoChargingSwitch, WebastoFixedCurrentUntilUnplugSwitch, WebastoSolarUntilUnplugSwitch
from custom_components.webasto_unite.wallbox_reader import WallboxReader
from custom_components.webasto_unite.control.write_queue import WriteQueueManager
from custom_components.webasto_unite.control.write_runtime import WriteRuntime


def make_controller(**kwargs):
    defaults = dict(
        max_current_a=16.0,
        min_current_a=6.0,
        stable_cycles_before_write=1,
        min_seconds_between_writes=0.0,
    )
    defaults.update(kwargs)
    return WallboxController(ControlConfig(**defaults))


def make_config_entry(data=None, options=None):
    return SimpleNamespace(
        entry_id="test-entry",
        title="Webasto Unite",
        data=data
        or {
            "host": "192.168.1.10",
            "port": 502,
            "unit_id": 255,
            "installed_phases": "3p",
        },
        options=options or {},
    )


def make_operating_state(
    *,
    effective_mode: ChargeMode,
    decision,
    control_config: ControlConfig | None = None,
    charging_paused: bool = False,
    solar_until_unplug_active: bool = False,
    fixed_current_until_unplug_active: bool = False,
) -> str:
    return build_operating_state(
        effective_mode=effective_mode,
        charging_paused=charging_paused,
        fixed_current_until_unplug_active=fixed_current_until_unplug_active,
        solar_until_unplug_active=solar_until_unplug_active,
        control_config=control_config or ControlConfig(control_mode=ControlMode.MANAGED_CONTROL),
        decision=decision,
    )


def read_control_inputs(coordinator, wallbox=None):
    return ControlInputReader(
        options=coordinator.entry.options,
        config=coordinator.control_config,
        sensor_adapter=coordinator.sensor_adapter,
        surplus_resolver=coordinator.controller.resolve_surplus_power,
        configured_phase_count=coordinator._configured_phase_count,
    ).read(wallbox)


async def enqueue_test_decision(coordinator, decision):
    if not hasattr(coordinator, "control_config"):
        coordinator.control_config = ControlConfig()
    if not hasattr(coordinator, "controller"):
        coordinator.controller = WallboxController(coordinator.control_config)
    runtime = WriteRuntime(
        coordinator.control_config,
        write_queue=coordinator.write_queue,
        client=None,
        controller=getattr(coordinator, "controller", None),
    )
    await runtime.enqueue_decision(
        decision,
        effective_mode=coordinator.effective_mode,
        current_snapshot=getattr(coordinator, "data", None),
        allows_control_writes=coordinator._allows_control_writes(),
        enqueue_keepalive=AsyncMock(),
    )


def install_mock_write_runtime(coordinator):
    coordinator.write_runtime = SimpleNamespace(
        enqueue_keepalive_if_needed=AsyncMock(),
        enqueue_decision=AsyncMock(),
        flush_write_queue=AsyncMock(),
        flush_lock=asyncio.Lock(),
        write_current_now=AsyncMock(),
        keepalive_age_seconds=lambda: 0.0,
        is_keepalive_overdue=lambda age_s: False,
        keepalive_sent_count=0,
        keepalive_write_failures=0,
        last_control_write_value_a=None,
        last_control_write_reason=None,
        last_control_write_register=None,
        last_control_write_age_seconds=lambda: None,
        last_control_write_blocked_reason=None,
        last_control_write_verification_status=None,
        last_control_write_verification_reported_a=None,
        last_control_write_verification_delta_a=None,
        update_current_write_verification=Mock(),
    )


def default_init_input(**overrides):
    data = {
        "host": "192.168.1.20",
        "port": 502,
        "unit_id": 255,
        "installed_phases": "3p",
        "min_current": 6.0,
        "max_current": 16.0,
        "safe_current": 6.0,
        "control_mode": "keepalive_only",
        "startup_charge_mode": "normal",
        "keepalive_interval": 10.0,
        "polling_interval": 2.0,
        "timeout": 3.0,
        "retries": 3,
    }
    data.update(overrides)
    return data


def default_dlb_input(**overrides):
    data = {
        "dlb_enabled": False,
        "dlb_input_model": "disabled",
        "dlb_sensor_scope": "load_excluding_charger",
        "dlb_require_units": False,
        "main_fuse": 25.0,
        "safety_margin": 2.0,
        "dlb_l1_sensor": None,
        "dlb_l2_sensor": None,
        "dlb_l3_sensor": None,
        "dlb_grid_power_sensor": None,
    }
    data.update(overrides)
    return data


def default_pv_input(**overrides):
    data = {
        "solar_control_strategy": "disabled",
        "solar_input_model": "grid_power_derived",
        "solar_sensor_failure_behavior": "pause",
        "solar_require_units": False,
        "solar_surplus_sensor": None,
        "solar_start_threshold": 1800.0,
        "solar_stop_threshold": 1200.0,
        "solar_start_delay": 0.0,
        "solar_stop_delay": 0.0,
        "solar_min_runtime": 0.0,
        "solar_min_pause": 0.0,
        "solar_min_current": 6.0,
        "solar_until_unplug_strategy": "inherit",
        "fixed_current": 6.0,
    }
    data.update(overrides)
    return data


def default_options_input(**overrides):
    data = {}
    data.update(default_init_input())
    data.update(default_dlb_input())
    data.update(default_pv_input())
    data.update(overrides)
    return data


def install_runtime_guards(coordinator, *, startup_ready: bool = False) -> None:
    coordinator.runtime_guards = RuntimeGuards(
        coordinator.control_config,
        state=RuntimeGuardState(
            startup_started_monotonic=monotonic() - (120.0 if startup_ready else 0.0),
            startup_refresh_count=10 if startup_ready else 0,
        ),
        monotonic_fn=lambda: monotonic(),
    )


def test_off_mode_writes_zero_current_when_vehicle_is_connected():
    controller = make_controller()
    wallbox = WallboxState(installed_phases=3, vehicle_connected=True, charging_active=True)
    sensors = HaSensorSnapshot(phase_currents=PhaseCurrents(l1=0.0, l2=0.0, l3=0.0), valid=True)

    controller.evaluate(ChargeMode.NORMAL, wallbox, sensors)
    decision = controller.evaluate(ChargeMode.OFF, wallbox, sensors)
    follow_up = controller.evaluate(ChargeMode.OFF, wallbox, sensors)

    assert decision.target_current_a == 0.0
    assert follow_up.target_current_a == 0.0
    assert decision.should_write is True
    assert follow_up.should_write is True


def test_service_handler_returns_clear_error_for_unknown_entry_id():
    class _Services:
        def __init__(self):
            self.handlers = {}

        def async_register(self, domain, service, handler, schema=None):
            self.handlers[(domain, service)] = handler

    services = _Services()
    hass = SimpleNamespace(data={"webasto_unite": {}}, services=services)

    asyncio.run(integration_async_setup(hass, {}))

    handler = services.handlers[("webasto_unite", "set_mode")]
    with pytest.raises(HomeAssistantError, match="Unknown Webasto Unite entry_id"):
        asyncio.run(handler(SimpleNamespace(data={"entry_id": "missing", "mode": "normal"})))


def test_setup_sensor_listeners_accepts_dlb_sensor_options():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.hass = SimpleNamespace()
    coordinator.entry = make_config_entry(
        options={
            "dlb_l1_sensor": "sensor.l1",
            "dlb_l2_sensor": "sensor.l2",
            "dlb_l3_sensor": "sensor.l3",
        }
    )
    coordinator._sensor_unsubscribers = []
    coordinator._schedule_sensor_refresh = Mock()
    coordinator.data = None

    coordinator._setup_sensor_listeners()

    assert len(coordinator._sensor_unsubscribers) == 1


def test_set_current_service_requires_external_controller_mode():
    class _Services:
        def __init__(self):
            self.handlers = {}

        def async_register(self, domain, service, handler, schema=None):
            self.handlers[(domain, service)] = handler

    services = _Services()
    coordinator = SimpleNamespace(
        control_config=ControlConfig(control_mode=ControlMode.MANAGED_CONTROL),
        async_set_external_current_limit=AsyncMock(),
        async_request_refresh=AsyncMock(),
    )
    hass = SimpleNamespace(data={"webasto_unite": {"entry": coordinator}}, services=services)

    asyncio.run(integration_async_setup(hass, {}))

    handler = services.handlers[("webasto_unite", "set_current")]
    with pytest.raises(HomeAssistantError, match="External Controller"):
        asyncio.run(handler(SimpleNamespace(data={"entry_id": "entry", "current_a": 16})))
    coordinator.async_set_external_current_limit.assert_not_awaited()


def test_set_current_service_writes_in_external_controller_mode():
    class _Services:
        def __init__(self):
            self.handlers = {}

        def async_register(self, domain, service, handler, schema=None):
            self.handlers[(domain, service)] = handler

    services = _Services()
    coordinator = SimpleNamespace(
        control_config=ControlConfig(control_mode=ControlMode.EXTERNAL_CONTROLLER),
        async_set_external_current_limit=AsyncMock(),
        async_request_refresh=AsyncMock(),
    )
    hass = SimpleNamespace(data={"webasto_unite": {"entry": coordinator}}, services=services)

    asyncio.run(integration_async_setup(hass, {}))

    handler = services.handlers[("webasto_unite", "set_current")]
    asyncio.run(handler(SimpleNamespace(data={"entry_id": "entry", "current_a": 16})))

    coordinator.async_set_external_current_limit.assert_awaited_once_with(16)
    coordinator.async_request_refresh.assert_awaited_once()


def test_set_current_service_accepts_fractional_current_for_external_controller_mode():
    class _Services:
        def __init__(self):
            self.handlers = {}

        def async_register(self, domain, service, handler, schema=None):
            self.handlers[(domain, service)] = handler

    services = _Services()
    coordinator = SimpleNamespace(
        control_config=ControlConfig(control_mode=ControlMode.EXTERNAL_CONTROLLER),
        async_set_external_current_limit=AsyncMock(),
        async_request_refresh=AsyncMock(),
    )
    hass = SimpleNamespace(data={"webasto_unite": {"entry": coordinator}}, services=services)

    asyncio.run(integration_async_setup(hass, {}))

    handler = services.handlers[("webasto_unite", "set_current")]
    asyncio.run(handler(SimpleNamespace(data={"entry_id": "entry", "current_a": 6.82})))

    coordinator.async_set_external_current_limit.assert_awaited_once_with(6.82)
    coordinator.async_request_refresh.assert_awaited_once()


def test_phase_switch_services_are_registered():
    class _Services:
        def __init__(self):
            self.handlers = {}

        def async_register(self, domain, service, handler, schema=None):
            self.handlers[(domain, service)] = handler

    services = _Services()
    hass = SimpleNamespace(data={"webasto_unite": {}}, services=services)

    asyncio.run(integration_async_setup(hass, {}))

    assert ("webasto_unite", "set_current") in services.handlers
    assert ("webasto_unite", "request_phase_1p") in services.handlers
    assert ("webasto_unite", "request_phase_3p") in services.handlers
    assert ("webasto_unite", SERVICE_RESTORE_DEFAULT_PHASE) in services.handlers
    assert ("webasto_unite", "reset_phase_switch_state") in services.handlers
    assert ("webasto_unite", SERVICE_SOFT_RESET_CHARGER) in services.handlers


def test_manual_phase_switch_is_blocked_by_default():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator.write_queue = WriteQueueManager()
        coordinator.client = SimpleNamespace(write=AsyncMock())
        coordinator.data = SimpleNamespace(
            wallbox=WallboxState(
                installed_phases=3,
                available=True,
                vehicle_connected=True,
                phase_switch_mode_raw=1,
            )
        )

        with pytest.raises(ValueError, match="manual_phase_switching_disabled"):
            await coordinator.async_request_phase_switch(1)

        coordinator.client.write.assert_not_called()
        assert coordinator._phase_switch_last_result == "blocked"

    asyncio.run(_run())


def test_manual_3p_phase_switch_uses_plain_register_write():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_MANUAL_ONLY
        coordinator.phase_switch_manager = SimpleNamespace(
            active=False,
            last_target=None,
            last_block_reason=None,
            state="idle",
        )
        coordinator._phase_switch_task = None
        coordinator._phase_restore_task = None
        coordinator.hass = SimpleNamespace(async_create_task=lambda coro: asyncio.create_task(coro))
        coordinator.async_request_phase_switch = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator._flush_pending_external_current_limit = AsyncMock()
        coordinator._sync_phase_switch_diagnostics = Mock()
        coordinator._clear_control_write_blocked = Mock()

        await coordinator.async_schedule_phase_switch(3)
        await coordinator._phase_switch_task

        coordinator.async_request_phase_switch.assert_awaited_once_with(
            3,
            request_refresh=False,
            force_edge_trigger=False,
            wallbox=None,
        )

    asyncio.run(_run())


def test_manual_phase_switch_writes_register_only():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(
            control_mode=ControlMode.MANAGED_CONTROL,
            min_current_a=6.0,
            max_current_a=20.0,
        )
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_MANUAL_ONLY
        coordinator.write_queue = WriteQueueManager()
        coordinator.client = SimpleNamespace(write=AsyncMock(), read=AsyncMock())
        coordinator.wallbox_reader = SimpleNamespace(read_wallbox_state=AsyncMock())
        coordinator._phase_switch_sleep = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator.data = SimpleNamespace(
            wallbox=WallboxState(
                installed_phases=3,
                charge_point_phase_count=3,
                available=True,
                vehicle_connected=True,
                charging_active=True,
                current_limit_a=16.0,
                phase_switch_mode_raw=1,
            )
        )

        await coordinator.async_request_phase_switch(1)

        coordinator.client.write.assert_awaited_once_with(PHASE_SWITCH_MODE, 0)
        coordinator.client.read.assert_not_awaited()
        coordinator.wallbox_reader.read_wallbox_state.assert_not_awaited()
        coordinator._phase_switch_sleep.assert_not_awaited()
        assert coordinator._phase_switch_last_result == "register_written"
        assert coordinator._phase_switch_state == "phase_switch_settling"
        assert coordinator._phase_switch_last_target == "1P"
        assert coordinator._phase_session_override_active is True
        assert coordinator._phase_session_target == "1P"
        assert coordinator.write_runtime.last_control_write_reason is None
        assert coordinator.write_runtime.last_control_write_value_a is None

    asyncio.run(_run())


def test_external_controller_phase_switch_writes_register_only():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(
            control_mode=ControlMode.EXTERNAL_CONTROLLER,
            min_current_a=6.0,
            max_current_a=20.0,
        )
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_MANUAL_ONLY
        coordinator.write_queue = WriteQueueManager()
        coordinator.client = SimpleNamespace(write=AsyncMock(), read=AsyncMock())
        coordinator.wallbox_reader = SimpleNamespace(read_wallbox_state=AsyncMock())
        coordinator._phase_switch_sleep = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator.data = SimpleNamespace(
            wallbox=WallboxState(
                installed_phases=3,
                charge_point_phase_count=3,
                available=True,
                vehicle_connected=True,
                charging_active=True,
                current_limit_a=16.0,
                phase_switch_mode_raw=1,
            )
        )

        await coordinator.async_request_phase_switch(1)

        coordinator.client.write.assert_awaited_once_with(PHASE_SWITCH_MODE, 0)
        coordinator.client.read.assert_not_awaited()
        coordinator.wallbox_reader.read_wallbox_state.assert_not_awaited()
        assert coordinator._phase_switch_last_result == "register_written"

    asyncio.run(_run())


def test_manual_3p_phase_switch_allows_observed_1p_session_because_observation_is_diagnostic_only():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_MANUAL_ONLY
        coordinator.write_queue = WriteQueueManager()
        coordinator.client = SimpleNamespace(write=AsyncMock(), read=AsyncMock())
        coordinator.wallbox_reader = SimpleNamespace(read_wallbox_state=AsyncMock())
        coordinator._phase_switch_sleep = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator.data = SimpleNamespace(
            wallbox=WallboxState(
                installed_phases=3,
                charge_point_phase_count=3,
                available=True,
                vehicle_connected=True,
                charging_active=True,
                current_limit_a=6.0,
                phases_in_use=1,
                phase_switch_mode_raw=0,
            )
        )

        await coordinator.async_request_phase_switch(3)

        coordinator.client.write.assert_awaited_once_with(PHASE_SWITCH_MODE, 1)
        coordinator.client.read.assert_not_awaited()
        coordinator.wallbox_reader.read_wallbox_state.assert_not_awaited()
        assert coordinator._phase_switch_last_result == "register_written"
        assert coordinator._phase_session_override_active is False
        assert coordinator._phase_session_target is None

    asyncio.run(_run())


def test_manual_phase_switch_ignores_register_404_capability_value():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_MANUAL_ONLY
        coordinator.write_queue = WriteQueueManager()
        coordinator.client = SimpleNamespace(write=AsyncMock(), read=AsyncMock())
        coordinator._phase_switch_sleep = AsyncMock()
        coordinator.data = SimpleNamespace(
            wallbox=WallboxState(
                installed_phases=3,
                charge_point_phase_count=1,
                available=True,
                vehicle_connected=True,
                phase_switch_mode_raw=1,
            )
        )

        await coordinator.async_request_phase_switch(1)

        coordinator.client.write.assert_awaited_once_with(PHASE_SWITCH_MODE, 0)
        coordinator.client.read.assert_not_awaited()
        assert coordinator._phase_switch_last_result == "register_written"

    asyncio.run(_run())


def test_manual_phase_switch_does_not_verify_register_405_after_write():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_MANUAL_ONLY
        coordinator.write_queue = WriteQueueManager()
        coordinator.client = SimpleNamespace(write=AsyncMock(), read=AsyncMock(return_value=1))
        coordinator._phase_switch_sleep = AsyncMock()
        coordinator.data = SimpleNamespace(
            wallbox=WallboxState(
                installed_phases=3,
                charge_point_phase_count=3,
                available=True,
                vehicle_connected=True,
                phase_switch_mode_raw=1,
            )
        )

        await coordinator.async_request_phase_switch(1)

        coordinator.client.write.assert_awaited_once_with(PHASE_SWITCH_MODE, 0)
        coordinator.client.read.assert_not_awaited()
        assert coordinator._phase_switch_last_result == "register_written"

    asyncio.run(_run())


def test_manual_phase_switch_does_not_touch_current_when_register_value_was_stale():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(
            control_mode=ControlMode.MANAGED_CONTROL,
            min_current_a=6.0,
            max_current_a=20.0,
        )
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_MANUAL_ONLY
        coordinator.write_queue = WriteQueueManager()
        coordinator.client = SimpleNamespace(write=AsyncMock(), read=AsyncMock(return_value=0))
        coordinator.wallbox_reader = SimpleNamespace(read_wallbox_state=AsyncMock())
        coordinator._phase_switch_sleep = AsyncMock()
        coordinator.data = SimpleNamespace(
            wallbox=WallboxState(
                installed_phases=3,
                charge_point_phase_count=3,
                available=True,
                vehicle_connected=True,
                charging_active=True,
                current_limit_a=16.0,
                phases_in_use=1,
                phase_switch_mode_raw=0,
            )
        )

        await coordinator.async_request_phase_switch(3)

        coordinator.client.write.assert_awaited_once_with(PHASE_SWITCH_MODE, 1)
        coordinator.client.read.assert_not_awaited()
        coordinator.wallbox_reader.read_wallbox_state.assert_not_awaited()
        assert coordinator._phase_switch_last_result == "register_written"
        assert coordinator._phase_switch_state == "phase_switch_settling"
        assert coordinator.charging_paused is False
        assert coordinator.write_runtime.last_control_write_reason is None
        assert coordinator.write_runtime.last_control_write_value_a is None

    asyncio.run(_run())


def test_manual_phase_switch_does_not_retry_register_write():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(
            control_mode=ControlMode.MANAGED_CONTROL,
            min_current_a=6.0,
            max_current_a=20.0,
        )
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_MANUAL_ONLY
        coordinator.write_queue = WriteQueueManager()
        coordinator.client = SimpleNamespace(write=AsyncMock(), read=AsyncMock(side_effect=([0] * 12) + [1, 1]))
        coordinator.wallbox_reader = SimpleNamespace(read_wallbox_state=AsyncMock())
        coordinator._phase_switch_sleep = AsyncMock()
        coordinator.data = SimpleNamespace(
            wallbox=WallboxState(
                installed_phases=3,
                charge_point_phase_count=3,
                available=True,
                vehicle_connected=True,
                charging_active=True,
                current_limit_a=16.0,
                phases_in_use=1,
                phase_switch_mode_raw=0,
            )
        )

        await coordinator.async_request_phase_switch(3)

        coordinator.client.write.assert_awaited_once_with(PHASE_SWITCH_MODE, 1)
        coordinator.client.read.assert_not_awaited()
        coordinator.wallbox_reader.read_wallbox_state.assert_not_awaited()
        assert coordinator._phase_switch_last_result == "register_written"
        assert coordinator._phase_switch_state == "phase_switch_settling"
        assert coordinator._phase_switch_last_target == "3P"
        assert coordinator.charging_paused is False
        assert coordinator.write_runtime.last_control_write_reason is None
        assert coordinator.write_runtime.last_control_write_value_a is None

    asyncio.run(_run())


def test_manual_phase_switch_does_not_fail_on_physical_mismatch():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_MANUAL_ONLY
        coordinator.write_queue = WriteQueueManager()
        coordinator.client = SimpleNamespace(write=AsyncMock(), read=AsyncMock(return_value=0))
        coordinator.wallbox_reader = SimpleNamespace(read_wallbox_state=AsyncMock())
        coordinator._phase_switch_sleep = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator.data = SimpleNamespace(
            wallbox=WallboxState(
                installed_phases=3,
                charge_point_phase_count=3,
                available=True,
                vehicle_connected=True,
                charging_active=True,
                current_limit_a=16.0,
                phases_in_use=3,
                phase_switch_mode_raw=1,
            )
        )

        await coordinator.async_request_phase_switch(1)

        coordinator.client.write.assert_awaited_once_with(PHASE_SWITCH_MODE, 0)
        coordinator.wallbox_reader.read_wallbox_state.assert_not_awaited()
        assert coordinator._phase_switch_last_result == "register_written"
        assert coordinator._phase_switch_last_block_reason is None
        assert coordinator._phase_switch_state == "phase_switch_settling"

    asyncio.run(_run())


def test_manual_phase_switch_does_not_retry_on_physical_mismatch():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_MANUAL_ONLY
        coordinator.write_queue = WriteQueueManager()
        coordinator.client = SimpleNamespace(write=AsyncMock(), read=AsyncMock(return_value=1))
        coordinator.wallbox_reader = SimpleNamespace(read_wallbox_state=AsyncMock())
        coordinator._phase_switch_sleep = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator.data = SimpleNamespace(
            wallbox=WallboxState(
                installed_phases=3,
                charge_point_phase_count=3,
                available=True,
                vehicle_connected=True,
                charging_active=True,
                current_limit_a=16.0,
                phases_in_use=1,
                phase_switch_mode_raw=0,
            )
        )

        await coordinator.async_request_phase_switch(3)

        coordinator.client.write.assert_awaited_once_with(PHASE_SWITCH_MODE, 1)
        coordinator.wallbox_reader.read_wallbox_state.assert_not_awaited()
        assert coordinator._phase_switch_last_result == "register_written"
        assert coordinator._phase_switch_state == "phase_switch_settling"
        assert coordinator._phase_switch_last_target == "3P"

    asyncio.run(_run())


def test_manual_phase_switch_keeps_session_override_after_accepted_1p_request():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_MANUAL_ONLY
        coordinator.write_queue = WriteQueueManager()
        coordinator.client = SimpleNamespace(write=AsyncMock(), read=AsyncMock(return_value=0))
        coordinator.wallbox_reader = SimpleNamespace(read_wallbox_state=AsyncMock())
        coordinator._phase_switch_sleep = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator.data = SimpleNamespace(
            wallbox=WallboxState(
                installed_phases=3,
                charge_point_phase_count=3,
                available=True,
                vehicle_connected=True,
                charging_active=True,
                current_limit_a=16.0,
                phases_in_use=3,
                phase_switch_mode_raw=1,
            )
        )

        await coordinator.async_request_phase_switch(1)

        assert coordinator._phase_switch_last_result == "register_written"
        assert coordinator._phase_session_override_active is True
        assert coordinator._phase_session_target == "1P"
        assert coordinator._phase_restore_pending is False

    asyncio.run(_run())


def test_manual_phase_switch_does_not_wait_for_vehicle_resume():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_MANUAL_ONLY
        coordinator.write_queue = WriteQueueManager()
        coordinator.client = SimpleNamespace(write=AsyncMock(), read=AsyncMock(return_value=0))
        coordinator.wallbox_reader = SimpleNamespace(read_wallbox_state=AsyncMock())
        coordinator._phase_switch_sleep = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator.data = SimpleNamespace(
            wallbox=WallboxState(
                installed_phases=3,
                charge_point_phase_count=3,
                available=True,
                vehicle_connected=True,
                charging_active=True,
                current_limit_a=16.0,
                phases_in_use=3,
                phase_switch_mode_raw=1,
            )
        )

        await coordinator.async_request_phase_switch(1)

        coordinator.client.write.assert_awaited_once_with(PHASE_SWITCH_MODE, 0)
        coordinator.wallbox_reader.read_wallbox_state.assert_not_awaited()
        assert coordinator._phase_switch_last_result == "register_written"
        assert coordinator._phase_switch_state == "phase_switch_settling"
        assert coordinator.write_runtime.last_control_write_reason is None
        assert coordinator.write_runtime.last_control_write_value_a is None

    asyncio.run(_run())


def test_phase_policy_waits_for_stable_target_before_auto_ready():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator._phase_policy_candidate_target = None
    coordinator._phase_policy_candidate_since_monotonic = None
    coordinator._phase_policy_last_switch_monotonic = None
    coordinator._phase_policy_session_switch_count = 0
    coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_AUTOMATIC_SOLAR
    coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)

    initial = coordinator._apply_phase_policy_runtime_state(PhasePolicyDecision(decision="would_request_1p", target="1P"))

    assert initial.auto_ready is False
    assert initial.auto_block_reason == "waiting_for_stable_phase_target"
    assert initial.stable_required_s == AUTO_PHASE_STABLE_TO_1P_S

    coordinator._phase_policy_candidate_since_monotonic = monotonic() - AUTO_PHASE_STABLE_TO_1P_S - 1

    ready = coordinator._apply_phase_policy_runtime_state(PhasePolicyDecision(decision="would_request_1p", target="1P"))

    assert ready.auto_ready is True
    assert ready.auto_block_reason is None
    assert ready.stable_elapsed_s >= AUTO_PHASE_STABLE_TO_1P_S


def test_phase_policy_cooldown_blocks_auto_ready_after_switch():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator._phase_policy_candidate_target = "1P"
    coordinator._phase_policy_candidate_since_monotonic = monotonic() - AUTO_PHASE_STABLE_TO_1P_S - 1
    coordinator._phase_policy_last_switch_monotonic = monotonic()
    coordinator._phase_policy_session_switch_count = 1
    coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_AUTOMATIC_SOLAR
    coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)

    decision = coordinator._apply_phase_policy_runtime_state(PhasePolicyDecision(decision="would_request_1p", target="1P"))

    assert decision.auto_ready is False
    assert decision.auto_block_reason == "cooldown_active"
    assert decision.cooldown_remaining_s > 0
    assert decision.session_switch_count == 1


def test_phase_policy_blocks_failed_automatic_target_for_current_session():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator._phase_policy_candidate_target = "1P"
    coordinator._phase_policy_candidate_since_monotonic = monotonic() - AUTO_PHASE_STABLE_TO_1P_S - 1
    coordinator._phase_policy_last_switch_monotonic = None
    coordinator._phase_policy_session_switch_count = 0
    coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_AUTOMATIC_SOLAR
    coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
    coordinator._phase_runtime().record_policy_failed_target("1P")

    decision = coordinator._apply_phase_policy_runtime_state(PhasePolicyDecision(decision="would_request_1p", target="1P"))

    assert decision.auto_ready is False
    assert decision.auto_block_reason == "automatic_phase_switch_failed_this_session"
    assert decision.cooldown_remaining_s == 0.0


def test_restore_default_phase_does_not_start_automatic_solar_cooldown():
    class _AcceptedPhaseSwitchManager:
        active = False

        def __init__(self):
            self.last_result = None
            self.last_block_reason = None
            self.last_target = None
            self.state = "idle"

        async def request(self, **kwargs):
            self.last_result = "register_written"
            self.last_block_reason = None
            self.last_target = f"{kwargs['target_phases']}P"
            self.state = "phase_switch_settling"

    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator.phase_switch_manager = _AcceptedPhaseSwitchManager()
        coordinator.client = SimpleNamespace()
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_AUTOMATIC_SOLAR
        coordinator._phase_policy_last_switch_monotonic = None
        coordinator._phase_policy_session_switch_count = 0

        await coordinator.async_restore_default_phase_mode(
            WallboxState(
                installed_phases=3,
                available=True,
                vehicle_connected=True,
                charging_active=True,
                phases_in_use=1,
                phase_switch_mode_raw=0,
            ),
            request_refresh=False,
            force_edge_trigger=True,
        )

        assert coordinator._phase_policy_last_switch_monotonic is None
        assert coordinator._phase_policy_session_switch_count == 0

    asyncio.run(_run())


def test_automatic_solar_phase_policy_executes_when_ready():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.hass = SimpleNamespace(async_create_task=lambda coro: asyncio.create_task(coro))
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_AUTOMATIC_SOLAR
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator.phase_switch_manager = SimpleNamespace(
            active=False,
            last_result=None,
            last_block_reason=None,
            last_target=None,
            state="idle",
        )
        coordinator._phase_switch_task = None
        coordinator._phase_restore_task = None
        coordinator.async_request_refresh = AsyncMock()

        async def _request_phase_switch_success(*args, **kwargs):
            coordinator._phase_switch_last_result = "register_written"

        coordinator.async_request_phase_switch = AsyncMock(side_effect=_request_phase_switch_success)

        executed = await coordinator._maybe_execute_automatic_phase_policy(
            PhasePolicyDecision(decision="would_request_3p", target="3P", auto_ready=True)
        )

        assert executed is True
        assert coordinator._phase_switch_task is not None
        await coordinator._phase_switch_task
        coordinator.async_request_phase_switch.assert_awaited_once_with(
            3,
            request_refresh=False,
            force_edge_trigger=False,
            wallbox=None,
        )

        coordinator.control_config = ControlConfig(control_mode=ControlMode.EXTERNAL_CONTROLLER)
        coordinator.async_request_phase_switch.reset_mock()
        coordinator._phase_switch_task = None

        executed = await coordinator._maybe_execute_automatic_phase_policy(
            PhasePolicyDecision(
                decision="would_request_1p",
                target="1P",
                auto_ready=False,
                auto_block_reason="external_controller_mode",
            )
        )

        assert executed is False
        coordinator.async_request_phase_switch.assert_not_awaited()

    asyncio.run(_run())


def test_automatic_phase_switch_not_ready_does_not_start_failed_attempt_cooldown():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.hass = SimpleNamespace(async_create_task=lambda coro: asyncio.create_task(coro))
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_AUTOMATIC_SOLAR
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator.phase_switch_manager = SimpleNamespace(
            active=False,
            last_result="blocked",
            last_block_reason="phase_switch_in_progress",
            last_target="1P",
            state="blocked",
        )
        coordinator._phase_switch_task = None
        coordinator._phase_restore_task = None
        coordinator._phase_policy_last_switch_monotonic = None
        coordinator._phase_policy_session_switch_count = 0
        coordinator._phase_policy_candidate_target = "1P"
        coordinator._phase_policy_candidate_since_monotonic = monotonic() - AUTO_PHASE_STABLE_TO_1P_S - 1
        coordinator.async_request_refresh = AsyncMock()
        coordinator.async_request_phase_switch = AsyncMock(side_effect=ValueError("phase_switch_in_progress"))

        executed = await coordinator._maybe_execute_automatic_phase_policy(
            PhasePolicyDecision(
                decision="would_request_1p",
                target="1P",
                auto_ready=False,
                auto_block_reason="waiting_for_stable_phase_target",
            )
        )

        assert executed is False
        assert coordinator._phase_switch_task is None
        assert coordinator._phase_policy_last_switch_monotonic is None
        assert coordinator._phase_policy_session_switch_count == 0
        assert coordinator._phase_policy_candidate_target == "1P"

    asyncio.run(_run())


def test_central_phase_action_does_not_restore_3p_when_solar_wants_1p():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator._mode = ChargeMode.SOLAR
        coordinator._charging_paused = False
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_AUTOMATIC_SOLAR
        coordinator.phase_switch_manager = SimpleNamespace(active=False)
        coordinator._phase_switch_task = None
        coordinator._phase_restore_task = None
        wallbox = WallboxState(
            installed_phases=3,
            available=True,
            vehicle_connected=True,
            charging_active=True,
            phases_in_use=3,
            phase_switch_mode_raw=1,
        )

        executed = await coordinator._maybe_schedule_phase_action(
            wallbox=wallbox,
            phase_observability=SimpleNamespace(phase_offer_state="offering_3p"),
            phase_policy=PhasePolicyDecision(
                decision="would_request_1p",
                target="1P",
                auto_ready=False,
                auto_block_reason="cooldown_active",
            ),
            vehicle_disconnected=False,
            phase_session_settling=False,
        )

        assert executed is False

    asyncio.run(_run())


def test_manual_phase_switch_writes_even_when_current_session_is_active():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_MANUAL_ONLY
        coordinator.write_queue = WriteQueueManager()
        coordinator.client = SimpleNamespace(write=AsyncMock(), read=AsyncMock(return_value=0))
        wallbox = WallboxState(
            installed_phases=3,
            charge_point_phase_count=3,
            available=True,
            vehicle_connected=True,
            charging_active=True,
            current_limit_a=9.0,
            phases_in_use=1,
            phase_switch_mode_raw=1,
        )
        coordinator.wallbox_reader = SimpleNamespace(read_wallbox_state=AsyncMock(return_value=wallbox))
        coordinator._phase_switch_sleep = AsyncMock()
        coordinator.data = SimpleNamespace(
            wallbox=WallboxState(
                installed_phases=3,
                charge_point_phase_count=3,
                available=True,
                vehicle_connected=True,
                charging_active=True,
                current_limit_a=16.0,
                phases_in_use=3,
                phase_switch_mode_raw=1,
            )
        )

        await coordinator.async_request_phase_switch(1)

        coordinator.client.write.assert_awaited_once_with(PHASE_SWITCH_MODE, 0)
        coordinator.wallbox_reader.read_wallbox_state.assert_not_awaited()
        assert coordinator._phase_switch_last_result == "register_written"
        assert coordinator._phase_switch_state == "phase_switch_settling"
        assert coordinator.charging_paused is False
        assert coordinator.write_runtime.last_control_write_reason is None
        assert coordinator.write_runtime.last_control_write_value_a is None

    asyncio.run(_run())


def test_restore_default_phase_does_not_write_without_connected_vehicle():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_MANUAL_ONLY
        coordinator.write_queue = WriteQueueManager()
        coordinator.client = SimpleNamespace(write=AsyncMock(), read=AsyncMock(return_value=1))
        coordinator._phase_switch_sleep = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator.data = SimpleNamespace(
            wallbox=WallboxState(
                installed_phases=3,
                charge_point_phase_count=3,
                available=True,
                vehicle_connected=False,
                phase_switch_mode_raw=0,
            )
        )

        await coordinator.async_restore_default_phase_mode()

        coordinator.client.write.assert_not_called()
        coordinator.client.read.assert_not_called()
        assert coordinator._phase_switch_last_result == "vehicle_not_connected"
        assert coordinator._phase_switch_last_block_reason == "vehicle_not_connected"
        assert coordinator._phase_switch_last_target == "3P"
        assert coordinator._phase_session_override_active is False
        assert coordinator._phase_restore_pending is False

    asyncio.run(_run())


def test_restore_default_phase_noops_when_already_in_default_mode():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_MANUAL_ONLY
        coordinator.write_queue = WriteQueueManager()
        coordinator.client = SimpleNamespace(write=AsyncMock(), read=AsyncMock())
        coordinator.async_request_refresh = AsyncMock()
        coordinator.data = SimpleNamespace(
            wallbox=WallboxState(
                installed_phases=3,
                charge_point_phase_count=3,
                available=True,
                vehicle_connected=False,
                phase_switch_mode_raw=1,
            )
        )

        await coordinator.async_restore_default_phase_mode()

        coordinator.client.write.assert_not_called()
        assert coordinator._phase_switch_last_result == "vehicle_not_connected"
        assert coordinator._phase_switch_last_target == "3P"

    asyncio.run(_run())


def test_restore_default_phase_rewrites_when_register_matches_but_physical_phases_do_not():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_MANUAL_ONLY
        coordinator.write_queue = WriteQueueManager()
        coordinator.client = SimpleNamespace(write=AsyncMock(), read=AsyncMock(return_value=1))
        wallbox = WallboxState(
            installed_phases=3,
            charge_point_phase_count=3,
            available=True,
            vehicle_connected=True,
            charging_active=True,
            current_limit_a=9.0,
            phases_in_use=1,
            phase_switch_mode_raw=1,
        )
        coordinator.wallbox_reader = SimpleNamespace(read_wallbox_state=AsyncMock(return_value=wallbox))
        coordinator._phase_switch_sleep = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator.data = SimpleNamespace(wallbox=wallbox)

        await coordinator.async_restore_default_phase_mode()

        coordinator.client.write.assert_awaited_once_with(PHASE_SWITCH_MODE, 1)
        coordinator.wallbox_reader.read_wallbox_state.assert_awaited_once()
        assert coordinator._phase_switch_last_result == "register_written"
        assert coordinator._phase_switch_last_target == "3P"

    asyncio.run(_run())


def test_phase_session_override_clears_without_register_write_after_unplug():
    async def _run():
        wallbox = WallboxState(
            installed_phases=3,
            charge_point_phase_count=3,
            available=True,
            vehicle_connected=False,
            phase_switch_mode_raw=0,
        )
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.hass = SimpleNamespace(states=SimpleNamespace(get=lambda entity_id: None))
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.sensor_adapter = HaSensorAdapter(coordinator.hass)
        coordinator.wallbox_reader = SimpleNamespace(read_wallbox_state=AsyncMock(return_value=wallbox))
        coordinator.write_queue = WriteQueueManager()
        coordinator.client = SimpleNamespace(
            write=AsyncMock(),
            read=AsyncMock(return_value=1),
            stats=SimpleNamespace(last_error=None),
        )
        coordinator._mode = ChargeMode.NORMAL
        coordinator._charging_paused = False
        coordinator._solar_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._last_vehicle_connected = True
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_MANUAL_ONLY
        coordinator._phase_session_override_active = True
        coordinator._phase_session_target = "1P"
        coordinator._phase_restore_pending = False
        coordinator._phase_switch_sleep = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator._sensor_refresh_task = None
        install_runtime_guards(coordinator, startup_ready=True)
        install_mock_write_runtime(coordinator)

        snapshot = await coordinator._async_update_data()

        assert coordinator._phase_restore_task is None
        coordinator.client.write.assert_not_called()
        assert snapshot.phase_restore_pending is False
        assert coordinator._phase_session_override_active is False
        assert coordinator._phase_session_target is None
        assert coordinator._phase_restore_pending is False
        assert coordinator._last_vehicle_connected is False

    asyncio.run(_run())


def test_new_session_3p_restore_is_diagnostic_only_after_settle():
    async def _run():
        wallbox = WallboxState(
            installed_phases=3,
            charge_point_phase_count=3,
            available=True,
            vehicle_connected=True,
            charging_active=False,
            phase_switch_mode_raw=1,
        )
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.hass = SimpleNamespace(
            states=SimpleNamespace(get=lambda entity_id: None),
            async_create_task=lambda coro: asyncio.create_task(coro),
        )
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.sensor_adapter = HaSensorAdapter(coordinator.hass)
        coordinator.wallbox_reader = SimpleNamespace(read_wallbox_state=AsyncMock(return_value=wallbox))
        coordinator.write_queue = WriteQueueManager()
        coordinator.client = SimpleNamespace(
            write=AsyncMock(),
            read=AsyncMock(side_effect=[0, 0, 1, 1]),
            stats=SimpleNamespace(last_error=None),
        )
        coordinator._mode = ChargeMode.NORMAL
        coordinator._charging_paused = False
        coordinator._solar_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._last_vehicle_connected = False
        coordinator._vehicle_connection_initialized = True
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_MANUAL_ONLY
        coordinator._phase_session_override_active = False
        coordinator._phase_session_target = None
        coordinator._phase_restore_pending = False
        coordinator._phase_switch_sleep = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator._sensor_refresh_task = None
        install_runtime_guards(coordinator, startup_ready=True)
        install_mock_write_runtime(coordinator)

        snapshot = await coordinator._async_update_data()

        assert snapshot.phase_recovery_warning == "waiting_for_phase_startup_settle"
        assert coordinator._phase_restore_task is None

        coordinator.phase_runtime.session_started_monotonic = monotonic() - 100.0
        coordinator.write_runtime.enqueue_decision.reset_mock()

        snapshot = await coordinator._async_update_data()

        assert snapshot.phase_recovery_warning is None
        assert coordinator.write_runtime.enqueue_decision.await_args.args[0].should_write is True
        assert coordinator._phase_restore_task is None
        coordinator.client.write.assert_not_called()

    asyncio.run(_run())


def test_session_end_resets_runtime_charge_mode_to_default():
    async def _run():
        wallbox = WallboxState(
            installed_phases=3,
            charge_point_phase_count=3,
            available=True,
            vehicle_connected=False,
            phase_switch_mode_raw=1,
        )
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.hass = SimpleNamespace(states=SimpleNamespace(get=lambda entity_id: None))
        coordinator.entry = make_config_entry(
            data={"host": "192.168.1.10", "installed_phases": "3p"},
            options={"startup_charge_mode": "normal"},
        )
        coordinator.control_config = ControlConfig(
            control_mode=ControlMode.MANAGED_CONTROL,
            solar_control_strategy=SolarControlStrategy.SMART_SOLAR,
        )
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.sensor_adapter = HaSensorAdapter(coordinator.hass)
        coordinator.wallbox_reader = SimpleNamespace(read_wallbox_state=AsyncMock(return_value=wallbox))
        coordinator.write_queue = WriteQueueManager()
        coordinator.client = SimpleNamespace(
            write=AsyncMock(),
            read=AsyncMock(return_value=1),
            stats=SimpleNamespace(last_error=None),
        )
        coordinator._mode = ChargeMode.SOLAR
        coordinator._active_solar_strategy = SolarControlStrategy.SMART_SOLAR
        coordinator._charging_paused = False
        coordinator._solar_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._last_vehicle_connected = True
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_AUTOMATIC_SOLAR
        coordinator._phase_session_override_active = False
        coordinator._phase_session_target = None
        coordinator._phase_restore_pending = False
        coordinator._phase_switch_sleep = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator._sensor_refresh_task = None
        install_runtime_guards(coordinator, startup_ready=True)
        install_mock_write_runtime(coordinator)

        snapshot = await coordinator._async_update_data()

        assert snapshot.mode == ChargeMode.NORMAL
        assert snapshot.effective_mode == ChargeMode.NORMAL
        assert coordinator._mode == ChargeMode.NORMAL
        assert coordinator._solar_until_unplug_active is False
        assert coordinator._fixed_current_until_unplug_active is False
        assert coordinator._last_vehicle_connected is False

    asyncio.run(_run())


def test_vehicle_disconnect_does_not_write_current_or_phase_registers():
    async def _run():
        wallbox = WallboxState(
            installed_phases=3,
            charge_point_phase_count=3,
            available=True,
            vehicle_connected=False,
            charging_active=False,
            current_limit_a=16.0,
            phase_switch_mode_raw=1,
        )
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.hass = SimpleNamespace(states=SimpleNamespace(get=lambda entity_id: None))
        coordinator.entry = make_config_entry(
            data={"host": "192.168.1.10", "installed_phases": "3p"},
            options={"startup_charge_mode": "normal"},
        )
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.sensor_adapter = HaSensorAdapter(coordinator.hass)
        coordinator.wallbox_reader = SimpleNamespace(read_wallbox_state=AsyncMock(return_value=wallbox))
        coordinator.write_queue = WriteQueueManager()
        coordinator.client = SimpleNamespace(
            write=AsyncMock(),
            read=AsyncMock(return_value=1),
            stats=SimpleNamespace(last_error=None),
        )
        coordinator._mode = ChargeMode.NORMAL
        coordinator._charging_paused = False
        coordinator._solar_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._last_vehicle_connected = True
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_MANUAL_ONLY
        coordinator._phase_session_override_active = False
        coordinator._phase_session_target = None
        coordinator._phase_restore_pending = False
        coordinator._phase_switch_sleep = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator._sensor_refresh_task = None
        install_runtime_guards(coordinator, startup_ready=True)
        install_mock_write_runtime(coordinator)

        await coordinator._async_update_data()

        coordinator.write_runtime.write_current_now.assert_not_called()
        coordinator.write_runtime.enqueue_decision.assert_awaited_once()
        assert coordinator.write_runtime.enqueue_decision.await_args.kwargs["current_snapshot"].wallbox is wallbox

    asyncio.run(_run())


def test_vehicle_disconnect_does_not_write_zero_current_in_monitoring_only():
    async def _run():
        wallbox = WallboxState(
            installed_phases=3,
            charge_point_phase_count=3,
            available=True,
            vehicle_connected=False,
            charging_active=False,
            current_limit_a=16.0,
            phase_switch_mode_raw=1,
        )
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.hass = SimpleNamespace(states=SimpleNamespace(get=lambda entity_id: None))
        coordinator.entry = make_config_entry(
            data={"host": "192.168.1.10", "installed_phases": "3p"},
            options={"startup_charge_mode": "normal"},
        )
        coordinator.control_config = ControlConfig(control_mode=ControlMode.KEEPALIVE_ONLY)
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.sensor_adapter = HaSensorAdapter(coordinator.hass)
        coordinator.wallbox_reader = SimpleNamespace(read_wallbox_state=AsyncMock(return_value=wallbox))
        coordinator.write_queue = WriteQueueManager()
        coordinator.client = SimpleNamespace(
            write=AsyncMock(),
            read=AsyncMock(return_value=1),
            stats=SimpleNamespace(last_error=None),
        )
        coordinator._mode = ChargeMode.NORMAL
        coordinator._charging_paused = False
        coordinator._solar_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._last_vehicle_connected = True
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_MANUAL_ONLY
        coordinator._phase_session_override_active = False
        coordinator._phase_session_target = None
        coordinator._phase_restore_pending = False
        coordinator._phase_switch_sleep = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator._sensor_refresh_task = None
        install_runtime_guards(coordinator, startup_ready=True)
        install_mock_write_runtime(coordinator)

        await coordinator._async_update_data()

        coordinator.write_runtime.write_current_now.assert_not_called()
        coordinator.write_runtime.enqueue_decision.assert_awaited_once()
        assert coordinator.write_runtime.enqueue_decision.await_args.kwargs["allows_control_writes"] is False

    asyncio.run(_run())


def test_no_vehicle_state_does_not_write_current_when_stale_limit_is_reported_after_restart():
    async def _run():
        wallbox = WallboxState(
            installed_phases=3,
            charge_point_phase_count=3,
            available=True,
            vehicle_connected=False,
            charging_active=False,
            current_limit_a=16.0,
            phase_switch_mode_raw=1,
        )
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.hass = SimpleNamespace(states=SimpleNamespace(get=lambda entity_id: None))
        coordinator.entry = make_config_entry(
            data={"host": "192.168.1.10", "installed_phases": "3p"},
            options={"startup_charge_mode": "normal"},
        )
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.sensor_adapter = HaSensorAdapter(coordinator.hass)
        coordinator.wallbox_reader = SimpleNamespace(read_wallbox_state=AsyncMock(return_value=wallbox))
        coordinator.write_queue = WriteQueueManager()
        coordinator.client = SimpleNamespace(
            write=AsyncMock(),
            read=AsyncMock(return_value=1),
            stats=SimpleNamespace(last_error=None),
        )
        coordinator._mode = ChargeMode.NORMAL
        coordinator._charging_paused = False
        coordinator._solar_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._last_vehicle_connected = False
        coordinator._vehicle_connection_initialized = True
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_MANUAL_ONLY
        coordinator._phase_session_override_active = False
        coordinator._phase_session_target = None
        coordinator._phase_restore_pending = False
        coordinator._phase_switch_sleep = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator._sensor_refresh_task = None
        install_runtime_guards(coordinator, startup_ready=True)
        install_mock_write_runtime(coordinator)

        await coordinator._async_update_data()

        coordinator.write_runtime.write_current_now.assert_not_called()
        coordinator.write_runtime.enqueue_decision.assert_awaited_once()

    asyncio.run(_run())


def test_session_end_clears_override_without_register_write_even_when_override_flag_was_lost():
    async def _run():
        wallbox = WallboxState(
            installed_phases=3,
            charge_point_phase_count=3,
            available=True,
            vehicle_connected=False,
            phase_switch_mode_raw=0,
        )
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.hass = SimpleNamespace(states=SimpleNamespace(get=lambda entity_id: None))
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.sensor_adapter = HaSensorAdapter(coordinator.hass)
        coordinator.wallbox_reader = SimpleNamespace(read_wallbox_state=AsyncMock(return_value=wallbox))
        coordinator.write_queue = WriteQueueManager()
        coordinator.client = SimpleNamespace(
            write=AsyncMock(),
            read=AsyncMock(return_value=1),
            stats=SimpleNamespace(last_error=None),
        )
        coordinator._mode = ChargeMode.SOLAR
        coordinator._charging_paused = False
        coordinator._solar_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._last_vehicle_connected = True
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_MANUAL_ONLY
        coordinator._phase_session_override_active = False
        coordinator._phase_session_target = None
        coordinator._phase_restore_pending = False
        coordinator._phase_switch_sleep = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator._sensor_refresh_task = None
        install_runtime_guards(coordinator, startup_ready=True)
        install_mock_write_runtime(coordinator)

        snapshot = await coordinator._async_update_data()

        assert coordinator._phase_restore_task is None
        coordinator.client.write.assert_not_called()
        assert snapshot.phase_restore_pending is False
        assert coordinator._phase_session_override_active is False
        assert coordinator._phase_session_target is None
        assert coordinator._phase_restore_pending is False

    asyncio.run(_run())


def test_restart_phase_mismatch_without_vehicle_does_not_write_default_phase():
    async def _run():
        wallbox = WallboxState(
            installed_phases=3,
            charge_point_phase_count=3,
            available=True,
            vehicle_connected=False,
            phase_switch_mode_raw=0,
        )
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.hass = SimpleNamespace(states=SimpleNamespace(get=lambda entity_id: None))
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.sensor_adapter = HaSensorAdapter(coordinator.hass)
        coordinator.wallbox_reader = SimpleNamespace(read_wallbox_state=AsyncMock(return_value=wallbox))
        coordinator.write_queue = WriteQueueManager()
        coordinator.client = SimpleNamespace(
            write=AsyncMock(),
            read=AsyncMock(return_value=1),
            stats=SimpleNamespace(last_error=None),
        )
        coordinator._mode = ChargeMode.NORMAL
        coordinator._charging_paused = False
        coordinator._solar_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._last_vehicle_connected = False
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_MANUAL_ONLY
        coordinator._phase_session_override_active = False
        coordinator._phase_session_target = None
        coordinator._phase_restore_pending = False
        coordinator._phase_switch_sleep = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator._sensor_refresh_task = None
        install_runtime_guards(coordinator, startup_ready=True)
        install_mock_write_runtime(coordinator)

        snapshot = await coordinator._async_update_data()

        assert coordinator._phase_restore_task is None
        assert snapshot.phase_restore_pending is False
        coordinator.client.write.assert_not_called()
        assert coordinator._phase_session_override_active is False
        assert coordinator._phase_restore_pending is False

    asyncio.run(_run())


def test_restart_phase_mismatch_with_connected_vehicle_is_diagnostic_only_in_normal_mode():
    async def _run():
        wallbox = WallboxState(
            installed_phases=3,
            charge_point_phase_count=3,
            available=True,
            vehicle_connected=True,
            phase_switch_mode_raw=0,
        )
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.hass = SimpleNamespace(states=SimpleNamespace(get=lambda entity_id: None))
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.sensor_adapter = HaSensorAdapter(coordinator.hass)
        coordinator.wallbox_reader = SimpleNamespace(read_wallbox_state=AsyncMock(return_value=wallbox))
        coordinator.write_queue = WriteQueueManager()
        coordinator.client = SimpleNamespace(
            write=AsyncMock(),
            read=AsyncMock(return_value=1),
            stats=SimpleNamespace(last_error=None),
        )
        coordinator._mode = ChargeMode.NORMAL
        coordinator._charging_paused = False
        coordinator._solar_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._last_vehicle_connected = False
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_MANUAL_ONLY
        coordinator._phase_session_override_active = False
        coordinator._phase_session_target = None
        coordinator._phase_restore_pending = False
        coordinator._phase_switch_sleep = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator._sensor_refresh_task = None
        install_runtime_guards(coordinator, startup_ready=True)
        install_mock_write_runtime(coordinator)

        snapshot = await coordinator._async_update_data()

        assert coordinator._phase_restore_task is None
        assert snapshot.phase_session_override_active is True
        assert snapshot.phase_session_target == "1P"
        assert snapshot.phase_restore_pending is False
        coordinator.client.write.assert_not_called()
        assert coordinator._phase_session_override_active is True
        assert coordinator._phase_session_target == "1P"
        assert coordinator._phase_restore_pending is False

    asyncio.run(_run())


def test_existing_phase_session_override_stays_diagnostic_only_in_normal_mode():
    async def _run():
        wallbox = WallboxState(
            installed_phases=3,
            charge_point_phase_count=3,
            available=True,
            vehicle_connected=True,
            phase_switch_mode_raw=0,
        )
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.hass = SimpleNamespace(states=SimpleNamespace(get=lambda entity_id: None))
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.sensor_adapter = HaSensorAdapter(coordinator.hass)
        coordinator.wallbox_reader = SimpleNamespace(read_wallbox_state=AsyncMock(return_value=wallbox))
        coordinator.write_queue = WriteQueueManager()
        coordinator.client = SimpleNamespace(
            write=AsyncMock(),
            read=AsyncMock(return_value=1),
            stats=SimpleNamespace(last_error=None),
        )
        coordinator._mode = ChargeMode.NORMAL
        coordinator._charging_paused = False
        coordinator._solar_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._last_vehicle_connected = True
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_AUTOMATIC_SOLAR
        coordinator._phase_session_override_active = True
        coordinator._phase_session_target = "1P"
        coordinator._phase_restore_pending = False
        coordinator._phase_switch_sleep = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator._sensor_refresh_task = None
        install_runtime_guards(coordinator, startup_ready=True)
        install_mock_write_runtime(coordinator)

        snapshot = await coordinator._async_update_data()

        assert coordinator._phase_restore_task is None
        assert snapshot.phase_session_override_active is True
        assert snapshot.phase_session_target == "1P"
        assert snapshot.phase_restore_pending is False
        coordinator.client.write.assert_not_called()
        assert coordinator._phase_session_override_active is True
        assert coordinator._phase_session_target == "1P"
        assert coordinator._phase_restore_pending is False

    asyncio.run(_run())


def test_existing_phase_session_override_stays_active_while_connected_in_solar_mode():
    async def _run():
        wallbox = WallboxState(
            installed_phases=3,
            charge_point_phase_count=3,
            available=True,
            vehicle_connected=True,
            phase_switch_mode_raw=0,
        )
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.hass = SimpleNamespace(states=SimpleNamespace(get=lambda entity_id: None))
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.sensor_adapter = HaSensorAdapter(coordinator.hass)
        coordinator.wallbox_reader = SimpleNamespace(read_wallbox_state=AsyncMock(return_value=wallbox))
        coordinator.write_queue = WriteQueueManager()
        coordinator.client = SimpleNamespace(
            write=AsyncMock(),
            read=AsyncMock(return_value=1),
            stats=SimpleNamespace(last_error=None),
        )
        coordinator._mode = ChargeMode.SOLAR
        coordinator._charging_paused = False
        coordinator._solar_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._last_vehicle_connected = True
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_AUTOMATIC_SOLAR
        coordinator._phase_session_override_active = True
        coordinator._phase_session_target = "1P"
        coordinator._phase_restore_pending = False
        coordinator._phase_switch_sleep = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator._sensor_refresh_task = None
        install_runtime_guards(coordinator, startup_ready=True)
        install_mock_write_runtime(coordinator)

        snapshot = await coordinator._async_update_data()

        coordinator.client.write.assert_not_called()
        assert snapshot.phase_session_override_active is True
        assert snapshot.phase_session_target == "1P"
        assert snapshot.phase_restore_pending is False
        assert snapshot.phase_policy_block_reason != "phase_restore_pending"

    asyncio.run(_run())


def test_restart_phase_mismatch_without_vehicle_does_not_write_even_when_register_404_reports_1p():
    async def _run():
        wallbox = WallboxState(
            installed_phases=3,
            charge_point_phase_count=1,
            available=True,
            vehicle_connected=False,
            phase_switch_mode_raw=0,
        )
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.hass = SimpleNamespace(states=SimpleNamespace(get=lambda entity_id: None))
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.sensor_adapter = HaSensorAdapter(coordinator.hass)
        coordinator.wallbox_reader = SimpleNamespace(read_wallbox_state=AsyncMock(return_value=wallbox))
        coordinator.write_queue = WriteQueueManager()
        coordinator.client = SimpleNamespace(
            write=AsyncMock(),
            read=AsyncMock(return_value=1),
            stats=SimpleNamespace(last_error=None),
        )
        coordinator._mode = ChargeMode.NORMAL
        coordinator._charging_paused = False
        coordinator._solar_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._last_vehicle_connected = False
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_MANUAL_ONLY
        coordinator._phase_session_override_active = False
        coordinator._phase_session_target = None
        coordinator._phase_restore_pending = False
        coordinator._phase_switch_sleep = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator._sensor_refresh_task = None
        install_runtime_guards(coordinator, startup_ready=True)
        install_mock_write_runtime(coordinator)

        snapshot = await coordinator._async_update_data()

        assert coordinator._phase_restore_task is None
        assert snapshot.phase_restore_pending is False
        coordinator.client.write.assert_not_called()
        assert coordinator._phase_session_override_active is False
        assert coordinator._phase_restore_pending is False

    asyncio.run(_run())


def test_new_plugin_session_resets_phase_policy_transients_without_phase_register_write():
    async def _run():
        wallbox = WallboxState(
            installed_phases=3,
            charge_point_phase_count=3,
            available=True,
            vehicle_connected=True,
            charging_active=False,
            phase_switch_mode_raw=1,
            current_limit_a=16.0,
        )
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.hass = SimpleNamespace(states=SimpleNamespace(get=lambda entity_id: None))
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.sensor_adapter = HaSensorAdapter(coordinator.hass)
        coordinator.wallbox_reader = SimpleNamespace(read_wallbox_state=AsyncMock(return_value=wallbox))
        coordinator.write_queue = WriteQueueManager()
        coordinator.client = SimpleNamespace(
            write=AsyncMock(),
            read=AsyncMock(return_value=1),
            stats=SimpleNamespace(last_error=None),
        )
        coordinator._mode = ChargeMode.NORMAL
        coordinator._charging_paused = False
        coordinator._solar_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._last_vehicle_connected = False
        coordinator._vehicle_connection_initialized = True
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_AUTOMATIC_SOLAR
        coordinator._phase_policy_candidate_target = "1P"
        coordinator._phase_policy_candidate_since_monotonic = monotonic() - AUTO_PHASE_STABLE_TO_1P_S
        coordinator._phase_policy_last_switch_monotonic = monotonic()
        coordinator._phase_policy_session_switch_count = 3
        coordinator._phase_runtime().record_policy_failed_target("1P")
        coordinator._phase_session_override_active = True
        coordinator._phase_session_target = "1P"
        coordinator._phase_restore_pending = True
        coordinator._phase_switch_sleep = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator._sensor_refresh_task = None
        install_runtime_guards(coordinator, startup_ready=True)
        install_mock_write_runtime(coordinator)

        snapshot = await coordinator._async_update_data()

        coordinator.client.write.assert_not_called()
        assert coordinator._phase_restore_task is None
        assert coordinator._phase_policy_candidate_target is None
        assert coordinator._phase_policy_candidate_since_monotonic is None
        assert coordinator._phase_policy_last_switch_monotonic is None
        assert coordinator._phase_policy_session_switch_count == 0
        assert coordinator._phase_runtime().policy_failed_targets == set()
        assert snapshot.phase_recovery_warning == "waiting_for_phase_startup_settle"

    asyncio.run(_run())


def test_automatic_solar_phase_policy_full_update_schedules_phase_switch():
    async def _run():
        wallbox = WallboxState(
            installed_phases=3,
            charge_point_phase_count=3,
            available=True,
            vehicle_connected=True,
            charging_active=True,
            phases_in_use=3,
            current_limit_a=6.0,
            active_power_w=4200.0,
            voltage_l1_v=230.0,
            voltage_l2_v=230.0,
            voltage_l3_v=230.0,
            phase_switch_mode_raw=1,
        )
        hass = SimpleNamespace(
            states=SimpleNamespace(
                get=lambda entity_id: {
                    "sensor.solar_surplus": SimpleNamespace(state="0", attributes={"unit_of_measurement": "W"}),
                }.get(entity_id)
            )
        )
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.hass = hass
        coordinator.entry = make_config_entry(
            data={"host": "192.168.1.10", "installed_phases": "3p"},
            options={
                "solar_control_strategy": "smart_solar",
                "solar_input_model": "surplus_sensor",
                "solar_surplus_sensor": "sensor.solar_surplus",
                "control_mode": "managed_control",
            },
        )
        coordinator.control_config = ControlConfig(
            control_mode=ControlMode.MANAGED_CONTROL,
            solar_control_strategy=SolarControlStrategy.SMART_SOLAR,
            solar_input_model=SolarInputModel.SURPLUS_SENSOR,
            min_current_a=6.0,
            max_current_a=16.0,
            solar_min_current_a=6.0,
        )
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.sensor_adapter = HaSensorAdapter(hass)
        coordinator.wallbox_reader = SimpleNamespace(read_wallbox_state=AsyncMock(return_value=wallbox))
        coordinator.write_queue = WriteQueueManager()
        coordinator.client = SimpleNamespace(
            write=AsyncMock(),
            read=AsyncMock(return_value=1),
            stats=SimpleNamespace(last_error=None),
        )
        coordinator._mode = ChargeMode.SOLAR
        coordinator._active_solar_strategy = SolarControlStrategy.SMART_SOLAR
        coordinator._charging_paused = False
        coordinator._solar_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._last_vehicle_connected = True
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_AUTOMATIC_SOLAR
        coordinator._phase_policy_candidate_target = "1P"
        coordinator._phase_policy_candidate_since_monotonic = monotonic() - AUTO_PHASE_STABLE_TO_1P_S - 1
        coordinator._phase_policy_last_switch_monotonic = None
        coordinator._phase_policy_session_switch_count = 0
        coordinator._phase_session_override_active = False
        coordinator._phase_session_target = None
        coordinator._phase_restore_pending = False
        coordinator._phase_switch_sleep = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator._sensor_refresh_task = None
        coordinator._phase_switch_task = None
        install_runtime_guards(coordinator, startup_ready=True)
        install_mock_write_runtime(coordinator)

        snapshot = await coordinator._async_update_data()

        assert coordinator._phase_switch_task is not None
        await coordinator._phase_switch_task
        coordinator.client.write.assert_awaited_once_with(PHASE_SWITCH_MODE, 0)
        assert snapshot.phase_policy_decision == "would_request_1p"
        assert snapshot.phase_policy_target == "1P"
        assert snapshot.phase_policy_auto_ready is True
        assert snapshot.phase_policy_auto_block_reason is None
        assert coordinator._phase_policy_session_switch_count == 1

    asyncio.run(_run())


def test_external_controller_full_update_blocks_automatic_control_but_keeps_current_interface_enabled():
    async def _run():
        wallbox = WallboxState(
            installed_phases=3,
            charge_point_phase_count=3,
            available=True,
            vehicle_connected=True,
            charging_active=True,
            phases_in_use=3,
            current_limit_a=10.0,
            phase_switch_mode_raw=1,
        )
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.hass = SimpleNamespace(states=SimpleNamespace(get=lambda entity_id: None))
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(
            control_mode=ControlMode.EXTERNAL_CONTROLLER,
            min_current_a=6.0,
            max_current_a=16.0,
        )
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.sensor_adapter = HaSensorAdapter(coordinator.hass)
        coordinator.wallbox_reader = SimpleNamespace(read_wallbox_state=AsyncMock(return_value=wallbox))
        coordinator.write_queue = WriteQueueManager()
        coordinator.client = SimpleNamespace(
            write=AsyncMock(),
            read=AsyncMock(return_value=1),
            stats=SimpleNamespace(last_error=None),
        )
        coordinator._mode = ChargeMode.NORMAL
        coordinator._charging_paused = False
        coordinator._solar_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._last_vehicle_connected = True
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_MANUAL_ONLY
        coordinator._phase_session_override_active = False
        coordinator._phase_session_target = None
        coordinator._phase_restore_pending = False
        coordinator._phase_switch_sleep = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator._sensor_refresh_task = None
        install_runtime_guards(coordinator, startup_ready=True)
        install_mock_write_runtime(coordinator)

        snapshot = await coordinator._async_update_data()

        coordinator.write_runtime.enqueue_decision.assert_awaited_once()
        assert coordinator.write_runtime.enqueue_decision.await_args.kwargs["allows_control_writes"] is False
        assert coordinator.write_runtime.enqueue_decision.await_args.kwargs["blocked_reason"] == "external_controller_mode"
        assert snapshot.control_writes_enabled is True
        assert snapshot.control_mode == ControlMode.EXTERNAL_CONTROLLER
        coordinator.client.write.assert_not_called()

    asyncio.run(_run())


def test_reset_phase_switch_state_clears_diagnostics():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator._phase_switch_last_result = "blocked"
    coordinator._phase_switch_last_block_reason = "observed_1p"
    coordinator._phase_switch_last_target = "3P"
    coordinator._phase_switch_state = "blocked"

    coordinator.reset_phase_switch_state()

    assert coordinator._phase_switch_last_result is None
    assert coordinator._phase_switch_last_block_reason is None
    assert coordinator._phase_switch_last_target is None
    assert coordinator._phase_switch_state == "idle"


def test_manual_phase_switch_buttons_call_phase_switch_services():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry()
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_MANUAL_ONLY
        coordinator.data = SimpleNamespace(phase_switch_available=True, phase_switch_register_available=True)
        coordinator.async_schedule_phase_switch = AsyncMock()
        coordinator.async_schedule_restore_default_phase_mode = AsyncMock()
        coordinator.reset_phase_switch_state = Mock()
        coordinator.async_request_refresh = AsyncMock()

        request_1p = WebastoRequestPhase1PButton(coordinator)
        request_3p = WebastoRequestPhase3PButton(coordinator)
        restore = WebastoRestoreDefaultPhaseButton(coordinator)
        reset = WebastoResetPhaseSwitchStateButton(coordinator)

        assert request_1p._attr_name == "Switch to 1P"
        assert request_3p._attr_name == "Switch to 3P"
        assert restore._attr_name == "Restore Configured Phase"
        assert not hasattr(request_1p, "_attr_entity_category")
        assert not hasattr(request_3p, "_attr_entity_category")
        assert not hasattr(restore, "_attr_entity_category")
        assert reset._attr_entity_category == EntityCategory.DIAGNOSTIC
        assert request_1p.available is True
        assert request_3p.available is True

        await request_1p.async_press()
        await request_3p.async_press()
        await restore.async_press()
        await reset.async_press()

        assert coordinator.async_schedule_phase_switch.await_args_list[0].args == (1,)
        assert coordinator.async_schedule_phase_switch.await_args_list[1].args == (3,)
        coordinator.async_schedule_restore_default_phase_mode.assert_awaited_once()
        coordinator.reset_phase_switch_state.assert_called_once()
        coordinator.async_request_refresh.assert_awaited_once()

    asyncio.run(_run())


def test_soft_reset_charger_button_requires_rest_credentials_and_calls_coordinator():
    async def _run():
        coordinator = SimpleNamespace(
            entry=make_config_entry(
                options={
                    "rest_diagnostics_enabled": True,
                    "rest_username": "admin",
                    "rest_password": "secret",
                }
            ),
            async_soft_reset_charger=AsyncMock(),
        )

        button = WebastoSoftResetChargerButton(coordinator)

        assert button._attr_name == "Restart Charger"
        assert button._attr_entity_category == EntityCategory.DIAGNOSTIC
        assert button.available is True

        await button.async_press()

        coordinator.async_soft_reset_charger.assert_awaited_once()

    asyncio.run(_run())


def test_soft_reset_charger_button_unavailable_without_rest_credentials():
    coordinator = SimpleNamespace(
        entry=make_config_entry(options={"rest_diagnostics_enabled": True}),
        async_soft_reset_charger=AsyncMock(),
    )

    button = WebastoSoftResetChargerButton(coordinator)

    assert button.available is False


def test_phase_switch_select_exposes_evcc_compatible_options():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry()
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_MANUAL_ONLY
        coordinator.data = SimpleNamespace(phase_switch_register_available=True, phase_switch_mode_raw=1)
        coordinator.async_schedule_phase_switch = AsyncMock()

        select = WebastoPhaseSwitchSelect(coordinator)

        assert select.options == ["1", "3"]
        assert select.current_option == "3"
        assert select.available is True

        await select.async_select_option("1")

        coordinator.async_schedule_phase_switch.assert_awaited_once_with(1)

    asyncio.run(_run())


def test_phase_controls_are_not_created_when_phase_switching_is_off():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry()
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_OFF
        hass = SimpleNamespace(data={DOMAIN: {coordinator.entry.entry_id: coordinator}})
        buttons = []
        selects = []

        await button_async_setup_entry(hass, coordinator.entry, lambda entities: buttons.extend(entities))
        await select_async_setup_entry(hass, coordinator.entry, lambda entities: selects.extend(entities))

        assert [entity._attr_name for entity in buttons] == ["Refresh", "Reconnect"]
        assert [entity._attr_name for entity in selects] == ["Charge Mode"]

    asyncio.run(_run())


def test_phase_controls_are_not_created_in_monitoring_only_mode():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry()
        coordinator.control_config = ControlConfig(control_mode=ControlMode.KEEPALIVE_ONLY)
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_MANUAL_ONLY
        coordinator.data = SimpleNamespace(phase_switch_register_available=True, phase_switch_mode_raw=1)
        hass = SimpleNamespace(data={DOMAIN: {coordinator.entry.entry_id: coordinator}})
        buttons = []
        selects = []

        await button_async_setup_entry(hass, coordinator.entry, lambda entities: buttons.extend(entities))
        await select_async_setup_entry(hass, coordinator.entry, lambda entities: selects.extend(entities))

        assert [entity._attr_name for entity in buttons] == ["Refresh", "Reconnect"]
        assert [entity._attr_name for entity in selects] == ["Charge Mode"]
        assert WebastoRequestPhase1PButton(coordinator).available is False
        assert WebastoPhaseSwitchSelect(coordinator).available is False

    asyncio.run(_run())


def test_phase_controls_are_created_when_phase_switching_is_enabled():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry()
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_MANUAL_ONLY
        hass = SimpleNamespace(data={DOMAIN: {coordinator.entry.entry_id: coordinator}})
        buttons = []
        selects = []

        await button_async_setup_entry(hass, coordinator.entry, lambda entities: buttons.extend(entities))
        await select_async_setup_entry(hass, coordinator.entry, lambda entities: selects.extend(entities))

        assert [entity._attr_name for entity in buttons] == [
            "Refresh",
            "Reconnect",
            "Switch to 1P",
            "Switch to 3P",
            "Restore Configured Phase",
            "Clear Phase Switch Status",
        ]
        assert [entity._attr_name for entity in selects] == ["Charge Mode", "Phase Switch"]

    asyncio.run(_run())


def test_managed_normal_mode_allows_manual_phase_request_controls():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "installed_phases": "3p"})
    coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
    coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_MANUAL_ONLY
    coordinator.data = SimpleNamespace(
        effective_mode=ChargeMode.NORMAL,
        phase_switch_available=True,
        phase_switch_register_available=True,
        phase_switch_mode_raw=0,
    )

    request_1p = WebastoRequestPhase1PButton(coordinator)
    request_3p = WebastoRequestPhase3PButton(coordinator)
    phase_select = WebastoPhaseSwitchSelect(coordinator)
    restore = WebastoRestoreDefaultPhaseButton(coordinator)

    assert request_1p.available is True
    assert request_3p.available is True
    assert phase_select.available is True
    assert restore.available is True


def test_manual_phase_switch_buttons_follow_register_availability_like_phase_select():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry()
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_MANUAL_ONLY
        coordinator.data = SimpleNamespace(phase_switch_available=False, phase_switch_register_available=True)
        coordinator.async_schedule_phase_switch = AsyncMock()

        request_1p = WebastoRequestPhase1PButton(coordinator)

        assert request_1p.available is True

        await request_1p.async_press()

        coordinator.async_schedule_phase_switch.assert_awaited_once_with(1)

    asyncio.run(_run())


def test_manual_phase_switch_buttons_are_unavailable_without_phase_register():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry()
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator._phase_switching_mode = PHASE_SWITCHING_MODE_MANUAL_ONLY
        coordinator.data = SimpleNamespace(phase_switch_available=True, phase_switch_register_available=False)
        coordinator.async_schedule_phase_switch = AsyncMock()

        request_1p = WebastoRequestPhase1PButton(coordinator)

        assert request_1p.available is False

        await request_1p.async_press()

        coordinator.async_schedule_phase_switch.assert_not_called()

    asyncio.run(_run())


def test_reported_current_mismatch_overrides_stale_internal_write_state():
    controller = make_controller()
    controller.mark_current_written(16.0)
    wallbox = WallboxState(
        installed_phases=3,
        vehicle_connected=True,
        charging_active=True,
        current_limit_a=6.0,
    )
    sensors = HaSensorSnapshot(phase_currents=PhaseCurrents(l1=0.0, l2=0.0, l3=0.0), valid=True)

    decision = controller.evaluate(ChargeMode.NORMAL, wallbox, sensors)

    assert decision.target_current_a == 16.0
    assert decision.should_write is True


def test_reported_current_matching_target_suppresses_unnecessary_write():
    controller = make_controller()
    wallbox = WallboxState(
        installed_phases=3,
        vehicle_connected=True,
        charging_active=True,
        current_limit_a=16.0,
    )
    sensors = HaSensorSnapshot(phase_currents=PhaseCurrents(l1=0.0, l2=0.0, l3=0.0), valid=True)

    decision = controller.evaluate(ChargeMode.NORMAL, wallbox, sensors)

    assert decision.target_current_a == 16.0
    assert decision.should_write is False


def test_reported_current_mismatch_respects_write_throttle_after_recent_write():
    controller = WallboxController(
        ControlConfig(
            max_current_a=16.0,
            min_current_a=6.0,
            stable_cycles_before_write=1,
            min_seconds_between_writes=60.0,
        )
    )
    controller.mark_current_written(16.0)
    wallbox = WallboxState(
        installed_phases=3,
        vehicle_connected=True,
        charging_active=True,
        current_limit_a=6.0,
    )
    sensors = HaSensorSnapshot(phase_currents=PhaseCurrents(l1=0.0, l2=0.0, l3=0.0), valid=True)

    decision = controller.evaluate(ChargeMode.NORMAL, wallbox, sensors)

    assert decision.target_current_a == 16.0
    assert decision.should_write is False


def test_current_validator_rejects_out_of_range_values():
    validator = _bounded_float(6.0, 32.0, "current")

    try:
        validator(40.0)
    except Exception as err:  # noqa: BLE001
        assert "between 6.0 and 32.0" in str(err)
    else:
        raise AssertionError("Expected validator to reject out-of-range value")


def test_integer_validator_rejects_fractional_amp_values():
    validator = _bounded_int(6, 32, "current")

    try:
        validator(10.5)
    except Exception as err:  # noqa: BLE001
        assert "whole number" in str(err)
    else:
        raise AssertionError("Expected validator to reject fractional value")


def test_init_options_reject_inconsistent_current_limits():
    try:
        _validate_init_options(
            {
                "min_current": 10.0,
                "max_current": 8.0,
                "safe_current": 7.0,
            }
        )
    except Exception as err:  # noqa: BLE001
        assert "min_current" in str(err)
    else:
        raise AssertionError("Expected init option validation to fail")


def test_dlb_options_require_matching_sensor_for_selected_model():
    try:
        _validate_dlb_options(
            {
                "dlb_input_model": "phase_currents",
                "dlb_l1_sensor": None,
            },
            "1p",
        )
    except Exception as err:  # noqa: BLE001
        assert "L1 phase current sensor" in str(err)
    else:
        raise AssertionError("Expected DLB option validation to fail")


def test_dlb_disabled_does_not_require_sensor_inputs():
    result = _validate_dlb_options(
        {
            "dlb_input_model": "disabled",
            "dlb_l1_sensor": None,
            "dlb_l2_sensor": None,
            "dlb_l3_sensor": None,
            "dlb_grid_power_sensor": None,
        },
        "3p",
    )

    assert result["dlb_input_model"] == "disabled"


def test_dlb_disabled_does_not_apply_current_limit():
    controller = make_controller(dlb_input_model="disabled")
    wallbox = WallboxState(installed_phases=3)
    sensors = HaSensorSnapshot(valid=False, reason_invalid="No DLB sensors configured")

    decision = controller.evaluate(ChargeMode.NORMAL, wallbox, sensors)

    assert decision.dlb_limit_a is None
    assert decision.fallback_active is False
    assert decision.final_target_a == 16.0


def test_options_flow_saves_connection_and_disabled_dlb_pv_at_final_step():
    async def _run():
        flow = WebastoUniteOptionsFlow(make_config_entry())

        result = await flow.async_step_init(
            default_options_input(host="192.168.1.55", port=1502, unit_id=42, installed_phases="1p")
        )
        assert result["type"] == "create_entry"
        assert result["data"]["host"] == "192.168.1.55"
        assert result["data"]["port"] == 1502
        assert result["data"]["unit_id"] == 42
        assert result["data"]["installed_phases"] == "1p"
        assert result["data"]["startup_charge_mode"] == "normal"
        assert result["data"]["dlb_input_model"] == "disabled"
        assert result["data"]["solar_control_strategy"] == "disabled"

    asyncio.run(_run())


def test_options_flow_schema_is_grouped_into_sections():
    async def _run():
        flow = WebastoUniteOptionsFlow(make_config_entry())

        result = await flow.async_step_init()

        assert result["type"] == "form"
        schema = result["data_schema"].args[0]
        assert set(schema) == {
            "connection",
            "general_charging",
            "dynamic_load_balancing",
            "solar_charging",
            "solar_advanced",
            "phase_switching",
            "rest_diagnostics",
            "advanced",
        }
        assert result["data_schema"].args[0]["connection"].options["collapsed"] is True
        assert result["data_schema"].args[0]["general_charging"].options["collapsed"] is True
        assert result["data_schema"].args[0]["dynamic_load_balancing"].options["collapsed"] is True
        assert result["data_schema"].args[0]["solar_charging"].options["collapsed"] is True
        assert result["data_schema"].args[0]["solar_advanced"].options["collapsed"] is True
        assert result["data_schema"].args[0]["phase_switching"].options["collapsed"] is True
        assert result["data_schema"].args[0]["rest_diagnostics"].options["collapsed"] is True
        assert result["data_schema"].args[0]["advanced"].options["collapsed"] is True

    asyncio.run(_run())


def test_options_flow_shows_session_overrides_for_managed_control():
    async def _run():
        flow = WebastoUniteOptionsFlow(
            make_config_entry(options=default_options_input(control_mode="managed_control"))
        )

        result = await flow.async_step_init()

        assert result["type"] == "form"
        schema = result["data_schema"].args[0]
        assert "session_overrides" in schema
        session_fields = schema["session_overrides"].schema.args[0]
        assert set(session_fields) == {"fixed_current", "solar_until_unplug_strategy"}

    asyncio.run(_run())


def test_options_flow_shows_all_dlb_fields_without_requiring_second_save():
    async def _run():
        flow = WebastoUniteOptionsFlow(make_config_entry())

        result = await flow.async_step_init()

        assert result["type"] == "form"
        dlb_fields = result["data_schema"].args[0]["dynamic_load_balancing"].schema.args[0]
        assert set(dlb_fields) == {
            "dlb_enabled",
            "dlb_sensor_scope",
            "dlb_require_units",
            "main_fuse",
            "safety_margin",
            "dlb_l1_sensor",
            "dlb_l2_sensor",
            "dlb_l3_sensor",
        }

    asyncio.run(_run())


def test_options_flow_section_defaults_do_not_include_none_entity_values():
    async def _run():
        flow = WebastoUniteOptionsFlow(make_config_entry())

        result = await flow.async_step_init()

        assert result["type"] == "form"
        validated = result["data_schema"](
            {
                "connection": {
                    "host": "192.168.1.60",
                    "port": 1502,
                    "unit_id": 11,
                    "polling_interval": 2.0,
                },
                "general_charging": {
                    "installed_phases": "3p",
                    "control_mode": "keepalive_only",
                    "startup_charge_mode": "normal",
                    "min_current": 6.0,
                    "max_current": 16.0,
                    "safe_current": 6.0,
                },
                "dynamic_load_balancing": {
                    "dlb_enabled": False,
                    "dlb_sensor_scope": "load_excluding_charger",
                    "dlb_require_units": False,
                    "main_fuse": 25.0,
                    "safety_margin": 2.0,
                },
                "solar_charging": {
                    "solar_control_strategy": "disabled",
                    "solar_input_model": "grid_power_derived",
                    "solar_min_current": 6.0,
                },
                "solar_advanced": {
                    "solar_start_threshold": 1800.0,
                    "solar_stop_threshold": 1200.0,
                    "solar_start_delay": 0.0,
                    "solar_stop_delay": 0.0,
                    "solar_min_runtime": 0.0,
                    "solar_min_pause": 0.0,
                },
                "phase_switching": {
                    "phase_switching_mode": "off",
                },
                "advanced": {
                    "keepalive_interval": 10.0,
                    "timeout": 3.0,
                    "retries": 3,
                },
            }
        )
        assert "dlb_l1_sensor" not in validated["dynamic_load_balancing"]
        assert "dlb_grid_power_sensor" not in validated["dynamic_load_balancing"]
        assert "solar_surplus_sensor" not in validated["solar_charging"]

    asyncio.run(_run())


def test_options_flow_shows_all_pv_fields_without_requiring_second_save():
    async def _run():
        flow = WebastoUniteOptionsFlow(make_config_entry())

        result = await flow.async_step_init()

        assert result["type"] == "form"
        pv_fields = result["data_schema"].args[0]["solar_charging"].schema.args[0]
        assert set(pv_fields) == {
            "solar_control_strategy",
            "solar_input_model",
            "solar_sensor_failure_behavior",
            "solar_surplus_sensor",
            "solar_grid_power_sensor",
            "solar_import_power_sensor",
            "solar_export_power_sensor",
            "solar_min_current",
        }
        solar_advanced_fields = result["data_schema"].args[0]["solar_advanced"].schema.args[0]
        assert set(solar_advanced_fields) == {
            "solar_grid_power_direction",
            "solar_require_units",
            "solar_start_threshold",
            "solar_stop_threshold",
            "solar_start_delay",
            "solar_stop_delay",
            "solar_min_runtime",
            "solar_min_pause",
        }

    asyncio.run(_run())


def test_options_flow_rejects_pv_min_current_above_max_current():
    async def _run():
        flow = WebastoUniteOptionsFlow(make_config_entry())

        result = await flow.async_step_init(
            default_options_input(max_current=10.0, safe_current=6.0, solar_min_current=12.0)
        )

        assert result["type"] == "form"
        assert result["errors"]["base"] == "solar_min_current_out_of_range"

    asyncio.run(_run())


def test_options_flow_saves_solar_sensor_failure_behavior():
    async def _run():
        flow = WebastoUniteOptionsFlow(make_config_entry())

        result = await flow.async_step_init(
            default_options_input(solar_sensor_failure_behavior="continue_minimum")
        )

        assert result["type"] == "create_entry"
        assert result["data"]["solar_sensor_failure_behavior"] == "continue_minimum"

    asyncio.run(_run())


def test_options_flow_accepts_nested_section_input():
    async def _run():
        flow = WebastoUniteOptionsFlow(make_config_entry())

        result = await flow.async_step_init(
            {
                "connection": {
                    "host": "192.168.1.60",
                    "port": 1502,
                    "unit_id": 11,
                    "installed_phases": "3p",
                    "polling_interval": 2.0,
                    "timeout": 3.0,
                    "retries": 3,
                },
                "general_charging": {
                    "installed_phases": "3p",
                    "control_mode": "keepalive_only",
                    "startup_charge_mode": "normal",
                    "min_current": 6.0,
                    "max_current": 16.0,
                    "safe_current": 6.0,
                },
                "dynamic_load_balancing": {
                    "dlb_input_model": "disabled",
                },
                "solar_charging": {
                    "solar_control_strategy": "disabled",
                },
            }
        )

        assert result["type"] == "create_entry"
        assert result["data"]["host"] == "192.168.1.60"
        assert result["data"]["port"] == 1502
        assert result["data"]["solar_control_strategy"] == "disabled"

    asyncio.run(_run())


def test_startup_charge_mode_can_be_configured():
    result = _validate_init_options(
        default_init_input(startup_charge_mode="pv")
    )

    assert result["startup_charge_mode"] == "solar"


def test_startup_charge_mode_falls_back_when_pv_is_disabled():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.control_config = ControlConfig(solar_control_strategy="disabled")

    assert coordinator._resolve_startup_mode({"startup_charge_mode": "pv"}) == ChargeMode.NORMAL


def test_startup_charge_mode_accepts_pv_when_pv_is_enabled():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.control_config = ControlConfig(solar_control_strategy="surplus")

    assert coordinator._resolve_startup_mode({"startup_charge_mode": "pv"}) == ChargeMode.SOLAR


def test_runtime_current_setters_reject_out_of_range_values():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.control_config = ControlConfig(min_current_a=6.0, max_current_a=16.0)

    try:
        coordinator.set_max_current(40.0)
    except ValueError as err:
        assert "Maximum Current must be between 6 A and 32 A" in str(err)
    else:
        raise AssertionError("Expected Maximum Current validation to fail")

    try:
        coordinator.set_fixed_current(0.0)
    except ValueError as err:
        assert "Fixed Current must be between 6 A and 16 A" in str(err)
    else:
        raise AssertionError("Expected Fixed Current validation to fail")


def test_runtime_current_setters_reject_fractional_amp_values():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.control_config = ControlConfig(min_current_a=6.0, max_current_a=16.0)

    with pytest.raises(ValueError, match="whole amp value"):
        coordinator.set_max_current(10.5)

    with pytest.raises(ValueError, match="whole amp value"):
        coordinator.set_fixed_current(7.2)


def test_options_flow_dlb_phase_current_3p_requires_all_phase_sensors():
    async def _run():
        flow = WebastoUniteOptionsFlow(make_config_entry())

        result = await flow.async_step_init(
            default_options_input(
                installed_phases="3p",
                dlb_enabled=True,
                dlb_l1_sensor="sensor.l1",
                dlb_l2_sensor=None,
                dlb_l3_sensor="sensor.l3",
            )
        )

        assert result["type"] == "form"
        assert result["step_id"] == "init"
        assert result["errors"]["base"] == "dlb_phase_sensor_required"

    asyncio.run(_run())


def test_options_flow_rejects_fractional_current_targets():
    async def _run():
        flow = WebastoUniteOptionsFlow(make_config_entry())

        result = await flow.async_step_init(default_options_input(max_current=10.5))

        assert result["type"] == "form"
        assert result["errors"]["base"] == "invalid_config"

    asyncio.run(_run())


def test_options_flow_dlb_phase_current_1p_requires_only_l1_sensor():
    async def _run():
        flow = WebastoUniteOptionsFlow(make_config_entry())

        result = await flow.async_step_init(
            default_options_input(
                installed_phases="1p",
                dlb_enabled=True,
                dlb_l1_sensor="sensor.l1",
                dlb_l2_sensor=None,
                dlb_l3_sensor=None,
            )
        )

        assert result["type"] == "create_entry"
        assert flow.options["dlb_enabled"] is True

    asyncio.run(_run())


def test_options_flow_can_switch_1p_to_3p_and_fill_all_sensors_in_one_submit():
    async def _run():
        flow = WebastoUniteOptionsFlow(
            make_config_entry(
                data={
                    "host": "192.168.1.10",
                    "port": 502,
                    "unit_id": 255,
                    "installed_phases": "1p",
                }
            )
        )

        result = await flow.async_step_init(
            default_options_input(
                installed_phases="3p",
                dlb_input_model="phase_currents",
                dlb_l1_sensor="sensor.l1",
                dlb_l2_sensor="sensor.l2",
                dlb_l3_sensor="sensor.l3",
            )
        )

        assert result["type"] == "create_entry"
        assert result["data"]["installed_phases"] == "3p"
        assert result["data"]["dlb_l2_sensor"] == "sensor.l2"
        assert result["data"]["dlb_l3_sensor"] == "sensor.l3"

    asyncio.run(_run())


def test_options_flow_pv_surplus_mode_requires_surplus_sensor():
    async def _run():
        flow = WebastoUniteOptionsFlow(make_config_entry())

        result = await flow.async_step_init(
            default_options_input(
                solar_control_strategy="surplus",
                solar_input_model="surplus_sensor",
                solar_surplus_sensor=None,
            )
        )

        assert result["type"] == "form"
        assert result["step_id"] == "init"
        assert result["errors"]["base"] == "solar_surplus_sensor_required"

    asyncio.run(_run())


def test_options_flow_pv_grid_derived_requires_grid_power_sensor():
    async def _run():
        flow = WebastoUniteOptionsFlow(make_config_entry())

        result = await flow.async_step_init(
            default_options_input(
                dlb_grid_power_sensor=None,
                solar_control_strategy="surplus",
                solar_input_model="grid_power_derived",
            )
        )

        assert result["type"] == "form"
        assert result["step_id"] == "init"
        assert result["errors"]["base"] == "solar_grid_sensor_required"

    asyncio.run(_run())


def test_dlb_phase_current_options_require_l1_for_1p():
    try:
        _validate_dlb_options(
            {
                "dlb_input_model": "phase_currents",
                "dlb_l1_sensor": None,
                "dlb_l2_sensor": "sensor.l2",
                "dlb_l3_sensor": "sensor.l3",
            },
            "1p",
        )
    except Exception as err:  # noqa: BLE001
        assert "L1 phase current sensor" in str(err)
    else:
        raise AssertionError("Expected 1p DLB option validation to fail")


def test_dlb_phase_current_options_require_all_phases_for_3p():
    try:
        _validate_dlb_options(
            {
                "dlb_input_model": "phase_currents",
                "dlb_l1_sensor": "sensor.l1",
                "dlb_l2_sensor": None,
                "dlb_l3_sensor": "sensor.l3",
            },
            "3p",
        )
    except Exception as err:  # noqa: BLE001
        assert "L1, L2 and L3" in str(err)
    else:
        raise AssertionError("Expected 3p DLB option validation to fail")


def test_pv_options_require_consistent_thresholds_and_sensor_model():
    try:
        _validate_pv_options(
            {
                "solar_input_model": "surplus_sensor",
                "solar_surplus_sensor": None,
                "solar_start_threshold": 1200.0,
                "solar_stop_threshold": 1800.0,
                "solar_min_current": 6.0,
                "dlb_grid_power_sensor": None,
            }
        )
    except Exception as err:  # noqa: BLE001
        assert "solar_stop_threshold" in str(err) or "surplus sensor" in str(err)
    else:
        raise AssertionError("Expected PV option validation to fail")


def test_fixed_current_validation_is_independent_from_pv_surplus_model():
    result = _validate_pv_options(
        {
            "solar_input_model": "grid_power_derived",
            "solar_control_strategy": "surplus",
            "solar_surplus_sensor": None,
            "solar_start_threshold": 1800.0,
            "solar_stop_threshold": 1200.0,
            "solar_min_current": 6.0,
            "fixed_current": 8.0,
            "dlb_grid_power_sensor": "sensor.grid_power",
        }
    )

    assert result["fixed_current"] == 8.0


def test_pv_min_plus_surplus_strategy_requires_surplus_model_inputs():
    result = _validate_pv_options(
        {
            "solar_input_model": "grid_power_derived",
            "solar_control_strategy": "smart_solar",
            "solar_until_unplug_strategy": "inherit",
            "solar_surplus_sensor": None,
            "solar_start_threshold": 1800.0,
            "solar_stop_threshold": 1200.0,
            "solar_min_current": 6.0,
            "fixed_current": 8.0,
            "dlb_grid_power_sensor": "sensor.grid_power",
        }
    )

    assert result["solar_control_strategy"] == "smart_solar"


def test_solar_dsmr_import_export_strategy_requires_both_sensors():
    try:
        _validate_pv_options(
            {
                "solar_input_model": "dsmr_import_export",
                "solar_control_strategy": "smart_solar",
                "solar_until_unplug_strategy": "inherit",
                "solar_import_power_sensor": "sensor.import",
                "solar_export_power_sensor": None,
                "solar_start_threshold": 1800.0,
                "solar_stop_threshold": 1200.0,
                "solar_min_current": 6.0,
                "fixed_current": 8.0,
            }
        )
    except Exception as err:  # noqa: BLE001
        assert "import and export" in str(err)
    else:
        raise AssertionError("Expected DSMR import/export validation to fail")


def test_solar_dsmr_import_export_strategy_accepts_both_sensors():
    result = _validate_pv_options(
        {
            "solar_input_model": "dsmr_import_export",
            "solar_control_strategy": "smart_solar",
            "solar_until_unplug_strategy": "inherit",
            "solar_import_power_sensor": "sensor.import",
            "solar_export_power_sensor": "sensor.export",
            "solar_start_threshold": 1800.0,
            "solar_stop_threshold": 1200.0,
            "solar_min_current": 6.0,
            "fixed_current": 8.0,
        }
    )

    assert result["solar_input_model"] == "dsmr_import_export"


def test_legacy_pv_min_always_plus_surplus_strategy_normalizes_to_min_plus_surplus():
    result = _validate_pv_options(
        {
            "solar_input_model": "grid_power_derived",
            "solar_control_strategy": "min_always_plus_surplus",
            "solar_until_unplug_strategy": "inherit",
            "solar_surplus_sensor": None,
            "solar_start_threshold": 1800.0,
            "solar_stop_threshold": 1200.0,
            "solar_min_current": 6.0,
            "fixed_current": 8.0,
            "dlb_grid_power_sensor": "sensor.grid_power",
        }
    )

    assert result["solar_control_strategy"] == "solar_boost"


def test_disabled_pv_strategy_allows_empty_pv_sensor_configuration():
    result = _validate_pv_options(
        {
            "solar_input_model": "surplus_sensor",
            "solar_control_strategy": "disabled",
            "solar_until_unplug_strategy": "inherit",
            "solar_surplus_sensor": None,
            "solar_start_threshold": 1800.0,
            "solar_stop_threshold": 1200.0,
            "solar_min_current": 6.0,
            "fixed_current": 8.0,
            "dlb_grid_power_sensor": None,
        }
    )

    assert result["solar_control_strategy"] == "disabled"


def test_missing_pv_strategy_defaults_to_disabled_for_validation():
    result = _validate_pv_options(
        {
            "solar_input_model": "surplus_sensor",
            "solar_surplus_sensor": None,
            "solar_start_threshold": 1800.0,
            "solar_stop_threshold": 1200.0,
            "solar_min_current": 6.0,
            "fixed_current": 8.0,
            "dlb_grid_power_sensor": None,
        }
    )

    assert result["solar_input_model"] == "surplus_sensor"
def test_fixed_current_must_stay_within_amp_range():
    try:
        _validate_pv_options(
            {
                "solar_input_model": "surplus_sensor",
                "solar_control_strategy": "surplus",
                "solar_surplus_sensor": None,
                "solar_start_threshold": 1800.0,
                "solar_stop_threshold": 1200.0,
                "solar_min_current": 6.0,
                "fixed_current": 40.0,
                "dlb_grid_power_sensor": None,
            }
        )
    except Exception as err:  # noqa: BLE001
        assert "fixed_current" in str(err)
    else:
        raise AssertionError("Expected fixed current validation to fail")


def test_sensor_values_are_normalized_from_common_units():
    hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda entity_id: {
                "sensor.current_ma": SimpleNamespace(state="6200", attributes={"unit_of_measurement": "mA"}),
                "sensor.grid_kw": SimpleNamespace(state="2.5", attributes={"unit_of_measurement": "kW"}),
                "sensor.bad_unit": SimpleNamespace(state="2.5", attributes={"unit_of_measurement": "V"}),
            }.get(entity_id)
        )
    )
    adapter = HaSensorAdapter(hass)

    assert adapter.state_as_current_a("sensor.current_ma") == 6.2
    assert adapter.state_as_power_w("sensor.grid_kw") == 2500.0
    assert adapter.state_as_power_w("sensor.bad_unit") is None


def test_sensor_adapter_can_require_supported_units_for_dlb_inputs():
    hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda entity_id: {
                "sensor.no_unit": SimpleNamespace(state="12.0", attributes={}),
                "sensor.with_unit": SimpleNamespace(state="12000", attributes={"unit_of_measurement": "mA"}),
            }.get(entity_id)
        )
    )
    adapter = HaSensorAdapter(hass)

    assert adapter.state_as_current_a("sensor.no_unit") == 12.0
    assert adapter.state_as_current_a("sensor.no_unit", require_supported_unit=True) is None
    assert adapter.state_as_current_a("sensor.with_unit", require_supported_unit=True) == 12.0


def test_sensor_adapter_rejects_stale_control_sensor_values():
    stale = datetime.now(timezone.utc) - timedelta(seconds=120)
    fresh = datetime.now(timezone.utc)
    hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda entity_id: {
                "sensor.stale": SimpleNamespace(
                    state="12.0",
                    attributes={"unit_of_measurement": "A"},
                    last_reported=stale,
                ),
                "sensor.fresh": SimpleNamespace(
                    state="12.0",
                    attributes={"unit_of_measurement": "A"},
                    last_reported=fresh,
                ),
            }.get(entity_id)
        )
    )
    adapter = HaSensorAdapter(hass)

    assert adapter.state_as_current_a("sensor.stale", max_age_s=60.0) is None
    assert adapter.state_is_stale("sensor.stale", max_age_s=60.0) is True
    assert adapter.state_as_current_a("sensor.fresh", max_age_s=60.0) == 12.0


def test_sensor_adapter_accepts_stale_zero_surplus_but_rejects_stale_positive_surplus():
    stale = datetime.now(timezone.utc) - timedelta(seconds=120)
    hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda entity_id: {
                "sensor.stale_zero": SimpleNamespace(
                    state="0.0",
                    attributes={"unit_of_measurement": "kW"},
                    last_reported=stale,
                ),
                "sensor.stale_positive": SimpleNamespace(
                    state="1.5",
                    attributes={"unit_of_measurement": "kW"},
                    last_reported=stale,
                ),
            }.get(entity_id)
        )
    )
    adapter = HaSensorAdapter(hass)

    assert adapter.stale_zero_state_as_power_w("sensor.stale_zero", max_age_s=60.0) == 0.0
    assert adapter.stale_zero_state_as_power_w("sensor.stale_positive", max_age_s=60.0) is None


def test_options_flow_shows_min_and_max_current_in_charging_section():
    async def _run():
        flow = WebastoUniteOptionsFlow(make_config_entry())

        result = await flow.async_step_init()

        fields = result["data_schema"].args[0]["general_charging"].schema.args[0]
        assert "min_current" in fields
        assert "max_current" in fields
        assert "user_limit" not in fields

    asyncio.run(_run())


def test_options_flow_migrates_legacy_user_limit_into_max_current():
    async def _run():
        flow = WebastoUniteOptionsFlow(make_config_entry())

        result = await flow.async_step_init(default_options_input(max_current=32.0, user_limit=20.0))

        assert result["type"] == "create_entry"
        assert result["data"]["max_current"] == 20
        assert "user_limit" not in result["data"]

    asyncio.run(_run())


def test_options_flow_migrates_legacy_user_limit_above_old_default_into_max_current():
    async def _run():
        flow = WebastoUniteOptionsFlow(make_config_entry())

        result = await flow.async_step_init(default_options_input(max_current=16.0, user_limit=20.0))

        assert result["type"] == "create_entry"
        assert result["data"]["max_current"] == 20
        assert "user_limit" not in result["data"]

    asyncio.run(_run())


def test_options_flow_user_max_current_input_overrides_stale_legacy_user_limit():
    async def _run():
        flow = WebastoUniteOptionsFlow(
            make_config_entry(options=default_options_input(max_current=16.0, user_limit=16.0))
        )

        result = await flow.async_step_init(default_options_input(max_current=20.0))

        assert result["type"] == "create_entry"
        assert result["data"]["max_current"] == 20
        assert "user_limit" not in result["data"]

    asyncio.run(_run())


def test_options_flow_allows_20a_current_when_max_current_is_20a():
    async def _run():
        flow = WebastoUniteOptionsFlow(make_config_entry())

        result = await flow.async_step_init(default_options_input(max_current=20.0))

        assert result["type"] == "create_entry"
        assert result["data"]["max_current"] == 20

    asyncio.run(_run())


def test_charging_switch_is_unavailable_in_monitoring_only_mode():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry()
        coordinator.control_config = ControlConfig(control_mode=ControlMode.KEEPALIVE_ONLY)
        coordinator._charging_paused = False
        coordinator.async_set_charging_enabled = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()

        charging_switch = WebastoChargingSwitch(coordinator)

        assert charging_switch.available is False
        await charging_switch.async_turn_off()
        coordinator.async_set_charging_enabled.assert_not_called()
        coordinator.async_request_refresh.assert_not_called()

    asyncio.run(_run())


def test_charging_switch_is_available_in_managed_control_mode():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.entry = make_config_entry()
    coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
    coordinator._charging_paused = False

    charging_switch = WebastoChargingSwitch(coordinator)

    assert charging_switch._attr_name == "Charging Enabled"
    assert charging_switch.available is True


def test_session_override_switches_are_unavailable_in_monitoring_only_mode():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry()
        coordinator.control_config = ControlConfig(
            control_mode=ControlMode.KEEPALIVE_ONLY,
            solar_control_strategy=SolarControlStrategy.ECO_SOLAR,
        )
        coordinator.set_solar_until_unplug = AsyncMock()
        coordinator.set_fixed_current_until_unplug = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()

        solar_switch = WebastoSolarUntilUnplugSwitch(coordinator)
        fixed_switch = WebastoFixedCurrentUntilUnplugSwitch(coordinator)

        assert solar_switch.available is False
        assert fixed_switch.available is False
        await solar_switch.async_turn_on()
        await fixed_switch.async_turn_on()
        coordinator.set_solar_until_unplug.assert_not_called()
        coordinator.set_fixed_current_until_unplug.assert_not_called()
        coordinator.async_request_refresh.assert_not_called()

    asyncio.run(_run())


def test_dlb_snapshot_marks_stale_phase_sensor_invalid():
    stale = datetime.now(timezone.utc) - timedelta(seconds=120)
    hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda entity_id: {
                "sensor.l1": SimpleNamespace(
                    state="12.0",
                    attributes={"unit_of_measurement": "A"},
                    last_reported=stale,
                ),
            }.get(entity_id)
        )
    )

    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.hass = hass
    coordinator.entry = SimpleNamespace(
        options={"dlb_l1_sensor": "sensor.l1"},
        data={"installed_phases": "1p"},
    )
    coordinator.sensor_adapter = HaSensorAdapter(hass)
    coordinator.control_config = ControlConfig(
        dlb_input_model=DlbInputModel.PHASE_CURRENTS,
        control_sensor_timeout_s=60.0,
    )
    coordinator.controller = make_controller()

    snapshot = read_control_inputs(coordinator)

    assert snapshot.valid is False
    assert snapshot.phase_currents.l1 is None
    assert snapshot.reason_invalid == "Required DLB phase sensors stale"


def test_dlb_snapshot_ignores_stale_inactive_phase_sensors_while_1p_charging_on_3p_installation():
    stale = datetime.now(timezone.utc) - timedelta(seconds=120)
    fresh = datetime.now(timezone.utc)
    hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda entity_id: {
                "sensor.l1": SimpleNamespace(
                    state="12.0",
                    attributes={"unit_of_measurement": "A"},
                    last_reported=fresh,
                ),
                "sensor.l2": SimpleNamespace(
                    state="0.0",
                    attributes={"unit_of_measurement": "A"},
                    last_reported=stale,
                ),
                "sensor.l3": SimpleNamespace(
                    state="0.0",
                    attributes={"unit_of_measurement": "A"},
                    last_reported=stale,
                ),
            }.get(entity_id)
        )
    )

    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.hass = hass
    coordinator.entry = SimpleNamespace(
        options={
            "dlb_l1_sensor": "sensor.l1",
            "dlb_l2_sensor": "sensor.l2",
            "dlb_l3_sensor": "sensor.l3",
        },
        data={"installed_phases": "3p"},
    )
    coordinator.sensor_adapter = HaSensorAdapter(hass)
    coordinator.control_config = ControlConfig(
        dlb_input_model=DlbInputModel.PHASE_CURRENTS,
        control_sensor_timeout_s=60.0,
        dlb_require_units=True,
    )
    coordinator.controller = make_controller()
    wallbox = WallboxState(
        charging_active=True,
        phase_currents=PhaseCurrents(l1=6.0, l2=0.0, l3=0.0),
    )

    snapshot = read_control_inputs(coordinator, wallbox)

    assert snapshot.valid is True
    assert snapshot.phase_currents.l1 == 12.0
    assert snapshot.phase_currents.l2 is None
    assert snapshot.phase_currents.l3 is None
    assert snapshot.reason_invalid is None


def test_solar_snapshot_marks_stale_input_sensor_unavailable():
    stale = datetime.now(timezone.utc) - timedelta(seconds=120)
    hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda entity_id: {
                "sensor.solar_surplus": SimpleNamespace(
                    state="2300",
                    attributes={"unit_of_measurement": "W"},
                    last_reported=stale,
                ),
            }.get(entity_id)
        )
    )

    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.hass = hass
    coordinator.entry = SimpleNamespace(
        options={"solar_surplus_sensor": "sensor.solar_surplus"},
        data={"installed_phases": "3p"},
    )
    coordinator.sensor_adapter = HaSensorAdapter(hass)
    coordinator.control_config = ControlConfig(
        dlb_input_model=DlbInputModel.DISABLED,
        solar_control_strategy=SolarControlStrategy.ECO_SOLAR,
        solar_input_model=SolarInputModel.SURPLUS_SENSOR,
        control_sensor_timeout_s=60.0,
    )
    coordinator.controller = make_controller(solar_control_strategy=SolarControlStrategy.ECO_SOLAR)

    snapshot = read_control_inputs(coordinator)

    assert snapshot.surplus_power_w is None
    assert snapshot.reason_invalid == "Required Solar sensor stale"
    assert snapshot.solar_input_state == "unavailable"


def test_pv_snapshot_requires_units_when_enabled():
    hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda entity_id: {
                "sensor.grid_no_unit": SimpleNamespace(state="-1500", attributes={}),
            }.get(entity_id)
        )
    )

    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.hass = hass
    coordinator.entry = SimpleNamespace(options={"solar_grid_power_sensor": "sensor.grid_no_unit"}, data={"installed_phases": "3p"})
    coordinator.sensor_adapter = HaSensorAdapter(hass)
    coordinator.control_config = ControlConfig(
        dlb_input_model=DlbInputModel.DISABLED,
        solar_control_strategy=SolarControlStrategy.SURPLUS,
        solar_input_model=SolarInputModel.GRID_POWER_DERIVED,
        solar_require_units=True,
    )
    coordinator.controller = make_controller(solar_control_strategy=SolarControlStrategy.SURPLUS)

    snapshot = read_control_inputs(coordinator)

    assert snapshot.grid_power_w is None
    assert snapshot.reason_invalid == "Required Solar sensor unavailable or invalid unit"
    assert snapshot.solar_input_state == "unavailable"


def test_pv_snapshot_reports_ready_when_input_is_available():
    hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda entity_id: {
                "sensor.grid_power": SimpleNamespace(state="-1800", attributes={"unit_of_measurement": "W"}),
            }.get(entity_id)
        )
    )

    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.hass = hass
    coordinator.entry = SimpleNamespace(options={"solar_grid_power_sensor": "sensor.grid_power"}, data={"installed_phases": "3p"})
    coordinator.sensor_adapter = HaSensorAdapter(hass)
    coordinator.control_config = ControlConfig(
        dlb_input_model=DlbInputModel.DISABLED,
        solar_control_strategy=SolarControlStrategy.SURPLUS,
        solar_input_model=SolarInputModel.GRID_POWER_DERIVED,
    )
    coordinator.controller = make_controller(solar_control_strategy=SolarControlStrategy.SURPLUS)

    snapshot = read_control_inputs(coordinator)

    assert snapshot.reason_invalid is None
    assert snapshot.solar_input_state == "ready"


def test_solar_dsmr_snapshot_derives_signed_grid_power_from_import_export():
    hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda entity_id: {
                "sensor.import_power": SimpleNamespace(state="0.5", attributes={"unit_of_measurement": "kW"}),
                "sensor.export_power": SimpleNamespace(state="3.0", attributes={"unit_of_measurement": "kW"}),
            }.get(entity_id)
        )
    )

    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.hass = hass
    coordinator.entry = SimpleNamespace(
        options={
            "solar_import_power_sensor": "sensor.import_power",
            "solar_export_power_sensor": "sensor.export_power",
        },
        data={"installed_phases": "3p"},
    )
    coordinator.sensor_adapter = HaSensorAdapter(hass)
    coordinator.control_config = ControlConfig(
        dlb_input_model=DlbInputModel.DISABLED,
        solar_control_strategy=SolarControlStrategy.SURPLUS,
        solar_input_model=SolarInputModel.DSMR_IMPORT_EXPORT,
        solar_require_units=True,
    )
    coordinator.controller = make_controller(solar_control_strategy=SolarControlStrategy.SURPLUS)

    snapshot = read_control_inputs(coordinator)

    assert snapshot.grid_power_w == -2500.0
    assert snapshot.reason_invalid is None
    assert snapshot.solar_input_state == "ready"


def test_solar_dsmr_surplus_ignores_grid_power_direction_setting():
    controller = make_controller(
        solar_input_model=SolarInputModel.DSMR_IMPORT_EXPORT,
        solar_grid_power_direction="positive_export",
    )
    sensors = HaSensorSnapshot(grid_power_w=-2500.0)
    wallbox = WallboxState(charging_active=False, active_power_w=0.0)

    assert controller.resolve_surplus_power(sensors, wallbox) == 2500.0


def test_solar_dsmr_snapshot_accepts_stale_zero_direction():
    stale = datetime.now(timezone.utc) - timedelta(seconds=120)
    fresh = datetime.now(timezone.utc)
    hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda entity_id: {
                "sensor.import_power": SimpleNamespace(
                    state="0.0",
                    attributes={"unit_of_measurement": "kW"},
                    last_reported=stale,
                ),
                "sensor.export_power": SimpleNamespace(
                    state="3.0",
                    attributes={"unit_of_measurement": "kW"},
                    last_reported=fresh,
                ),
            }.get(entity_id)
        )
    )

    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.hass = hass
    coordinator.entry = SimpleNamespace(
        options={
            "solar_import_power_sensor": "sensor.import_power",
            "solar_export_power_sensor": "sensor.export_power",
        },
        data={"installed_phases": "3p"},
    )
    coordinator.sensor_adapter = HaSensorAdapter(hass)
    coordinator.control_config = ControlConfig(
        dlb_input_model=DlbInputModel.DISABLED,
        solar_control_strategy=SolarControlStrategy.SURPLUS,
        solar_input_model=SolarInputModel.DSMR_IMPORT_EXPORT,
        solar_require_units=True,
        control_sensor_timeout_s=60.0,
    )
    coordinator.controller = make_controller(solar_control_strategy=SolarControlStrategy.SURPLUS)

    snapshot = read_control_inputs(coordinator)

    assert snapshot.grid_power_w == -3000.0
    assert snapshot.reason_invalid is None
    assert snapshot.solar_input_state == "ready"


def test_keepalive_only_mode_blocks_control_writes():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.control_config = ControlConfig(control_mode=ControlMode.KEEPALIVE_ONLY)
        coordinator.write_queue = WriteQueueManager()

        decision = SimpleNamespace(
            charging_enabled=True,
            reason=ControlReason.NORMAL_MODE,
            dominant_limit_reason=None,
            fallback_active=False,
            sensor_invalid_reason=None,
            should_write=True,
            target_current_a=10.0,
        )
        await enqueue_test_decision(coordinator, decision)

        assert await coordinator.write_queue.size() == 0

    asyncio.run(_run())


def test_managed_control_mode_allows_control_writes():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)

    assert coordinator._allows_control_writes() is True


def test_keepalive_only_mode_disables_control_writes():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.control_config = ControlConfig(control_mode=ControlMode.KEEPALIVE_ONLY)

    assert coordinator._allows_control_writes() is False


def test_external_controller_mode_blocks_automatic_writes():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.control_config = ControlConfig(control_mode=ControlMode.EXTERNAL_CONTROLLER)

    assert coordinator._allows_control_writes() is False
    assert coordinator._allows_current_writes() is True
    assert coordinator._control_write_blocked_reason() == "external_controller_mode"


def test_external_controller_current_limit_writes_directly():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.control_config = ControlConfig(
            control_mode=ControlMode.EXTERNAL_CONTROLLER,
            min_current_a=6.0,
            max_current_a=32.0,
        )
        coordinator.write_queue = WriteQueueManager()
        client = SimpleNamespace(write=AsyncMock())
        coordinator.write_runtime = WriteRuntime(
            coordinator.control_config,
            write_queue=coordinator.write_queue,
            client=client,
            controller=None,
        )
        coordinator._external_current_a = None

        await coordinator.async_set_external_current_limit(20)

        client.write.assert_awaited_once_with(SET_CHARGE_CURRENT_A, 20)
        assert coordinator._external_current_a == 20.0
        assert coordinator.write_runtime.last_control_write_reason == "external_controller"

    asyncio.run(_run())


def test_external_controller_current_limit_rounds_fractional_values_for_modbus_register():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.control_config = ControlConfig(
            control_mode=ControlMode.EXTERNAL_CONTROLLER,
            min_current_a=6.0,
            max_current_a=32.0,
        )
        coordinator.write_queue = WriteQueueManager()
        client = SimpleNamespace(write=AsyncMock())
        coordinator.write_runtime = WriteRuntime(
            coordinator.control_config,
            write_queue=coordinator.write_queue,
            client=client,
            controller=None,
        )
        coordinator._external_current_a = None

        await coordinator.async_set_external_current_limit(6.82)

        client.write.assert_awaited_once_with(SET_CHARGE_CURRENT_A, 7)
        assert coordinator._external_current_a == 7.0
        assert coordinator.write_runtime.last_control_write_reason == "external_controller"

    asyncio.run(_run())


def test_external_controller_positive_current_is_blocked_without_connected_vehicle():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.control_config = ControlConfig(
            control_mode=ControlMode.EXTERNAL_CONTROLLER,
            min_current_a=6.0,
            max_current_a=32.0,
        )
        coordinator.write_queue = WriteQueueManager()
        client = SimpleNamespace(write=AsyncMock())
        coordinator.write_runtime = WriteRuntime(
            coordinator.control_config,
            write_queue=coordinator.write_queue,
            client=client,
            controller=None,
        )
        coordinator.data = SimpleNamespace(wallbox=WallboxState(vehicle_connected=False))
        coordinator._external_current_a = None

        await coordinator.async_set_external_current_limit(10)

        client.write.assert_not_called()
        assert coordinator._external_current_a == 10.0
        assert coordinator.write_runtime.last_control_write_blocked_reason == "vehicle_not_connected"
        assert coordinator.write_runtime.last_control_write_value_a is None

    asyncio.run(_run())


def test_external_controller_zero_current_is_allowed_without_connected_vehicle():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.control_config = ControlConfig(
            control_mode=ControlMode.EXTERNAL_CONTROLLER,
            min_current_a=6.0,
            max_current_a=32.0,
        )
        coordinator.write_queue = WriteQueueManager()
        client = SimpleNamespace(write=AsyncMock())
        coordinator.write_runtime = WriteRuntime(
            coordinator.control_config,
            write_queue=coordinator.write_queue,
            client=client,
            controller=None,
        )
        coordinator.data = SimpleNamespace(wallbox=WallboxState(vehicle_connected=False))
        coordinator._external_current_a = 10.0

        await coordinator.async_set_external_current_limit(0)

        client.write.assert_awaited_once_with(SET_CHARGE_CURRENT_A, 0)
        assert coordinator._external_current_a == 10.0
        assert coordinator.write_runtime.last_control_write_reason == "external_controller"
        assert coordinator.write_runtime.last_control_write_value_a == 0.0

    asyncio.run(_run())


def test_external_controller_current_limit_is_deferred_during_phase_switch():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.control_config = ControlConfig(
            control_mode=ControlMode.EXTERNAL_CONTROLLER,
            min_current_a=6.0,
            max_current_a=32.0,
        )
        coordinator.write_queue = WriteQueueManager()
        client = SimpleNamespace(write=AsyncMock())
        coordinator.write_runtime = WriteRuntime(
            coordinator.control_config,
            write_queue=coordinator.write_queue,
            client=client,
            controller=None,
        )
        coordinator._external_current_a = None
        coordinator._pending_external_current_a = None
        coordinator._phase_restore_task = None
        coordinator._phase_switch_task = asyncio.Future()

        await coordinator.async_set_external_current_limit(18)

        client.write.assert_not_awaited()
        assert coordinator._external_current_a == 18.0
        assert coordinator._pending_external_current_a == 18.0
        assert coordinator.write_runtime.last_control_write_blocked_reason == "phase_switch_in_progress"

        coordinator._phase_switch_task.set_result(None)
        await coordinator._flush_pending_external_current_limit()

        client.write.assert_awaited_once_with(SET_CHARGE_CURRENT_A, 18)
        assert coordinator._pending_external_current_a is None
        assert coordinator.write_runtime.last_control_write_reason == "external_controller"

    asyncio.run(_run())


def test_phase_switch_completion_clears_stale_phase_switch_blocked_reason():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry()
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator.write_queue = WriteQueueManager()
        coordinator.write_runtime = WriteRuntime(
            coordinator.control_config,
            write_queue=coordinator.write_queue,
            client=SimpleNamespace(write=AsyncMock()),
            controller=None,
        )
        coordinator.phase_switch_manager = SimpleNamespace(
            last_result="register_written",
            last_block_reason=None,
            last_target="3P",
            state="phase_switch_settling",
        )
        coordinator.phase_runtime = PhaseRuntimeState()
        coordinator.async_request_phase_switch = AsyncMock()
        coordinator._flush_pending_external_current_limit = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator.write_runtime.state.last_control_write_blocked_reason = "phase_switch_in_progress"

        await coordinator._run_scheduled_phase_switch(3, source="manual")

        assert coordinator.write_runtime.last_control_write_blocked_reason is None
        coordinator.async_request_refresh.assert_awaited_once()

    asyncio.run(_run())


def test_write_runtime_marks_current_write_accepted_from_next_reported_limit():
    now = 1000.0
    runtime = WriteRuntime(
        ControlConfig(),
        write_queue=WriteQueueManager(),
        client=SimpleNamespace(write=AsyncMock()),
        controller=None,
        monotonic_fn=lambda: now,
    )

    runtime._record_current_write(16.0, "normal_mode")
    runtime.update_current_write_verification(16.0)

    assert runtime.last_control_write_verification_status == "accepted"
    assert runtime.last_control_write_verification_reported_a == 16.0
    assert runtime.last_control_write_verification_delta_a == 0.0


def test_write_runtime_keeps_recent_current_mismatch_pending_until_timeout():
    now = 1000.0
    runtime = WriteRuntime(
        ControlConfig(),
        write_queue=WriteQueueManager(),
        client=SimpleNamespace(write=AsyncMock()),
        controller=None,
        monotonic_fn=lambda: now,
    )

    runtime._record_current_write(16.0, "normal_mode")
    now = 1005.0
    runtime.update_current_write_verification(6.0)

    assert runtime.last_control_write_verification_status == "pending"
    assert runtime.last_control_write_verification_reported_a == 6.0
    assert runtime.last_control_write_verification_delta_a == 10.0


def test_write_runtime_marks_current_mismatch_after_timeout():
    now = 1000.0
    runtime = WriteRuntime(
        ControlConfig(),
        write_queue=WriteQueueManager(),
        client=SimpleNamespace(write=AsyncMock()),
        controller=None,
        monotonic_fn=lambda: now,
    )

    runtime._record_current_write(32.0, "fixed_current_mode")
    now = 1021.0
    runtime.update_current_write_verification(16.0)

    assert runtime.last_control_write_verification_status == "mismatch"
    assert runtime.last_control_write_verification_reported_a == 16.0
    assert runtime.last_control_write_verification_delta_a == 16.0


def test_requested_current_number_writes_directly_in_external_controller_mode():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry()
        coordinator.control_config = ControlConfig(
            control_mode=ControlMode.EXTERNAL_CONTROLLER,
            min_current_a=6.0,
            max_current_a=32.0,
        )
        coordinator.write_queue = WriteQueueManager()
        client = SimpleNamespace(write=AsyncMock())
        coordinator.write_runtime = WriteRuntime(
            coordinator.control_config,
            write_queue=coordinator.write_queue,
            client=client,
            controller=None,
        )
        coordinator._external_current_a = None
        coordinator.async_request_refresh = AsyncMock()

        requested_current = WebastoRequestedCurrentNumber(coordinator)
        assert requested_current._attr_name == "External Requested Current"
        await requested_current.async_set_native_value(24)

        client.write.assert_awaited_once_with(SET_CHARGE_CURRENT_A, 24)
        coordinator.async_request_refresh.assert_awaited_once()

    asyncio.run(_run())


def test_requested_current_number_accepts_fractional_evcc_values_and_rounds_to_register_value():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry()
        coordinator.control_config = ControlConfig(
            control_mode=ControlMode.EXTERNAL_CONTROLLER,
            min_current_a=6.0,
            max_current_a=32.0,
        )
        coordinator.write_queue = WriteQueueManager()
        client = SimpleNamespace(write=AsyncMock())
        coordinator.write_runtime = WriteRuntime(
            coordinator.control_config,
            write_queue=coordinator.write_queue,
            client=client,
            controller=None,
        )
        coordinator._external_current_a = None
        coordinator.async_request_refresh = AsyncMock()

        requested_current = WebastoRequestedCurrentNumber(coordinator)
        await requested_current.async_set_native_value(6.82)

        client.write.assert_awaited_once_with(SET_CHARGE_CURRENT_A, 7)
        assert coordinator._external_current_a == 7.0
        coordinator.async_request_refresh.assert_awaited_once()

    asyncio.run(_run())


def test_maximum_current_number_remains_configuration_limit_in_external_controller_mode():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry()
        coordinator.control_config = ControlConfig(
            control_mode=ControlMode.EXTERNAL_CONTROLLER,
            min_current_a=6.0,
            max_current_a=16.0,
        )
        coordinator.write_queue = WriteQueueManager()
        client = SimpleNamespace(write=AsyncMock())
        coordinator.write_runtime = WriteRuntime(
            coordinator.control_config,
            write_queue=coordinator.write_queue,
            client=client,
            controller=None,
        )
        coordinator.async_request_refresh = AsyncMock()

        current_limit = WebastoMaximumCurrentNumber(coordinator)
        await current_limit.async_set_native_value(24)

        client.write.assert_not_awaited()
        coordinator.async_request_refresh.assert_awaited_once()
        assert coordinator.control_config.max_current_a == 24.0

    asyncio.run(_run())


def test_external_controller_mode_makes_charging_switch_available():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.entry = make_config_entry()
    coordinator.control_config = ControlConfig(control_mode=ControlMode.EXTERNAL_CONTROLLER)
    coordinator._charging_paused = False

    charging_switch = WebastoChargingSwitch(coordinator)

    assert charging_switch.available is True


def test_external_controller_charging_switch_off_writes_zero():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry()
        coordinator.control_config = ControlConfig(control_mode=ControlMode.EXTERNAL_CONTROLLER)
        coordinator._charging_paused = False
        coordinator._charging_state_store = SimpleNamespace(async_save=AsyncMock())
        coordinator.write_queue = WriteQueueManager()
        client = SimpleNamespace(write=AsyncMock())
        coordinator.write_runtime = WriteRuntime(
            coordinator.control_config,
            write_queue=coordinator.write_queue,
            client=client,
            controller=None,
        )
        coordinator._external_current_a = 16.0
        coordinator.async_request_refresh = AsyncMock()

        charging_switch = WebastoChargingSwitch(coordinator)
        await charging_switch.async_turn_off()

        client.write.assert_awaited_once_with(SET_CHARGE_CURRENT_A, 0)
        coordinator.async_request_refresh.assert_awaited_once()

    asyncio.run(_run())


def test_sensor_refresh_is_debounced_to_single_refresh():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.hass = SimpleNamespace(async_create_task=lambda coro: asyncio.create_task(coro))
        coordinator.async_request_refresh = AsyncMock()
        coordinator._sensor_refresh_task = None

        coordinator._schedule_sensor_refresh()
        coordinator._schedule_sensor_refresh()
        await asyncio.sleep(0.55)

        assert coordinator.async_request_refresh.await_count == 1

    asyncio.run(_run())


def test_clock_formatter_returns_human_readable_time():
    assert WallboxReader.format_clock_hhmmss(123045) == "12:30:45"
    assert WallboxReader.format_clock_hhmmss(0) is None
    assert WallboxReader.format_clock_hhmmss(250001) == "250001"


def test_wallbox_reader_treats_non_positive_optional_current_limits_as_unknown():
    assert WallboxReader._normalize_optional_current_limit_a(None) is None
    assert WallboxReader._normalize_optional_current_limit_a(0) is None
    assert WallboxReader._normalize_optional_current_limit_a(-1) is None
    assert WallboxReader._normalize_optional_current_limit_a(16) == 16.0


def test_resume_charging_restores_previous_non_off_mode():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator._mode = ChargeMode.SOLAR
    coordinator._charging_paused = False
    coordinator._solar_until_unplug_active = False
    coordinator._fixed_current_until_unplug_active = False

    coordinator.pause_charging()
    assert coordinator.mode == ChargeMode.SOLAR
    assert coordinator.effective_mode == ChargeMode.OFF

    coordinator.resume_charging()
    assert coordinator.mode == ChargeMode.SOLAR
    assert coordinator.effective_mode == ChargeMode.SOLAR


def test_set_mode_updates_resume_mode_only_for_active_modes():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator._mode = ChargeMode.NORMAL
    coordinator._charging_paused = False
    coordinator._solar_until_unplug_active = False
    coordinator._fixed_current_until_unplug_active = False
    coordinator.controller = make_controller()
    coordinator.controller.solar_state.active = True

    coordinator.set_mode(ChargeMode.SOLAR)
    assert coordinator.mode == ChargeMode.SOLAR

    coordinator.set_mode(ChargeMode.OFF)
    assert coordinator.mode == ChargeMode.OFF
    assert coordinator.controller.solar_state.active is False


def test_set_mode_clears_temporary_until_unplug_overrides():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator._mode = ChargeMode.NORMAL
    coordinator._charging_paused = False
    coordinator._solar_until_unplug_active = True
    coordinator._fixed_current_until_unplug_active = False
    coordinator.controller = make_controller()
    coordinator.controller.solar_state.active = True

    coordinator.set_mode(ChargeMode.FIXED_CURRENT)

    assert coordinator.mode == ChargeMode.FIXED_CURRENT
    assert coordinator.solar_until_unplug_active is False
    assert coordinator.fixed_current_until_unplug_active is False
    assert coordinator.effective_mode == ChargeMode.FIXED_CURRENT
    assert coordinator.controller.solar_state.active is False


def test_set_fixed_current_updates_runtime_config():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.control_config = ControlConfig(fixed_current_a=6.0)

    coordinator.set_fixed_current(8.0)

    assert coordinator.control_config.fixed_current_a == 8.0


def test_effective_mode_uses_temporary_pv_override_above_normal():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator._mode = ChargeMode.NORMAL
    coordinator._charging_paused = False
    coordinator._solar_until_unplug_active = True
    coordinator._fixed_current_until_unplug_active = False

    assert coordinator.effective_mode == ChargeMode.SOLAR


def test_effective_mode_uses_temporary_fixed_current_override_above_pv_override():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator._mode = ChargeMode.NORMAL
    coordinator._charging_paused = False
    coordinator._solar_until_unplug_active = True
    coordinator._fixed_current_until_unplug_active = True

    assert coordinator.effective_mode == ChargeMode.FIXED_CURRENT


def test_effective_mode_keeps_off_as_highest_priority():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator._mode = ChargeMode.OFF
    coordinator._charging_paused = False
    coordinator._solar_until_unplug_active = True
    coordinator._fixed_current_until_unplug_active = True

    assert coordinator.effective_mode == ChargeMode.OFF


def test_pause_and_resume_do_not_clear_temporary_pv_override():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator._mode = ChargeMode.NORMAL
    coordinator._charging_paused = False
    coordinator._solar_until_unplug_active = True
    coordinator._fixed_current_until_unplug_active = False
    coordinator.controller = make_controller()
    coordinator.controller.solar_state.active = True

    coordinator.pause_charging()
    assert coordinator.effective_mode == ChargeMode.OFF
    assert coordinator.controller.solar_state.active is False

    coordinator.resume_charging()
    assert coordinator.effective_mode == ChargeMode.SOLAR


def test_pv_until_unplug_is_reset_after_vehicle_disconnect():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator._mode = ChargeMode.NORMAL
    coordinator._charging_paused = False
    coordinator._solar_until_unplug_active = True
    coordinator._fixed_current_until_unplug_active = True
    coordinator._last_vehicle_connected = True
    coordinator.controller = make_controller()
    coordinator.controller.solar_state.active = True

    wallbox = WallboxState(vehicle_connected=False)
    if (
        (coordinator._solar_until_unplug_active or coordinator._fixed_current_until_unplug_active)
        and coordinator._last_vehicle_connected
        and not wallbox.vehicle_connected
    ):
        coordinator._solar_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator.controller.reset_solar_state()
    coordinator._last_vehicle_connected = wallbox.vehicle_connected

    assert coordinator._solar_until_unplug_active is False
    assert coordinator._fixed_current_until_unplug_active is False
    assert coordinator.controller.solar_state.active is False


def test_set_pv_until_unplug_updates_override_state():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator._mode = ChargeMode.NORMAL
    coordinator._charging_paused = False
    coordinator._solar_until_unplug_active = False
    coordinator._fixed_current_until_unplug_active = False

    coordinator.set_solar_until_unplug(True)
    assert coordinator.solar_until_unplug_active is True
    assert coordinator.fixed_current_until_unplug_active is False

    coordinator.set_solar_until_unplug(False)
    assert coordinator.solar_until_unplug_active is False


def test_set_fixed_current_until_unplug_updates_override_state():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator._solar_until_unplug_active = True
    coordinator._fixed_current_until_unplug_active = False
    coordinator._mode = ChargeMode.NORMAL
    coordinator._charging_paused = False

    coordinator.set_fixed_current_until_unplug(True)
    assert coordinator.fixed_current_until_unplug_active is True
    assert coordinator.solar_until_unplug_active is False

    coordinator.set_fixed_current_until_unplug(False)
    assert coordinator.fixed_current_until_unplug_active is False


def test_set_mode_off_does_not_clear_charging_pause_state():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator._mode = ChargeMode.NORMAL
    coordinator._charging_paused = True
    coordinator._solar_until_unplug_active = False
    coordinator._fixed_current_until_unplug_active = False
    coordinator.controller = make_controller()

    coordinator.set_mode(ChargeMode.OFF)

    assert coordinator.mode == ChargeMode.OFF
    assert coordinator.charging_enabled is False


def test_charging_on_restart_restores_default_mode_and_allows_charging():
    hass = SimpleNamespace(_storage_data={})
    entry = make_config_entry(options={"startup_charge_mode": "normal"})

    first = WebastoUniteCoordinator(hass, entry)
    asyncio.run(first.async_set_charging_enabled(True))

    restarted = WebastoUniteCoordinator(hass, entry)
    asyncio.run(restarted._async_restore_charging_enabled_state())

    wallbox = WallboxState(vehicle_connected=True, charging_active=True)
    sensors = HaSensorSnapshot(phase_currents=PhaseCurrents(l1=0.0, l2=0.0, l3=0.0), valid=True)
    decision = restarted.controller.evaluate(restarted.effective_mode, wallbox, sensors)

    assert restarted.mode == ChargeMode.NORMAL
    assert restarted.charging_enabled is True
    assert decision.charging_enabled is True


def test_charging_off_stays_off_after_restart_with_vehicle_connected():
    hass = SimpleNamespace(_storage_data={})
    entry = make_config_entry(options={"startup_charge_mode": "normal"})

    first = WebastoUniteCoordinator(hass, entry)
    asyncio.run(first.async_set_charging_enabled(False))

    restarted = WebastoUniteCoordinator(hass, entry)
    asyncio.run(restarted._async_restore_charging_enabled_state())

    wallbox = WallboxState(vehicle_connected=True, charging_active=True)
    sensors = HaSensorSnapshot(phase_currents=PhaseCurrents(l1=0.0, l2=0.0, l3=0.0), valid=True)
    decision = restarted.controller.evaluate(restarted.effective_mode, wallbox, sensors)

    assert restarted.mode == ChargeMode.NORMAL
    assert restarted.charging_enabled is False
    assert restarted.effective_mode == ChargeMode.OFF
    assert decision.charging_enabled is False


def test_charging_off_stays_off_after_restart_without_vehicle_connected():
    hass = SimpleNamespace(_storage_data={})
    entry = make_config_entry(options={"startup_charge_mode": "normal"})

    first = WebastoUniteCoordinator(hass, entry)
    asyncio.run(first.async_set_charging_enabled(False))

    restarted = WebastoUniteCoordinator(hass, entry)
    asyncio.run(restarted._async_restore_charging_enabled_state())
    charging_switch = WebastoChargingSwitch(restarted)

    assert restarted.mode == ChargeMode.NORMAL
    assert restarted.charging_enabled is False
    assert charging_switch.is_on is False


def test_reenabling_charging_after_restart_resumes_default_mode_behavior():
    hass = SimpleNamespace(_storage_data={})
    entry = make_config_entry(options={"startup_charge_mode": "normal"})

    first = WebastoUniteCoordinator(hass, entry)
    asyncio.run(first.async_set_charging_enabled(False))

    restarted = WebastoUniteCoordinator(hass, entry)
    asyncio.run(restarted._async_restore_charging_enabled_state())
    asyncio.run(restarted.async_set_charging_enabled(True))

    wallbox = WallboxState(vehicle_connected=True, charging_active=True)
    sensors = HaSensorSnapshot(phase_currents=PhaseCurrents(l1=0.0, l2=0.0, l3=0.0), valid=True)
    decision = restarted.controller.evaluate(restarted.effective_mode, wallbox, sensors)

    assert restarted.mode == ChargeMode.NORMAL
    assert restarted.charging_enabled is True
    assert restarted.effective_mode == ChargeMode.NORMAL
    assert decision.charging_enabled is True


def test_public_surplus_resolver_is_available_and_returns_expected_value():
    controller = make_controller()
    sensors = HaSensorSnapshot(surplus_power_w=1234.0, valid=True)

    assert controller.resolve_surplus_power(sensors) == 1234.0


def test_dlb_start_guard_suppresses_first_transient_downscale_sample():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.control_config = ControlConfig(dlb_sensor_scope="total_including_charger")
    install_runtime_guards(coordinator)

    wallbox = WallboxState(charging_active=True, current_limit_a=16.0)
    first = SimpleNamespace(
        should_write=True,
        target_current_a=12.0,
        dominant_limit_reason=ControlReason.DLB_LIMITED,
    )
    coordinator.runtime_guards.apply_dlb_start_transient_guard(
        wallbox=wallbox,
        decision=first,
        now_monotonic=10.0,
    )
    assert first.should_write is False

    second = SimpleNamespace(
        should_write=True,
        target_current_a=12.0,
        dominant_limit_reason=ControlReason.DLB_LIMITED,
    )
    coordinator.runtime_guards.apply_dlb_start_transient_guard(
        wallbox=wallbox,
        decision=second,
        now_monotonic=11.0,
    )
    assert second.should_write is True


def test_dlb_start_guard_is_disabled_for_load_excluding_charger_scope():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.control_config = ControlConfig(dlb_sensor_scope="load_excluding_charger")
    install_runtime_guards(coordinator)

    wallbox = WallboxState(charging_active=True, current_limit_a=16.0)
    decision = SimpleNamespace(
        should_write=True,
        target_current_a=12.0,
        dominant_limit_reason=ControlReason.DLB_LIMITED,
    )
    coordinator.runtime_guards.apply_dlb_start_transient_guard(
        wallbox=wallbox,
        decision=decision,
        now_monotonic=10.0,
    )
    assert decision.should_write is True


def test_dlb_start_guard_does_not_delay_reduction_to_minimum_current():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.control_config = ControlConfig(
        dlb_sensor_scope="total_including_charger",
        min_current_a=6.0,
    )
    install_runtime_guards(coordinator)

    wallbox = WallboxState(charging_active=True, current_limit_a=16.0)
    decision = SimpleNamespace(
        should_write=True,
        target_current_a=6.0,
        dominant_limit_reason=ControlReason.DLB_LIMITED,
    )
    coordinator.runtime_guards.apply_dlb_start_transient_guard(
        wallbox=wallbox,
        decision=decision,
        now_monotonic=10.0,
    )
    assert decision.should_write is True


def test_pv_start_guard_suppresses_first_transient_pause_write_when_input_unavailable():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.control_config = ControlConfig(solar_control_strategy=SolarControlStrategy.SURPLUS)
    coordinator._mode = ChargeMode.SOLAR
    coordinator._charging_paused = False
    coordinator._solar_until_unplug_active = False
    coordinator._fixed_current_until_unplug_active = False
    install_runtime_guards(coordinator)

    wallbox = WallboxState(charging_active=True, current_limit_a=16.0)
    decision = SimpleNamespace(
        should_write=True,
        charging_enabled=False,
        target_current_a=0.0,
        reason=ControlReason.BELOW_MIN_CURRENT,
        dominant_limit_reason=None,
    )
    sensors = SimpleNamespace(solar_input_state="unavailable")

    coordinator.runtime_guards.apply_solar_start_transient_guard(
        effective_mode=coordinator.effective_mode,
        wallbox=wallbox,
        decision=decision,
        sensors=sensors,
        now_monotonic=10.0,
    )

    assert decision.should_write is False


def test_pv_start_guard_allows_confirmed_pause_write_on_second_sample():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.control_config = ControlConfig(solar_control_strategy=SolarControlStrategy.SURPLUS)
    coordinator._mode = ChargeMode.SOLAR
    coordinator._charging_paused = False
    coordinator._solar_until_unplug_active = False
    coordinator._fixed_current_until_unplug_active = False
    install_runtime_guards(coordinator)

    wallbox = WallboxState(charging_active=True, current_limit_a=16.0)
    first = SimpleNamespace(
        should_write=True,
        charging_enabled=False,
        target_current_a=0.0,
        reason=ControlReason.BELOW_MIN_CURRENT,
        dominant_limit_reason=None,
    )
    sensors = SimpleNamespace(solar_input_state="unavailable")
    coordinator.runtime_guards.apply_solar_start_transient_guard(
        effective_mode=coordinator.effective_mode,
        wallbox=wallbox,
        decision=first,
        sensors=sensors,
        now_monotonic=10.0,
    )
    assert first.should_write is False

    second = SimpleNamespace(
        should_write=True,
        charging_enabled=False,
        target_current_a=0.0,
        reason=ControlReason.BELOW_MIN_CURRENT,
        dominant_limit_reason=None,
    )
    coordinator.runtime_guards.apply_solar_start_transient_guard(
        effective_mode=coordinator.effective_mode,
        wallbox=wallbox,
        decision=second,
        sensors=sensors,
        now_monotonic=11.0,
    )
    assert second.should_write is True


def test_async_setup_entry_cleans_up_when_first_refresh_fails(monkeypatch):
    integration = importlib.import_module("custom_components.webasto_unite.__init__")
    coordinator = SimpleNamespace(
        async_setup=AsyncMock(),
        async_shutdown=AsyncMock(),
        async_config_entry_first_refresh=AsyncMock(side_effect=Exception("boom")),
    )

    monkeypatch.setattr(integration, "WebastoUniteCoordinator", lambda hass, entry: coordinator)

    hass = SimpleNamespace(
        data={},
        config_entries=SimpleNamespace(async_forward_entry_setups=AsyncMock()),
    )
    entry = SimpleNamespace(
        entry_id="test-entry",
        add_update_listener=lambda listener: listener,
        async_on_unload=lambda callback: None,
    )

    async def _run():
        with pytest.raises(ConfigEntryNotReady):
            await integration.async_setup_entry(hass, entry)

    asyncio.run(_run())

    coordinator.async_setup.assert_awaited_once()
    coordinator.async_shutdown.assert_awaited_once()
    hass.config_entries.async_forward_entry_setups.assert_not_awaited()


def test_number_entities_use_runtime_current_limits():
    coordinator = SimpleNamespace(
        entry=SimpleNamespace(entry_id="test-entry"),
        control_config=ControlConfig(
            min_current_a=8.0,
            max_current_a=16.0,
            fixed_current_a=10.0,
        ),
        async_request_refresh=AsyncMock(),
    )

    current_limit = WebastoMaximumCurrentNumber(coordinator)
    requested_current = WebastoRequestedCurrentNumber(coordinator)
    fixed_current = WebastoFixedCurrentNumber(coordinator)

    assert current_limit._attr_entity_registry_enabled_default is False
    assert current_limit.native_min_value == 8.0
    assert current_limit.native_max_value == 32.0
    assert requested_current.native_min_value == 8.0
    assert requested_current.native_max_value == 16.0
    assert fixed_current.native_min_value == 8.0
    assert fixed_current.native_max_value == 16.0


def test_requested_current_number_only_created_in_external_controller_mode():
    async def _run():
        entry = SimpleNamespace(entry_id="entry-id")
        added_entities = []
        hass = SimpleNamespace(
            data={
                "webasto_unite": {
                    "entry-id": SimpleNamespace(
                        control_config=ControlConfig(control_mode=ControlMode.MANAGED_CONTROL),
                        entry=entry,
                    )
                }
            }
        )

        def _add_entities(entities):
            added_entities.extend(entities)

        await number_async_setup_entry(hass, entry, _add_entities)
        assert not any(isinstance(entity, WebastoRequestedCurrentNumber) for entity in added_entities)

        added_entities.clear()
        hass.data["webasto_unite"]["entry-id"].control_config = ControlConfig(
            control_mode=ControlMode.EXTERNAL_CONTROLLER
        )

        await number_async_setup_entry(hass, entry, _add_entities)
        assert any(isinstance(entity, WebastoRequestedCurrentNumber) for entity in added_entities)

    asyncio.run(_run())


def test_diagnostics_redacts_identity_fields():
    wallbox = WallboxState(
        serial_number="SERIAL-123",
        charge_point_id="CPID-456",
        firmware_version="fw",
        brand="Webasto",
        model_name="Unite",
    )
    snapshot = RuntimeSnapshot(
        wallbox=wallbox,
        mode=ChargeMode.NORMAL,
        effective_mode=ChargeMode.NORMAL,
        operating_state="normal",
        control_mode=ControlMode.MANAGED_CONTROL,
        control_reason="normal_mode",
        charging_paused=False,
        solar_until_unplug_active=False,
        fixed_current_until_unplug_active=False,
        keepalive_age_s=None,
        keepalive_interval_s=None,
        keepalive_overdue=False,
        keepalive_sent_count=0,
        keepalive_write_failures=0,
        queue_depth=0,
        pending_write_kind=None,
    )
    coordinator = SimpleNamespace(
        data=snapshot,
        client=SimpleNamespace(
            stats=SimpleNamespace(
                connected=True,
                connect_attempts=1,
                read_failures=0,
                write_failures=0,
                reconnects=0,
                last_error=None,
            )
        ),
    )
    hass = SimpleNamespace(data={"webasto_unite": {"test-entry": coordinator}})
    entry = SimpleNamespace(
        entry_id="test-entry",
        data={"host": "192.168.1.10"},
        options={"charge_point_id": "CPID-456"},
    )

    result = asyncio.run(async_get_config_entry_diagnostics(hass, entry))

    assert result["entry"]["host"] == "REDACTED"
    assert result["identity_summary"]["serial_number"] == "REDACTED"
    assert result["identity_summary"]["charge_point_id"] == "REDACTED"
    assert result["wallbox_summary"]["serial_number"] == "REDACTED"
    assert result["wallbox_summary"]["charge_point_id"] == "REDACTED"


def test_only_charging_on_off_is_persistent_not_runtime_mode():
    hass = SimpleNamespace(_storage_data={})
    entry = make_config_entry(options={"startup_charge_mode": "normal"})

    first = WebastoUniteCoordinator(hass, entry)
    first.set_mode(ChargeMode.SOLAR)
    asyncio.run(first.async_set_charging_enabled(False))

    restarted = WebastoUniteCoordinator(hass, entry)
    asyncio.run(restarted._async_restore_charging_enabled_state())

    assert restarted.mode == ChargeMode.NORMAL
    assert restarted.charging_enabled is False
    assert restarted.effective_mode == ChargeMode.OFF


def test_temporary_session_settings_are_not_restored_after_restart():
    hass = SimpleNamespace(_storage_data={})
    entry = make_config_entry(options={"startup_charge_mode": "normal"})

    first = WebastoUniteCoordinator(hass, entry)
    first.set_solar_until_unplug(True)
    first.set_fixed_current_until_unplug(True)
    asyncio.run(first.async_set_charging_enabled(True))

    restarted = WebastoUniteCoordinator(hass, entry)
    asyncio.run(restarted._async_restore_charging_enabled_state())

    assert restarted.mode == ChargeMode.NORMAL
    assert restarted.charging_enabled is True
    assert restarted.solar_until_unplug_active is False
    assert restarted.fixed_current_until_unplug_active is False
    assert restarted.effective_mode == ChargeMode.NORMAL


def test_charge_mode_select_exposes_specific_solar_modes_when_solar_is_configured():
    coordinator = SimpleNamespace(
        entry=make_config_entry(),
        control_config=ControlConfig(solar_control_strategy=SolarControlStrategy.ECO_SOLAR),
        data=None,
    )
    select = WebastoModeSelect(coordinator)

    assert select.options == ["Off", "Normal", "Eco Solar", "Smart Solar", "Solar Boost", "Fixed Current"]


def test_charge_mode_select_hides_solar_modes_when_solar_is_disabled():
    coordinator = SimpleNamespace(
        entry=make_config_entry(),
        control_config=ControlConfig(solar_control_strategy=SolarControlStrategy.DISABLED),
        data=None,
    )
    select = WebastoModeSelect(coordinator)

    assert select.options == ["Off", "Normal", "Fixed Current"]


def test_charge_mode_select_sets_runtime_solar_strategy():
    coordinator = SimpleNamespace(
        entry=make_config_entry(),
        control_config=ControlConfig(solar_control_strategy=SolarControlStrategy.ECO_SOLAR),
        data=None,
        set_mode=Mock(),
        async_request_refresh=AsyncMock(),
    )
    select = WebastoModeSelect(coordinator)

    asyncio.run(select.async_select_option("Smart Solar"))

    coordinator.set_mode.assert_called_once_with(ChargeMode.SOLAR, SolarControlStrategy.SMART_SOLAR)
    coordinator.async_request_refresh.assert_awaited_once()


def test_charge_mode_select_shows_active_runtime_solar_strategy():
    coordinator = SimpleNamespace(
        entry=make_config_entry(),
        control_config=ControlConfig(solar_control_strategy=SolarControlStrategy.ECO_SOLAR),
        active_solar_strategy=SolarControlStrategy.ECO_SOLAR,
        data=RuntimeSnapshot(
            wallbox=WallboxState(),
            mode=ChargeMode.SOLAR,
            effective_mode=ChargeMode.SOLAR,
            operating_state="solar_boost",
            control_mode=ControlMode.MANAGED_CONTROL,
            control_reason=ControlReason.SOLAR_MODE.value,
            charging_paused=False,
            solar_until_unplug_active=False,
            fixed_current_until_unplug_active=False,
            keepalive_age_s=None,
            keepalive_interval_s=None,
            keepalive_overdue=False,
            keepalive_sent_count=0,
            keepalive_write_failures=0,
            queue_depth=0,
            pending_write_kind=None,
            active_solar_strategy=SolarControlStrategy.SOLAR_BOOST,
        ),
    )
    select = WebastoModeSelect(coordinator)

    assert select.current_option == "Solar Boost"


def test_capability_builder_marks_unconfirmed_and_optional_features():
    wallbox = WallboxState(ev_max_current_a=None)

    capabilities = build_capabilities(wallbox)

    assert capabilities["current_control_5004"] == "confirmed"
    assert capabilities["keepalive_6000"] == "confirmed"
    assert capabilities["ev_max_current_1108"] == "optional_absent"


def test_capability_summary_reflects_partial_validation_state():
    wallbox = WallboxState(ev_max_current_a=None)

    assert build_capability_summary(wallbox) == "validated_with_optional_gaps"


def test_operating_state_reports_temporary_pv_override():
    decision = SimpleNamespace(
        fallback_active=False,
        reason=ControlReason.SOLAR_MODE,
        dominant_limit_reason=None,
    )

    assert make_operating_state(
        effective_mode=ChargeMode.SOLAR,
        decision=decision,
        solar_until_unplug_active=True,
    ) == "solar_until_unplug"


def test_operating_state_reports_monitoring_only_not_writing():
    decision = SimpleNamespace(
        fallback_active=False,
        reason=ControlReason.FIXED_CURRENT_MODE,
        dominant_limit_reason=None,
    )

    assert make_operating_state(
        effective_mode=ChargeMode.FIXED_CURRENT,
        decision=decision,
        control_config=ControlConfig(control_mode=ControlMode.KEEPALIVE_ONLY),
    ) == "monitoring_only_not_writing"


def test_operating_state_reports_temporary_fixed_current_override():
    decision = SimpleNamespace(
        fallback_active=False,
        reason=ControlReason.FIXED_CURRENT_MODE,
        dominant_limit_reason=None,
    )

    assert make_operating_state(
        effective_mode=ChargeMode.FIXED_CURRENT,
        decision=decision,
        fixed_current_until_unplug_active=True,
    ) == "fixed_current_until_unplug"


def test_pv_waiting_for_surplus_writes_zero_current():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.write_queue = WriteQueueManager()
        coordinator.data = SimpleNamespace(
            wallbox=WallboxState(
                charging_active=True,
                vehicle_connected=True,
                current_limit_a=6.0,
            )
        )
        coordinator._mode = ChargeMode.SOLAR
        coordinator._charging_paused = False
        coordinator._solar_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._allows_control_writes = lambda: True

        decision = SimpleNamespace(
            charging_enabled=False,
            reason=ControlReason.BELOW_MIN_CURRENT,
            target_current_a=None,
            should_write=False,
        )

        await enqueue_test_decision(coordinator, decision)
        item = await coordinator.write_queue.peek_next()

        assert item.key == "current_limit"
        assert item.value == 0

    asyncio.run(_run())


def test_pv_min_always_sensor_unavailable_does_not_force_pause():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.write_queue = WriteQueueManager()
        coordinator.data = SimpleNamespace(
            wallbox=WallboxState(
                charging_active=True,
                vehicle_connected=True,
                current_limit_a=6.0,
            )
        )
        coordinator._mode = ChargeMode.SOLAR
        coordinator._charging_paused = False
        coordinator._solar_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._allows_control_writes = lambda: True

        decision = SimpleNamespace(
            charging_enabled=True,
            reason=ControlReason.SENSOR_UNAVAILABLE,
            target_current_a=6.0,
            should_write=True,
        )

        await enqueue_test_decision(coordinator, decision)
        item = await coordinator.write_queue.peek_next()

        assert item.key == "current_limit"
        assert item.value == 6

    asyncio.run(_run())


def test_safety_below_minimum_writes_zero_current():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.write_queue = WriteQueueManager()
        coordinator.data = SimpleNamespace(
            wallbox=WallboxState(
                charging_active=True,
                vehicle_connected=True,
                current_limit_a=16.0,
            )
        )
        coordinator._mode = ChargeMode.NORMAL
        coordinator._charging_paused = False
        coordinator._solar_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._allows_control_writes = lambda: True

        decision = SimpleNamespace(
            charging_enabled=False,
            reason=ControlReason.BELOW_MIN_CURRENT,
            dominant_limit_reason=ControlReason.DLB_LIMITED,
            target_current_a=None,
            should_write=False,
        )

        await enqueue_test_decision(coordinator, decision)
        item = await coordinator.write_queue.peek_next()

        assert item.key == "current_limit"
        assert item.value == 0

    asyncio.run(_run())


def test_pv_waiting_for_surplus_does_not_repeat_zero_current_when_already_paused():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.write_queue = WriteQueueManager()
        coordinator.data = SimpleNamespace(
            wallbox=WallboxState(
                charging_active=False,
                vehicle_connected=False,
                current_limit_a=0.0,
            )
        )
        coordinator._mode = ChargeMode.SOLAR
        coordinator._charging_paused = False
        coordinator._solar_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._allows_control_writes = lambda: True

        decision = SimpleNamespace(
            charging_enabled=False,
            reason=ControlReason.BELOW_MIN_CURRENT,
            target_current_a=None,
            should_write=False,
        )

        await enqueue_test_decision(coordinator, decision)

        assert await coordinator.write_queue.size() == 0

    asyncio.run(_run())


def test_operating_state_reports_waiting_for_surplus():
    decision = SimpleNamespace(
        fallback_active=False,
        reason=ControlReason.BELOW_MIN_CURRENT,
        dominant_limit_reason=None,
    )

    assert make_operating_state(effective_mode=ChargeMode.SOLAR, decision=decision) == "waiting_for_solar"


def test_operating_state_reports_dlb_limited():
    decision = SimpleNamespace(
        fallback_active=False,
        reason=ControlReason.DLB_LIMITED,
        dominant_limit_reason=ControlReason.DLB_LIMITED,
    )

    assert make_operating_state(effective_mode=ChargeMode.NORMAL, decision=decision) == "dlb_limited"


def test_operating_state_reports_min_plus_surplus():
    decision = SimpleNamespace(
        fallback_active=False,
        reason=ControlReason.SOLAR_MODE,
        dominant_limit_reason=None,
    )

    assert make_operating_state(
        effective_mode=ChargeMode.SOLAR,
        decision=decision,
        control_config=ControlConfig(
            control_mode=ControlMode.MANAGED_CONTROL,
            solar_control_strategy="smart_solar",
        ),
    ) == "smart_solar"


def test_operating_state_reports_solar_boost():
    decision = SimpleNamespace(
        fallback_active=False,
        reason=ControlReason.SOLAR_MODE,
        dominant_limit_reason=None,
    )

    assert make_operating_state(
        effective_mode=ChargeMode.SOLAR,
        decision=decision,
        control_config=ControlConfig(
            control_mode=ControlMode.MANAGED_CONTROL,
            solar_control_strategy="solar_boost",
        ),
    ) == "solar_boost"


def test_legacy_min_always_operating_state_normalizes_to_min_plus_surplus():
    decision = SimpleNamespace(
        fallback_active=False,
        reason=ControlReason.SOLAR_MODE,
        dominant_limit_reason=None,
    )

    assert make_operating_state(
        effective_mode=ChargeMode.SOLAR,
        decision=decision,
        control_config=ControlConfig(
            control_mode=ControlMode.MANAGED_CONTROL,
            solar_control_strategy="min_always_plus_surplus",
        ),
    ) == "solar_boost"


def test_operating_state_reports_fallback_before_mode():
    decision = SimpleNamespace(
        fallback_active=True,
        reason=ControlReason.SAFE_CURRENT_FALLBACK,
        dominant_limit_reason=None,
    )

    assert make_operating_state(effective_mode=ChargeMode.NORMAL, decision=decision) == "fallback"


def test_coordinator_solar_runtime_uses_adaptive_start_then_observed_three_phase_state():
    async def _run():
        first_wallbox = WallboxState(
            installed_phases=3,
            vehicle_connected=True,
            charging_active=False,
            phases_in_use=None,
            current_limit_a=0.0,
        )
        second_wallbox = WallboxState(
            installed_phases=3,
            vehicle_connected=True,
            charging_active=True,
            phases_in_use=3,
            current_limit_a=0.0,
        )
        wallbox_reader = SimpleNamespace(read_wallbox_state=AsyncMock(side_effect=[first_wallbox, second_wallbox]))
        hass = SimpleNamespace(
            states=SimpleNamespace(
                get=lambda entity_id: {
                    "sensor.solar_surplus": SimpleNamespace(state="2300", attributes={"unit_of_measurement": "W"}),
                }.get(entity_id)
            ),
            async_create_task=lambda coro: asyncio.create_task(coro),
        )

        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.hass = hass
        coordinator.entry = make_config_entry(
            options={
                "solar_control_strategy": "eco_solar",
                "solar_input_model": "surplus_sensor",
                "solar_surplus_sensor": "sensor.solar_surplus",
                "control_mode": "managed_control",
                "min_current": 6.0,
                "max_current": 16.0,
                "fixed_current": 6.0,
                "safe_current": 6.0,
                "keepalive_interval": 10.0,
                "polling_interval": 2.0,
                "timeout": 3.0,
                "retries": 3,
            },
        )
        coordinator.control_config = ControlConfig(
            control_mode=ControlMode.MANAGED_CONTROL,
            solar_control_strategy=SolarControlStrategy.ECO_SOLAR,
            solar_input_model=SolarInputModel.SURPLUS_SENSOR,
            min_current_a=6.0,
            max_current_a=16.0,
            fixed_current_a=6.0,
            safe_current_a=6.0,
        )
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.sensor_adapter = HaSensorAdapter(hass)
        coordinator.wallbox_reader = wallbox_reader
        coordinator.write_queue = WriteQueueManager()
        coordinator.client = SimpleNamespace(stats=SimpleNamespace(last_error=None))
        coordinator._mode = ChargeMode.SOLAR
        coordinator._charging_paused = False
        coordinator._solar_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._last_vehicle_connected = False
        install_runtime_guards(coordinator, startup_ready=True)
        coordinator._last_keepalive_sent_monotonic = monotonic()
        coordinator._keepalive_started_monotonic = monotonic() - 10.0
        coordinator._keepalive_sent_count = 0
        coordinator._keepalive_write_failures = 0
        coordinator._sensor_refresh_task = None
        coordinator._flush_lock = asyncio.Lock()
        install_mock_write_runtime(coordinator)

        first_snapshot = await coordinator._async_update_data()
        second_snapshot = await coordinator._async_update_data()

        assert first_snapshot.mode_target_a is None
        assert first_snapshot.final_target_a is None
        assert first_snapshot.wallbox.phases_in_use is None
        assert first_snapshot.solar_input_state == "ready"

        assert second_snapshot.mode_target_a is None
        assert second_snapshot.final_target_a is None
        assert second_snapshot.wallbox.phases_in_use == 3
        assert second_snapshot.operating_state == "waiting_for_solar"

    asyncio.run(_run())


def test_coordinator_caches_observed_session_phase_for_later_solar_restarts():
    async def _run():
        wallboxes = [
            WallboxState(
                installed_phases=3,
                vehicle_connected=True,
                charging_active=True,
                phases_in_use=3,
                current_limit_a=0.0,
            ),
            WallboxState(
                installed_phases=3,
                vehicle_connected=True,
                charging_active=True,
                phases_in_use=3,
                current_limit_a=0.0,
            ),
            WallboxState(
                installed_phases=3,
                vehicle_connected=True,
                charging_active=False,
                phases_in_use=None,
                current_limit_a=0.0,
            ),
            WallboxState(
                installed_phases=3,
                vehicle_connected=False,
                charging_active=False,
                phases_in_use=None,
                current_limit_a=0.0,
            ),
        ]
        wallbox_reader = SimpleNamespace(read_wallbox_state=AsyncMock(side_effect=wallboxes))
        hass = SimpleNamespace(
            states=SimpleNamespace(
                get=lambda entity_id: {
                    "sensor.solar_surplus": SimpleNamespace(state="2300", attributes={"unit_of_measurement": "W"}),
                }.get(entity_id)
            ),
            async_create_task=lambda coro: asyncio.create_task(coro),
        )

        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.hass = hass
        coordinator.entry = make_config_entry(
            options={
                "solar_control_strategy": "eco_solar",
                "solar_input_model": "surplus_sensor",
                "solar_surplus_sensor": "sensor.solar_surplus",
                "control_mode": "managed_control",
            },
        )
        coordinator.control_config = ControlConfig(
            control_mode=ControlMode.MANAGED_CONTROL,
            solar_control_strategy=SolarControlStrategy.ECO_SOLAR,
            solar_input_model=SolarInputModel.SURPLUS_SENSOR,
            min_current_a=6.0,
            max_current_a=16.0,
            fixed_current_a=6.0,
            safe_current_a=6.0,
        )
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.sensor_adapter = HaSensorAdapter(hass)
        coordinator.wallbox_reader = wallbox_reader
        coordinator.write_queue = WriteQueueManager()
        coordinator.client = SimpleNamespace(stats=SimpleNamespace(last_error=None))
        coordinator._mode = ChargeMode.SOLAR
        coordinator._charging_paused = False
        coordinator._solar_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._last_vehicle_connected = True
        install_runtime_guards(coordinator, startup_ready=True)
        coordinator._last_keepalive_sent_monotonic = monotonic()
        coordinator._keepalive_started_monotonic = monotonic() - 10.0
        coordinator._keepalive_sent_count = 0
        coordinator._keepalive_write_failures = 0
        coordinator._sensor_refresh_task = None
        coordinator._flush_lock = asyncio.Lock()
        install_mock_write_runtime(coordinator)

        first_snapshot = await coordinator._async_update_data()
        second_snapshot = await coordinator._async_update_data()
        third_snapshot = await coordinator._async_update_data()

        assert coordinator.controller.observed_session_phase_count == 3
        assert coordinator.controller.session_observed_3p is True
        assert first_snapshot.wallbox.phases_in_use == 3
        assert second_snapshot.wallbox.phases_in_use == 3
        assert third_snapshot.wallbox.phases_in_use is None
        assert third_snapshot.mode_target_a is None
        assert third_snapshot.final_target_a is None
        assert third_snapshot.operating_state == "waiting_for_solar"

        await coordinator._async_update_data()

        assert coordinator.controller.observed_session_phase_count is None
        assert coordinator.controller.session_observed_3p is False

    asyncio.run(_run())


def test_sensor_presentation_uses_clear_solar_labels():
    assert WebastoSensor._present_value("fallback") == "Safe Fallback"
    assert (
        WebastoSensor._present_value("unavailable", value_key="solar_input_state")
        == "Solar Input Unavailable"
    )


def test_evcc_status_reports_unknown_without_runtime_data():
    status = build_evcc_status(None)

    assert status["charger_state"] == "unknown"
    assert status["charger_state_label"] == "Unknown"
    assert status["iec61851_state"] == "Unknown"
    assert status["unavailable_reason"] == "no_runtime_data"
    assert status["unavailable_reason_label"] == "No Runtime Data"


def test_evcc_status_exposes_stable_fields_from_runtime_snapshot():
    snapshot = RuntimeSnapshot(
        wallbox=WallboxState(
            available=True,
            vehicle_connected=True,
            charging_active=True,
            charge_point_state_raw=2,
            charge_state_raw=1,
            evse_state_raw=1,
            cable_state_raw=3,
            current_limit_a=10.0,
            actual_current_a=9.8,
            active_power_w=6800.0,
            session_energy_kwh=4.2,
            phases_in_use=3,
            hardware_min_current_a=6.0,
            session_max_current_a=16.0,
        ),
        mode=ChargeMode.SOLAR,
        effective_mode=ChargeMode.SOLAR,
        operating_state="smart_solar",
        control_mode=ControlMode.MANAGED_CONTROL,
        control_reason=ControlReason.SOLAR_MODE.value,
        charging_paused=False,
        solar_until_unplug_active=False,
        fixed_current_until_unplug_active=False,
        keepalive_age_s=1.0,
        keepalive_interval_s=10.0,
        keepalive_overdue=False,
        keepalive_sent_count=1,
        keepalive_write_failures=0,
        queue_depth=0,
        pending_write_kind=None,
        final_target_a=12.0,
        dlb_limit_a=14.0,
        solar_input_state="ready",
        solar_raw_surplus_w=3000.0,
        solar_filtered_surplus_w=2900.0,
        solar_target_current_a=12.0,
    )
    status = build_evcc_status(snapshot, ControlConfig(min_current_a=6.0, max_current_a=20.0))

    assert status["charger_state"] == "smart_solar"
    assert status["charger_state_label"] == "Smart Solar"
    assert status["iec61851_state"] == "C"
    assert status["max_current"] == 12.0
    assert status["offered_current"] == 10.0
    assert status["actual_current"] == 9.8
    assert status["actual_power"] == 6800.0
    assert status["phase_count_observed"] == 3
    assert status["configured_current_min"] == 6.0
    assert status["configured_current_max"] == 20.0
    assert status["control_owner"] == "solar"
    assert status["control_owner_label"] == "Solar"
    assert status["enabled"] is True
    assert status["charging_enabled"] is True
    assert status["vehicle_connected"] is True
    assert status["charging"] is True
    assert status["faulted"] is False


def test_evcc_status_sensor_uses_attributes_for_compatibility_fields():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.entry = make_config_entry()
    coordinator.control_config = ControlConfig(min_current_a=6.0, max_current_a=16.0)
    coordinator.data = RuntimeSnapshot(
        wallbox=WallboxState(available=True, vehicle_connected=True, charging_active=False),
        mode=ChargeMode.NORMAL,
        effective_mode=ChargeMode.NORMAL,
        operating_state="normal",
        control_mode=ControlMode.MANAGED_CONTROL,
        control_reason=ControlReason.NORMAL_MODE.value,
        charging_paused=False,
        solar_until_unplug_active=False,
        fixed_current_until_unplug_active=False,
        keepalive_age_s=1.0,
        keepalive_interval_s=10.0,
        keepalive_overdue=False,
        keepalive_sent_count=1,
        keepalive_write_failures=0,
        queue_depth=0,
        pending_write_kind=None,
    )
    description = next(item for item in SENSORS if item.key == "evcc_status")
    sensor = WebastoSensor(coordinator, description)

    assert sensor.native_value == "normal"
    assert sensor.extra_state_attributes["iec61851_state"] == "B"
    assert sensor.extra_state_attributes["charger_state_label"] == "Normal"
    assert sensor.extra_state_attributes["control_owner"] == "integration"
    assert sensor.extra_state_attributes["control_owner_label"] == "Integration"
    assert sensor.extra_state_attributes["configured_current_max"] == 16.0


def test_control_owner_sensor_reports_external_controller():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.entry = make_config_entry()
    coordinator.control_config = ControlConfig(control_mode=ControlMode.EXTERNAL_CONTROLLER)
    coordinator.data = RuntimeSnapshot(
        wallbox=WallboxState(available=True, vehicle_connected=True, charging_active=False),
        mode=ChargeMode.NORMAL,
        effective_mode=ChargeMode.NORMAL,
        operating_state="external_controller",
        control_mode=ControlMode.EXTERNAL_CONTROLLER,
        control_reason=ControlReason.NORMAL_MODE.value,
        charging_paused=False,
        solar_until_unplug_active=False,
        fixed_current_until_unplug_active=False,
        keepalive_age_s=1.0,
        keepalive_interval_s=10.0,
        keepalive_overdue=False,
        keepalive_sent_count=1,
        keepalive_write_failures=0,
        queue_depth=0,
        pending_write_kind=None,
    )
    description = next(item for item in SENSORS if item.key == "control_owner")
    sensor = WebastoSensor(coordinator, description)

    assert sensor.native_value == "External Controller"


def test_requested_phase_sensor_does_not_duplicate_phase_policy_attributes():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.entry = make_config_entry()
    coordinator.control_config = ControlConfig()
    coordinator.data = RuntimeSnapshot(
        wallbox=WallboxState(phase_switch_mode_raw=1),
        mode=ChargeMode.NORMAL,
        effective_mode=ChargeMode.NORMAL,
        operating_state="normal",
        control_mode=ControlMode.MANAGED_CONTROL,
        control_reason=ControlReason.NORMAL_MODE.value,
        charging_paused=False,
        solar_until_unplug_active=False,
        fixed_current_until_unplug_active=False,
        keepalive_age_s=1.0,
        keepalive_interval_s=10.0,
        keepalive_overdue=False,
        keepalive_sent_count=1,
        keepalive_write_failures=0,
        queue_depth=0,
        pending_write_kind=None,
        phase_switch_mode_raw=1,
        phase_switch_mode="3P",
        phase_switch_register_available=True,
        phase_policy_decision="would_request_1p",
        phase_policy_target="1P",
    )
    description = next(item for item in SENSORS if item.key == "phase_requested")
    sensor = WebastoSensor(coordinator, description)

    attrs = sensor.extra_state_attributes

    assert sensor.native_value == "3P"
    assert "policy_decision" not in attrs
    assert "policy_target" not in attrs
