from custom_components.webasto_unite.controller import WallboxController
from custom_components.webasto_unite.models import ChargeMode, ControlConfig, HaSensorSnapshot, PhaseCurrents, WallboxState, ControlReason, SolarControlStrategy, SolarOverrideStrategy, normalize_solar_control_strategy, normalize_solar_override_strategy
from time import monotonic


def make_controller(**kwargs):
    defaults = dict(
        max_current_a=16.0,
        main_fuse_a=25.0,
        safety_margin_a=2.0,
        min_current_a=6.0,
        stable_cycles_before_write=1,
        min_seconds_between_writes=0.0,
    )
    defaults.update(kwargs)
    return WallboxController(ControlConfig(**defaults))


def test_normal_mode_is_bounded_by_dlb():
    controller = make_controller(dlb_input_model="phase_currents", dlb_sensor_scope="load_excluding_charger")
    wallbox = WallboxState(installed_phases=3, vehicle_connected=True)
    sensors = HaSensorSnapshot(
        phase_currents=PhaseCurrents(l1=14.0, l2=10.0, l3=9.0),
        valid=True,
    )

    decision = controller.evaluate(ChargeMode.NORMAL, wallbox, sensors)

    assert decision.charging_enabled is True
    assert decision.final_target_a == 9.0
    assert decision.reason == ControlReason.DLB_LIMITED
    assert decision.dominant_limit_reason == ControlReason.DLB_LIMITED


def test_dlb_total_load_including_charger_adds_back_charger_current():
    controller = make_controller(dlb_input_model="phase_currents", dlb_sensor_scope="total_including_charger")
    wallbox = WallboxState(
        installed_phases=3,
        vehicle_connected=True,
        phase_currents=PhaseCurrents(l1=15.6, l2=15.6, l3=15.6),
    )
    sensors = HaSensorSnapshot(
        phase_currents=PhaseCurrents(l1=18.0, l2=18.0, l3=18.0),
        valid=True,
    )

    decision = controller.evaluate(ChargeMode.NORMAL, wallbox, sensors)

    assert decision.dlb_limit_a == 20.6
    assert decision.final_target_a == 16.0
    assert decision.dominant_limit_reason is None


def test_dlb_3p_configuration_tracks_active_charging_phase_dynamically():
    controller = make_controller(
        dlb_input_model="phase_currents",
        dlb_sensor_scope="total_including_charger",
        main_fuse_a=25.0,
        safety_margin_a=2.0,
    )
    wallbox = WallboxState(
        installed_phases=3,
        vehicle_connected=True,
        phase_currents=PhaseCurrents(l1=15.0, l2=0.0, l3=0.0),
    )
    sensors = HaSensorSnapshot(
        phase_currents=PhaseCurrents(l1=18.0, l2=24.0, l3=23.0),
        valid=True,
    )

    decision = controller.evaluate(ChargeMode.NORMAL, wallbox, sensors)

    assert decision.dlb_limit_a == 20.0


def test_dlb_3p_configuration_only_requires_active_phase_sensor_while_1p_charging():
    controller = make_controller(
        dlb_input_model="phase_currents",
        dlb_sensor_scope="total_including_charger",
    )
    wallbox = WallboxState(
        installed_phases=3,
        vehicle_connected=True,
        charging_active=True,
        phase_currents=PhaseCurrents(l1=10.0, l2=0.0, l3=0.0),
    )
    sensors = HaSensorSnapshot(
        phase_currents=PhaseCurrents(l1=20.0, l2=None, l3=None),
        valid=True,
    )

    decision = controller.evaluate(ChargeMode.NORMAL, wallbox, sensors)

    assert decision.fallback_active is False
    assert decision.dlb_limit_a == 13.0


