from types import SimpleNamespace
import asyncio

from custom_components.webasto_unite.config_flow import (
    _bounded_float,
    _validate_dlb_options,
    _validate_init_options,
    _validate_pv_options,
)
from custom_components.webasto_unite.controller import WallboxController
from custom_components.webasto_unite.coordinator import WebastoUniteCoordinator
from custom_components.webasto_unite.models import ChargeMode, ControlConfig, ControlMode, ControlReason, HaSensorSnapshot, PhaseCurrents, WallboxState
from custom_components.webasto_unite.sensor_adapter import HaSensorAdapter
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


def test_off_mode_always_requests_cancel_when_vehicle_is_connected():
    controller = make_controller()
    wallbox = WallboxState(installed_phases=3, vehicle_connected=True)
    sensors = HaSensorSnapshot(phase_currents=PhaseCurrents(l1=0.0, l2=0.0, l3=0.0), valid=True)

    controller.evaluate(ChargeMode.NORMAL, wallbox, sensors)
    decision = controller.evaluate(ChargeMode.OFF, wallbox, sensors)
    follow_up = controller.evaluate(ChargeMode.OFF, wallbox, sensors)

    assert decision.issue_cancel_command is True
    assert follow_up.issue_cancel_command is False


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
            "3p",
        )
    except Exception as err:  # noqa: BLE001
        assert "grid power sensor" in str(err)
    else:
        raise AssertionError("Expected DLB option validation to fail")


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
            issue_cancel_command=True,
            issue_start_command=True,
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

    coordinator.set_fixed_current_until_unplug(True)
    assert coordinator.fixed_current_until_unplug_active is True
    assert coordinator.pv_until_unplug_active is False

    coordinator.set_fixed_current_until_unplug(False)
    assert coordinator.fixed_current_until_unplug_active is False


def test_capability_builder_marks_unconfirmed_and_optional_features():
    coordinator = WebastoUniteCoordinator.__new__(WebastoUniteCoordinator)
    wallbox = WallboxState(ev_max_current_a=None)

    capabilities = coordinator._build_capabilities(wallbox)

    assert capabilities["phase_switch_405"] == "unconfirmed"
    assert capabilities["current_control_5004"] == "confirmed"
    assert capabilities["keepalive_6000"] == "confirmed"
    assert capabilities["session_command_5006"] == "unconfirmed"
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
