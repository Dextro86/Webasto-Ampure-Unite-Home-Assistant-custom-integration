from types import SimpleNamespace
import asyncio
from time import monotonic
from unittest.mock import AsyncMock

from custom_components.webasto_unite.config_flow import (
    WebastoUniteOptionsFlow,
    _bounded_float,
    _validate_dlb_options,
    _validate_init_options,
    _validate_pv_options,
)
from custom_components.webasto_unite.const import DEFAULT_PV_PHASE_SWITCHING_MAX_PER_SESSION, DEFAULT_PV_PHASE_SWITCHING_MIN_INTERVAL_S
from custom_components.webasto_unite.controller import WallboxController
from custom_components.webasto_unite.coordinator import WebastoUniteCoordinator
from custom_components.webasto_unite.models import ChargeMode, ControlConfig, ControlMode, ControlReason, HaSensorSnapshot, PhaseCurrents, PvPhaseSwitchingMode, WallboxState
from custom_components.webasto_unite.sensor_adapter import HaSensorAdapter
from custom_components.webasto_unite.wallbox_reader import WallboxReader
from custom_components.webasto_unite.write_queue import WriteQueueManager
from custom_components.webasto_unite.registers import PHASE_SWITCH_MODE


def make_controller(**kwargs):
    defaults = dict(
        user_limit_a=16.0,
        max_current_a=16.0,
        min_current_a=6.0,
        stable_cycles_before_write=1,
        min_seconds_between_writes=0.0,
    )
    defaults.update(kwargs)
    return WallboxController(ControlConfig(**defaults))


def make_config_entry(data=None, options=None):
    return SimpleNamespace(
        data=data
        or {
            "host": "192.168.1.10",
            "port": 502,
            "unit_id": 255,
            "installed_phases": "3p",
        },
        options=options or {},
    )


def default_init_input(**overrides):
    data = {
        "host": "192.168.1.20",
        "port": 502,
        "unit_id": 255,
        "installed_phases": "3p",
        "min_current": 6.0,
        "max_current": 16.0,
        "user_limit": 16.0,
        "safe_current": 6.0,
        "control_mode": "keepalive_only",
        "startup_charge_mode": "normal",
        "keepalive_mode": "auto",
        "keepalive_interval": 10.0,
        "polling_interval": 2.0,
        "timeout": 3.0,
        "retries": 3,
    }
    data.update(overrides)
    return data


def default_dlb_input(**overrides):
    data = {
        "dlb_input_model": "disabled",
        "dlb_sensor_scope": "load_excluding_charger",
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
        "pv_control_strategy": "disabled",
        "pv_input_model": "grid_power_derived",
        "pv_surplus_sensor": None,
        "pv_start_threshold": 1800.0,
        "pv_stop_threshold": 1200.0,
        "pv_start_delay": 0.0,
        "pv_stop_delay": 0.0,
        "pv_min_runtime": 0.0,
        "pv_min_pause": 0.0,
        "pv_min_current": 6.0,
        "pv_until_unplug_strategy": "inherit",
        "pv_phase_switching_mode": "manual_only",
        "pv_phase_switching_hysteresis": 500.0,
        "pv_phase_switching_min_interval": DEFAULT_PV_PHASE_SWITCHING_MIN_INTERVAL_S,
        "pv_phase_switching_max_per_session": DEFAULT_PV_PHASE_SWITCHING_MAX_PER_SESSION,
        "fixed_current": 6.0,
    }
    data.update(overrides)
    return data


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
            user_limit_a=16.0,
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


def test_init_options_reject_inconsistent_current_limits():
    try:
        _validate_init_options(
            {
                "min_current": 10.0,
                "max_current": 8.0,
                "user_limit": 9.0,
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
                "dlb_input_model": "grid_power",
                "dlb_grid_power_sensor": None,
            }
            ,
            "1p",
        )
    except Exception as err:  # noqa: BLE001
        assert "grid power sensor" in str(err)
    else:
        raise AssertionError("Expected DLB option validation to fail")


def test_dlb_grid_power_is_rejected_for_3p_dlb():
    try:
        _validate_dlb_options(
            {
                "dlb_input_model": "grid_power",
                "dlb_grid_power_sensor": "sensor.grid_power",
            },
            "3p",
        )
    except Exception as err:  # noqa: BLE001
        assert "only supported for 1p" in str(err)
    else:
        raise AssertionError("Expected 3p grid-power DLB validation to fail")


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

        dlb_form = await flow.async_step_init(
            default_init_input(host="192.168.1.55", port=1502, unit_id=42, installed_phases="1p")
        )
        assert dlb_form["type"] == "form"
        assert dlb_form["step_id"] == "dlb"
        assert flow.options["host"] == "192.168.1.55"
        assert flow.options["port"] == 1502
        assert flow.options["unit_id"] == 42
        assert flow.options["installed_phases"] == "1p"

        pv_form = await flow.async_step_dlb(default_dlb_input())
        assert pv_form["type"] == "form"
        assert pv_form["step_id"] == "pv"

        result = await flow.async_step_pv(default_pv_input())
        assert result["type"] == "create_entry"
        assert result["data"]["host"] == "192.168.1.55"
        assert result["data"]["port"] == 1502
        assert result["data"]["unit_id"] == 42
        assert result["data"]["installed_phases"] == "1p"
        assert result["data"]["startup_charge_mode"] == "normal"
        assert result["data"]["dlb_input_model"] == "disabled"
        assert result["data"]["pv_control_strategy"] == "disabled"
        assert result["data"]["pv_phase_switching_mode"] == "manual_only"

    asyncio.run(_run())


def test_startup_charge_mode_can_be_configured():
    result = _validate_init_options(
        default_init_input(startup_charge_mode="pv")
    )

    assert result["startup_charge_mode"] == "pv"


def test_startup_charge_mode_falls_back_when_pv_is_disabled():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.control_config = ControlConfig(pv_control_strategy="disabled")

    assert coordinator._resolve_startup_mode({"startup_charge_mode": "pv"}) == ChargeMode.NORMAL


def test_startup_charge_mode_accepts_pv_when_pv_is_enabled():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.control_config = ControlConfig(pv_control_strategy="surplus")

    assert coordinator._resolve_startup_mode({"startup_charge_mode": "pv"}) == ChargeMode.PV


def test_runtime_current_setters_reject_out_of_range_values():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.control_config = ControlConfig(min_current_a=6.0, max_current_a=16.0)

    try:
        coordinator.set_user_limit(32.0)
    except ValueError as err:
        assert "Current Limit must be between 6 A and 16 A" in str(err)
    else:
        raise AssertionError("Expected Current Limit validation to fail")

    try:
        coordinator.set_fixed_current(0.0)
    except ValueError as err:
        assert "Fixed Current must be between 6 A and 16 A" in str(err)
    else:
        raise AssertionError("Expected Fixed Current validation to fail")


def test_options_flow_dlb_phase_current_3p_requires_all_phase_sensors():
    async def _run():
        flow = WebastoUniteOptionsFlow(make_config_entry())

        await flow.async_step_init(default_init_input(installed_phases="3p"))
        result = await flow.async_step_dlb(
            default_dlb_input(
                dlb_input_model="phase_currents",
                dlb_l1_sensor="sensor.l1",
                dlb_l2_sensor=None,
                dlb_l3_sensor="sensor.l3",
            )
        )

        assert result["type"] == "form"
        assert result["step_id"] == "dlb"
        assert result["errors"]["base"] == "dlb_phase_sensor_required"

    asyncio.run(_run())


def test_options_flow_dlb_phase_current_1p_requires_only_l1_sensor():
    async def _run():
        flow = WebastoUniteOptionsFlow(make_config_entry())

        await flow.async_step_init(default_init_input(installed_phases="1p"))
        result = await flow.async_step_dlb(
            default_dlb_input(
                dlb_input_model="phase_currents",
                dlb_l1_sensor="sensor.l1",
                dlb_l2_sensor=None,
                dlb_l3_sensor=None,
            )
        )

        assert result["type"] == "form"
        assert result["step_id"] == "pv"
        assert flow.options["dlb_input_model"] == "phase_currents"

    asyncio.run(_run())


def test_options_flow_pv_surplus_mode_requires_surplus_sensor():
    async def _run():
        flow = WebastoUniteOptionsFlow(make_config_entry())

        await flow.async_step_init(default_init_input())
        await flow.async_step_dlb(default_dlb_input())
        result = await flow.async_step_pv(
            default_pv_input(
                pv_control_strategy="surplus",
                pv_input_model="surplus_sensor",
                pv_surplus_sensor=None,
            )
        )

        assert result["type"] == "form"
        assert result["step_id"] == "pv"
        assert result["errors"]["base"] == "pv_surplus_sensor_required"

    asyncio.run(_run())


def test_options_flow_pv_grid_derived_requires_grid_power_sensor():
    async def _run():
        flow = WebastoUniteOptionsFlow(make_config_entry())

        await flow.async_step_init(default_init_input())
        await flow.async_step_dlb(default_dlb_input(dlb_grid_power_sensor=None))
        result = await flow.async_step_pv(
            default_pv_input(
                pv_control_strategy="surplus",
                pv_input_model="grid_power_derived",
            )
        )

        assert result["type"] == "form"
        assert result["step_id"] == "pv"
        assert result["errors"]["base"] == "pv_grid_sensor_required"

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
                "pv_input_model": "surplus_sensor",
                "pv_surplus_sensor": None,
                "pv_start_threshold": 1200.0,
                "pv_stop_threshold": 1800.0,
                "pv_min_current": 6.0,
                "dlb_grid_power_sensor": None,
            }
        )
    except Exception as err:  # noqa: BLE001
        assert "pv_stop_threshold" in str(err) or "surplus sensor" in str(err)
    else:
        raise AssertionError("Expected PV option validation to fail")


def test_fixed_current_validation_is_independent_from_pv_surplus_model():
    result = _validate_pv_options(
        {
            "pv_input_model": "grid_power_derived",
            "pv_control_strategy": "surplus",
            "pv_surplus_sensor": None,
            "pv_start_threshold": 1800.0,
            "pv_stop_threshold": 1200.0,
            "pv_min_current": 6.0,
            "fixed_current": 8.0,
            "dlb_grid_power_sensor": "sensor.grid_power",
        }
    )

    assert result["fixed_current"] == 8.0


def test_pv_min_plus_surplus_strategy_requires_surplus_model_inputs():
    result = _validate_pv_options(
        {
            "pv_input_model": "grid_power_derived",
            "pv_control_strategy": "min_plus_surplus",
            "pv_until_unplug_strategy": "inherit",
            "pv_surplus_sensor": None,
            "pv_start_threshold": 1800.0,
            "pv_stop_threshold": 1200.0,
            "pv_min_current": 6.0,
            "fixed_current": 8.0,
            "dlb_grid_power_sensor": "sensor.grid_power",
        }
    )

    assert result["pv_control_strategy"] == "min_plus_surplus"


