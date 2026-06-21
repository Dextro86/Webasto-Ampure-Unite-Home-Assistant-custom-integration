from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Callable

from ..models import ControlConfig


@dataclass(slots=True)
class WriteState:
    last_written_current_a: float | None = None
    last_write_monotonic: float = 0.0
    pending_stable_cycles: int = 0
    pending_target_a: float | None = None
    pending_started_monotonic: float | None = None


class CurrentWriteDecider:
    """Tracks current-write throttling, stability and meaningful-change rules."""

    def __init__(
        self,
        config: ControlConfig,
        *,
        state: WriteState | None = None,
        monotonic_fn: Callable[[], float] = monotonic,
    ) -> None:
        self.config = config
        self.state = state or WriteState()
        self._monotonic = monotonic_fn

    def mark_current_written(self, current_a: float) -> None:
        self.state.last_written_current_a = current_a
        self.state.last_write_monotonic = self._monotonic()
        self.reset_pending_write_state()

    def reset_pending_write_state(self) -> None:
        self.state.pending_stable_cycles = 0
        self.state.pending_target_a = None
        self.state.pending_started_monotonic = None

    def reset_current_write_state(self) -> None:
        self.state.last_written_current_a = None
        self.state.last_write_monotonic = 0.0
        self.reset_pending_write_state()

    def should_write_current(
        self,
        target_current_a: float,
        *,
        reported_current_limit_a: float | None = None,
        immediate_if_lower: bool = False,
    ) -> bool:
        now = self._monotonic()
        last = self.state.last_written_current_a
        reported_mismatch = False
        if reported_current_limit_a is not None:
            reported_delta = abs(target_current_a - reported_current_limit_a)
            if reported_delta < self.config.min_current_change_a:
                self.reset_pending_write_state()
                return False
            reported_mismatch = True

        if last is None:
            self._track_pending_write_target(target_current_a, now)
        else:
            if immediate_if_lower and target_current_a < last:
                self._start_pending_write_window_if_needed(now)
                self.state.pending_target_a = target_current_a
                self.state.pending_stable_cycles = self.config.stable_cycles_before_write
                return True

            delta = abs(target_current_a - last)
            if delta < self.config.min_current_change_a and not reported_mismatch:
                self.reset_pending_write_state()
                return False

            self._track_pending_write_target(target_current_a, now)

        if (now - self.state.last_write_monotonic) < self.config.min_seconds_between_writes:
            return False

        if self.state.pending_stable_cycles >= self.config.stable_cycles_before_write:
            return True

        if (
            self.state.pending_started_monotonic is not None
            and (now - self.state.pending_started_monotonic) >= self.config.pending_stable_max_age_s
        ):
            self.state.pending_stable_cycles = self.config.stable_cycles_before_write
            return True

        return False

    def _start_pending_write_window_if_needed(self, now: float) -> None:
        if self.state.pending_started_monotonic is None:
            self.state.pending_started_monotonic = now

    def _track_pending_write_target(self, target_current_a: float, now: float) -> None:
        self._start_pending_write_window_if_needed(now)
        if self.state.pending_target_a == target_current_a:
            self.state.pending_stable_cycles += 1
            return
        self.state.pending_target_a = target_current_a
        self.state.pending_stable_cycles = 1
