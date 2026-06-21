from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic

from ..const import (
    PHASE_SWITCHING_MODE_OFF,
)


@dataclass(slots=True)
class PhaseRuntimeState:
    """Mutable runtime state for phase switching and phase policy diagnostics.

    Coordinator still orchestrates the flow. This object only groups the
    phase-related mutable fields so they are no longer scattered as independent
    coordinator attributes.
    """

    switching_mode: str = PHASE_SWITCHING_MODE_OFF
    switch_last_result: str | None = None
    switch_last_block_reason: str | None = None
    switch_last_target: str | None = None
    switch_state: str | None = "idle"
    session_override_active: bool = False
    session_target: str | None = None
    restore_pending: bool = False
    policy_candidate_target: str | None = None
    policy_candidate_since_monotonic: float | None = None
    policy_last_switch_monotonic: float | None = None
    policy_session_switch_count: int = 0
    session_started_monotonic: float | None = None
    recovery_warning: str | None = None
    policy_failed_targets: set[str] = field(default_factory=set)

    def reset_policy_state(self) -> None:
        self.policy_candidate_target = None
        self.policy_candidate_since_monotonic = None
        self.policy_last_switch_monotonic = None
        self.policy_session_switch_count = 0

    def record_policy_switch_attempt(self) -> None:
        self.policy_last_switch_monotonic = monotonic()
        self.policy_session_switch_count += 1
        self.policy_candidate_target = None
        self.policy_candidate_since_monotonic = None

    def record_policy_failed_attempt(self) -> None:
        self.policy_last_switch_monotonic = monotonic()
        self.policy_candidate_target = None
        self.policy_candidate_since_monotonic = None

    def record_policy_failed_target(self, target: str) -> None:
        self.policy_failed_targets.add(target)
        self.policy_candidate_target = None
        self.policy_candidate_since_monotonic = None

    def reset_session_transient_state(self) -> None:
        self.reset_policy_state()
        self.session_started_monotonic = None
        self.recovery_warning = None
        self.policy_failed_targets.clear()

    def mark_session_started(self) -> None:
        self.session_started_monotonic = monotonic()

    def clear_session_override(self) -> None:
        self.session_override_active = False
        self.session_target = None
        self.restore_pending = False

    def set_session_override(self, target: str) -> None:
        self.session_override_active = True
        self.session_target = target
        self.restore_pending = False

    def mark_restore_pending(self, target: str | None = None) -> None:
        self.restore_pending = True
        if target is not None:
            self.session_override_active = True
            self.session_target = target