def test_pv_min_always_plus_surplus_strategy_requires_surplus_model_inputs():
    result = _validate_pv_options(
        {
            "pv_input_model": "grid_power_derived",
            "pv_control_strategy": "min_always_plus_surplus",
            "pv_until_unplug_strategy": "inherit",
            "pv_surplus_sensor": None,
            "pv_start_threshold": 1800.0,
            "pv_stop_threshold": 1200.0,
            "pv_min_current": 6.0,
            "fixed_current": 8.0,
            "dlb_grid_power_sensor": "sensor.grid_power",
        }
    )

    assert result["pv_control_strategy"] == "min_always_plus_surplus"


def test_disabled_pv_strategy_allows_empty_pv_sensor_configuration():
    result = _validate_pv_options(
        {
            "pv_input_model": "surplus_sensor",
            "pv_control_strategy": "disabled",
            "pv_until_unplug_strategy": "inherit",
            "pv_surplus_sensor": None,
            "pv_start_threshold": 1800.0,
            "pv_stop_threshold": 1200.0,
            "pv_min_current": 6.0,
            "fixed_current": 8.0,
            "dlb_grid_power_sensor": None,
        }
    )

    assert result["pv_control_strategy"] == "disabled"


def test_missing_pv_strategy_defaults_to_disabled_for_validation():
    result = _validate_pv_options(
        {
            "pv_input_model": "surplus_sensor",
            "pv_surplus_sensor": None,
            "pv_start_threshold": 1800.0,
            "pv_stop_threshold": 1200.0,
            "pv_min_current": 6.0,
            "fixed_current": 8.0,
            "dlb_grid_power_sensor": None,
        }
    )

    assert result["pv_input_model"] == "surplus_sensor"


def test_pv_options_reject_invalid_phase_switch_session_limit():
    try:
        _validate_pv_options(
            {
                "pv_input_model": "surplus_sensor",
                "pv_control_strategy": "disabled",
                "pv_surplus_sensor": None,
                "pv_start_threshold": 1800.0,
                "pv_stop_threshold": 1200.0,
                "pv_min_current": 6.0,
                "fixed_current": 8.0,
                "dlb_grid_power_sensor": None,
                "pv_phase_switching_max_per_session": 0,
            }
        )
    except Exception as err:  # noqa: BLE001
        assert "pv_phase_switching_max_per_session" in str(err)
    else:
        raise AssertionError("Expected phase switch session limit validation to fail")


def test_fixed_current_must_stay_within_amp_range():
    try:
        _validate_pv_options(
            {
                "pv_input_model": "surplus_sensor",
                "pv_control_strategy": "surplus",
                "pv_surplus_sensor": None,
                "pv_start_threshold": 1800.0,
                "pv_stop_threshold": 1200.0,
                "pv_min_current": 6.0,
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
        await coordinator._enqueue_decision(decision)

        assert await coordinator.write_queue.size() == 0

    asyncio.run(_run())


def test_managed_control_mode_allows_static_sync():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)

    assert coordinator._allows_static_sync() is True
    assert coordinator._allows_control_writes() is True
    assert coordinator._allows_keepalive() is True


def test_keepalive_only_mode_disables_control_writes_and_static_sync():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.control_config = ControlConfig(control_mode=ControlMode.KEEPALIVE_ONLY)

    assert coordinator._allows_static_sync() is False
    assert coordinator._allows_control_writes() is False
    assert coordinator._allows_keepalive() is True


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
    coordinator._mode = ChargeMode.PV
    coordinator._charging_paused = False
    coordinator._pv_until_unplug_active = False
    coordinator._fixed_current_until_unplug_active = False

    coordinator.pause_charging()
    assert coordinator.mode == ChargeMode.PV
    assert coordinator.effective_mode == ChargeMode.OFF

    coordinator.resume_charging()
    assert coordinator.mode == ChargeMode.PV
    assert coordinator.effective_mode == ChargeMode.PV


def test_set_mode_updates_resume_mode_only_for_active_modes():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator._mode = ChargeMode.NORMAL
    coordinator._charging_paused = False
    coordinator._pv_until_unplug_active = False
    coordinator._fixed_current_until_unplug_active = False
    coordinator.controller = make_controller()
    coordinator.controller.pv_state.active = True

    coordinator.set_mode(ChargeMode.PV)
    assert coordinator.mode == ChargeMode.PV

    coordinator.set_mode(ChargeMode.OFF)
    assert coordinator.mode == ChargeMode.OFF
    assert coordinator.controller.pv_state.active is False


def test_set_mode_clears_temporary_until_unplug_overrides():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator._mode = ChargeMode.NORMAL
    coordinator._charging_paused = False
    coordinator._pv_until_unplug_active = True
    coordinator._fixed_current_until_unplug_active = False
    coordinator.controller = make_controller()
    coordinator.controller.pv_state.active = True

    coordinator.set_mode(ChargeMode.FIXED_CURRENT)

    assert coordinator.mode == ChargeMode.FIXED_CURRENT
    assert coordinator.pv_until_unplug_active is False
    assert coordinator.fixed_current_until_unplug_active is False
    assert coordinator.effective_mode == ChargeMode.FIXED_CURRENT
    assert coordinator.controller.pv_state.active is False


def test_set_fixed_current_updates_runtime_config():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.control_config = ControlConfig(fixed_current_a=6.0)

    coordinator.set_fixed_current(8.0)

    assert coordinator.control_config.fixed_current_a == 8.0


def test_effective_mode_uses_temporary_pv_override_above_normal():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator._mode = ChargeMode.NORMAL
    coordinator._charging_paused = False
    coordinator._pv_until_unplug_active = True
    coordinator._fixed_current_until_unplug_active = False

    assert coordinator.effective_mode == ChargeMode.PV


def test_effective_mode_uses_temporary_fixed_current_override_above_pv_override():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator._mode = ChargeMode.NORMAL
    coordinator._charging_paused = False
    coordinator._pv_until_unplug_active = True
    coordinator._fixed_current_until_unplug_active = True

    assert coordinator.effective_mode == ChargeMode.FIXED_CURRENT


def test_effective_mode_keeps_off_as_highest_priority():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator._mode = ChargeMode.OFF
    coordinator._charging_paused = False
    coordinator._pv_until_unplug_active = True
    coordinator._fixed_current_until_unplug_active = True

    assert coordinator.effective_mode == ChargeMode.OFF


def test_pause_and_resume_do_not_clear_temporary_pv_override():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator._mode = ChargeMode.NORMAL
    coordinator._charging_paused = False
    coordinator._pv_until_unplug_active = True
    coordinator._fixed_current_until_unplug_active = False
    coordinator.controller = make_controller()
    coordinator.controller.pv_state.active = True

    coordinator.pause_charging()
    assert coordinator.effective_mode == ChargeMode.OFF
    assert coordinator.controller.pv_state.active is False

    coordinator.resume_charging()
    assert coordinator.effective_mode == ChargeMode.PV


def test_pv_until_unplug_is_reset_after_vehicle_disconnect():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator._mode = ChargeMode.NORMAL
    coordinator._charging_paused = False
    coordinator._pv_until_unplug_active = True
    coordinator._fixed_current_until_unplug_active = True
    coordinator._last_vehicle_connected = True
    coordinator.controller = make_controller()
    coordinator.controller.pv_state.active = True

    wallbox = WallboxState(vehicle_connected=False)
    if (
        (coordinator._pv_until_unplug_active or coordinator._fixed_current_until_unplug_active)
        and coordinator._last_vehicle_connected
        and not wallbox.vehicle_connected
    ):
        coordinator._pv_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator.controller.reset_pv_state()
    coordinator._last_vehicle_connected = wallbox.vehicle_connected

    assert coordinator._pv_until_unplug_active is False
    assert coordinator._fixed_current_until_unplug_active is False
    assert coordinator.controller.pv_state.active is False


def test_set_pv_until_unplug_updates_override_state():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator._mode = ChargeMode.NORMAL
    coordinator._charging_paused = False
    coordinator._pv_until_unplug_active = False
    coordinator._fixed_current_until_unplug_active = False

    coordinator.set_pv_until_unplug(True)
    assert coordinator.pv_until_unplug_active is True
    assert coordinator.fixed_current_until_unplug_active is False

    coordinator.set_pv_until_unplug(False)
    assert coordinator.pv_until_unplug_active is False


def test_set_fixed_current_until_unplug_updates_override_state():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator._pv_until_unplug_active = True
    coordinator._fixed_current_until_unplug_active = False
    coordinator._mode = ChargeMode.NORMAL
    coordinator._charging_paused = False

    coordinator.set_fixed_current_until_unplug(True)
    assert coordinator.fixed_current_until_unplug_active is True
    assert coordinator.pv_until_unplug_active is False

    coordinator.set_fixed_current_until_unplug(False)
    assert coordinator.fixed_current_until_unplug_active is False


def test_mode_and_override_changes_do_not_reset_phase_recovery_state():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.control_config = ControlConfig()
    coordinator.controller = WallboxController(coordinator.control_config)
    coordinator._mode = ChargeMode.PV
    coordinator._pv_until_unplug_active = True
    coordinator._fixed_current_until_unplug_active = False
    coordinator._pending_phase_switch_target = 3
    coordinator._pending_phase_switch_force_write = True
    coordinator._pending_phase_switch_reason = "mismatch_retry"
    coordinator._phase_mismatch_retry_count = 1
    coordinator._phase_mismatch_target = 3
    coordinator._phase_mismatch_unverified = False

    coordinator.set_pv_until_unplug(False)
    coordinator.set_fixed_current_until_unplug(True)
    coordinator.set_mode(ChargeMode.NORMAL)

    assert coordinator._pending_phase_switch_target == 3
    assert coordinator._pending_phase_switch_force_write is True
    assert coordinator._pending_phase_switch_reason == "mismatch_retry"
    assert coordinator._phase_mismatch_retry_count == 1
    assert coordinator._phase_mismatch_target == 3
    assert coordinator._phase_mismatch_unverified is False


def test_leaving_pv_does_not_restore_configured_phases():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "port": 502, "unit_id": 255, "installed_phases": "3p"})
    coordinator.data = SimpleNamespace(wallbox=WallboxState(charging_active=True, phase_switch_mode_raw=0))
    coordinator._mode = ChargeMode.NORMAL
    coordinator._charging_paused = False
    coordinator._pv_until_unplug_active = True
    coordinator._fixed_current_until_unplug_active = False
    coordinator._pending_phase_switch_target = None
    coordinator._phase_switch_up_condition_since = monotonic()
    coordinator._phase_switch_decision = None

    coordinator.set_fixed_current_until_unplug(True)

    assert coordinator.fixed_current_until_unplug_active is True
    assert coordinator.pv_until_unplug_active is False
    assert coordinator._pending_phase_switch_target is None
    assert coordinator._phase_switch_decision is None