def test_pv_mode_without_grid_assist_can_disable_when_below_minimum():
    controller = make_controller(solar_start_threshold_w=1800.0, solar_stop_threshold_w=1200.0)
    wallbox = WallboxState(installed_phases=1, vehicle_connected=True)
    sensors = HaSensorSnapshot(grid_power_w=200.0, valid=True)

    decision = controller.evaluate(ChargeMode.SOLAR, wallbox, sensors)

    assert decision.charging_enabled is False


def test_signed_grid_power_defaults_to_negative_export():
    controller = make_controller()

    assert controller.resolve_surplus_power(HaSensorSnapshot(grid_power_w=-1800.0)) == 1800.0
    assert controller.resolve_surplus_power(HaSensorSnapshot(grid_power_w=1800.0)) == 0.0
    assert (
        controller.resolve_surplus_power(
            HaSensorSnapshot(grid_power_w=-1500.0),
            WallboxState(charging_active=True, active_power_w=1500.0),
        )
        == 3000.0
    )
    assert (
        controller.resolve_surplus_power(
            HaSensorSnapshot(grid_power_w=1000.0),
            WallboxState(charging_active=True, active_power_w=1500.0),
        )
        == 500.0
    )
    assert (
        controller.resolve_surplus_power(
            HaSensorSnapshot(grid_power_w=2000.0),
            WallboxState(charging_active=True, active_power_w=1500.0),
        )
        == 0.0
    )


def test_signed_grid_power_can_use_positive_export():
    controller = make_controller(solar_grid_power_direction="positive_export")

    assert controller.resolve_surplus_power(HaSensorSnapshot(grid_power_w=1800.0)) == 1800.0
    assert controller.resolve_surplus_power(HaSensorSnapshot(grid_power_w=-1800.0)) == 0.0
    assert (
        controller.resolve_surplus_power(
            HaSensorSnapshot(grid_power_w=1500.0),
            WallboxState(charging_active=True, active_power_w=1500.0),
        )
        == 3000.0
    )
    assert (
        controller.resolve_surplus_power(
            HaSensorSnapshot(grid_power_w=-1000.0),
            WallboxState(charging_active=True, active_power_w=1500.0),
        )
        == 500.0
    )
    assert (
        controller.resolve_surplus_power(
            HaSensorSnapshot(grid_power_w=-2000.0),
            WallboxState(charging_active=True, active_power_w=1500.0),
        )
        == 0.0
    )


def test_signed_grid_power_does_not_add_charger_power_when_not_charging():
    controller = make_controller()

    assert (
        controller.resolve_surplus_power(
            HaSensorSnapshot(grid_power_w=-1500.0),
            WallboxState(charging_active=False, active_power_w=1500.0),
        )
        == 1500.0
    )


def test_smart_solar_signed_grid_power_adds_current_charger_power_on_active_1p_session():
    controller = make_controller(
        solar_control_strategy="min_plus_surplus",
        solar_min_current_a=6.0,
        max_current_a=20.0,
    )
    wallbox = WallboxState(
        installed_phases=1,
        vehicle_connected=True,
        charging_active=True,
        active_power_w=1500.0,
        voltage_l1_v=230.0,
    )
    sensors = HaSensorSnapshot(grid_power_w=-1500.0, valid=True)

    decision = controller.evaluate(ChargeMode.SOLAR, wallbox, sensors)

    assert decision.mode_target_a == 13.043478260869565


def test_fixed_current_mode_can_target_fixed_current_without_surplus_dependency():
    controller = make_controller(fixed_current_a=8.0)
    wallbox = WallboxState(installed_phases=1, vehicle_connected=True)
    sensors = HaSensorSnapshot(phase_currents=PhaseCurrents(l1=0.0), valid=True)

    decision = controller.evaluate(ChargeMode.FIXED_CURRENT, wallbox, sensors)

    assert decision.charging_enabled is True
    assert decision.reason == ControlReason.FIXED_CURRENT_MODE
    assert decision.mode_target_a == 8.0
    assert decision.final_target_a == 8.0


