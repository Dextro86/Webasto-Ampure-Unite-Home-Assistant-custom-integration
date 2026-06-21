from custom_components.webasto_unite.control.current import CurrentWriteDecider, WriteState
from custom_components.webasto_unite.models import ControlConfig


def test_current_write_decider_waits_for_stable_cycles():
    state = WriteState()
    decider = CurrentWriteDecider(
        ControlConfig(stable_cycles_before_write=2, min_seconds_between_writes=0.0),
        state=state,
        monotonic_fn=lambda: 100.0,
    )

    assert decider.should_write_current(10.0) is False
    assert decider.should_write_current(10.0) is True


def test_current_write_decider_forces_after_pending_age():
    now = 100.0
    state = WriteState()
    decider = CurrentWriteDecider(
        ControlConfig(
            stable_cycles_before_write=10,
            min_seconds_between_writes=0.0,
            pending_stable_max_age_s=5.0,
        ),
        state=state,
        monotonic_fn=lambda: now,
    )

    assert decider.should_write_current(10.0) is False
    now = 106.0

    assert decider.should_write_current(10.0) is True


def test_current_write_decider_immediate_lower_for_safety_limits():
    state = WriteState(last_written_current_a=16.0, last_write_monotonic=100.0)
    decider = CurrentWriteDecider(
        ControlConfig(stable_cycles_before_write=5, min_seconds_between_writes=0.0),
        state=state,
        monotonic_fn=lambda: 101.0,
    )

    assert decider.should_write_current(6.0, immediate_if_lower=True) is True