def test_phase_switch_queues_register_405_when_charging_inactive():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.control_config = ControlConfig()
    coordinator.write_queue = WriteQueueManager()
    coordinator.data = SimpleNamespace(wallbox=WallboxState(charging_active=False, phase_switch_mode_raw=1))
    coordinator._flush_write_queue = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()

    asyncio.run(coordinator.async_set_phase_switch_mode(3))

    item = asyncio.run(coordinator.write_queue.peek_next())
    assert item.key == "phase_switch_mode"
    assert item.register == PHASE_SWITCH_MODE
    assert item.value == 1
    coordinator._flush_write_queue.assert_awaited_once()
    coordinator.async_request_refresh.assert_awaited_once()


def test_phase_switch_is_blocked_while_charging_active():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.control_config = ControlConfig()
    coordinator.write_queue = WriteQueueManager()
    coordinator.data = SimpleNamespace(wallbox=WallboxState(charging_active=True, phase_switch_mode_raw=1))

    try:
        asyncio.run(coordinator.async_set_phase_switch_mode(1))
    except ValueError as err:
        assert "only allowed while charging is inactive" in str(err)
    else:
        raise AssertionError("Expected phase switching to be blocked while charging is active")


def test_manual_phase_switch_is_blocked_when_phase_switching_is_disabled():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.control_config = ControlConfig(pv_phase_switching_mode=PvPhaseSwitchingMode.DISABLED)
    coordinator.write_queue = WriteQueueManager()
    coordinator.data = SimpleNamespace(wallbox=WallboxState(charging_active=False, phase_switch_mode_raw=1))

    try:
        asyncio.run(coordinator.async_set_phase_switch_mode(1))
    except ValueError as err:
        assert "Phase switching is disabled" in str(err)
    else:
        raise AssertionError("Expected phase switching to be blocked when disabled")


def test_automatic_pv_phase_switch_pauses_before_writing_register_405():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.control_config = ControlConfig(
            control_mode=ControlMode.MANAGED_CONTROL,
            pv_phase_switching_mode=PvPhaseSwitchingMode.AUTOMATIC_1P3P,
        )
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.write_queue = WriteQueueManager()
        coordinator._pending_phase_switch_target = None
        coordinator._last_phase_switch_monotonic = 0.0
        coordinator._phase_switch_count_this_session = 0
        coordinator._phase_switch_decision = None
        coordinator._mode = ChargeMode.PV
        coordinator._charging_paused = False
        coordinator._pv_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._allows_control_writes = lambda: True
        coordinator._enqueue_keepalive_if_needed = AsyncMock()
        wallbox = WallboxState(charging_active=True, phase_switch_mode_raw=1)
        sensors = HaSensorSnapshot(surplus_power_w=3000.0, valid=True)

        handled = await coordinator._enqueue_pv_phase_switch_if_needed(wallbox, sensors)
        item = await coordinator.write_queue.peek_next()

        assert handled is True
        assert coordinator._pending_phase_switch_target == 1
        assert coordinator._phase_switch_decision == "pausing_before_phase_switch"
        assert item.key == "current_limit"
        assert item.value == 0

    asyncio.run(_run())


def test_automatic_pv_phase_switch_writes_register_405_after_charging_stops():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.control_config = ControlConfig(
            control_mode=ControlMode.MANAGED_CONTROL,
            pv_phase_switching_mode=PvPhaseSwitchingMode.AUTOMATIC_1P3P,
        )
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.write_queue = WriteQueueManager()
        coordinator._pending_phase_switch_target = 1
        coordinator._last_phase_switch_monotonic = 0.0
        coordinator._phase_switch_count_this_session = 0
        coordinator._phase_switch_decision = None
        coordinator._mode = ChargeMode.PV
        coordinator._charging_paused = False
        coordinator._pv_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._allows_control_writes = lambda: True
        coordinator._enqueue_keepalive_if_needed = AsyncMock()
        wallbox = WallboxState(charging_active=False, phase_switch_mode_raw=1)
        sensors = HaSensorSnapshot(surplus_power_w=3000.0, valid=True)

        handled = await coordinator._enqueue_pv_phase_switch_if_needed(wallbox, sensors)
        item = await coordinator.write_queue.peek_next()

        assert handled is True
        assert coordinator._phase_switch_decision == "writing_phase_switch_mode"
        assert item.key == "phase_switch_mode"
        assert item.register == PHASE_SWITCH_MODE
        assert item.value == 0

    asyncio.run(_run())


def test_automatic_pv_phase_switch_reasserts_when_register_and_measured_phases_mismatch():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.control_config = ControlConfig(
            control_mode=ControlMode.MANAGED_CONTROL,
            pv_phase_switching_mode=PvPhaseSwitchingMode.AUTOMATIC_1P3P,
            pv_phase_switching_min_interval_s=0.0,
        )
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.write_queue = WriteQueueManager()
        coordinator._pending_phase_switch_target = None
        coordinator._pending_phase_switch_force_write = False
        coordinator._last_phase_switch_monotonic = 0.0
        coordinator._phase_switch_up_condition_since = monotonic() - 1.0
        coordinator._phase_switch_count_this_session = 0
        coordinator._phase_switch_decision = None
        coordinator._mode = ChargeMode.PV
        coordinator._charging_paused = False
        coordinator._pv_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._allows_control_writes = lambda: True
        coordinator._enqueue_keepalive_if_needed = AsyncMock()
        wallbox = WallboxState(
            charging_active=True,
            phase_switch_mode_raw=1,
            phases_in_use=1,
        )
        sensors = HaSensorSnapshot(surplus_power_w=6000.0, valid=True)

        handled = await coordinator._enqueue_pv_phase_switch_if_needed(wallbox, sensors)
        item = await coordinator.write_queue.peek_next()

        assert handled is True
        assert coordinator._pending_phase_switch_target == 3
        assert coordinator._pending_phase_switch_force_write is True
        assert coordinator._phase_switch_decision == "phase_switch_mismatch_detected"
        assert item.key == "current_limit"
        assert item.value == 0

        coordinator.write_queue = WriteQueueManager()
        wallbox = WallboxState(charging_active=False, phase_switch_mode_raw=1)
        handled = await coordinator._enqueue_pv_phase_switch_if_needed(wallbox, sensors)
        item = await coordinator.write_queue.peek_next()

        assert handled is True
        assert coordinator._phase_switch_decision == "phase_switch_retry"
        assert coordinator._phase_mismatch_retry_count == 1
        assert item.key == "phase_switch_mode"
        assert item.value == 1

    asyncio.run(_run())


def test_automatic_pv_phase_switch_mismatch_recovery_respects_cooldown():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.control_config = ControlConfig(
            control_mode=ControlMode.MANAGED_CONTROL,
            pv_phase_switching_mode=PvPhaseSwitchingMode.AUTOMATIC_1P3P,
            pv_phase_switching_min_interval_s=0.0,
        )
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.write_queue = WriteQueueManager()
        coordinator._pending_phase_switch_target = None
        coordinator._pending_phase_switch_force_write = False
        coordinator._phase_mismatch_retry_count = 1
        coordinator._last_phase_mismatch_retry_monotonic = monotonic()
        coordinator._phase_mismatch_target = 3
        coordinator._phase_mismatch_unverified = False
        coordinator._last_phase_switch_monotonic = 0.0
        coordinator._phase_switch_up_condition_since = monotonic() - 1.0
        coordinator._phase_switch_count_this_session = 0
        coordinator._phase_switch_decision = None
        coordinator._mode = ChargeMode.PV
        coordinator._charging_paused = False
        coordinator._pv_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._allows_control_writes = lambda: True
        wallbox = WallboxState(charging_active=True, phase_switch_mode_raw=1, phases_in_use=1)
        sensors = HaSensorSnapshot(surplus_power_w=6000.0, valid=True)

        handled = await coordinator._enqueue_pv_phase_switch_if_needed(wallbox, sensors)

        assert handled is False
        assert coordinator._phase_switch_decision == "phase_switch_mismatch_detected"
        assert await coordinator.write_queue.size() == 0

    asyncio.run(_run())


def test_automatic_pv_phase_switch_mismatch_recovery_stops_after_retry_limit():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.control_config = ControlConfig(
            control_mode=ControlMode.MANAGED_CONTROL,
            pv_phase_switching_mode=PvPhaseSwitchingMode.AUTOMATIC_1P3P,
            pv_phase_switching_min_interval_s=0.0,
        )
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.write_queue = WriteQueueManager()
        coordinator._pending_phase_switch_target = None
        coordinator._pending_phase_switch_force_write = False
        coordinator._phase_mismatch_retry_count = 2
        coordinator._last_phase_mismatch_retry_monotonic = monotonic() - 121.0
        coordinator._phase_mismatch_target = 3
        coordinator._phase_mismatch_unverified = False
        coordinator._last_phase_switch_monotonic = 0.0
        coordinator._phase_switch_up_condition_since = monotonic() - 1.0
        coordinator._phase_switch_count_this_session = 0
        coordinator._phase_switch_decision = None
        coordinator._mode = ChargeMode.PV
        coordinator._charging_paused = False
        coordinator._pv_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._allows_control_writes = lambda: True
        wallbox = WallboxState(charging_active=True, phase_switch_mode_raw=1, phases_in_use=1)
        sensors = HaSensorSnapshot(surplus_power_w=6000.0, valid=True)

        handled = await coordinator._enqueue_pv_phase_switch_if_needed(wallbox, sensors)

        assert handled is False
        assert coordinator._phase_mismatch_unverified is True
        assert coordinator._phase_switch_decision == "phase_switch_unverified"
        assert await coordinator.write_queue.size() == 0

    asyncio.run(_run())


def test_automatic_pv_phase_switch_mismatch_success_resets_state():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.control_config = ControlConfig(
            control_mode=ControlMode.MANAGED_CONTROL,
            pv_phase_switching_mode=PvPhaseSwitchingMode.AUTOMATIC_1P3P,
        )
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.write_queue = WriteQueueManager()
        coordinator._pending_phase_switch_target = None
        coordinator._pending_phase_switch_force_write = False
        coordinator._phase_mismatch_retry_count = 1
        coordinator._last_phase_mismatch_retry_monotonic = monotonic()
        coordinator._phase_mismatch_target = 3
        coordinator._phase_mismatch_unverified = False
        coordinator._last_phase_switch_monotonic = 0.0
        coordinator._phase_switch_up_condition_since = None
        coordinator._phase_switch_count_this_session = 0
        coordinator._phase_switch_decision = None
        coordinator._mode = ChargeMode.PV
        coordinator._charging_paused = False
        coordinator._pv_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._allows_control_writes = lambda: True
        wallbox = WallboxState(charging_active=True, phase_switch_mode_raw=1, phases_in_use=3)
        sensors = HaSensorSnapshot(surplus_power_w=6000.0, valid=True)

        handled = await coordinator._enqueue_pv_phase_switch_if_needed(wallbox, sensors)

        assert handled is False
        assert coordinator._phase_switch_decision == "phase_switch_success"
        assert coordinator._phase_mismatch_retry_count == 0
        assert coordinator._phase_mismatch_target is None
        assert coordinator._phase_mismatch_unverified is False

    asyncio.run(_run())


