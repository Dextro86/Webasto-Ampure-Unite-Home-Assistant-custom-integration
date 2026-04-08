from custom_components.webasto_unite.registers import (
    CHARGE_POINT_ID,
    CHARGE_POINT_STATE,
    CURRENT_L1_A,
    ENERGY_METER_KWH,
    FIRMWARE_VERSION,
    MAX_CURRENT_CABLE_A,
    NUMBER_OF_PHASES,
    PHASE_SWITCH_MODE,
    SERIAL_NUMBER,
    SESSION_DURATION_S,
    SESSION_END_TIME,
    SESSION_ENERGY_KWH,
    SESSION_START_TIME,
    SET_CHARGE_CURRENT_A,
    VOLTAGE_L1_V,
    RegisterType,
    ValueType,
)


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


def test_phase_switch_register_is_tracked_as_unconfirmed_candidate():
    assert PHASE_SWITCH_MODE.address == 405
    assert PHASE_SWITCH_MODE.register_type == RegisterType.HOLDING
    assert PHASE_SWITCH_MODE.value_type == ValueType.UINT16
    assert PHASE_SWITCH_MODE.writable is True
    assert PHASE_SWITCH_MODE.readable is True


def test_voltage_and_session_time_registers_are_available():
    assert VOLTAGE_L1_V.register_type == RegisterType.INPUT
    assert VOLTAGE_L1_V.value_type == ValueType.UINT16
    assert SESSION_START_TIME.count == 2
    assert SESSION_DURATION_S.value_type == ValueType.UINT32
    assert SESSION_END_TIME.count == 2


def test_charge_current_register_is_readable_and_writable():
    assert SET_CHARGE_CURRENT_A.readable is True
    assert SET_CHARGE_CURRENT_A.writable is True
