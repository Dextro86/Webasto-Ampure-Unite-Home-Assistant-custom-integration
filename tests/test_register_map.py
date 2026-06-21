import importlib
import pkgutil

import custom_components.webasto_unite as webasto_unite_package
from custom_components.webasto_unite.registers import (
    CHARGE_POINT_ID,
    CHARGE_POINT_STATE,
    COMM_TIMEOUT_S,
    CURRENT_L1_A,
    ENERGY_METER_KWH,
    FIRMWARE_VERSION,
    MAX_CURRENT_CABLE_A,
    NUMBER_OF_PHASES,
    PHASE_SWITCH_MODE,
    READ_REGISTERS,
    SERIAL_NUMBER,
    SESSION_DURATION_S,
    SESSION_END_TIME,
    SESSION_ENERGY_KWH,
    SESSION_START_TIME,
    SET_CHARGE_CURRENT_A,
    VOLTAGE_L1_V,
    WRITE_REGISTERS,
    RegisterType,
    ValueType,
)
from custom_components.webasto_unite.const import PHASE_SWITCHING_MODE_MANUAL_ONLY
from custom_components.webasto_unite.models import ChargeMode, ChargingState, ControlDecision, ControlReason, PhaseCurrents, SolarControlStrategy, WallboxState
from custom_components.webasto_unite.features.phase_observer import (
    PHASE_SWITCH_VALUE_1P,
    PHASE_SWITCH_VALUE_3P,
    build_phase_consistency,
    build_phase_offer_state,
    build_phase_observability,
    detect_observed_session_phase_usage,
    interpret_phase_switch_mode,
)
from custom_components.webasto_unite.features.phase_policy import evaluate_phase_policy
from custom_components.webasto_unite.sensor import SENSORS, WebastoSensor
from custom_components.webasto_unite.wallbox_reader import WallboxReader


def test_all_integration_modules_import_and_wallbox_reader_instantiates():
    for module_info in pkgutil.iter_modules(webasto_unite_package.__path__):
        if module_info.ispkg:
            continue
        importlib.import_module(f"{webasto_unite_package.__name__}.{module_info.name}")

    client = object()
    reader = WallboxReader(client)

    assert reader.client is client


def test_energy_and_measurement_sensors_expose_statistics_metadata():
    sensors = {description.key: description for description in SENSORS}

    assert sensors["energy_meter"].device_class == "energy"
    assert sensors["energy_meter"].state_class == "total_increasing"
    assert sensors["session_energy"].device_class == "energy"
    assert sensors["session_energy"].state_class == "total"
    assert sensors["active_power"].device_class == "power"
    assert sensors["active_power"].state_class == "measurement"
    assert sensors["current_l1"].device_class == "current"
    assert sensors["current_l1"].state_class == "measurement"
    assert sensors["voltage_l1"].device_class == "voltage"
    assert sensors["voltage_l1"].state_class == "measurement"


def test_runtime_measurement_registers_use_input_registers():
    assert CHARGE_POINT_STATE.register_type == RegisterType.INPUT
    assert CURRENT_L1_A.register_type == RegisterType.INPUT
    assert MAX_CURRENT_CABLE_A.register_type == RegisterType.INPUT


def test_session_energy_register_matches_confirmed_unite_mapping():
    assert SESSION_ENERGY_KWH.register_type == RegisterType.INPUT
    assert SESSION_ENERGY_KWH.value_type == ValueType.UINT32
    assert SESSION_ENERGY_KWH.count == 2
    assert SESSION_ENERGY_KWH.scale == 0.001


def test_energy_meter_register_matches_official_unite_pdf():
    assert ENERGY_METER_KWH.register_type == RegisterType.INPUT
    assert ENERGY_METER_KWH.value_type == ValueType.UINT32
    assert ENERGY_METER_KWH.count == 2
    assert ENERGY_METER_KWH.scale == 0.1


def test_identity_and_phase_registers_match_official_unite_pdf():
    assert SERIAL_NUMBER.register_type == RegisterType.INPUT
    assert SERIAL_NUMBER.value_type == ValueType.STRING
    assert SERIAL_NUMBER.count == 25
    assert CHARGE_POINT_ID.count == 50
    assert FIRMWARE_VERSION.count == 50
    assert NUMBER_OF_PHASES.register_type == RegisterType.INPUT