def test_startup_consistency_waits_during_stabilization_window():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "port": 502, "unit_id": 255, "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.write_queue = WriteQueueManager()
        coordinator._startup_started_monotonic = monotonic()
        coordinator._startup_refresh_count = 0
        coordinator._startup_consistency_checked = False
        coordinator._startup_mismatch_observations = []
        coordinator._phase_switch_decision = None
        coordinator._mode = ChargeMode.NORMAL
        coordinator._charging_paused = False
        coordinator._pv_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._allows_control_writes = lambda: True

        wallbox = WallboxState(charging_active=True, phase_switch_mode_raw=1, phases_in_use=1)
        sensors = HaSensorSnapshot(valid=True)

        handled = await coordinator._enqueue_startup_consistency_recovery_if_needed(wallbox, sensors)

        assert handled is False
        assert coordinator._startup_consistency_checked is False
        assert coordinator._phase_switch_decision == "startup_stabilizing"
        assert await coordinator.write_queue.size() == 0

    asyncio.run(_run())


def test_startup_consistency_requires_stable_mismatch_before_recovery():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "port": 502, "unit_id": 255, "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.write_queue = WriteQueueManager()
        coordinator._startup_started_monotonic = monotonic() - 30.0
        coordinator._startup_refresh_count = 3
        coordinator._startup_consistency_checked = False
        coordinator._startup_mismatch_observations = []
        coordinator._pending_phase_switch_target = None
        coordinator._pending_phase_switch_force_write = False
        coordinator._phase_mismatch_retry_count = 0
        coordinator._last_phase_mismatch_retry_monotonic = 0.0
        coordinator._phase_mismatch_target = None
        coordinator._phase_mismatch_unverified = False
        coordinator._phase_switch_decision = None
        coordinator._mode = ChargeMode.NORMAL
        coordinator._charging_paused = False
        coordinator._pv_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._allows_control_writes = lambda: True
        coordinator._enqueue_keepalive_if_needed = AsyncMock()

        wallbox = WallboxState(charging_active=True, phase_switch_mode_raw=1, phases_in_use=1)
        sensors = HaSensorSnapshot(valid=True)

        first = await coordinator._enqueue_startup_consistency_recovery_if_needed(wallbox, sensors)

        assert first is False
        assert coordinator._startup_consistency_checked is False
        assert coordinator._phase_switch_decision == "startup_consistency_observing"
        assert await coordinator.write_queue.size() == 0

        second = await coordinator._enqueue_startup_consistency_recovery_if_needed(wallbox, sensors)
        item = await coordinator.write_queue.peek_next()

        assert second is True
        assert coordinator._startup_consistency_checked is True
        assert coordinator._pending_phase_switch_target == 3
        assert coordinator._pending_phase_switch_force_write is True
        assert coordinator._phase_switch_decision == "phase_switch_mismatch_detected"
        assert item.key == "current_limit"
        assert item.value == 0

        wallbox = WallboxState(charging_active=False, phase_switch_mode_raw=0, phases_in_use=0)
        handled = await coordinator._enqueue_non_pv_phase_reconcile_if_needed(wallbox)
        item = await coordinator.write_queue.peek_next()

        assert handled is True
        assert coordinator._pending_phase_switch_target == 3
        assert coordinator._phase_switch_decision == "phase_switch_retry"
        assert item.key == "phase_switch_mode"
        assert item.value == 1

        wallbox = WallboxState(charging_active=True, phase_switch_mode_raw=1, phases_in_use=3)
        handled = await coordinator._enqueue_non_pv_phase_reconcile_if_needed(wallbox)

        assert handled is False
        assert coordinator._pending_phase_switch_target is None
        assert coordinator._phase_mismatch_target is None
        assert coordinator._phase_switch_decision == "phase_switch_success"

    asyncio.run(_run())


def test_startup_consistency_in_normal_mode_ignores_pv_phase_switch_disabled():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "port": 502, "unit_id": 255, "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(
            control_mode=ControlMode.MANAGED_CONTROL,
            pv_phase_switching_mode=PvPhaseSwitchingMode.DISABLED,
        )
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.write_queue = WriteQueueManager()
        coordinator._startup_started_monotonic = monotonic() - 30.0
        coordinator._startup_refresh_count = 3
        coordinator._startup_consistency_checked = False
        coordinator._startup_mismatch_observations = []
        coordinator._pending_phase_switch_target = None
        coordinator._pending_phase_switch_force_write = False
        coordinator._phase_mismatch_retry_count = 0
        coordinator._last_phase_mismatch_retry_monotonic = 0.0
        coordinator._phase_mismatch_target = None
        coordinator._phase_mismatch_unverified = False
        coordinator._phase_switch_decision = None
        coordinator._mode = ChargeMode.NORMAL
        coordinator._charging_paused = False
        coordinator._pv_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._allows_control_writes = lambda: True
        coordinator._enqueue_keepalive_if_needed = AsyncMock()

        wallbox = WallboxState(charging_active=True, phase_switch_mode_raw=1, phases_in_use=1)
        sensors = HaSensorSnapshot(valid=True)

        first = await coordinator._enqueue_startup_consistency_recovery_if_needed(wallbox, sensors)
        second = await coordinator._enqueue_startup_consistency_recovery_if_needed(wallbox, sensors)
        item = await coordinator.write_queue.peek_next()

        assert first is False
        assert second is True
        assert coordinator._pending_phase_switch_target == 3
        assert coordinator._phase_switch_decision == "phase_switch_mismatch_detected"
        assert item.key == "current_limit"
        assert item.value == 0

    asyncio.run(_run())


def test_startup_consistency_in_pv_uses_normal_pv_phase_target_only():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "port": 502, "unit_id": 255, "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(
            control_mode=ControlMode.MANAGED_CONTROL,
            pv_phase_switching_mode=PvPhaseSwitchingMode.AUTOMATIC_1P3P,
        )
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.write_queue = WriteQueueManager()
        coordinator._startup_started_monotonic = monotonic() - 30.0
        coordinator._startup_refresh_count = 3
        coordinator._startup_consistency_checked = False
        coordinator._startup_mismatch_observations = []
        coordinator._phase_switch_decision = None
        coordinator._mode = ChargeMode.PV
        coordinator._charging_paused = False
        coordinator._pv_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._allows_control_writes = lambda: True

        wallbox = WallboxState(charging_active=True, phase_switch_mode_raw=1, phases_in_use=1)
        sensors = HaSensorSnapshot(surplus_power_w=3000.0, valid=True)

        handled = await coordinator._enqueue_startup_consistency_recovery_if_needed(wallbox, sensors)

        assert handled is False
        assert coordinator._startup_consistency_checked is True
        assert await coordinator.write_queue.size() == 0

    asyncio.run(_run())


def test_pending_phase_switch_is_cancelled_outside_pv_mode():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "port": 502, "unit_id": 255, "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(
            control_mode=ControlMode.MANAGED_CONTROL,
            pv_phase_switching_mode=PvPhaseSwitchingMode.AUTOMATIC_1P3P,
        )
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.write_queue = WriteQueueManager()
        coordinator._pending_phase_switch_target = 3
        coordinator._last_phase_switch_monotonic = 0.0
        coordinator._phase_switch_up_condition_since = None
        coordinator._phase_switch_count_this_session = 1
        coordinator._phase_switch_decision = None
        coordinator._mode = ChargeMode.NORMAL
        coordinator._charging_paused = False
        coordinator._pv_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = True
        coordinator._allows_control_writes = lambda: True
        coordinator._enqueue_keepalive_if_needed = AsyncMock()
        wallbox = WallboxState(charging_active=False, phase_switch_mode_raw=0)
        sensors = HaSensorSnapshot(surplus_power_w=0.0, valid=True)

        handled = await coordinator._enqueue_pv_phase_switch_if_needed(wallbox, sensors)

        assert handled is False
        assert coordinator._pending_phase_switch_target is None
        assert coordinator._phase_switch_decision == "outside_pv_mode"
        assert await coordinator.write_queue.size() == 0

    asyncio.run(_run())


def test_disabling_pv_until_unplug_reconciles_non_pv_phases_during_active_session():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "port": 502, "unit_id": 255, "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.write_queue = WriteQueueManager()
        coordinator._mode = ChargeMode.NORMAL
        coordinator._charging_paused = False
        coordinator._pv_until_unplug_active = True
        coordinator._fixed_current_until_unplug_active = False
        coordinator._pending_phase_switch_target = None
        coordinator._pending_phase_switch_force_write = False
        coordinator._phase_mismatch_retry_count = 0
        coordinator._last_phase_mismatch_retry_monotonic = 0.0
        coordinator._phase_mismatch_target = None
        coordinator._phase_mismatch_unverified = False
        coordinator._phase_switch_decision = None
        coordinator._allows_control_writes = lambda: True
        coordinator._enqueue_keepalive_if_needed = AsyncMock()

        coordinator.set_pv_until_unplug(False)
        wallbox = WallboxState(charging_active=True, phase_switch_mode_raw=0, phases_in_use=1)

        handled = await coordinator._enqueue_non_pv_phase_reconcile_if_needed(wallbox)
        item = await coordinator.write_queue.peek_next()

        assert coordinator.effective_mode == ChargeMode.NORMAL
        assert handled is True
        assert coordinator._pending_phase_switch_target == 3
        assert coordinator._pending_phase_switch_force_write is True
        assert coordinator._phase_switch_decision == "phase_switch_mismatch_detected"
        assert item.key == "current_limit"
        assert item.value == 0

    asyncio.run(_run())


def test_leaving_pv_writes_configured_phase_when_inactive_and_still_requested_1p():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "port": 502, "unit_id": 255, "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.write_queue = WriteQueueManager()
        coordinator._mode = ChargeMode.NORMAL
        coordinator._charging_paused = False
        coordinator._pv_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._pending_phase_switch_target = None
        coordinator._pending_phase_switch_force_write = False
        coordinator._phase_mismatch_retry_count = 0
        coordinator._last_phase_mismatch_retry_monotonic = 0.0
        coordinator._phase_mismatch_target = None
        coordinator._phase_mismatch_unverified = False
        coordinator._phase_switch_decision = None
        coordinator._allows_control_writes = lambda: True
        coordinator._enqueue_keepalive_if_needed = AsyncMock()

        wallbox = WallboxState(charging_active=False, phase_switch_mode_raw=0, phases_in_use=0)

        handled = await coordinator._enqueue_non_pv_phase_reconcile_if_needed(wallbox)
        item = await coordinator.write_queue.peek_next()

        assert handled is True
        assert coordinator._pending_phase_switch_target == 3
        assert coordinator._phase_switch_decision == "writing_phase_switch_mode"
        assert item.key == "phase_switch_mode"
        assert item.value == 1

    asyncio.run(_run())