def test_pv_mode_min_plus_surplus_uses_minimum_current_without_surplus():
    controller = make_controller(solar_control_strategy="min_plus_surplus", solar_min_current_a=6.0)
    wallbox = WallboxState(installed_phases=3, vehicle_connected=True)
    sensors = HaSensorSnapshot(
        phase_currents=PhaseCurrents(l1=0.0, l2=0.0, l3=0.0),
        grid_power_w=500.0,
        valid=True,
    )

    decision = controller.evaluate(ChargeMode.SOLAR, wallbox, sensors)

    assert decision.charging_enabled is True
    assert decision.reason == ControlReason.SOLAR_MODE
    assert decision.mode_target_a == 6.0
    assert decision.final_target_a == 6.0


def test_pv_mode_min_plus_surplus_scales_above_minimum_when_surplus_is_high():
    controller = make_controller(solar_control_strategy="min_plus_surplus", solar_min_current_a=6.0)
    wallbox = WallboxState(installed_phases=1, vehicle_connected=True)
    sensors = HaSensorSnapshot(
        phase_currents=PhaseCurrents(l1=0.0),
        surplus_power_w=2300.0,
        valid=True,
    )

    decision = controller.evaluate(ChargeMode.SOLAR, wallbox, sensors)

    assert decision.charging_enabled is True
    assert decision.reason == ControlReason.SOLAR_MODE
    assert decision.mode_target_a == 10.0
    assert decision.final_target_a == 10.0


def test_pv_mode_min_plus_surplus_uses_measured_phase_voltage_when_available():
    controller = make_controller(solar_control_strategy="min_plus_surplus", solar_min_current_a=6.0)
    wallbox = WallboxState(
        installed_phases=3,
        charging_active=True,
        phases_in_use=3,
        vehicle_connected=True,
        voltage_l1_v=232.0,
        voltage_l2_v=236.0,
        voltage_l3_v=238.0,
    )
    sensors = HaSensorSnapshot(surplus_power_w=7060.0, valid=True)

    decision = controller.evaluate(ChargeMode.SOLAR, wallbox, sensors)

    assert decision.charging_enabled is True
    assert decision.mode_target_a == 10.0
    assert decision.final_target_a == 10.0


def test_pv_mode_falls_back_to_nominal_voltage_for_implausible_voltage():
    controller = make_controller(solar_control_strategy="min_plus_surplus", solar_min_current_a=6.0)
    wallbox = WallboxState(installed_phases=1, vehicle_connected=True, voltage_l1_v=400.0)
    sensors = HaSensorSnapshot(surplus_power_w=2300.0, valid=True)

    decision = controller.evaluate(ChargeMode.SOLAR, wallbox, sensors)

    assert decision.mode_target_a == 10.0


def test_pv_mode_uses_effective_active_phases_while_charging():
    controller = make_controller(solar_control_strategy="min_plus_surplus", solar_min_current_a=6.0)
    wallbox = WallboxState(
        installed_phases=3,
        phases_in_use=1,
        charging_active=True,
        vehicle_connected=True,
    )
    sensors = HaSensorSnapshot(surplus_power_w=2300.0, valid=True)

    decision = controller.evaluate(ChargeMode.SOLAR, wallbox, sensors)

    assert decision.charging_enabled is True
    assert decision.reason == ControlReason.SOLAR_MODE
    assert decision.mode_target_a == 10.0
    assert decision.final_target_a == 10.0


def test_pv_mode_on_3p_configuration_uses_adaptive_1p_assumption_before_charging_starts():
    controller = make_controller(solar_control_strategy="surplus", solar_min_current_a=6.0)
    wallbox = WallboxState(
        installed_phases=3,
        charging_active=False,
        phases_in_use=None,
        vehicle_connected=True,
    )
    sensors = HaSensorSnapshot(surplus_power_w=2300.0, valid=True)

    decision = controller.evaluate(ChargeMode.SOLAR, wallbox, sensors)

    assert decision.charging_enabled is True
    assert decision.reason == ControlReason.SOLAR_MODE
    assert decision.mode_target_a == 10.0
    assert decision.final_target_a == 10.0


