from time import monotonic

from custom_components.webasto_unite.features.phase_runtime import PhaseRuntimeState


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