def test_non_pv_reconcile_starts_when_normal_mode_observes_1p_but_target_is_3p():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "port": 502, "unit_id": 255, "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.write_queue = WriteQueueManager()
        coordinator._mode = ChargeMode.NORMAL
        coordinator._charging_paused = False
        coordinator._pv_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._pending_phase_switch_target = None
        coordinator._pending_phase_switch_force_write = False
        coordinator._phase_mismatch_retry_count = 0
        coordinator._last_phase_mismatch_retry_monotonic = 0.0
        coordinator._phase_mismatch_target = None
        coordinator._phase_mismatch_unverified = False
        coordinator._phase_switch_decision = None
        coordinator._allows_control_writes = lambda: True
        coordinator._enqueue_keepalive_if_needed = AsyncMock()

        wallbox = WallboxState(charging_active=True, phase_switch_mode_raw=1, phases_in_use=1)

        handled = await coordinator._enqueue_non_pv_phase_reconcile_if_needed(wallbox)
        item = await coordinator.write_queue.peek_next()

        assert handled is True
        assert coordinator._pending_phase_switch_target == 3
        assert coordinator._pending_phase_switch_force_write is True
        assert coordinator._phase_switch_decision == "phase_switch_mismatch_detected"
        assert item.key == "current_limit"
        assert item.value == 0

    asyncio.run(_run())


def test_pending_non_pv_restore_is_not_cleared_outside_pv_mode():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "port": 502, "unit_id": 255, "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.write_queue = WriteQueueManager()
        coordinator._mode = ChargeMode.NORMAL
        coordinator._charging_paused = False
        coordinator._pv_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._pending_phase_switch_target = 3
        coordinator._pending_phase_switch_force_write = True
        coordinator._phase_mismatch_retry_count = 0
        coordinator._last_phase_mismatch_retry_monotonic = 0.0
        coordinator._phase_mismatch_target = 3
        coordinator._phase_mismatch_unverified = False
        coordinator._phase_switch_decision = None
        coordinator._allows_control_writes = lambda: True
        coordinator._enqueue_keepalive_if_needed = AsyncMock()

        wallbox = WallboxState(charging_active=False, phase_switch_mode_raw=1)

        handled = await coordinator._enqueue_non_pv_phase_reconcile_if_needed(wallbox)
        item = await coordinator.write_queue.peek_next()

        assert handled is True
        assert coordinator._pending_phase_switch_target == 3
        assert coordinator._phase_switch_decision == "phase_switch_retry"
        assert coordinator._phase_mismatch_retry_count == 1
        assert item.key == "phase_switch_mode"
        assert item.value == 1

    asyncio.run(_run())


def test_recent_mismatch_continues_recovery_when_observed_phases_are_temporarily_unknown():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "port": 502, "unit_id": 255, "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.write_queue = WriteQueueManager()
        coordinator._mode = ChargeMode.NORMAL
        coordinator._charging_paused = False
        coordinator._pv_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._pending_phase_switch_target = None
        coordinator._pending_phase_switch_force_write = False
        coordinator._phase_mismatch_retry_count = 0
        coordinator._last_phase_mismatch_retry_monotonic = 0.0
        coordinator._phase_mismatch_target = 3
        coordinator._phase_mismatch_unverified = False
        coordinator._phase_mismatch_observation_key = (3, 3, 1)
        coordinator._phase_mismatch_observation_count = 1
        coordinator._phase_switch_decision = None
        coordinator._allows_control_writes = lambda: True
        coordinator._enqueue_keepalive_if_needed = AsyncMock()

        wallbox = WallboxState(charging_active=False, phase_switch_mode_raw=1, phases_in_use=0)

        handled = await coordinator._enqueue_non_pv_phase_reconcile_if_needed(wallbox)
        item = await coordinator.write_queue.peek_next()

        assert handled is True
        assert coordinator._pending_phase_switch_target is None
        assert coordinator._phase_switch_decision == "waiting_for_observed_phases"
        assert coordinator._phase_mismatch_retry_count == 0
        assert item is None

    asyncio.run(_run())


def test_pending_phase_write_waits_for_register_feedback_instead_of_rewriting():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "port": 502, "unit_id": 255, "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.write_queue = WriteQueueManager()
        coordinator._mode = ChargeMode.NORMAL
        coordinator._charging_paused = False
        coordinator._pv_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._pending_phase_switch_target = 3
        coordinator._pending_phase_switch_force_write = False
        coordinator._pending_phase_switch_reason = "non_pv_reconcile"
        coordinator._phase_mismatch_target = None
        coordinator._phase_mismatch_unverified = False
        coordinator._phase_switch_decision = None
        coordinator._last_phase_switch_monotonic = monotonic()
        coordinator._allows_control_writes = lambda: True
        coordinator._enqueue_keepalive_if_needed = AsyncMock()

        wallbox = WallboxState(charging_active=False, phase_switch_mode_raw=0, phases_in_use=0)

        handled = await coordinator._enqueue_non_pv_phase_reconcile_if_needed(wallbox)

        assert handled is True
        assert coordinator._pending_phase_switch_target == 3
        assert coordinator._phase_switch_decision == "waiting_for_phase_switch_feedback"
        assert await coordinator.write_queue.size() == 0

    asyncio.run(_run())


def test_active_recovery_with_unknown_observed_phases_waits_instead_of_rewriting_target():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "port": 502, "unit_id": 255, "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.write_queue = WriteQueueManager()
        coordinator._mode = ChargeMode.NORMAL
        coordinator._charging_paused = False
        coordinator._pv_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._pending_phase_switch_target = None
        coordinator._pending_phase_switch_force_write = False
        coordinator._pending_phase_switch_reason = "mismatch_retry"
        coordinator._phase_mismatch_retry_count = 1
        coordinator._last_phase_mismatch_retry_monotonic = monotonic()
        coordinator._phase_mismatch_target = 3
        coordinator._phase_mismatch_unverified = False
        coordinator._phase_switch_decision = None
        coordinator._allows_control_writes = lambda: True
        coordinator._enqueue_keepalive_if_needed = AsyncMock()

        wallbox = WallboxState(charging_active=False, phase_switch_mode_raw=1, phases_in_use=0)

        handled = await coordinator._enqueue_non_pv_phase_reconcile_if_needed(wallbox)

        assert handled is True
        assert coordinator._pending_phase_switch_target is None
        assert coordinator._phase_switch_decision == "waiting_for_observed_phases"
        assert await coordinator.write_queue.size() == 0

    asyncio.run(_run())


def test_non_pv_reconcile_runs_before_startup_consistency_is_checked():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = SimpleNamespace(
            data={"host": "192.168.1.10", "port": 502, "unit_id": 255, "installed_phases": "3p"},
            options={},
            title="Webasto Unite",
        )
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.write_queue = WriteQueueManager()
        coordinator.wallbox_reader = SimpleNamespace(
            read_wallbox_state=AsyncMock(
                return_value=WallboxState(
                    charging_active=True,
                    vehicle_connected=True,
                    phase_switch_mode_raw=1,
                    phases_in_use=1,
                )
            )
        )
        coordinator.client = SimpleNamespace(stats=SimpleNamespace(last_error=None))
        coordinator._mode = ChargeMode.NORMAL
        coordinator._charging_paused = False
        coordinator._pv_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._last_vehicle_connected = True
        coordinator._startup_consistency_checked = False
        coordinator._phase_switch_count_this_session = 0
        coordinator._phase_switch_decision = None
        coordinator._keepalive_sent_count = 0
        coordinator._keepalive_write_failures = 0
        coordinator._read_sensor_snapshot = lambda: HaSensorSnapshot(valid=True)
        coordinator._keepalive_age_seconds = lambda: 0.0
        coordinator._allows_keepalive = lambda: False
        coordinator._is_keepalive_overdue = lambda age: False
        coordinator._enqueue_startup_consistency_recovery_if_needed = AsyncMock(return_value=False)
        coordinator._enqueue_non_pv_phase_reconcile_if_needed = AsyncMock(return_value=True)
        coordinator._enqueue_pv_phase_switch_if_needed = AsyncMock(return_value=False)
        coordinator._enqueue_decision = AsyncMock()
        coordinator._flush_write_queue = AsyncMock()

        await coordinator._async_update_data()

        coordinator._enqueue_non_pv_phase_reconcile_if_needed.assert_awaited_once()
        coordinator._enqueue_pv_phase_switch_if_needed.assert_not_called()
        coordinator._enqueue_decision.assert_not_called()

    asyncio.run(_run())


def test_vehicle_reconnect_rewrites_normal_target_when_charger_starts_at_safe_current():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = SimpleNamespace(
            data={"host": "192.168.1.10", "port": 502, "unit_id": 255, "installed_phases": "3p"},
            options={},
            title="Webasto Unite",
        )
        coordinator.control_config = ControlConfig(
            control_mode=ControlMode.MANAGED_CONTROL,
            stable_cycles_before_write=1,
            min_seconds_between_writes=0.0,
        )
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.controller.mark_current_written(16.0)
        coordinator.write_queue = WriteQueueManager()
        coordinator.wallbox_reader = SimpleNamespace(
            read_wallbox_state=AsyncMock(
                return_value=WallboxState(
                    charging_active=True,
                    vehicle_connected=True,
                    current_limit_a=6.0,
                    phase_switch_mode_raw=1,
                    phases_in_use=3,
                )
            )
        )
        coordinator.client = SimpleNamespace(stats=SimpleNamespace(last_error=None))
        coordinator._mode = ChargeMode.NORMAL
        coordinator._charging_paused = False
        coordinator._pv_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._last_vehicle_connected = False
        coordinator._startup_consistency_checked = True
        coordinator._phase_switch_count_this_session = 0
        coordinator._phase_switch_decision = None
        coordinator._keepalive_sent_count = 0
        coordinator._keepalive_write_failures = 0
        coordinator._read_sensor_snapshot = lambda: HaSensorSnapshot(valid=True)
        coordinator._keepalive_age_seconds = lambda: 0.0
        coordinator._allows_keepalive = lambda: False
        coordinator._is_keepalive_overdue = lambda age: False
        coordinator._enqueue_startup_consistency_recovery_if_needed = AsyncMock(return_value=False)
        coordinator._enqueue_pv_phase_switch_if_needed = AsyncMock(return_value=False)
        coordinator._flush_write_queue = AsyncMock()

        await coordinator._async_update_data()
        item = await coordinator.write_queue.peek_next()

        assert item.key == "current_limit"
        assert item.value == 16

    asyncio.run(_run())