def test_phase_switch_register_mapping_uses_known_webasto_values():
    assert PHASE_SWITCH_MODE.address == 405
    assert PHASE_SWITCH_MODE.register_type == RegisterType.HOLDING
    assert PHASE_SWITCH_MODE.readable is True
    assert PHASE_SWITCH_MODE.writable is True
    assert PHASE_SWITCH_VALUE_1P == 0
    assert PHASE_SWITCH_VALUE_3P == 1


def test_phase_switch_mode_interpretation_uses_known_webasto_values():
    assert interpret_phase_switch_mode(0) == "1P"
    assert interpret_phase_switch_mode(1) == "3P"
    assert interpret_phase_switch_mode(None) is None
    assert interpret_phase_switch_mode(9) == "Unknown"


def test_phase_observer_reports_manual_switch_availability():
    wallbox = WallboxState(
        installed_phases=3,
        charge_point_phase_count=3,
        vehicle_connected=True,
        phase_switch_mode_raw=1,
        charging_active=True,
        phases_in_use=3,
    )

    state = build_phase_observability(wallbox)

    assert state.phase_switch_mode == "3P"
    assert state.phase_switch_register_available is True
    assert state.phase_switch_available is True
    assert state.phase_switch_block_reason is None
    assert state.observed_session_phase_usage == "observed_3p"
    assert state.write_register_address == 405


def test_phase_observer_blocks_when_register_is_unavailable():
    wallbox = WallboxState(installed_phases=3, charge_point_phase_count=3, vehicle_connected=True, phase_switch_mode_raw=None)

    state = build_phase_observability(wallbox)

    assert state.phase_switch_available is False
    assert state.phase_switch_block_reason == "phase_switch_register_unavailable"


def test_phase_observer_treats_register_404_as_diagnostic_only():
    wallbox = WallboxState(installed_phases=3, charge_point_phase_count=1, vehicle_connected=True, phase_switch_mode_raw=1)

    state = build_phase_observability(wallbox)

    assert state.phase_switch_available is True
    assert state.phase_switch_block_reason is None


def test_observed_session_phase_usage_is_observed_only():
    assert detect_observed_session_phase_usage(WallboxState(vehicle_connected=False)) == "unknown"
    assert detect_observed_session_phase_usage(
        WallboxState(vehicle_connected=True, charging_active=True, phases_in_use=1)
    ) == "observed_1p"
    assert detect_observed_session_phase_usage(
        WallboxState(vehicle_connected=True, charging_active=True, phases_in_use=3)
    ) == "observed_3p"


def test_phase_consistency_reports_register_physical_mismatches_without_correcting():
    assert build_phase_consistency(WallboxState(phase_switch_mode_raw=None)) == "unknown"
    assert build_phase_consistency(WallboxState(phase_switch_mode_raw=1, charging_active=False)) == "not_charging"
    assert (
        build_phase_consistency(WallboxState(phase_switch_mode_raw=1, charging_active=True, phases_in_use=3))
        == "register_and_physical_match"
    )
    assert (
        build_phase_consistency(WallboxState(phase_switch_mode_raw=1, charging_active=True, phases_in_use=1))
        == "register_3p_physical_1p"
    )
    assert (
        build_phase_consistency(WallboxState(phase_switch_mode_raw=0, charging_active=True, phases_in_use=3))
        == "register_1p_physical_3p"
    )


def test_phase_offer_state_reports_requested_vs_observed_without_vehicle_claim():
    assert build_phase_offer_state(WallboxState(phase_switch_mode_raw=None)) == "unknown"
    assert build_phase_offer_state(WallboxState(phase_switch_mode_raw=1, charging_active=False)) == "not_charging"
    assert (
        build_phase_offer_state(WallboxState(phase_switch_mode_raw=1, charging_active=True, phases_in_use=3))
        == "offering_3p"
    )
    assert (
        build_phase_offer_state(WallboxState(phase_switch_mode_raw=0, charging_active=True, phases_in_use=1))
        == "offering_1p"
    )
    assert (
        build_phase_offer_state(WallboxState(phase_switch_mode_raw=1, charging_active=True, phases_in_use=1))
        == "requested_3p_observed_1p"
    )


