from __future__ import annotations

from dataclasses import dataclass

from ..models import ControlMode

BLOCK_REASON_EXTERNAL_CONTROLLER = "external_controller_mode"
BLOCK_REASON_MONITORING_ONLY = "monitoring_only"
BLOCK_REASON_PHASE_SWITCH_IN_PROGRESS = "phase_switch_in_progress"


@dataclass(frozen=True, slots=True)
class ControlWriteAccess:
    """Write access decision for the current control cycle."""

    automatic_control_writes: bool
    current_writes: bool
    blocked_reason: str | None


def resolve_control_write_access(
    *,
    control_mode: ControlMode,
    phase_switch_in_progress: bool,
) -> ControlWriteAccess:
    """Resolve which write paths are allowed for the current owner.

    automatic_control_writes covers the integration's own controller decisions.
    current_writes covers direct current commands such as External Requested
    Current and Charging Enabled. Static charger setup registers such as
    failsafe current/timeout (2000/2002) are not auto-synced.
    """
    active_control_mode = control_mode in {
        ControlMode.MANAGED_CONTROL,
        ControlMode.EXTERNAL_CONTROLLER,
    }
    if phase_switch_in_progress:
        return ControlWriteAccess(
            automatic_control_writes=False,
            current_writes=False,
            blocked_reason=BLOCK_REASON_PHASE_SWITCH_IN_PROGRESS,
        )
    if control_mode == ControlMode.MANAGED_CONTROL:
        return ControlWriteAccess(
            automatic_control_writes=True,
            current_writes=True,
            blocked_reason=None,
        )
    if control_mode == ControlMode.EXTERNAL_CONTROLLER:
        return ControlWriteAccess(
            automatic_control_writes=False,
            current_writes=True,
            blocked_reason=BLOCK_REASON_EXTERNAL_CONTROLLER,
        )
    return ControlWriteAccess(
        automatic_control_writes=False,
        current_writes=False,
        blocked_reason=BLOCK_REASON_MONITORING_ONLY,
    )
