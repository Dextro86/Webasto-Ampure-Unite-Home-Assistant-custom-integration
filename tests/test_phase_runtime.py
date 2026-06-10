from time import monotonic

from custom_components.webasto_unite.phase_runtime import PhaseRuntimeState


def test_phase_runtime_policy_switch_attempt_resets_candidate_and_counts_switch():
    runtime = PhaseRuntimeState(
        policy_candidate_target="1P",
        policy_candidate_since_monotonic=monotonic() - 130.0,
        policy_session_switch_count=1,
    )

    runtime.record_policy_switch_attempt()

    assert runtime.policy_last_switch_monotonic is not None
    assert runtime.policy_session_switch_count == 2
    assert runtime.policy_candidate_target is None
    assert runtime.policy_candidate_since_monotonic is None


def test_phase_runtime_session_override_helpers():
    runtime = PhaseRuntimeState()

    runtime.set_session_override("1P")
    assert runtime.session_override_active is True
    assert runtime.session_target == "1P"
    assert runtime.restore_pending is False

    runtime.clear_session_override()
    assert runtime.session_override_active is False
    assert runtime.session_target is None
    assert runtime.restore_pending is False


def test_phase_runtime_3p_mismatch_recovery_waits_for_delay(monkeypatch):
    now = 1000.0
    monkeypatch.setattr("custom_components.webasto_unite.phase_runtime.monotonic", lambda: now)
    runtime = PhaseRuntimeState()

    assert runtime.mark_3p_mismatch_or_ready_for_recovery(delay_s=120.0) is False
    assert runtime.recovery_warning == "possible_1p_vehicle_or_charger_stuck"
    assert runtime.recovery_3p_attempted is False

    now = 1119.0
    assert runtime.mark_3p_mismatch_or_ready_for_recovery(delay_s=120.0) is False

    now = 1121.0
    assert runtime.mark_3p_mismatch_or_ready_for_recovery(delay_s=120.0) is True
    runtime.mark_3p_recovery_attempted()
    assert runtime.recovery_3p_attempted is True