def test_phase_switch_diagnostic_sensors_are_exposed():
    sensors = {description.key: description for description in SENSORS}

    assert sensors["phase_requested"].name == "Requested Phase"
    assert sensors["phase_requested"].entity_category == "diagnostic"
    assert sensors["phase_observed"].name == "Observed Phase"
    assert sensors["phase_observed"].entity_category == "diagnostic"
    assert sensors["phase_recovery_state"].name == "Phase Recovery State"
    assert sensors["phase_recovery_state"].entity_category == "diagnostic"
    assert "phase_switch_mode" not in sensors
    assert "phase_policy_target" not in sensors
    assert "phase_session_target" not in sensors
    assert "phase_offer_state" not in sensors
    assert sensors["control_writes_enabled"].entity_category == "diagnostic"
    assert sensors["last_control_write_reason"].entity_category == "diagnostic"
    assert sensors["last_control_write_blocked_reason"].entity_category == "diagnostic"


def test_technical_measurements_are_diagnostic_to_keep_default_ui_small():
    sensors = {description.key: description for description in SENSORS}

    for key in (
        "active_power_l1",
        "active_power_l2",
        "active_power_l3",
        "current_l1",
        "current_l2",
        "current_l3",
        "voltage_l1",
        "voltage_l2",
        "voltage_l3",
        "safe_current",
        "session_max_current",
        "dlb_limit",
        "final_target",
    ):
        assert sensors[key].entity_category == "diagnostic"

    assert sensors["active_power"].entity_category is None
    assert sensors["actual_current"].entity_category is None
    assert sensors["configured_limit"].entity_category is None
    assert sensors["session_energy"].entity_category is None
    assert sensors["energy_meter"].entity_category is None


def test_phase_policy_would_request_1p_when_surplus_supports_1p_but_not_3p():
    decision = evaluate_phase_policy(
        effective_mode=ChargeMode.SOLAR,
        solar_strategy=SolarControlStrategy.ECO_SOLAR,
        phase_switching_mode=PHASE_SWITCHING_MODE_MANUAL_ONLY,
        configured_installed_phases="3p",
        wallbox=WallboxState(
            installed_phases=3,
            charge_point_phase_count=3,
            vehicle_connected=True,
            phase_switch_mode_raw=1,
            voltage_l1_v=230.0,
            voltage_l2_v=230.0,
            voltage_l3_v=230.0,
        ),
        control_decision=ControlDecision(
            charging_enabled=True,
            target_current_a=6.0,
            reason=ControlReason.SOLAR_MODE,
            final_target_a=6.0,
        ),
        solar_input_state="ready",
        filtered_surplus_w=1600.0,
        phase_restore_pending=False,
        solar_min_current_a=6.0,
        session_observed_3p=False,
    )

    assert decision.decision == "would_request_1p"
    assert decision.target == "1P"
    assert decision.required_surplus_1p_w == 1380.0
    assert decision.required_surplus_3p_w == 4440.0


def test_phase_policy_eco_solar_does_not_request_1p_below_1p_minimum():
    decision = evaluate_phase_policy(
        effective_mode=ChargeMode.SOLAR,
        solar_strategy=SolarControlStrategy.ECO_SOLAR,
        phase_switching_mode=PHASE_SWITCHING_MODE_MANUAL_ONLY,
        configured_installed_phases="3p",
        wallbox=WallboxState(
            installed_phases=3,
            charge_point_phase_count=3,
            vehicle_connected=True,
            phase_switch_mode_raw=1,
            voltage_l1_v=230.0,
            voltage_l2_v=230.0,
            voltage_l3_v=230.0,
        ),
        control_decision=ControlDecision(
            charging_enabled=True,
            target_current_a=6.0,
            reason=ControlReason.SOLAR_MODE,
            final_target_a=6.0,
        ),
        solar_input_state="ready",
        filtered_surplus_w=600.0,
        phase_restore_pending=False,
        solar_min_current_a=6.0,
        session_observed_3p=False,
    )

    assert decision.decision == "no_action"
    assert decision.target is None