def test_pv_mode_on_3p_configuration_uses_observed_3p_while_charging():
    controller = make_controller(solar_control_strategy="surplus", solar_min_current_a=6.0)
    wallbox = WallboxState(
        installed_phases=3,
        charging_active=True,
        phases_in_use=3,
        vehicle_connected=True,
    )
    sensors = HaSensorSnapshot(surplus_power_w=2300.0, valid=True)

    decision = controller.evaluate(ChargeMode.SOLAR, wallbox, sensors)

    assert decision.charging_enabled is False
    assert decision.reason == ControlReason.BELOW_MIN_CURRENT
    assert decision.mode_target_a is None
    assert decision.final_target_a is None


def test_pv_mode_reuses_observed_session_phase_count_for_later_starts():
    controller = make_controller(solar_control_strategy="surplus", solar_min_current_a=6.0)

    observed_wallbox = WallboxState(
        installed_phases=3,
        charging_active=True,
        phases_in_use=3,
        vehicle_connected=True,
    )
    later_wallbox = WallboxState(
        installed_phases=3,
        charging_active=False,
        phases_in_use=None,
        vehicle_connected=True,
    )
    sensors = HaSensorSnapshot(surplus_power_w=2300.0, valid=True)

    first_decision = controller.evaluate(ChargeMode.SOLAR, observed_wallbox, sensors)
    second_observed_decision = controller.evaluate(ChargeMode.SOLAR, observed_wallbox, sensors)
    decision = controller.evaluate(ChargeMode.SOLAR, later_wallbox, sensors)

    assert first_decision.charging_enabled is False
    assert second_observed_decision.charging_enabled is False
    assert controller.observed_session_phase_count == 3
    assert decision.charging_enabled is False
    assert decision.reason == ControlReason.BELOW_MIN_CURRENT
    assert decision.mode_target_a is None
    assert decision.final_target_a is None


def test_session_phase_observation_requires_two_matching_polls():
    controller = make_controller(solar_control_strategy="surplus", solar_min_current_a=6.0)
    wallbox = WallboxState(
        installed_phases=3,
        charging_active=True,
        phases_in_use=3,
        vehicle_connected=True,
    )
    sensors = HaSensorSnapshot(surplus_power_w=2300.0, valid=True)

    controller.evaluate(ChargeMode.SOLAR, wallbox, sensors)

    assert controller.observed_session_phase_count is None
    assert controller._pending_session_phase_count == 3
    assert controller._pending_session_phase_polls == 1

    controller.evaluate(ChargeMode.SOLAR, wallbox, sensors)

    assert controller.observed_session_phase_count == 3
    assert controller._pending_session_phase_count is None
    assert controller._pending_session_phase_polls == 0


def test_session_phase_observation_resets_when_vehicle_disconnects():
    controller = make_controller(solar_control_strategy="surplus", solar_min_current_a=6.0)
    controller.observed_session_phase_count = 3

    wallbox = WallboxState(
        installed_phases=3,
        charging_active=False,
        phases_in_use=None,
        vehicle_connected=False,
    )
    sensors = HaSensorSnapshot(surplus_power_w=2300.0, valid=True)

    controller.evaluate(ChargeMode.SOLAR, wallbox, sensors)

    assert controller.observed_session_phase_count is None


def test_pv_mode_min_plus_surplus_pauses_when_sensor_is_unavailable():
    controller = make_controller(solar_control_strategy="min_plus_surplus", solar_min_current_a=6.0)
    wallbox = WallboxState(installed_phases=3, vehicle_connected=True)
    sensors = HaSensorSnapshot(phase_currents=PhaseCurrents(l1=0.0, l2=0.0, l3=0.0), valid=True)

    decision = controller.evaluate(ChargeMode.SOLAR, wallbox, sensors)

    assert decision.charging_enabled is False
    assert decision.reason == ControlReason.SENSOR_UNAVAILABLE
    assert decision.mode_target_a is None
    assert decision.final_target_a is None