def test_running_integration_rewrites_normal_target_when_wallbox_reports_safe_current():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = SimpleNamespace(
            data={"host": "192.168.1.10", "port": 502, "unit_id": 255, "installed_phases": "3p"},
            options={},
            title="Webasto Unite",
        )
        coordinator.control_config = ControlConfig(
            control_mode=ControlMode.MANAGED_CONTROL,
            stable_cycles_before_write=1,
            min_seconds_between_writes=0.0,
        )
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.controller.mark_current_written(16.0)
        coordinator.write_queue = WriteQueueManager()
        coordinator.wallbox_reader = SimpleNamespace(
            read_wallbox_state=AsyncMock(
                return_value=WallboxState(
                    charging_active=True,
                    vehicle_connected=True,
                    current_limit_a=6.0,
                    phase_switch_mode_raw=1,
                    phases_in_use=3,
                )
            )
        )
        coordinator.client = SimpleNamespace(stats=SimpleNamespace(last_error=None))
        coordinator._mode = ChargeMode.NORMAL
        coordinator._charging_paused = False
        coordinator._pv_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._last_vehicle_connected = True
        coordinator._startup_consistency_checked = True
        coordinator._phase_switch_count_this_session = 0
        coordinator._phase_switch_decision = None
        coordinator._keepalive_sent_count = 0
        coordinator._keepalive_write_failures = 0
        coordinator._read_sensor_snapshot = lambda: HaSensorSnapshot(valid=True)
        coordinator._keepalive_age_seconds = lambda: 0.0
        coordinator._allows_keepalive = lambda: False
        coordinator._is_keepalive_overdue = lambda age: False
        coordinator._enqueue_startup_consistency_recovery_if_needed = AsyncMock(return_value=False)
        coordinator._enqueue_pv_phase_switch_if_needed = AsyncMock(return_value=False)
        coordinator._flush_write_queue = AsyncMock()

        await coordinator._async_update_data()
        item = await coordinator.write_queue.peek_next()

        assert item.key == "current_limit"
        assert item.value == 16

    asyncio.run(_run())


def test_non_pv_reconcile_suppresses_decision_when_reconcile_returns_false_but_mismatch_exists():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = SimpleNamespace(
            data={"host": "192.168.1.10", "port": 502, "unit_id": 255, "installed_phases": "3p"},
            options={},
            title="Webasto Unite",
        )
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.write_queue = WriteQueueManager()
        coordinator.wallbox_reader = SimpleNamespace(
            read_wallbox_state=AsyncMock(
                return_value=WallboxState(
                    charging_active=True,
                    vehicle_connected=True,
                    phase_switch_mode_raw=1,
                    phases_in_use=1,
                )
            )
        )
        coordinator.client = SimpleNamespace(stats=SimpleNamespace(last_error=None))
        coordinator._mode = ChargeMode.NORMAL
        coordinator._charging_paused = False
        coordinator._pv_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._last_vehicle_connected = True
        coordinator._startup_consistency_checked = True
        coordinator._phase_switch_count_this_session = 0
        coordinator._phase_switch_decision = None
        coordinator._keepalive_sent_count = 0
        coordinator._keepalive_write_failures = 0
        coordinator._read_sensor_snapshot = lambda: HaSensorSnapshot(valid=True)
        coordinator._keepalive_age_seconds = lambda: 0.0
        coordinator._allows_keepalive = lambda: False
        coordinator._is_keepalive_overdue = lambda age: False
        coordinator._enqueue_startup_consistency_recovery_if_needed = AsyncMock(return_value=False)
        coordinator._enqueue_non_pv_phase_reconcile_if_needed = AsyncMock(return_value=False)
        coordinator._enqueue_pv_phase_switch_if_needed = AsyncMock(return_value=False)
        coordinator._enqueue_decision = AsyncMock()
        coordinator._flush_write_queue = AsyncMock()

        await coordinator._async_update_data()

        coordinator._enqueue_non_pv_phase_reconcile_if_needed.assert_awaited_once()
        coordinator._enqueue_decision.assert_not_called()

    asyncio.run(_run())


def test_non_pv_reconcile_false_once_keeps_state_and_completes_later():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "port": 502, "unit_id": 255, "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.write_queue = WriteQueueManager()
        coordinator._mode = ChargeMode.NORMAL
        coordinator._charging_paused = False
        coordinator._pv_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._pending_phase_switch_target = None
        coordinator._pending_phase_switch_force_write = False
        coordinator._pending_phase_switch_reason = "mismatch_retry"
        coordinator._phase_mismatch_retry_count = 1
        coordinator._last_phase_mismatch_retry_monotonic = monotonic()
        coordinator._phase_mismatch_target = 3
        coordinator._phase_mismatch_unverified = False
        coordinator._phase_mismatch_observation_key = None
        coordinator._phase_mismatch_observation_count = 0
        coordinator._phase_mismatch_bounce_used = False
        coordinator._phase_mismatch_bounce_stage = None
        coordinator._phase_mismatch_bounce_target = None
        coordinator._phase_mismatch_bounce_verify_count = 0
        coordinator._last_phase_mismatch_bounce_monotonic = monotonic()
        coordinator._phase_switch_decision = None
        coordinator._allows_control_writes = lambda: True
        coordinator._enqueue_keepalive_if_needed = AsyncMock()

        wallbox = WallboxState(charging_active=True, phase_switch_mode_raw=1, phases_in_use=1)
        handled = await coordinator._enqueue_non_pv_phase_reconcile_if_needed(wallbox)

        assert handled is False
        assert coordinator._phase_mismatch_target == 3
        assert coordinator._phase_mismatch_unverified is False
        assert await coordinator.write_queue.size() == 0

        coordinator.write_queue = WriteQueueManager()
        wallbox = WallboxState(charging_active=False, phase_switch_mode_raw=1, phases_in_use=0)
        handled = await coordinator._enqueue_non_pv_phase_reconcile_if_needed(wallbox)
        item = await coordinator.write_queue.peek_next()

        assert handled is True
        assert coordinator._pending_phase_switch_target is None
        assert coordinator._phase_switch_decision == "waiting_for_observed_phases"
        assert item is None

        coordinator.write_queue = WriteQueueManager()
        wallbox = WallboxState(charging_active=True, phase_switch_mode_raw=1, phases_in_use=3)
        handled = await coordinator._enqueue_non_pv_phase_reconcile_if_needed(wallbox)

        assert handled is False
        assert coordinator._phase_mismatch_target is None
        assert coordinator._pending_phase_switch_target is None
        assert coordinator._phase_switch_decision == "phase_switch_success"
        assert await coordinator.write_queue.size() == 0

    asyncio.run(_run())


def test_stable_requested_target_observed_mismatch_uses_normal_recovery_first():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "port": 502, "unit_id": 255, "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.write_queue = WriteQueueManager()
        coordinator._mode = ChargeMode.NORMAL
        coordinator._charging_paused = False
        coordinator._pv_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._pending_phase_switch_target = None
        coordinator._pending_phase_switch_force_write = False
        coordinator._pending_phase_switch_reason = None
        coordinator._phase_mismatch_retry_count = 0
        coordinator._last_phase_mismatch_retry_monotonic = 0.0
        coordinator._phase_mismatch_target = None
        coordinator._phase_mismatch_unverified = False
        coordinator._phase_mismatch_observation_key = None
        coordinator._phase_mismatch_observation_count = 0
        coordinator._phase_mismatch_bounce_used = False
        coordinator._phase_mismatch_bounce_stage = None
        coordinator._phase_mismatch_bounce_target = None
        coordinator._phase_mismatch_bounce_verify_count = 0
        coordinator._last_phase_mismatch_bounce_monotonic = 0.0
        coordinator._phase_switch_decision = None
        coordinator._allows_control_writes = lambda: True
        coordinator._enqueue_keepalive_if_needed = AsyncMock()

        wallbox = WallboxState(charging_active=True, phase_switch_mode_raw=1, phases_in_use=1)

        handled = await coordinator._enqueue_non_pv_phase_reconcile_if_needed(wallbox)
        item = await coordinator.write_queue.peek_next()

        assert handled is True
        assert coordinator._phase_mismatch_bounce_used is False
        assert coordinator._phase_mismatch_bounce_stage is None
        assert coordinator._pending_phase_switch_target == 3
        assert coordinator._pending_phase_switch_force_write is True
        assert coordinator._phase_switch_decision == "phase_switch_mismatch_detected"
        assert item.key == "current_limit"
        assert item.value == 0

    asyncio.run(_run())


def test_persistent_3p_mismatch_can_trigger_single_bounce_recovery():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "port": 502, "unit_id": 255, "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.write_queue = WriteQueueManager()
        coordinator._mode = ChargeMode.NORMAL
        coordinator._charging_paused = False
        coordinator._pv_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._pending_phase_switch_target = 3
        coordinator._pending_phase_switch_force_write = False
        coordinator._pending_phase_switch_reason = "mismatch_retry"
        coordinator._phase_mismatch_retry_count = 1
        coordinator._last_phase_mismatch_retry_monotonic = 0.0
        coordinator._phase_mismatch_target = 3
        coordinator._phase_mismatch_unverified = False
        coordinator._phase_mismatch_observation_key = (3, 3, 1)
        coordinator._phase_mismatch_observation_count = 1
        coordinator._phase_mismatch_bounce_used = False
        coordinator._phase_mismatch_bounce_stage = None
        coordinator._phase_mismatch_bounce_target = None
        coordinator._phase_mismatch_bounce_verify_count = 0
        coordinator._last_phase_mismatch_bounce_monotonic = 0.0
        coordinator._phase_switch_decision = None
        coordinator._allows_control_writes = lambda: True
        coordinator._enqueue_keepalive_if_needed = AsyncMock()

        wallbox = WallboxState(charging_active=True, phase_switch_mode_raw=1, phases_in_use=1)
        handled = await coordinator._enqueue_non_pv_phase_reconcile_if_needed(wallbox)
        item = await coordinator.write_queue.peek_next()

        assert handled is True
        assert coordinator._phase_mismatch_bounce_used is True
        assert coordinator._phase_mismatch_bounce_stage == "write_1p"
        assert coordinator._pending_phase_switch_reason == "mismatch_bounce_recovery"
        assert coordinator._phase_switch_decision == "phase_switch_bounce_pausing"
        assert item.key == "current_limit"
        assert item.value == 0

        coordinator.write_queue = WriteQueueManager()
        wallbox = WallboxState(charging_active=False, phase_switch_mode_raw=1)
        handled = await coordinator._enqueue_non_pv_phase_reconcile_if_needed(wallbox)
        item = await coordinator.write_queue.peek_next()

        assert handled is True
        assert coordinator._phase_mismatch_bounce_stage == "wait_1p"
        assert coordinator._phase_switch_decision == "phase_switch_bounce_to_1p"
        assert item.key == "phase_switch_mode"
        assert item.value == 0

        coordinator.write_queue = WriteQueueManager()
        wallbox = WallboxState(charging_active=False, phase_switch_mode_raw=0)
        handled = await coordinator._enqueue_non_pv_phase_reconcile_if_needed(wallbox)

        assert handled is True
        assert coordinator._phase_mismatch_bounce_stage == "write_target"
        assert coordinator._phase_switch_decision == "phase_switch_bounce_settled_1p"
        assert await coordinator.write_queue.size() == 0

        wallbox = WallboxState(charging_active=False, phase_switch_mode_raw=0)
        handled = await coordinator._enqueue_non_pv_phase_reconcile_if_needed(wallbox)
        item = await coordinator.write_queue.peek_next()

        assert handled is True
        assert coordinator._phase_mismatch_bounce_stage == "verify"
        assert coordinator._phase_switch_decision == "phase_switch_bounce_to_target"
        assert item.key == "phase_switch_mode"
        assert item.value == 1

    asyncio.run(_run())