def test_phase_policy_smart_solar_requests_1p_below_1p_minimum():
    decision = evaluate_phase_policy(
        effective_mode=ChargeMode.SOLAR,
        solar_strategy=SolarControlStrategy.SMART_SOLAR,
        phase_switching_mode=PHASE_SWITCHING_MODE_MANUAL_ONLY,
        configured_installed_phases="3p",
        wallbox=WallboxState(
            installed_phases=3,
            charge_point_phase_count=3,
            vehicle_connected=True,
            phase_switch_mode_raw=1,
            voltage_l1_v=230.0,
            voltage_l2_v=230.0,
            voltage_l3_v=230.0,
        ),
        control_decision=ControlDecision(
            charging_enabled=True,
            target_current_a=6.0,
            reason=ControlReason.SOLAR_MODE,
            final_target_a=6.0,
        ),
        solar_input_state="ready",
        filtered_surplus_w=600.0,
        phase_restore_pending=False,
        solar_min_current_a=6.0,
        session_observed_3p=False,
    )

    assert decision.decision == "would_request_1p"
    assert decision.target == "1P"


def test_phase_policy_uses_observed_active_phases_over_register_405_during_charging():
    decision = evaluate_phase_policy(
        effective_mode=ChargeMode.SOLAR,
        solar_strategy=SolarControlStrategy.SMART_SOLAR,
        phase_switching_mode=PHASE_SWITCHING_MODE_MANUAL_ONLY,
        configured_installed_phases="3p",
        wallbox=WallboxState(
            installed_phases=3,
            charge_point_phase_count=1,
            vehicle_connected=True,
            charging_active=True,
            phases_in_use=3,
            phase_switch_mode_raw=0,
            voltage_l1_v=230.0,
            voltage_l2_v=230.0,
            voltage_l3_v=230.0,
        ),
        control_decision=ControlDecision(
            charging_enabled=True,
            target_current_a=6.0,
            reason=ControlReason.SOLAR_MODE,
            final_target_a=6.0,
        ),
        solar_input_state="ready",
        filtered_surplus_w=600.0,
        phase_restore_pending=False,
        solar_min_current_a=6.0,
        session_observed_3p=False,
    )

    assert decision.decision == "would_request_1p"
    assert decision.target == "1P"


def test_phase_policy_would_request_3p_when_surplus_supports_3p():
    decision = evaluate_phase_policy(
        effective_mode=ChargeMode.SOLAR,
        solar_strategy=SolarControlStrategy.ECO_SOLAR,
        phase_switching_mode=PHASE_SWITCHING_MODE_MANUAL_ONLY,
        configured_installed_phases="3p",
        wallbox=WallboxState(
            installed_phases=3,
            charge_point_phase_count=3,
            vehicle_connected=True,
            phase_switch_mode_raw=0,
            voltage_l1_v=230.0,
            voltage_l2_v=230.0,
            voltage_l3_v=230.0,
        ),
        control_decision=ControlDecision(
            charging_enabled=True,
            target_current_a=6.0,
            reason=ControlReason.SOLAR_MODE,
            final_target_a=6.0,
        ),
        solar_input_state="ready",
        filtered_surplus_w=4500.0,
        phase_restore_pending=False,
        solar_min_current_a=6.0,
        session_observed_3p=True,
    )

    assert decision.decision == "would_request_3p"
    assert decision.target == "3P"


def test_phase_policy_allows_3p_request_before_3p_was_observed_in_session():
    decision = evaluate_phase_policy(
        effective_mode=ChargeMode.SOLAR,
        solar_strategy=SolarControlStrategy.ECO_SOLAR,
        phase_switching_mode=PHASE_SWITCHING_MODE_MANUAL_ONLY,
        configured_installed_phases="3p",
        wallbox=WallboxState(
            installed_phases=3,
            charge_point_phase_count=3,
            vehicle_connected=True,
            phase_switch_mode_raw=0,
            voltage_l1_v=230.0,
            voltage_l2_v=230.0,
            voltage_l3_v=230.0,
        ),
        control_decision=ControlDecision(
            charging_enabled=True,
            target_current_a=6.0,
            reason=ControlReason.SOLAR_MODE,
            final_target_a=6.0,
        ),
        solar_input_state="ready",
        filtered_surplus_w=4500.0,
        phase_restore_pending=False,
        solar_min_current_a=6.0,
        session_observed_3p=False,
    )

    assert decision.decision == "would_request_3p"
    assert decision.target == "3P"


