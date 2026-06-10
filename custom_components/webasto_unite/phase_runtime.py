from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

from .const import (
    DEFAULT_3P_RESTORE_EDGE_TRIGGER,
    DEFAULT_3P_RESTORE_MAX_ATTEMPTS,
    DEFAULT_RESTORE_3P_ON_NEW_SESSION,
    PHASE_SWITCHING_MODE_OFF,
)


@dataclass(slots=True)
class PhaseRuntimeState:
    """Mutable runtime state for phase switching, policy and recovery.

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
    restore_3p_on_new_session: bool = DEFAULT_RESTORE_3P_ON_NEW_SESSION
    restore_3p_edge_trigger: bool = DEFAULT_3P_RESTORE_EDGE_TRIGGER
    restore_3p_max_attempts: int = DEFAULT_3P_RESTORE_MAX_ATTEMPTS
    new_session_3p_restore_attempt_count: int = 0
    recovery_3p_attempted: bool = False
    mismatch_3p_since_monotonic: float | None = None
    session_started_monotonic: float | None = None
    recovery_warning: str | None = None

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

    def reset_session_transient_state(self) -> None:
        self.reset_policy_state()
        self.new_session_3p_restore_attempt_count = 0
        self.recovery_3p_attempted = False
        self.mismatch_3p_since_monotonic = None
        self.session_started_monotonic = None
        self.recovery_warning = None

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

    def mark_new_session_3p_restore_attempt(self) -> None:
        self.new_session_3p_restore_attempt_count += 1
        self.recovery_warning = "restore_3p_on_session_start"

    def mark_3p_mismatch_or_ready_for_recovery(self, *, delay_s: float) -> bool:
        self.recovery_warning = "possible_1p_vehicle_or_charger_stuck"
        now = monotonic()
        if self.mismatch_3p_since_monotonic is None:
            self.mismatch_3p_since_monotonic = now
            return False
        if now - self.mismatch_3p_since_monotonic < delay_s:
            return False
        return True

    def mark_3p_recovery_attempted(self) -> None:
        self.recovery_3p_attempted = True
