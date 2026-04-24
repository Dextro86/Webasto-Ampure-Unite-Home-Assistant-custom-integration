from custom_components.webasto_unite.registers import (
    CHARGE_POINT_ID,
    CHARGE_POINT_STATE,
    CURRENT_L1_A,
    ENERGY_METER_KWH,
    FIRMWARE_VERSION,
    MAX_CURRENT_CABLE_A,
    NUMBER_OF_PHASES,
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
from custom_components.webasto_unite.models import ChargingState, PhaseCurrents, WallboxState
from custom_components.webasto_unite.sensor import WebastoSensor
from custom_components.webasto_unite.wallbox_reader import WallboxReader


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
def test_voltage_and_session_time_registers_are_available():
    assert VOLTAGE_L1_V.register_type == RegisterType.INPUT
    assert VOLTAGE_L1_V.value_type == ValueType.UINT16
    assert SESSION_START_TIME.count == 2
    assert SESSION_DURATION_S.value_type == ValueType.UINT32
    assert SESSION_END_TIME.count == 2


def test_charge_current_register_is_readable_and_writable():
    assert SET_CHARGE_CURRENT_A.readable is True
    assert SET_CHARGE_CURRENT_A.writable is True


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


def test_human_readable_charge_point_state_mapping_uses_conservative_labels():
    assert WebastoSensor._format_charge_point_state(0) == "No Vehicle"
    assert WebastoSensor._format_charge_point_state(1) == "Preparing"
    assert WebastoSensor._format_charge_point_state(2) == "Charging"
    assert WebastoSensor._format_charge_point_state(3) == "Charging"
    assert WebastoSensor._format_charge_point_state(4) == "Paused"
    assert WebastoSensor._format_charge_point_state(7) == "Error"
    assert WebastoSensor._format_charge_point_state(8) == "Reserved"
    assert WebastoSensor._format_charge_point_state(99) == "Unknown (99)"


def test_human_readable_charge_state_mapping_uses_known_values():
    assert WebastoSensor._format_charge_state(0) == "Idle"
    assert WebastoSensor._format_charge_state(1) == "Charging"
    assert WebastoSensor._format_charge_state(5) == "Unknown (5)"


def test_human_readable_equipment_and_cable_state_mappings_use_fallback_for_unknown():
    assert WebastoSensor._format_equipment_state(0) == "Starting"
    assert WebastoSensor._format_equipment_state(1) == "Running"
    assert WebastoSensor._format_equipment_state(2) == "Error"
    assert WebastoSensor._format_equipment_state(9) == "Unknown (9)"

    assert WebastoSensor._format_cable_state(0) == "No Cable"
    assert WebastoSensor._format_cable_state(1) == "Cable Attached"
    assert WebastoSensor._format_cable_state(2) == "Vehicle Connected"
    assert WebastoSensor._format_cable_state(3) == "Vehicle Connected Locked"
    assert WebastoSensor._format_cable_state(9) == "Unknown (9)"