def test_phase_policy_thresholds_use_solar_min_current_not_current_target():
    decision = evaluate_phase_policy(
        effective_mode=ChargeMode.SOLAR,
        solar_strategy=SolarControlStrategy.SOLAR_BOOST,
        phase_switching_mode=PHASE_SWITCHING_MODE_MANUAL_ONLY,
        configured_installed_phases="3p",
        wallbox=WallboxState(
            installed_phases=3,
            charge_point_phase_count=3,
            vehicle_connected=True,
            phase_switch_mode_raw=0,
            voltage_l1_v=230.0,
            voltage_l2_v=230.0,
            voltage_l3_v=230.0,
        ),
        control_decision=ControlDecision(
            charging_enabled=True,
            target_current_a=16.0,
            reason=ControlReason.SOLAR_MODE,
            final_target_a=16.0,
        ),
        solar_input_state="ready",
        filtered_surplus_w=4500.0,
        phase_restore_pending=False,
        solar_min_current_a=6.0,
        session_observed_3p=True,
    )

    assert decision.decision == "would_request_3p"
    assert decision.required_surplus_1p_w == 1380.0
    assert decision.required_surplus_3p_w == 4440.0


def test_phase_policy_blocks_when_dlb_is_limiting():
    decision = evaluate_phase_policy(
        effective_mode=ChargeMode.SOLAR,
        solar_strategy=SolarControlStrategy.ECO_SOLAR,
        phase_switching_mode=PHASE_SWITCHING_MODE_MANUAL_ONLY,
        configured_installed_phases="3p",
        wallbox=WallboxState(
            installed_phases=3,
            charge_point_phase_count=3,
            vehicle_connected=True,
            phase_switch_mode_raw=1,
        ),
        control_decision=ControlDecision(
            charging_enabled=True,
            target_current_a=6.0,
            reason=ControlReason.DLB_LIMITED,
            dominant_limit_reason=ControlReason.DLB_LIMITED,
            final_target_a=6.0,
        ),
        solar_input_state="ready",
        filtered_surplus_w=4500.0,
        phase_restore_pending=False,
        solar_min_current_a=6.0,
        session_observed_3p=False,
    )

    assert decision.decision == "blocked"
    assert decision.block_reason == "dlb_limited"


def test_voltage_and_session_time_registers_are_available():
    assert VOLTAGE_L1_V.register_type == RegisterType.INPUT
    assert VOLTAGE_L1_V.value_type == ValueType.UINT16
    assert SESSION_START_TIME.count == 2
    assert SESSION_DURATION_S.value_type == ValueType.UINT32
    assert SESSION_END_TIME.count == 2


def test_charge_current_register_is_readable_and_writable():
    assert SET_CHARGE_CURRENT_A.readable is True
    assert SET_CHARGE_CURRENT_A.writable is True


def test_comm_timeout_register_is_read_only_for_integration():
    assert COMM_TIMEOUT_S.readable is True
    assert COMM_TIMEOUT_S.writable is False
    assert COMM_TIMEOUT_S not in WRITE_REGISTERS


def test_charge_point_state_mapping_matches_unite_status_codes():
    assert WallboxReader.map_charging_state(0) == ChargingState.IDLE
    assert WallboxReader.map_charging_state(1) == ChargingState.PREPARING
    assert WallboxReader.map_charging_state(2) == ChargingState.CHARGING
    assert WallboxReader.map_charging_state(3) == ChargingState.SUSPENDED
    assert WallboxReader.map_charging_state(4) == ChargingState.SUSPENDED
    assert WallboxReader.map_charging_state(5) == ChargingState.IDLE
    assert WallboxReader.map_charging_state(6) == ChargingState.RESERVED
    assert WallboxReader.map_charging_state(7) == ChargingState.ERROR
    assert WallboxReader.map_charging_state(8) == ChargingState.ERROR


