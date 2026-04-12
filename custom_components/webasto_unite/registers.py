
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RegisterType(str, Enum):
    HOLDING = "holding"
    INPUT = "input"


class ValueType(str, Enum):
    BOOL = "bool"
    UINT16 = "uint16"
    INT16 = "int16"
    UINT32 = "uint32"
    INT32 = "int32"
    STRING = "string"


@dataclass(frozen=True, slots=True)
class RegisterDef:
    name: str
    address: int
    count: int = 1
    register_type: RegisterType = RegisterType.HOLDING
    value_type: ValueType = ValueType.UINT16
    scale: float = 1.0
    writable: bool = False
    readable: bool = True


# Webasto Unite field map validated against the official "Modbus Specification
# Webasto UNITE" draft (revision 1.00, 28.06.2022), a working Home Assistant
# Modbus setup, and community reports. Where those differ, the official PDF and
# the known-good HA mapping take precedence over generic NEXT-style assumptions.
SERIAL_NUMBER = RegisterDef("serial_number", 100, count=25, register_type=RegisterType.INPUT, value_type=ValueType.STRING)
CHARGE_POINT_ID = RegisterDef("charge_point_id", 130, count=50, register_type=RegisterType.INPUT, value_type=ValueType.STRING)
BRAND = RegisterDef("brand", 190, count=10, register_type=RegisterType.INPUT, value_type=ValueType.STRING)
MODEL = RegisterDef("model", 210, count=5, register_type=RegisterType.INPUT, value_type=ValueType.STRING)
FIRMWARE_VERSION = RegisterDef("firmware_version", 230, count=50, register_type=RegisterType.INPUT, value_type=ValueType.STRING)

CHARGE_POINT_POWER_W = RegisterDef("charge_point_power_w", 400, count=2, register_type=RegisterType.INPUT, value_type=ValueType.UINT32)
NUMBER_OF_PHASES = RegisterDef("number_of_phases", 404, register_type=RegisterType.INPUT, value_type=ValueType.UINT16)
# Community Vestel/Webasto mappings consistently point to holding register 405
# as the manual 1p/3p phase switch control path. It is tracked here as an
# unconfirmed future feature candidate and is exposed read-only for discovery
# before any write control is added.
PHASE_SWITCH_MODE = RegisterDef("phase_switch_mode", 405, value_type=ValueType.UINT16, writable=True, readable=True)

CHARGE_POINT_STATE = RegisterDef("charge_point_state", 1000, register_type=RegisterType.INPUT)
CHARGE_STATE = RegisterDef("charge_state", 1001, register_type=RegisterType.INPUT)
EVSE_STATE = RegisterDef("evse_state", 1002, register_type=RegisterType.INPUT)
CABLE_STATE = RegisterDef("cable_state", 1004, register_type=RegisterType.INPUT)
ERROR_CODE = RegisterDef("error_code", 1006, register_type=RegisterType.INPUT)

CURRENT_L1_A = RegisterDef("current_l1_a", 1008, register_type=RegisterType.INPUT, value_type=ValueType.UINT16, scale=0.001)
CURRENT_L2_A = RegisterDef("current_l2_a", 1010, register_type=RegisterType.INPUT, value_type=ValueType.UINT16, scale=0.001)
CURRENT_L3_A = RegisterDef("current_l3_a", 1012, register_type=RegisterType.INPUT, value_type=ValueType.UINT16, scale=0.001)
VOLTAGE_L1_V = RegisterDef("voltage_l1_v", 1014, register_type=RegisterType.INPUT, value_type=ValueType.UINT16)
VOLTAGE_L2_V = RegisterDef("voltage_l2_v", 1016, register_type=RegisterType.INPUT, value_type=ValueType.UINT16)
VOLTAGE_L3_V = RegisterDef("voltage_l3_v", 1018, register_type=RegisterType.INPUT, value_type=ValueType.UINT16)

TOTAL_CHARGE_ACTIVE_POWER_W = RegisterDef("total_charge_active_power_w", 1020, count=2, register_type=RegisterType.INPUT, value_type=ValueType.UINT32)
ACTIVE_POWER_L1_W = RegisterDef("active_power_l1_w", 1024, count=2, register_type=RegisterType.INPUT, value_type=ValueType.UINT32)
ACTIVE_POWER_L2_W = RegisterDef("active_power_l2_w", 1028, count=2, register_type=RegisterType.INPUT, value_type=ValueType.UINT32)
ACTIVE_POWER_L3_W = RegisterDef("active_power_l3_w", 1032, count=2, register_type=RegisterType.INPUT, value_type=ValueType.UINT32)
ENERGY_METER_KWH = RegisterDef("energy_meter_kwh", 1036, count=2, register_type=RegisterType.INPUT, value_type=ValueType.UINT32, scale=0.1)

