from types import SimpleNamespace
import asyncio
import importlib
from datetime import datetime, timedelta, timezone
from time import monotonic
from unittest.mock import AsyncMock
import pytest

from homeassistant.config_entries import ConfigEntryNotReady
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
from custom_components.webasto_unite.controller import WallboxController
from custom_components.webasto_unite.coordinator import WebastoUniteCoordinator
from custom_components.webasto_unite.diagnostics import async_get_config_entry_diagnostics
from custom_components.webasto_unite.models import ChargeMode, ControlConfig, ControlMode, ControlReason, DlbInputModel, HaSensorSnapshot, PhaseCurrents, SolarControlStrategy, SolarInputModel, RuntimeSnapshot, WallboxState
from custom_components.webasto_unite.number import WebastoCurrentLimitNumber, WebastoFixedCurrentNumber
from custom_components.webasto_unite.sensor import WebastoSensor
from custom_components.webasto_unite.sensor_adapter import HaSensorAdapter
from custom_components.webasto_unite.switch import WebastoChargingSwitch
from custom_components.webasto_unite.wallbox_reader import WallboxReader
from custom_components.webasto_unite.write_queue import WriteQueueManager


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
            "advanced",
        }
        assert result["data_schema"].args[0]["connection"].options["collapsed"] is False
        assert result["data_schema"].args[0]["general_charging"].options["collapsed"] is False
        assert result["data_schema"].args[0]["dynamic_load_balancing"].options["collapsed"] is True
        assert result["data_schema"].args[0]["solar_charging"].options["collapsed"] is True

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
                    "user_limit": 16.0,
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
                    "solar_start_threshold": 1800.0,
                    "solar_stop_threshold": 1200.0,
                    "solar_start_delay": 0.0,
                    "solar_stop_delay": 0.0,
                    "solar_min_runtime": 0.0,
                    "solar_min_pause": 0.0,
                    "solar_min_current": 6.0,
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
                "solar_grid_power_direction",
                "solar_require_units",
            "solar_surplus_sensor",
            "solar_grid_power_sensor",
            "solar_start_threshold",
            "solar_stop_threshold",
            "solar_start_delay",
            "solar_stop_delay",
            "solar_min_runtime",
            "solar_min_pause",
            "solar_min_current",
        }

    asyncio.run(_run())


def test_options_flow_rejects_pv_min_current_above_max_current():
    async def _run():
        flow = WebastoUniteOptionsFlow(make_config_entry())

        result = await flow.async_step_init(
            default_options_input(max_current=10.0, user_limit=10.0, safe_current=6.0, solar_min_current=12.0)
        )

        assert result["type"] == "form"
        assert result["errors"]["base"] == "solar_min_current_out_of_range"

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
                    "user_limit": 16.0,
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


def test_runtime_current_setters_reject_fractional_amp_values():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.control_config = ControlConfig(min_current_a=6.0, max_current_a=16.0)

    with pytest.raises(ValueError, match="whole amp value"):
        coordinator.set_user_limit(10.5)

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

        result = await flow.async_step_init(default_options_input(user_limit=10.5))

        assert result["type"] == "form"
        assert result["errors"]["base"] == "user_limit_out_of_range"

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

    assert result["solar_control_strategy"] == "smart_solar"


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


def test_options_flow_shows_min_and_max_current_in_charging_section():
    async def _run():
        flow = WebastoUniteOptionsFlow(make_config_entry())

        result = await flow.async_step_init()

        fields = result["data_schema"].args[0]["general_charging"].schema.args[0]
        assert "min_current" in fields
        assert "max_current" in fields

    asyncio.run(_run())