def test_bounce_recovery_is_not_used_on_1p_installations():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "port": 502, "unit_id": 255, "installed_phases": "1p"})
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.write_queue = WriteQueueManager()
        coordinator._phase_mismatch_retry_count = 1
        coordinator._last_phase_mismatch_retry_monotonic = 0.0
        coordinator._phase_mismatch_unverified = False
        coordinator._phase_mismatch_observation_key = (1, 1, 3)
        coordinator._phase_mismatch_observation_count = 1
        coordinator._phase_mismatch_bounce_used = False
        coordinator._phase_mismatch_bounce_stage = None
        coordinator._phase_mismatch_bounce_target = None
        coordinator._phase_mismatch_bounce_verify_count = 0
        coordinator._last_phase_mismatch_bounce_monotonic = 0.0
        coordinator._pending_phase_switch_target = None
        coordinator._pending_phase_switch_force_write = False
        coordinator._pending_phase_switch_reason = None
        coordinator._allows_control_writes = lambda: True
        coordinator._enqueue_keepalive_if_needed = AsyncMock()

        wallbox = WallboxState(charging_active=True, phase_switch_mode_raw=0, phases_in_use=3)

        handled = await coordinator._handle_phase_mismatch_recovery(
            wallbox=wallbox,
            requested_phases=1,
            observed_phases=3,
            target_phases=1,
        )

        assert handled is True
        assert coordinator._phase_mismatch_bounce_used is False
        assert coordinator._phase_mismatch_bounce_stage is None

    asyncio.run(_run())


def test_bounce_recovery_is_not_used_when_target_is_1p():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "port": 502, "unit_id": 255, "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.write_queue = WriteQueueManager()
        coordinator._phase_mismatch_retry_count = 1
        coordinator._last_phase_mismatch_retry_monotonic = 0.0
        coordinator._phase_mismatch_unverified = False
        coordinator._phase_mismatch_observation_key = (1, 1, 3)
        coordinator._phase_mismatch_observation_count = 1
        coordinator._phase_mismatch_bounce_used = False
        coordinator._phase_mismatch_bounce_stage = None
        coordinator._phase_mismatch_bounce_target = None
        coordinator._phase_mismatch_bounce_verify_count = 0
        coordinator._last_phase_mismatch_bounce_monotonic = 0.0
        coordinator._pending_phase_switch_target = None
        coordinator._pending_phase_switch_force_write = False
        coordinator._pending_phase_switch_reason = None
        coordinator._allows_control_writes = lambda: True
        coordinator._enqueue_keepalive_if_needed = AsyncMock()

        wallbox = WallboxState(charging_active=True, phase_switch_mode_raw=0, phases_in_use=3)

        handled = await coordinator._handle_phase_mismatch_recovery(
            wallbox=wallbox,
            requested_phases=1,
            observed_phases=3,
            target_phases=1,
        )

        assert handled is True
        assert coordinator._phase_mismatch_bounce_used is False
        assert coordinator._phase_mismatch_bounce_stage is None

    asyncio.run(_run())


def test_bounce_recovery_runs_at_most_once_for_failed_incident():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "port": 502, "unit_id": 255, "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.write_queue = WriteQueueManager()
        coordinator._phase_mismatch_retry_count = 2
        coordinator._last_phase_mismatch_retry_monotonic = 0.0
        coordinator._phase_mismatch_unverified = False
        coordinator._phase_mismatch_observation_key = (3, 3, 1)
        coordinator._phase_mismatch_observation_count = 2
        coordinator._phase_mismatch_bounce_used = True
        coordinator._phase_mismatch_bounce_stage = None
        coordinator._phase_mismatch_bounce_target = None
        coordinator._phase_mismatch_bounce_verify_count = 0
        coordinator._last_phase_mismatch_bounce_monotonic = 0.0
        coordinator._pending_phase_switch_target = None
        coordinator._pending_phase_switch_force_write = False
        coordinator._pending_phase_switch_reason = None
        coordinator._allows_control_writes = lambda: True

        wallbox = WallboxState(charging_active=True, phase_switch_mode_raw=1, phases_in_use=1)

        handled = await coordinator._handle_phase_mismatch_recovery(
            wallbox=wallbox,
            requested_phases=3,
            observed_phases=1,
            target_phases=3,
        )

        assert handled is False
        assert coordinator._phase_mismatch_unverified is True
        assert coordinator._phase_switch_decision == "phase_switch_unverified"
        assert await coordinator.write_queue.size() == 0

    asyncio.run(_run())


def test_failed_bounce_verification_stops_without_oscillation():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "port": 502, "unit_id": 255, "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.write_queue = WriteQueueManager()
        coordinator._phase_mismatch_retry_count = 1
        coordinator._last_phase_mismatch_retry_monotonic = 0.0
        coordinator._phase_mismatch_unverified = False
        coordinator._phase_mismatch_observation_key = (3, 3, 1)
        coordinator._phase_mismatch_observation_count = 2
        coordinator._phase_mismatch_bounce_used = True
        coordinator._phase_mismatch_bounce_stage = "verify"
        coordinator._phase_mismatch_bounce_target = 3
        coordinator._phase_mismatch_bounce_verify_count = 1
        coordinator._last_phase_mismatch_bounce_monotonic = 0.0
        coordinator._pending_phase_switch_target = None
        coordinator._pending_phase_switch_force_write = False
        coordinator._pending_phase_switch_reason = "mismatch_bounce_recovery"
        coordinator._allows_control_writes = lambda: True

        wallbox = WallboxState(charging_active=True, phase_switch_mode_raw=1, phases_in_use=1)

        handled = await coordinator._handle_phase_mismatch_recovery(
            wallbox=wallbox,
            requested_phases=3,
            observed_phases=1,
            target_phases=3,
        )

        assert handled is False
        assert coordinator._phase_mismatch_unverified is True
        assert coordinator._phase_mismatch_bounce_stage is None
        assert coordinator._phase_switch_decision == "phase_switch_unverified"
        assert await coordinator.write_queue.size() == 0

    asyncio.run(_run())


def test_restart_requested_3p_observed_1p_can_escalate_to_bounce_after_normal_retry():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.entry = make_config_entry(data={"host": "192.168.1.10", "port": 502, "unit_id": 255, "installed_phases": "3p"})
        coordinator.control_config = ControlConfig(control_mode=ControlMode.MANAGED_CONTROL)
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.write_queue = WriteQueueManager()
        coordinator._mode = ChargeMode.NORMAL
        coordinator._charging_paused = False
        coordinator._pv_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._pending_phase_switch_target = 3
        coordinator._pending_phase_switch_force_write = False
        coordinator._pending_phase_switch_reason = "startup_restore"
        coordinator._phase_mismatch_retry_count = 1
        coordinator._last_phase_mismatch_retry_monotonic = 0.0
        coordinator._phase_mismatch_target = 3
        coordinator._phase_mismatch_unverified = False
        coordinator._phase_mismatch_observation_key = (3, 3, 1)
        coordinator._phase_mismatch_observation_count = 1
        coordinator._phase_mismatch_bounce_used = False
        coordinator._phase_mismatch_bounce_stage = None
        coordinator._phase_mismatch_bounce_target = None
        coordinator._phase_mismatch_bounce_verify_count = 0
        coordinator._last_phase_mismatch_bounce_monotonic = 0.0
        coordinator._phase_switch_decision = None
        coordinator._allows_control_writes = lambda: True
        coordinator._enqueue_keepalive_if_needed = AsyncMock()

        wallbox = WallboxState(charging_active=True, phase_switch_mode_raw=1, phases_in_use=1)

        handled = await coordinator._enqueue_non_pv_phase_reconcile_if_needed(wallbox)

        assert handled is True
        assert coordinator._phase_mismatch_bounce_used is True
        assert coordinator._phase_mismatch_bounce_stage == "write_1p"
        assert coordinator._pending_phase_switch_reason == "mismatch_bounce_recovery"
        assert coordinator._phase_switch_decision == "phase_switch_bounce_pausing"

    asyncio.run(_run())


def test_automatic_pv_phase_switch_is_rate_limited_after_recent_switch():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.control_config = ControlConfig(
            control_mode=ControlMode.MANAGED_CONTROL,
            pv_phase_switching_mode=PvPhaseSwitchingMode.AUTOMATIC_1P3P,
            pv_phase_switching_min_interval_s=300.0,
        )
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.write_queue = WriteQueueManager()
        coordinator._pending_phase_switch_target = None
        coordinator._last_phase_switch_monotonic = monotonic()
        coordinator._phase_switch_up_condition_since = monotonic() - 301.0
        coordinator._phase_switch_count_this_session = 1
        coordinator._phase_switch_decision = None
        coordinator._mode = ChargeMode.PV
        coordinator._charging_paused = False
        coordinator._pv_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._allows_control_writes = lambda: True
        wallbox = WallboxState(charging_active=False, phase_switch_mode_raw=0)
        sensors = HaSensorSnapshot(surplus_power_w=6000.0, valid=True)

        handled = await coordinator._enqueue_pv_phase_switch_if_needed(wallbox, sensors)

        assert handled is False
        assert coordinator._phase_switch_decision == "phase_switch_rate_limited"
        assert await coordinator.write_queue.size() == 0

    asyncio.run(_run())


