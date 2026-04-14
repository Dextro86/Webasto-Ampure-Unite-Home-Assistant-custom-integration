from custom_components.webasto_unite.controller import WallboxController
from custom_components.webasto_unite.models import ChargeMode, ControlConfig, HaSensorSnapshot, PhaseCurrents, WallboxState, ControlReason, PvControlStrategy, PvOverrideStrategy, PvPhaseSwitchingMode
from time import monotonic


def make_controller(**kwargs):
    defaults = dict(
        user_limit_a=16.0,
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


def test_pv_mode_without_grid_assist_can_disable_when_below_minimum():
    controller = make_controller(pv_start_threshold_w=1800.0, pv_stop_threshold_w=1200.0)
    wallbox = WallboxState(installed_phases=1, vehicle_connected=True)
    sensors = HaSensorSnapshot(grid_power_w=200.0, valid=True)

    decision = controller.evaluate(ChargeMode.PV, wallbox, sensors)

    assert decision.charging_enabled is False


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
    controller = make_controller(pv_control_strategy="min_plus_surplus", pv_min_current_a=6.0)
    wallbox = WallboxState(installed_phases=3, vehicle_connected=True)
    sensors = HaSensorSnapshot(
        phase_currents=PhaseCurrents(l1=0.0, l2=0.0, l3=0.0),
        grid_power_w=500.0,
        valid=True,
    )

    decision = controller.evaluate(ChargeMode.PV, wallbox, sensors)

    assert decision.charging_enabled is True
    assert decision.reason == ControlReason.PV_MODE
    assert decision.mode_target_a == 6.0
    assert decision.final_target_a == 6.0


def test_pv_mode_min_plus_surplus_scales_above_minimum_when_surplus_is_high():
    controller = make_controller(pv_control_strategy="min_plus_surplus", pv_min_current_a=6.0)
    wallbox = WallboxState(installed_phases=1, vehicle_connected=True)
    sensors = HaSensorSnapshot(
        phase_currents=PhaseCurrents(l1=0.0),
        surplus_power_w=2300.0,
        valid=True,
    )

    decision = controller.evaluate(ChargeMode.PV, wallbox, sensors)

    assert decision.charging_enabled is True
    assert decision.reason == ControlReason.PV_MODE
    assert decision.mode_target_a == 10.0
    assert decision.final_target_a == 10.0


def test_pv_mode_min_plus_surplus_keeps_minimum_current_when_sensor_is_unavailable():
    controller = make_controller(pv_control_strategy="min_plus_surplus", pv_min_current_a=6.0)
    wallbox = WallboxState(installed_phases=3, vehicle_connected=True)
    sensors = HaSensorSnapshot(phase_currents=PhaseCurrents(l1=0.0, l2=0.0, l3=0.0), valid=True)

    decision = controller.evaluate(ChargeMode.PV, wallbox, sensors)

    assert decision.charging_enabled is True
    assert decision.reason == ControlReason.SENSOR_UNAVAILABLE
    assert decision.mode_target_a == 6.0
    assert decision.final_target_a == 6.0


def test_pv_until_unplug_strategy_can_override_base_pv_strategy():
    strategy = WallboxController.resolve_effective_pv_strategy(
        PvControlStrategy.SURPLUS,
        PvOverrideStrategy.MIN_PLUS_SURPLUS,
        True,
    )

    assert strategy == PvControlStrategy.MIN_PLUS_SURPLUS


def test_pv_until_unplug_strategy_can_inherit_base_pv_strategy():
    strategy = WallboxController.resolve_effective_pv_strategy(
        PvControlStrategy.MIN_PLUS_SURPLUS,
        PvOverrideStrategy.INHERIT,
        True,
    )

    assert strategy == PvControlStrategy.MIN_PLUS_SURPLUS


def test_pv_surplus_start_delay_prevents_immediate_start():
    controller = make_controller(
        pv_control_strategy="surplus",
        pv_start_threshold_w=1800.0,
        pv_stop_threshold_w=1200.0,
        pv_start_delay_s=60.0,
    )
    wallbox = WallboxState(installed_phases=1, vehicle_connected=True)
    sensors = HaSensorSnapshot(phase_currents=PhaseCurrents(l1=0.0), surplus_power_w=2300.0, valid=True)

    first = controller.evaluate(ChargeMode.PV, wallbox, sensors)
    assert first.charging_enabled is False

    controller.pv_state.start_condition_since = monotonic() - 61.0
    second = controller.evaluate(ChargeMode.PV, wallbox, sensors)
    assert second.charging_enabled is True
    assert second.final_target_a == 10.0


def test_pv_surplus_stop_delay_and_min_runtime_hold_minimum_current_temporarily():
    controller = make_controller(
        pv_control_strategy="surplus",
        pv_start_threshold_w=1800.0,
        pv_stop_threshold_w=1200.0,
        pv_stop_delay_s=120.0,
        pv_min_runtime_s=300.0,
        pv_min_current_a=6.0,
    )
    wallbox = WallboxState(installed_phases=1, vehicle_connected=True)
    sensors = HaSensorSnapshot(phase_currents=PhaseCurrents(l1=0.0), surplus_power_w=300.0, valid=True)

    controller.pv_state.active = True
    controller.pv_state.last_transition_monotonic = monotonic()
    decision = controller.evaluate(ChargeMode.PV, wallbox, sensors)

    assert decision.charging_enabled is True
    assert decision.final_target_a == 6.0


def test_pv_surplus_min_pause_blocks_restart_after_recent_stop():
    controller = make_controller(
        pv_control_strategy="surplus",
        pv_start_threshold_w=1800.0,
        pv_stop_threshold_w=1200.0,
        pv_min_pause_s=120.0,
    )
    wallbox = WallboxState(installed_phases=1, vehicle_connected=True)
    sensors = HaSensorSnapshot(phase_currents=PhaseCurrents(l1=0.0), surplus_power_w=2300.0, valid=True)

    controller.pv_state.last_stop_monotonic = monotonic()
    decision = controller.evaluate(ChargeMode.PV, wallbox, sensors)

    assert decision.charging_enabled is False


def test_pv_phase_switching_is_manual_only_by_default():
    controller = make_controller()
    wallbox = WallboxState(installed_phases=1, phase_switch_mode_raw=0)
    sensors = HaSensorSnapshot(surplus_power_w=6000.0, valid=True)

    assert controller.resolve_pv_phase_target(ChargeMode.PV, wallbox, sensors) is None


def test_pv_phase_switching_requests_3p_when_surplus_is_high():
    controller = make_controller(pv_phase_switching_mode=PvPhaseSwitchingMode.AUTOMATIC_1P3P)
    wallbox = WallboxState(installed_phases=1, phase_switch_mode_raw=0)
    sensors = HaSensorSnapshot(surplus_power_w=6000.0, valid=True)

    assert controller.resolve_pv_phase_target(ChargeMode.PV, wallbox, sensors) == 3


def test_pv_phase_switching_requests_1p_when_surplus_is_below_3p_range():
    controller = make_controller(pv_phase_switching_mode=PvPhaseSwitchingMode.AUTOMATIC_1P3P)
    wallbox = WallboxState(installed_phases=3, phase_switch_mode_raw=1)
    sensors = HaSensorSnapshot(surplus_power_w=3000.0, valid=True)

    assert controller.resolve_pv_phase_target(ChargeMode.PV, wallbox, sensors) == 1


def test_pv_phase_switching_hysteresis_is_configurable():
    controller = make_controller(
        pv_phase_switching_mode=PvPhaseSwitchingMode.AUTOMATIC_1P3P,
        pv_phase_switching_hysteresis_w=1000.0,
    )

    assert controller.resolve_pv_phase_target(
        ChargeMode.PV,
        WallboxState(installed_phases=1, phase_switch_mode_raw=0),
        HaSensorSnapshot(surplus_power_w=5000.0, valid=True),
    ) is None
    assert controller.resolve_pv_phase_target(
        ChargeMode.PV,
        WallboxState(installed_phases=1, phase_switch_mode_raw=0),
        HaSensorSnapshot(surplus_power_w=5200.0, valid=True),
    ) == 3


def test_pv_phase_switching_does_not_request_switch_outside_pv_mode():
    controller = make_controller(pv_phase_switching_mode=PvPhaseSwitchingMode.AUTOMATIC_1P3P)
    wallbox = WallboxState(installed_phases=1, phase_switch_mode_raw=0)
    sensors = HaSensorSnapshot(surplus_power_w=6000.0, valid=True)

    assert controller.resolve_pv_phase_target(ChargeMode.NORMAL, wallbox, sensors) is None


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


def test_normal_mode_loads_to_user_limit_but_is_still_limited_by_dlb():
    controller = make_controller(
        dlb_input_model="phase_currents",
        max_current_a=20.0,
        user_limit_a=16.0,
        dlb_sensor_scope="load_excluding_charger",
    )
    wallbox = WallboxState(installed_phases=1, vehicle_connected=True)
    sensors = HaSensorSnapshot(phase_currents=PhaseCurrents(l1=17.0), valid=True)

    decision = controller.evaluate(ChargeMode.NORMAL, wallbox, sensors)

    assert decision.reason == ControlReason.DLB_LIMITED
    assert decision.dominant_limit_reason == ControlReason.DLB_LIMITED
    assert decision.final_target_a == 6.0