SESSION_MAX_CURRENT_A = RegisterDef("session_max_current_a", 1100, register_type=RegisterType.INPUT, value_type=ValueType.UINT16)
MIN_CURRENT_HW_A = RegisterDef("min_current_hw_a", 1102, register_type=RegisterType.INPUT, value_type=ValueType.UINT16)
MAX_CURRENT_EVSE_A = RegisterDef("max_current_evse_a", 1104, register_type=RegisterType.INPUT, value_type=ValueType.UINT16)
MAX_CURRENT_CABLE_A = RegisterDef("max_current_cable_a", 1106, register_type=RegisterType.INPUT, value_type=ValueType.UINT16)
# 1108 has been seen in NEXT-style maps but is not yet confirmed by the user's
# current Unite screenshots.
MAX_CURRENT_EV_A = RegisterDef("max_current_ev_a", 1108, register_type=RegisterType.INPUT, value_type=ValueType.UINT16)

SESSION_ENERGY_KWH = RegisterDef("session_energy_kwh", 1502, count=2, register_type=RegisterType.INPUT, value_type=ValueType.UINT32, scale=0.001)
SESSION_START_TIME = RegisterDef("session_start_time", 1504, count=2, register_type=RegisterType.INPUT, value_type=ValueType.UINT32)
SESSION_DURATION_S = RegisterDef("session_duration_s", 1508, count=2, register_type=RegisterType.INPUT, value_type=ValueType.UINT32)
SESSION_END_TIME = RegisterDef("session_end_time", 1512, count=2, register_type=RegisterType.INPUT, value_type=ValueType.UINT32)

SAFE_CURRENT_A = RegisterDef("safe_current_a", 2000, value_type=ValueType.UINT16, writable=True)
COMM_TIMEOUT_S = RegisterDef("comm_timeout_s", 2002, value_type=ValueType.UINT16, writable=True)

SET_CHARGE_POWER_W = RegisterDef("set_charge_power_w", 5000, count=2, value_type=ValueType.UINT32, writable=True)
SET_CHARGE_CURRENT_A = RegisterDef("set_charge_current_a", 5004, value_type=ValueType.UINT16, writable=True, readable=True)

LIFE_BIT = RegisterDef("life_bit", 6000, value_type=ValueType.UINT16, writable=True)

PHASE_REGISTERS = (CURRENT_L1_A, CURRENT_L2_A, CURRENT_L3_A)


READ_REGISTERS = (
    SERIAL_NUMBER,
    CHARGE_POINT_ID,
    BRAND,
    MODEL,
    FIRMWARE_VERSION,
    CHARGE_POINT_POWER_W,
    NUMBER_OF_PHASES,
    PHASE_SWITCH_MODE,
    CHARGE_POINT_STATE,
    CHARGE_STATE,
    EVSE_STATE,
    CABLE_STATE,
    ERROR_CODE,
    CURRENT_L1_A,
    CURRENT_L2_A,
    CURRENT_L3_A,
    VOLTAGE_L1_V,
    VOLTAGE_L2_V,
    VOLTAGE_L3_V,
    TOTAL_CHARGE_ACTIVE_POWER_W,
    ACTIVE_POWER_L1_W,
    ACTIVE_POWER_L2_W,
    ACTIVE_POWER_L3_W,
    ENERGY_METER_KWH,
    SESSION_MAX_CURRENT_A,
    MIN_CURRENT_HW_A,
    MAX_CURRENT_EVSE_A,
    MAX_CURRENT_CABLE_A,
    MAX_CURRENT_EV_A,
    SAFE_CURRENT_A,
    COMM_TIMEOUT_S,
    SESSION_ENERGY_KWH,
    SESSION_START_TIME,
    SESSION_DURATION_S,
    SESSION_END_TIME,
    LIFE_BIT,
)

WRITE_REGISTERS = (
    SAFE_CURRENT_A,
    COMM_TIMEOUT_S,
    PHASE_SWITCH_MODE,
    SET_CHARGE_POWER_W,
    SET_CHARGE_CURRENT_A,
    LIFE_BIT,
)