def test_options_flow_allows_20a_current_when_max_current_is_20a():
    async def _run():
        flow = WebastoUniteOptionsFlow(make_config_entry())

        result = await flow.async_step_init(default_options_input(max_current=20.0, user_limit=20.0))

        assert result["type"] == "create_entry"
        assert result["data"]["max_current"] == 20
        assert result["data"]["user_limit"] == 20

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

    assert charging_switch.available is True


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

    snapshot = coordinator._read_sensor_snapshot()

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

    snapshot = coordinator._read_sensor_snapshot(wallbox)

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

    snapshot = coordinator._read_sensor_snapshot()

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

    snapshot = coordinator._read_sensor_snapshot()

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

    snapshot = coordinator._read_sensor_snapshot()

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
    coordinator._last_charging_active = False
    coordinator._dlb_start_guard_until_monotonic = 0.0
    coordinator._dlb_start_guard_downscale_samples = 0

    wallbox = WallboxState(charging_active=True, current_limit_a=16.0)
    first = SimpleNamespace(
        should_write=True,
        target_current_a=12.0,
        dominant_limit_reason=ControlReason.DLB_LIMITED,
    )
    coordinator._apply_dlb_start_transient_guard(
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
    coordinator._apply_dlb_start_transient_guard(
        wallbox=wallbox,
        decision=second,
        now_monotonic=11.0,
    )
    assert second.should_write is True


def test_dlb_start_guard_is_disabled_for_load_excluding_charger_scope():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator.control_config = ControlConfig(dlb_sensor_scope="load_excluding_charger")
    coordinator._last_charging_active = False
    coordinator._dlb_start_guard_until_monotonic = 0.0
    coordinator._dlb_start_guard_downscale_samples = 0

    wallbox = WallboxState(charging_active=True, current_limit_a=16.0)
    decision = SimpleNamespace(
        should_write=True,
        target_current_a=12.0,
        dominant_limit_reason=ControlReason.DLB_LIMITED,
    )
    coordinator._apply_dlb_start_transient_guard(
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
    coordinator._last_charging_active = False
    coordinator._dlb_start_guard_until_monotonic = 0.0
    coordinator._dlb_start_guard_downscale_samples = 0

    wallbox = WallboxState(charging_active=True, current_limit_a=16.0)
    decision = SimpleNamespace(
        should_write=True,
        target_current_a=6.0,
        dominant_limit_reason=ControlReason.DLB_LIMITED,
    )
    coordinator._apply_dlb_start_transient_guard(
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
    coordinator._last_solar_charging_active = False
    coordinator._solar_start_guard_until_monotonic = 0.0
    coordinator._solar_start_guard_pause_samples = 0
    coordinator._startup_refresh_count = 0
    coordinator._startup_started_monotonic = monotonic()

    wallbox = WallboxState(charging_active=True, current_limit_a=16.0)
    decision = SimpleNamespace(
        should_write=True,
        charging_enabled=False,
        target_current_a=0.0,
        reason=ControlReason.BELOW_MIN_CURRENT,
        dominant_limit_reason=None,
    )
    sensors = SimpleNamespace(solar_input_state="unavailable")

    coordinator._apply_solar_start_transient_guard(
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
    coordinator._last_solar_charging_active = False
    coordinator._solar_start_guard_until_monotonic = 0.0
    coordinator._solar_start_guard_pause_samples = 0
    coordinator._startup_refresh_count = 0
    coordinator._startup_started_monotonic = monotonic()

    wallbox = WallboxState(charging_active=True, current_limit_a=16.0)
    first = SimpleNamespace(
        should_write=True,
        charging_enabled=False,
        target_current_a=0.0,
        reason=ControlReason.BELOW_MIN_CURRENT,
        dominant_limit_reason=None,
    )
    sensors = SimpleNamespace(solar_input_state="unavailable")
    coordinator._apply_solar_start_transient_guard(
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
    coordinator._apply_solar_start_transient_guard(
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
            user_limit_a=12.0,
            fixed_current_a=10.0,
        ),
        async_request_refresh=AsyncMock(),
    )

    current_limit = WebastoCurrentLimitNumber(coordinator)
    fixed_current = WebastoFixedCurrentNumber(coordinator)

    assert current_limit.native_min_value == 8.0
    assert current_limit.native_max_value == 16.0
    assert fixed_current.native_min_value == 8.0
    assert fixed_current.native_max_value == 16.0


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

def test_capability_builder_marks_unconfirmed_and_optional_features():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    wallbox = WallboxState(ev_max_current_a=None)

    capabilities = coordinator._build_capabilities(wallbox)

    assert capabilities["current_control_5004"] == "confirmed"
    assert capabilities["keepalive_6000"] == "confirmed"
    assert capabilities["ev_max_current_1108"] == "optional_absent"


def test_capability_summary_reflects_partial_validation_state():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    wallbox = WallboxState(ev_max_current_a=None)

    assert coordinator._build_capability_summary(wallbox) == "validated_with_optional_gaps"


def test_operating_state_reports_temporary_pv_override():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator._mode = ChargeMode.NORMAL
    coordinator._charging_paused = False
    coordinator._solar_until_unplug_active = True
    coordinator._fixed_current_until_unplug_active = False
    coordinator.control_config = ControlConfig()

    decision = SimpleNamespace(
        fallback_active=False,
        reason=ControlReason.SOLAR_MODE,
        dominant_limit_reason=None,
    )

    assert coordinator._build_operating_state(decision) == "solar_until_unplug"


def test_operating_state_reports_temporary_fixed_current_override():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator._mode = ChargeMode.NORMAL
    coordinator._charging_paused = False
    coordinator._solar_until_unplug_active = False
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
        coordinator._mode = ChargeMode.SOLAR
        coordinator._charging_paused = False
        coordinator._solar_until_unplug_active = False
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
        coordinator._mode = ChargeMode.SOLAR
        coordinator._charging_paused = False
        coordinator._solar_until_unplug_active = False
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
        coordinator._solar_until_unplug_active = False
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
        coordinator._mode = ChargeMode.SOLAR
        coordinator._charging_paused = False
        coordinator._solar_until_unplug_active = False
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
    coordinator._mode = ChargeMode.SOLAR
    coordinator._charging_paused = False
    coordinator._solar_until_unplug_active = False
    coordinator._fixed_current_until_unplug_active = False
    coordinator.control_config = ControlConfig()

    decision = SimpleNamespace(
        fallback_active=False,
        reason=ControlReason.BELOW_MIN_CURRENT,
        dominant_limit_reason=None,
    )

    assert coordinator._build_operating_state(decision) == "waiting_for_solar"


def test_operating_state_reports_dlb_limited():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator._mode = ChargeMode.NORMAL
    coordinator._charging_paused = False
    coordinator.control_config = ControlConfig()
    coordinator._solar_until_unplug_active = False
    coordinator._fixed_current_until_unplug_active = False

    decision = SimpleNamespace(
        fallback_active=False,
        reason=ControlReason.DLB_LIMITED,
        dominant_limit_reason=ControlReason.DLB_LIMITED,
    )

    assert coordinator._build_operating_state(decision) == "dlb_limited"


def test_operating_state_reports_min_plus_surplus():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator._mode = ChargeMode.SOLAR
    coordinator._charging_paused = False
    coordinator._solar_until_unplug_active = False
    coordinator._fixed_current_until_unplug_active = False
    coordinator.control_config = ControlConfig(solar_control_strategy="smart_solar")

    decision = SimpleNamespace(
        fallback_active=False,
        reason=ControlReason.SOLAR_MODE,
        dominant_limit_reason=None,
    )

    assert coordinator._build_operating_state(decision) == "smart_solar"


def test_legacy_min_always_operating_state_normalizes_to_min_plus_surplus():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator._mode = ChargeMode.SOLAR
    coordinator._charging_paused = False
    coordinator._solar_until_unplug_active = False
    coordinator._fixed_current_until_unplug_active = False
    coordinator.control_config = ControlConfig(solar_control_strategy="smart_solar")

    decision = SimpleNamespace(
        fallback_active=False,
        reason=ControlReason.SOLAR_MODE,
        dominant_limit_reason=None,
    )

    assert coordinator._build_operating_state(decision) == "smart_solar"


def test_operating_state_reports_fallback_before_mode():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    coordinator._mode = ChargeMode.NORMAL
    coordinator._charging_paused = False
    coordinator._solar_until_unplug_active = False
    coordinator._fixed_current_until_unplug_active = False

    decision = SimpleNamespace(
        fallback_active=True,
        reason=ControlReason.SAFE_CURRENT_FALLBACK,
        dominant_limit_reason=None,
    )

    assert coordinator._build_operating_state(decision) == "fallback"


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
                "user_limit": 16.0,
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
            user_limit_a=16.0,
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
        coordinator._last_charging_active = False
        coordinator._last_solar_charging_active = False
        coordinator._dlb_start_guard_until_monotonic = 0.0
        coordinator._dlb_start_guard_downscale_samples = 0
        coordinator._solar_start_guard_until_monotonic = 0.0
        coordinator._solar_start_guard_pause_samples = 0
        coordinator._startup_started_monotonic = monotonic() - 120.0
        coordinator._startup_refresh_count = 10
        coordinator._last_keepalive_sent_monotonic = monotonic()
        coordinator._keepalive_started_monotonic = monotonic() - 10.0
        coordinator._keepalive_sent_count = 0
        coordinator._keepalive_write_failures = 0
        coordinator._sensor_refresh_task = None
        coordinator._flush_lock = asyncio.Lock()
        coordinator._enqueue_decision = AsyncMock()
        coordinator._flush_write_queue = AsyncMock()

        first_snapshot = await coordinator._async_update_data()
        second_snapshot = await coordinator._async_update_data()

        assert first_snapshot.mode_target_a == 10.0
        assert first_snapshot.final_target_a == 10.0
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
            user_limit_a=16.0,
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
        coordinator._last_charging_active = False
        coordinator._last_solar_charging_active = False
        coordinator._dlb_start_guard_until_monotonic = 0.0
        coordinator._dlb_start_guard_downscale_samples = 0
        coordinator._solar_start_guard_until_monotonic = 0.0
        coordinator._solar_start_guard_pause_samples = 0
        coordinator._startup_started_monotonic = monotonic() - 120.0
        coordinator._startup_refresh_count = 10
        coordinator._last_keepalive_sent_monotonic = monotonic()
        coordinator._keepalive_started_monotonic = monotonic() - 10.0
        coordinator._keepalive_sent_count = 0
        coordinator._keepalive_write_failures = 0
        coordinator._sensor_refresh_task = None
        coordinator._flush_lock = asyncio.Lock()
        coordinator._enqueue_decision = AsyncMock()
        coordinator._flush_write_queue = AsyncMock()

        first_snapshot = await coordinator._async_update_data()
        second_snapshot = await coordinator._async_update_data()
        third_snapshot = await coordinator._async_update_data()

        assert coordinator.controller.observed_session_phase_count == 3
        assert first_snapshot.wallbox.phases_in_use == 3
        assert second_snapshot.wallbox.phases_in_use == 3
        assert third_snapshot.wallbox.phases_in_use is None
        assert third_snapshot.mode_target_a is None
        assert third_snapshot.final_target_a is None
        assert third_snapshot.operating_state == "waiting_for_solar"

    asyncio.run(_run())


def test_sensor_presentation_uses_clear_solar_labels():
    assert WebastoSensor._present_value("fallback") == "Safe Fallback"
    assert (
        WebastoSensor._present_value("unavailable", value_key="solar_input_state")
        == "Solar Input Unavailable"
    )