def test_charging_active_uses_charging_state_register_with_measurement_fallback():
    wallbox = WallboxState(charge_state_raw=1)
    wallbox.update_charging_active()
    assert wallbox.charging_active is True

    wallbox = WallboxState(charge_state_raw=0, phase_currents=PhaseCurrents(l1=0.6))
    wallbox.update_charging_active()
    assert wallbox.charging_active is True

    wallbox = WallboxState(charge_state_raw=0, active_power_w=0.0, phase_currents=PhaseCurrents(l1=0.0))
    wallbox.update_charging_active()
    assert wallbox.charging_active is False


def test_implausible_active_power_is_zero_when_vehicle_disconnected():
    assert (
        WallboxReader._normalize_active_power_w(
            4_294_967_295,
            vehicle_connected=False,
            register_name="active_power",
        )
        == 0.0
    )


def test_implausible_active_power_is_unavailable_when_vehicle_connected():
    assert (
        WallboxReader._normalize_active_power_w(
            4_294_967_295,
            vehicle_connected=True,
            register_name="active_power",
        )
        is None
    )


def test_plausible_active_power_is_kept():
    assert (
        WallboxReader._normalize_active_power_w(
            11_000,
            vehicle_connected=True,
            register_name="active_power",
        )
        == 11_000.0
    )


def test_human_readable_charge_point_state_mapping_uses_conservative_labels():
    assert WebastoSensor._format_charge_point_state(0) == "Available"
    assert WebastoSensor._format_charge_point_state(1) == "Preparing"
    assert WebastoSensor._format_charge_point_state(2) == "Charging"
    assert WebastoSensor._format_charge_point_state(3) == "SuspendedEVSE"
    assert WebastoSensor._format_charge_point_state(4) == "SuspendedEV"
    assert WebastoSensor._format_charge_point_state(5) == "Finishing"
    assert WebastoSensor._format_charge_point_state(6) == "Reserved"
    assert WebastoSensor._format_charge_point_state(7) == "Unavailable"
    assert WebastoSensor._format_charge_point_state(8) == "Faulted"
    assert WebastoSensor._format_charge_point_state(99) == "Unknown (99)"


def test_human_readable_charge_state_mapping_uses_known_values():
    assert WebastoSensor._format_charge_state(0) == "Idle"
    assert WebastoSensor._format_charge_state(1) == "Charging"
    assert WebastoSensor._format_charge_state(5) == "Unknown (5)"


def test_human_readable_equipment_and_cable_state_mappings_use_fallback_for_unknown():
    assert WebastoSensor._format_equipment_state(0) == "Initializing"
    assert WebastoSensor._format_equipment_state(1) == "Running"
    assert WebastoSensor._format_equipment_state(2) == "Fault"
    assert WebastoSensor._format_equipment_state(3) == "Disabled"
    assert WebastoSensor._format_equipment_state(4) == "Updating"
    assert WebastoSensor._format_equipment_state(9) == "Unknown (9)"

    assert WebastoSensor._format_cable_state(0) == "Cable Not Connected"
    assert WebastoSensor._format_cable_state(1) == "Cable Connected, Vehicle Not Connected"
    assert WebastoSensor._format_cable_state(2) == "Cable Connected, Vehicle Connected"
    assert WebastoSensor._format_cable_state(3) == "Cable Connected, Vehicle Connected, Cable Locked"
    assert WebastoSensor._format_cable_state(9) == "Unknown (9)"


def test_iec61851_state_is_derived_conservatively():
    assert WebastoSensor._derive_iec61851_state(
        WallboxState(vehicle_connected=False, charging_active=False)
    ) == "A"
    assert WebastoSensor._derive_iec61851_state(
        WallboxState(vehicle_connected=True, charging_active=False)
    ) == "B"
    assert WebastoSensor._derive_iec61851_state(
        WallboxState(vehicle_connected=True, charge_state_raw=1)
    ) == "C"
    assert WebastoSensor._derive_iec61851_state(
        WallboxState(vehicle_connected=True, charging_active=True)
    ) == "C"
    assert WebastoSensor._derive_iec61851_state(
        WallboxState(vehicle_connected=True, charge_point_state_raw=8)
    ) == "E"
    assert WebastoSensor._derive_iec61851_state(
        WallboxState(vehicle_connected=True, evse_state_raw=2)
    ) == "E"
    assert WebastoSensor._derive_iec61851_state(
        WallboxState(vehicle_connected=True, charge_point_state_raw=7)
    ) == "F"
