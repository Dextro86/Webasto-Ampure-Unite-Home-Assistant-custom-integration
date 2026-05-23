from __future__ import annotations

from dataclasses import dataclass

from .const import PHASE_SWITCHING_MODE_MANUAL_ONLY
from .models import ControlConfig, ControlMode, WallboxState
from .phase_observer import (
    PHASE_SWITCH_VALUE_1P,
    PHASE_SWITCH_VALUE_3P,
    build_phase_observability,
)


PHASE_SWITCH_PAUSE_BEFORE_S = 10.0
PHASE_SWITCH_PAUSE_AFTER_S = 20.0


@dataclass(frozen=True, slots=True)
class PhaseSwitchPlan:
    target_phases: int
    write_value: int
    was_charging: bool
    resume_current_a: float | None


@dataclass(frozen=True, slots=True)
class PhaseSwitchDecision:
    allowed: bool
    plan: PhaseSwitchPlan | None = None
    block_reason: str | None = None


def build_manual_phase_switch_decision(
    *,
    phase_switching_mode: str,
    wallbox: WallboxState | None,
    target_phases: int,
    config: ControlConfig,
) -> PhaseSwitchDecision:
    """Validate an explicit manual phase-switch request.

    This module only decides whether a manual request is allowed and what value
    should be written. The coordinator owns the actual Modbus writes.
    """
    if phase_switching_mode != PHASE_SWITCHING_MODE_MANUAL_ONLY:
        return _blocked("manual_phase_switching_disabled")
    if config.control_mode != ControlMode.MANAGED_CONTROL:
        return _blocked("integration_control_disabled")
    if target_phases not in (1, 3):
        return _blocked("invalid_target_phase")
    if wallbox is None:
        return _blocked("charger_state_unavailable")
    if not wallbox.available:
        return _blocked("charger_unavailable")

    observability = build_phase_observability(wallbox)
    if observability.phase_switch_block_reason is not None:
        return _blocked(observability.phase_switch_block_reason)
    if target_phases == 3 and observability.vehicle_phase_capability == "likely_1p":
        return _blocked("vehicle_likely_1p")
    if observability.phase_switch_mode == f"{target_phases}P":
        return _blocked("already_in_target_phase")

    resume_current_a = None
    was_charging = bool(wallbox.charging_active)
    if was_charging:
        reported_limit = wallbox.current_limit_a
        if reported_limit is not None and config.min_current_a <= reported_limit <= config.max_current_a:
            resume_current_a = float(round(reported_limit))
        else:
            resume_current_a = float(round(config.min_current_a))

    return PhaseSwitchDecision(
        allowed=True,
        plan=PhaseSwitchPlan(
            target_phases=target_phases,
            write_value=PHASE_SWITCH_VALUE_1P if target_phases == 1 else PHASE_SWITCH_VALUE_3P,
            was_charging=was_charging,
            resume_current_a=resume_current_a,
        ),
    )

def _blocked(reason: str) -> PhaseSwitchDecision:
    return PhaseSwitchDecision(allowed=False, block_reason=reason)