def test_pv_surplus_mode_requires_enough_power_for_min_current_on_3p():
    controller = make_controller(
        solar_control_strategy="surplus",
        solar_start_threshold_w=1800.0,
        solar_stop_threshold_w=1200.0,
        solar_min_current_a=6.0,
    )
    wallbox = WallboxState(
        installed_phases=3,
        charging_active=True,
        phases_in_use=3,
        vehicle_connected=True,
    )
    sensors = HaSensorSnapshot(surplus_power_w=2300.0, valid=True)

    decision = controller.evaluate(ChargeMode.SOLAR, wallbox, sensors)

    assert decision.charging_enabled is False
    assert decision.reason == ControlReason.BELOW_MIN_CURRENT
    assert decision.mode_target_a is None


def test_legacy_min_always_pv_strategy_normalizes_to_min_plus_surplus():
    assert normalize_solar_control_strategy("min_always_plus_surplus") == SolarControlStrategy.MIN_PLUS_SURPLUS


def test_pv_until_unplug_strategy_can_override_base_pv_strategy():
    strategy = WallboxController.resolve_effective_solar_strategy(
        SolarControlStrategy.SURPLUS,
        SolarOverrideStrategy.MIN_PLUS_SURPLUS,
        True,
    )

    assert strategy == SolarControlStrategy.MIN_PLUS_SURPLUS


def test_legacy_min_always_pv_until_unplug_strategy_normalizes_to_min_plus_surplus():
    assert normalize_solar_override_strategy("min_always_plus_surplus") == SolarOverrideStrategy.MIN_PLUS_SURPLUS


def test_pv_until_unplug_strategy_can_inherit_base_pv_strategy():
    strategy = WallboxController.resolve_effective_solar_strategy(
        SolarControlStrategy.MIN_PLUS_SURPLUS,
        SolarOverrideStrategy.INHERIT,
        True,
    )

    assert strategy == SolarControlStrategy.MIN_PLUS_SURPLUS


def test_pv_surplus_start_delay_prevents_immediate_start():
    controller = make_controller(
        solar_control_strategy="surplus",
        solar_start_threshold_w=1800.0,
        solar_stop_threshold_w=1200.0,
        solar_start_delay_s=60.0,
    )
    wallbox = WallboxState(installed_phases=1, vehicle_connected=True)
    sensors = HaSensorSnapshot(phase_currents=PhaseCurrents(l1=0.0), surplus_power_w=2300.0, valid=True)

    first = controller.evaluate(ChargeMode.SOLAR, wallbox, sensors)
    assert first.charging_enabled is False

    controller.solar_state.start_condition_since = monotonic() - 61.0
    second = controller.evaluate(ChargeMode.SOLAR, wallbox, sensors)
    assert second.charging_enabled is True
    assert second.final_target_a == 10.0


def test_pv_surplus_stop_delay_and_min_runtime_hold_minimum_current_temporarily():
    controller = make_controller(
        solar_control_strategy="surplus",
        solar_start_threshold_w=1800.0,
        solar_stop_threshold_w=1200.0,
        solar_stop_delay_s=120.0,
        solar_min_runtime_s=300.0,
        solar_min_current_a=6.0,
    )
    wallbox = WallboxState(installed_phases=1, vehicle_connected=True)
    sensors = HaSensorSnapshot(phase_currents=PhaseCurrents(l1=0.0), surplus_power_w=300.0, valid=True)

    controller.solar_state.active = True
    controller.solar_state.last_transition_monotonic = monotonic()
    decision = controller.evaluate(ChargeMode.SOLAR, wallbox, sensors)

    assert decision.charging_enabled is True
    assert decision.final_target_a == 6.0


