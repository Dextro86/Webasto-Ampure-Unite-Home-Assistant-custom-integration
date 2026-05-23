from __future__ import annotations

from dataclasses import dataclass

from .models import WallboxState
from .registers import PHASE_SWITCH_MODE


PHASE_SWITCH_VALUE_1P = 0
PHASE_SWITCH_VALUE_3P = 1


@dataclass(frozen=True, slots=True)
class PhaseObservability:
    phase_switch_mode_raw: int | None
    phase_switch_mode: str | None
    phase_switch_register_available: bool
    phase_switch_available: bool
    phase_switch_block_reason: str | None
    vehicle_phase_capability: str
    write_register_address: int
    write_value_1p: int
    write_value_3p: int


def build_phase_observability(wallbox: WallboxState) -> PhaseObservability:
    register_available = wallbox.phase_switch_mode_raw in (PHASE_SWITCH_VALUE_1P, PHASE_SWITCH_VALUE_3P)
    block_reason = _phase_switch_block_reason(wallbox, register_available)
    return PhaseObservability(
        phase_switch_mode_raw=wallbox.phase_switch_mode_raw,
        phase_switch_mode=interpret_phase_switch_mode(wallbox.phase_switch_mode_raw),
        phase_switch_register_available=register_available,
        phase_switch_available=block_reason is None,
        phase_switch_block_reason=block_reason,
        vehicle_phase_capability=detect_vehicle_phase_capability(wallbox),
        write_register_address=PHASE_SWITCH_MODE.address,
        write_value_1p=PHASE_SWITCH_VALUE_1P,
        write_value_3p=PHASE_SWITCH_VALUE_3P,
    )


def interpret_phase_switch_mode(raw_value: int | None) -> str | None:
    if raw_value == PHASE_SWITCH_VALUE_1P:
        return "1P"
    if raw_value == PHASE_SWITCH_VALUE_3P:
        return "3P"
    if raw_value is None:
        return None
    return "Unknown"


def detect_vehicle_phase_capability(wallbox: WallboxState) -> str:
    if not wallbox.vehicle_connected or not wallbox.charging_active:
        return "unknown"
    if wallbox.phases_in_use == 3:
        return "likely_3p"
    if wallbox.phases_in_use == 1:
        return "likely_1p"
    return "unknown"


def _phase_switch_block_reason(wallbox: WallboxState, register_available: bool) -> str | None:
    if wallbox.installed_phases != 3:
        return "charger_not_configured_3p"
    if not register_available:
        return "phase_switch_register_unavailable"
    if not wallbox.vehicle_connected:
        return "vehicle_not_connected"
    return None
