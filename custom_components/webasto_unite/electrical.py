from __future__ import annotations

NOMINAL_PHASE_VOLTAGE_V = 230.0
MIN_PLAUSIBLE_PHASE_VOLTAGE_V = 180.0
MAX_PLAUSIBLE_PHASE_VOLTAGE_V = 260.0


def normalized_phase_voltage(value: float | None) -> float:
    if value is None:
        return NOMINAL_PHASE_VOLTAGE_V
    if MIN_PLAUSIBLE_PHASE_VOLTAGE_V <= value <= MAX_PLAUSIBLE_PHASE_VOLTAGE_V:
        return value
    return NOMINAL_PHASE_VOLTAGE_V


def voltage_sum_for_phases(
    phases: int,
    voltage_l1_v: float | None = None,
    voltage_l2_v: float | None = None,
    voltage_l3_v: float | None = None,
) -> float:
    if phases == 1:
        return normalized_phase_voltage(voltage_l1_v)
    return (
        normalized_phase_voltage(voltage_l1_v)
        + normalized_phase_voltage(voltage_l2_v)
        + normalized_phase_voltage(voltage_l3_v)
    )
