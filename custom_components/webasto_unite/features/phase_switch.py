from __future__ import annotations

from dataclasses import dataclass

from ..const import PHASE_MODE_1P, PHASE_SWITCHING_MODE_OFF
from ..models import WallboxState
from .phase_runtime import PhaseRuntimeState


def default_phase_target(installed_phases: str) -> int:
    return 1 if installed_phases == PHASE_MODE_1P else 3


def default_phase_switch_raw_value(installed_phases: str) -> int:
    return 0 if default_phase_target(installed_phases) == 1 else 1


def phase_label(target_phases: int) -> str:
    return f"{target_phases}P"


def phase_label_from_register(raw_value: int) -> str:
    return "1P" if raw_value == 0 else "3P"


def observed_phases_match_target(wallbox: WallboxState, target_phases: int) -> bool:
    if not wallbox.charging_active:
        return True
    return wallbox.phases_in_use == target_phases


def wallbox_matches_default_phase(wallbox: WallboxState, installed_phases: str) -> bool:
    target_phases = default_phase_target(installed_phases)
    if wallbox.phase_switch_mode_raw != default_phase_switch_raw_value(installed_phases):
        return False
    if not wallbox.charging_active:
        return not wallbox.vehicle_connected
    return observed_phases_match_target(wallbox, target_phases)


def phase_register_control_available(*, phase_switching_mode: str | None, data: object | None) -> bool:
    """Return whether explicit phase controls can address register 405.

    This only checks whether the phase-switch register is available and phase
    switching is not off. The phase engine still performs safety checks such as
    vehicle-connected and control-mode validation when a request is executed.
    """
    return (
        phase_switching_mode != PHASE_SWITCHING_MODE_OFF
        and data is not None
        and getattr(data, "phase_switch_register_available", False) is True
    )


@dataclass(slots=True)
class PhaseSwitchRuntimeFacade:
    """Small facade for phase diagnostics and session override state."""

    runtime: PhaseRuntimeState
    manager: object

    def sync_diagnostics(self) -> None:
        self.runtime.switch_last_result = getattr(self.manager, "last_result", None)
        self.runtime.switch_last_block_reason = getattr(self.manager, "last_block_reason", None)
        self.runtime.switch_last_target = getattr(self.manager, "last_target", None)
        self.runtime.switch_state = getattr(self.manager, "state", "idle")

    def clear_session_override(self) -> None:
        self.runtime.clear_session_override()

    def update_session_override(self, *, target_phases: int, installed_phases: str) -> None:
        if target_phases == default_phase_target(installed_phases):
            self.clear_session_override()
            return
        self.runtime.set_session_override(phase_label(target_phases))

    def handle_observed_register_state(self, wallbox: WallboxState, installed_phases: str) -> bool:
        """Record register/session override diagnostics without writing phases."""
        if wallbox.phase_switch_mode_raw not in (0, 1):
            return False
        if wallbox.phase_switch_mode_raw == default_phase_switch_raw_value(installed_phases):
            self.clear_session_override()
            return False
        if not wallbox.vehicle_connected:
            self.clear_session_override()
            return False
        register_target = phase_label_from_register(wallbox.phase_switch_mode_raw)
        if self.runtime.session_override_active and self.runtime.session_target == register_target:
            self.runtime.restore_pending = False
            return False
        self.runtime.restore_pending = False
        self.runtime.session_override_active = True
        self.runtime.session_target = register_target
        return False