def test_automatic_pv_phase_switch_obeys_session_limit():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.control_config = ControlConfig(
            control_mode=ControlMode.MANAGED_CONTROL,
            pv_phase_switching_mode=PvPhaseSwitchingMode.AUTOMATIC_1P3P,
            pv_phase_switching_max_per_session=1,
        )
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.write_queue = WriteQueueManager()
        coordinator._pending_phase_switch_target = None
        coordinator._last_phase_switch_monotonic = 0.0
        coordinator._phase_switch_up_condition_since = monotonic() - 301.0
        coordinator._phase_switch_count_this_session = 1
        coordinator._phase_switch_decision = None
        coordinator._mode = ChargeMode.PV
        coordinator._charging_paused = False
        coordinator._pv_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._allows_control_writes = lambda: True
        wallbox = WallboxState(charging_active=False, phase_switch_mode_raw=0)
        sensors = HaSensorSnapshot(surplus_power_w=6000.0, valid=True)

        handled = await coordinator._enqueue_pv_phase_switch_if_needed(wallbox, sensors)

        assert handled is False
        assert coordinator._phase_switch_decision == "phase_switch_session_limit_reached"
        assert await coordinator.write_queue.size() == 0

    asyncio.run(_run())


def test_automatic_pv_phase_switch_allows_return_to_1p_after_session_limit():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.control_config = ControlConfig(
            control_mode=ControlMode.MANAGED_CONTROL,
            pv_phase_switching_mode=PvPhaseSwitchingMode.AUTOMATIC_1P3P,
            pv_phase_switching_max_per_session=1,
        )
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.write_queue = WriteQueueManager()
        coordinator._pending_phase_switch_target = None
        coordinator._last_phase_switch_monotonic = monotonic()
        coordinator._phase_switch_up_condition_since = None
        coordinator._phase_switch_count_this_session = 1
        coordinator._phase_switch_decision = None
        coordinator._mode = ChargeMode.PV
        coordinator._charging_paused = False
        coordinator._pv_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._allows_control_writes = lambda: True
        coordinator._enqueue_keepalive_if_needed = AsyncMock()
        wallbox = WallboxState(charging_active=False, phase_switch_mode_raw=1)
        sensors = HaSensorSnapshot(surplus_power_w=3000.0, valid=True)

        handled = await coordinator._enqueue_pv_phase_switch_if_needed(wallbox, sensors)
        item = await coordinator.write_queue.peek_next()

        assert handled is True
        assert coordinator._phase_switch_decision == "writing_phase_switch_mode"
        assert item.key == "phase_switch_mode"
        assert item.value == 0

    asyncio.run(_run())


def test_pending_automatic_pv_phase_switch_is_cancelled_when_surplus_changes():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.control_config = ControlConfig(
            control_mode=ControlMode.MANAGED_CONTROL,
            pv_phase_switching_mode=PvPhaseSwitchingMode.AUTOMATIC_1P3P,
        )
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.write_queue = WriteQueueManager()
        coordinator._pending_phase_switch_target = 3
        coordinator._last_phase_switch_monotonic = 0.0
        coordinator._phase_switch_up_condition_since = monotonic() - 301.0
        coordinator._phase_switch_count_this_session = 0
        coordinator._phase_switch_decision = None
        coordinator._mode = ChargeMode.PV
        coordinator._charging_paused = False
        coordinator._pv_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._allows_control_writes = lambda: True
        wallbox = WallboxState(charging_active=False, phase_switch_mode_raw=0)
        sensors = HaSensorSnapshot(surplus_power_w=3000.0, valid=True)

        handled = await coordinator._enqueue_pv_phase_switch_if_needed(wallbox, sensors)

        assert handled is False
        assert coordinator._pending_phase_switch_target is None
        assert coordinator._phase_switch_decision == "phase_switch_cancelled"
        assert await coordinator.write_queue.size() == 0

    asyncio.run(_run())


def test_pending_automatic_pv_phase_switch_is_cleared_outside_pv_mode():
    async def _run():
        coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
        coordinator.control_config = ControlConfig(
            control_mode=ControlMode.MANAGED_CONTROL,
            pv_phase_switching_mode=PvPhaseSwitchingMode.AUTOMATIC_1P3P,
        )
        coordinator.controller = WallboxController(coordinator.control_config)
        coordinator.write_queue = WriteQueueManager()
        coordinator._pending_phase_switch_target = 1
        coordinator._last_phase_switch_monotonic = 0.0
        coordinator._phase_switch_count_this_session = 0
        coordinator._phase_switch_decision = None
        coordinator._mode = ChargeMode.NORMAL
        coordinator._charging_paused = False
        coordinator._pv_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._allows_control_writes = lambda: True
        wallbox = WallboxState(charging_active=False, phase_switch_mode_raw=1)
        sensors = HaSensorSnapshot(surplus_power_w=3000.0, valid=True)

        handled = await coordinator._enqueue_pv_phase_switch_if_needed(wallbox, sensors)

        assert handled is False
        assert coordinator._pending_phase_switch_target is None
        assert await coordinator.write_queue.size() == 0

    asyncio.run(_run())


def test_capability_builder_marks_unconfirmed_and_optional_features():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    wallbox = WallboxState(ev_max_current_a=None)

    capabilities = coordinator._build_capabilities(wallbox)

    assert capabilities["phase_switch_405"] == "unconfirmed"
    assert capabilities["current_control_5004"] == "confirmed"
    assert capabilities["keepalive_6000"] == "confirmed"
    assert capabilities["ev_max_current_1108"] == "optional_absent"


def test_capability_summary_reflects_partial_validation_state():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    wallbox = WallboxState(ev_max_current_a=None)

    assert coordinator._build_capability_summary(wallbox) == "partially_validated"


def test_operating_state_reports_temporary_pv_override():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator._mode = ChargeMode.NORMAL
    coordinator._charging_paused = False
    coordinator._pv_until_unplug_active = True
    coordinator._fixed_current_until_unplug_active = False
    coordinator.control_config = ControlConfig()

    decision = SimpleNamespace(
        fallback_active=False,
        reason=ControlReason.PV_MODE,
        dominant_limit_reason=None,
    )

    assert coordinator._build_operating_state(decision) == "pv_until_unplug"


def test_operating_state_reports_temporary_fixed_current_override():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator._mode = ChargeMode.NORMAL
    coordinator._charging_paused = False
    coordinator._pv_until_unplug_active = False
    coordinator._fixed_current_until_unplug_active = True
    coordinator.control_config = ControlConfig()

    decision = SimpleNamespace(
        fallback_active=False,
        reason=ControlReason.FIXED_CURRENT_MODE,
        dominant_limit_reason=None,
    )

    assert coordinator._build_operating_state(decision) == "fixed_current_until_unplug"


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
        coordinator._mode = ChargeMode.PV
        coordinator._charging_paused = False
        coordinator._pv_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._allows_control_writes = lambda: True
        coordinator._enqueue_keepalive_if_needed = AsyncMock()

        decision = SimpleNamespace(
            charging_enabled=False,
            reason=ControlReason.BELOW_MIN_CURRENT,
            target_current_a=None,
            should_write=False,
        )

        await coordinator._enqueue_decision(decision)
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
        coordinator._mode = ChargeMode.PV
        coordinator._charging_paused = False
        coordinator._pv_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._allows_control_writes = lambda: True
        coordinator._enqueue_keepalive_if_needed = AsyncMock()

        decision = SimpleNamespace(
            charging_enabled=True,
            reason=ControlReason.SENSOR_UNAVAILABLE,
            target_current_a=6.0,
            should_write=True,
        )

        await coordinator._enqueue_decision(decision)
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
        coordinator._pv_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._allows_control_writes = lambda: True
        coordinator._enqueue_keepalive_if_needed = AsyncMock()

        decision = SimpleNamespace(
            charging_enabled=False,
            reason=ControlReason.BELOW_MIN_CURRENT,
            dominant_limit_reason=ControlReason.DLB_LIMITED,
            target_current_a=None,
            should_write=False,
        )

        await coordinator._enqueue_decision(decision)
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
        coordinator._mode = ChargeMode.PV
        coordinator._charging_paused = False
        coordinator._pv_until_unplug_active = False
        coordinator._fixed_current_until_unplug_active = False
        coordinator._allows_control_writes = lambda: True
        coordinator._enqueue_keepalive_if_needed = AsyncMock()

        decision = SimpleNamespace(
            charging_enabled=False,
            reason=ControlReason.BELOW_MIN_CURRENT,
            target_current_a=None,
            should_write=False,
        )

        await coordinator._enqueue_decision(decision)

        assert await coordinator.write_queue.size() == 0

    asyncio.run(_run())


def test_operating_state_reports_waiting_for_surplus():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator._mode = ChargeMode.PV
    coordinator._charging_paused = False
    coordinator._pv_until_unplug_active = False
    coordinator._fixed_current_until_unplug_active = False
    coordinator.control_config = ControlConfig()

    decision = SimpleNamespace(
        fallback_active=False,
        reason=ControlReason.BELOW_MIN_CURRENT,
        dominant_limit_reason=None,
    )

    assert coordinator._build_operating_state(decision) == "waiting_for_surplus"


def test_operating_state_reports_dlb_limited():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator._mode = ChargeMode.NORMAL
    coordinator._charging_paused = False
    coordinator.control_config = ControlConfig()
    coordinator._pv_until_unplug_active = False
    coordinator._fixed_current_until_unplug_active = False

    decision = SimpleNamespace(
        fallback_active=False,
        reason=ControlReason.DLB_LIMITED,
        dominant_limit_reason=ControlReason.DLB_LIMITED,
    )

    assert coordinator._build_operating_state(decision) == "dlb_limited"


def test_operating_state_reports_min_plus_surplus():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator._mode = ChargeMode.PV
    coordinator._charging_paused = False
    coordinator._pv_until_unplug_active = False
    coordinator._fixed_current_until_unplug_active = False
    coordinator.control_config = ControlConfig(pv_control_strategy="min_plus_surplus")

    decision = SimpleNamespace(
        fallback_active=False,
        reason=ControlReason.PV_MODE,
        dominant_limit_reason=None,
    )

    assert coordinator._build_operating_state(decision) == "min_plus_surplus"


def test_operating_state_reports_min_always_plus_surplus():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator._mode = ChargeMode.PV
    coordinator._charging_paused = False
    coordinator._pv_until_unplug_active = False
    coordinator._fixed_current_until_unplug_active = False
    coordinator.control_config = ControlConfig(pv_control_strategy="min_always_plus_surplus")

    decision = SimpleNamespace(
        fallback_active=False,
        reason=ControlReason.PV_MODE,
        dominant_limit_reason=None,
    )

    assert coordinator._build_operating_state(decision) == "min_always_plus_surplus"


def test_operating_state_reports_fallback_before_mode():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator._mode = ChargeMode.NORMAL
    coordinator._charging_paused = False
    coordinator._pv_until_unplug_active = False
    coordinator._fixed_current_until_unplug_active = False

    decision = SimpleNamespace(
        fallback_active=True,
        reason=ControlReason.SAFE_CURRENT_FALLBACK,
        dominant_limit_reason=None,
    )

    assert coordinator._build_operating_state(decision) == "fallback"
