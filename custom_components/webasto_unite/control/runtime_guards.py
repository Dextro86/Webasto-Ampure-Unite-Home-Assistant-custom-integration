from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Callable

from ..models import (
    ChargeMode,
    ControlConfig,
    ControlReason,
    DlbInputModel,
    DlbSensorScope,
)

STARTUP_STABILIZATION_MIN_POLLS = 3
STARTUP_STABILIZATION_MIN_SECONDS = 30.0
DLB_START_GUARD_SECONDS = 4.0
DLB_START_GUARD_CONFIRM_SAMPLES = 2
SOLAR_START_GUARD_SECONDS = 8.0
SOLAR_START_GUARD_CONFIRM_SAMPLES = 2


@dataclass(slots=True)
class RuntimeGuardState:
    startup_started_monotonic: float
    startup_refresh_count: int = 0
    last_charging_active: bool = False
    dlb_start_guard_until_monotonic: float = 0.0
    dlb_start_guard_downscale_samples: int = 0
    solar_start_guard_until_monotonic: float = 0.0
    solar_start_guard_pause_samples: int = 0
    last_solar_charging_active: bool = False


class RuntimeGuards:
    """Runtime protections around startup and charge-start transients."""

    def __init__(
        self,
        config: ControlConfig,
        *,
        state: RuntimeGuardState | None = None,
        monotonic_fn: Callable[[], float] = monotonic,
    ) -> None:
        self.config = config
        self._monotonic = monotonic_fn
        self.state = state or RuntimeGuardState(startup_started_monotonic=self._monotonic())

    def record_startup_refresh(self) -> None:
        self.state.startup_refresh_count += 1

    def startup_stabilization_ready(self) -> bool:
        return (
            self.state.startup_refresh_count >= STARTUP_STABILIZATION_MIN_POLLS
            and (self._monotonic() - self.state.startup_started_monotonic)
            >= STARTUP_STABILIZATION_MIN_SECONDS
        )

    def should_defer_startup_safe_current_fallback_write(self, *, wallbox, sensors, decision) -> bool:
        if self.config.dlb_input_model == DlbInputModel.DISABLED:
            return False
        if self.startup_stabilization_ready():
            return False
        if not wallbox.charging_active:
            return False
        if sensors.valid:
            return False
        if not decision.fallback_active or decision.reason != ControlReason.SAFE_CURRENT_FALLBACK:
            return False
        if decision.target_current_a is None:
            return False
        if abs(decision.target_current_a - self.config.safe_current_a) > 0.01:
            return False
        if wallbox.current_limit_a is None:
            return False
        return wallbox.current_limit_a > (self.config.safe_current_a + 0.01)

    def apply_dlb_start_transient_guard(
        self,
        *,
        wallbox,
        decision,
        now_monotonic: float | None = None,
    ) -> None:
        now = now_monotonic if now_monotonic is not None else self._monotonic()

        if self.config.dlb_sensor_scope != DlbSensorScope.TOTAL_INCLUDING_CHARGER:
            self.state.dlb_start_guard_until_monotonic = 0.0
            self.state.dlb_start_guard_downscale_samples = 0
            self.state.last_charging_active = wallbox.charging_active
            return

        if wallbox.charging_active and not self.state.last_charging_active:
            self.state.dlb_start_guard_until_monotonic = now + DLB_START_GUARD_SECONDS
            self.state.dlb_start_guard_downscale_samples = 0

        within_guard = wallbox.charging_active and now <= self.state.dlb_start_guard_until_monotonic
        if not within_guard:
            self.state.dlb_start_guard_downscale_samples = 0
            self.state.last_charging_active = wallbox.charging_active
            return

        downscale_requested = (
            decision.should_write
            and decision.target_current_a is not None
            and wallbox.current_limit_a is not None
            and decision.target_current_a + 0.01 < wallbox.current_limit_a
            and decision.dominant_limit_reason == ControlReason.DLB_LIMITED
        )
        if not downscale_requested:
            self.state.dlb_start_guard_downscale_samples = 0
            self.state.last_charging_active = wallbox.charging_active
            return

        # Safety first: do not delay hard reductions down to minimum current.
        if decision.target_current_a <= (self.config.min_current_a + 0.01):
            self.state.last_charging_active = wallbox.charging_active
            return

        self.state.dlb_start_guard_downscale_samples += 1
        if self.state.dlb_start_guard_downscale_samples < DLB_START_GUARD_CONFIRM_SAMPLES:
            decision.should_write = False

        self.state.last_charging_active = wallbox.charging_active

    def apply_solar_start_transient_guard(
        self,
        *,
        effective_mode: ChargeMode,
        wallbox,
        decision,
        sensors,
        now_monotonic: float | None = None,
    ) -> None:
        now = now_monotonic if now_monotonic is not None else self._monotonic()

        if effective_mode != ChargeMode.SOLAR:
            self.state.solar_start_guard_until_monotonic = 0.0
            self.state.solar_start_guard_pause_samples = 0
            self.state.last_solar_charging_active = wallbox.charging_active
            return

        if wallbox.charging_active and not self.state.last_solar_charging_active:
            self.state.solar_start_guard_until_monotonic = now + SOLAR_START_GUARD_SECONDS
            self.state.solar_start_guard_pause_samples = 0

        within_start_guard = wallbox.charging_active and now <= self.state.solar_start_guard_until_monotonic
        within_startup_guard = wallbox.charging_active and not self.startup_stabilization_ready()
        within_guard = within_start_guard or within_startup_guard
        if not within_guard:
            self.state.solar_start_guard_pause_samples = 0
            self.state.last_solar_charging_active = wallbox.charging_active
            return

        solar_pause_requested = (
            decision.should_write
            and not decision.charging_enabled
            and decision.target_current_a is not None
            and decision.target_current_a <= 0.01
            and decision.reason in {ControlReason.BELOW_MIN_CURRENT, ControlReason.SENSOR_UNAVAILABLE}
            and decision.dominant_limit_reason is None
        )
        if not solar_pause_requested:
            self.state.solar_start_guard_pause_samples = 0
            self.state.last_solar_charging_active = wallbox.charging_active
            return

        # If input is explicitly unavailable, require a short confirmation window
        # before writing 0A during startup/charge-start transients.
        if sensors.solar_input_state != "ready":
            self.state.solar_start_guard_pause_samples += 1
            if self.state.solar_start_guard_pause_samples < SOLAR_START_GUARD_CONFIRM_SAMPLES:
                decision.should_write = False

        self.state.last_solar_charging_active = wallbox.charging_active