def test_pv_surplus_min_pause_blocks_restart_after_recent_stop():
    controller = make_controller(
        solar_control_strategy="surplus",
        solar_start_threshold_w=1800.0,
        solar_stop_threshold_w=1200.0,
        solar_min_pause_s=120.0,
    )
    wallbox = WallboxState(installed_phases=1, vehicle_connected=True)
    sensors = HaSensorSnapshot(phase_currents=PhaseCurrents(l1=0.0), surplus_power_w=2300.0, valid=True)

    controller.solar_state.last_stop_monotonic = monotonic()
    decision = controller.evaluate(ChargeMode.SOLAR, wallbox, sensors)

    assert decision.charging_enabled is False


def test_invalid_sensors_fall_back_to_safe_current():
    controller = make_controller(dlb_input_model="phase_currents", safe_current_a=7.0)
    wallbox = WallboxState(installed_phases=3, vehicle_connected=True)
    sensors = HaSensorSnapshot(valid=False, reason_invalid='missing sensors')

    decision = controller.evaluate(ChargeMode.NORMAL, wallbox, sensors)

    assert decision.charging_enabled is True
    assert decision.final_target_a == 7.0
    assert decision.reason == ControlReason.SAFE_CURRENT_FALLBACK
    assert decision.fallback_active is True
    assert decision.sensor_invalid_reason == "missing sensors"


def test_safety_reduction_bypasses_write_debounce():
    controller = make_controller(
        dlb_input_model="phase_currents",
        stable_cycles_before_write=3,
        min_seconds_between_writes=300.0,
    )
    controller.mark_current_written(16.0)
    wallbox = WallboxState(installed_phases=3, vehicle_connected=True)
    sensors = HaSensorSnapshot(
        phase_currents=PhaseCurrents(l1=16.0, l2=10.0, l3=10.0),
        valid=True,
    )

    decision = controller.evaluate(ChargeMode.NORMAL, wallbox, sensors)

    assert decision.final_target_a == 7.0
    assert decision.should_write is True


def test_unvalidated_hardware_limit_does_not_cap_target_current():
    controller = make_controller()
    wallbox = WallboxState(installed_phases=3, vehicle_connected=True, session_max_current_a=10.0)
    sensors = HaSensorSnapshot(phase_currents=PhaseCurrents(l1=0.0, l2=0.0, l3=0.0), valid=True)

    decision = controller.evaluate(ChargeMode.NORMAL, wallbox, sensors)

    assert decision.final_target_a == 16.0
    assert decision.reason == ControlReason.NORMAL_MODE
    assert decision.dominant_limit_reason is None


def test_transition_to_off_writes_zero_current_when_vehicle_connected():
    controller = make_controller()
    wallbox = WallboxState(installed_phases=3, vehicle_connected=True, charging_active=True)
    sensors = HaSensorSnapshot(phase_currents=PhaseCurrents(l1=0.0, l2=0.0, l3=0.0), valid=True)

    controller.evaluate(ChargeMode.NORMAL, wallbox, sensors)
    off_decision = controller.evaluate(ChargeMode.OFF, wallbox, sensors)

    assert off_decision.target_current_a == 0.0
    assert off_decision.final_target_a == 0.0
    assert off_decision.should_write is True


def test_normal_mode_loads_to_max_current_but_is_still_limited_by_dlb():
    controller = make_controller(
        dlb_input_model="phase_currents",
        max_current_a=20.0,
        dlb_sensor_scope="load_excluding_charger",
    )
    wallbox = WallboxState(installed_phases=1, vehicle_connected=True)
    sensors = HaSensorSnapshot(phase_currents=PhaseCurrents(l1=17.0), valid=True)

    decision = controller.evaluate(ChargeMode.NORMAL, wallbox, sensors)

    assert decision.reason == ControlReason.DLB_LIMITED
    assert decision.dominant_limit_reason == ControlReason.DLB_LIMITED
    assert decision.final_target_a == 6.0
