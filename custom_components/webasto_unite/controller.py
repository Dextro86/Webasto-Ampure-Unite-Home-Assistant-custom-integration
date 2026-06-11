from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic

from .dlb import DlbEngine
from .models import (
    ChargeMode,
    ControlConfig,
    ControlDecision,
    ControlReason,
    HaSensorSnapshot,
    SolarControlStrategy,
    SolarOverrideStrategy,
    SolarResult,
    WallboxState,
    normalize_solar_control_strategy,
)
from .solar import PvRuntimeState, SolarEngine


@dataclass(slots=True)
class WriteState:
    last_written_current_a: float | None = None
    last_write_monotonic: float = 0.0
    pending_stable_cycles: int = 0
    pending_target_a: float | None = None
    pending_started_monotonic: float | None = None


@dataclass(slots=True)
class WallboxController:
    config: ControlConfig
    dlb: DlbEngine = field(init=False)
    solar: SolarEngine = field(init=False)
    write_state: WriteState = field(default_factory=WriteState)
    solar_state: PvRuntimeState = field(init=False)
    observed_session_phase_count: int | None = None
    session_observed_3p: bool = False
    _pending_session_phase_count: int | None = None
    _pending_session_phase_polls: int = 0

    def __post_init__(self) -> None:
        self.dlb = DlbEngine(self.config)
        self.solar = SolarEngine(self.config, monotonic_fn=lambda: monotonic())
        self.solar_state = self.solar.state

    def evaluate(
        self,
        mode: ChargeMode,
        wallbox: WallboxState,
        sensors: HaSensorSnapshot,
        pv_strategy: SolarControlStrategy | None = None,
    ) -> ControlDecision:
        effective_pv_strategy = pv_strategy or self.config.solar_control_strategy
        self._update_session_phase_observation(wallbox)

        if mode == ChargeMode.OFF:
            self.reset_solar_state()
            self.reset_pending_write_state()
            should_write_stop = wallbox.charging_active or wallbox.vehicle_connected
            return ControlDecision(
                charging_enabled=False,
                target_current_a=0.0,
                reason=ControlReason.OFF_MODE,
                dominant_limit_reason=None,
                final_target_a=0.0,
                fallback_active=False,
                sensor_invalid_reason=sensors.reason_invalid,
                should_write=should_write_stop,
            )

        if mode != ChargeMode.SOLAR:
            self.reset_solar_state()

        installed_phases = self._resolve_installed_phases(wallbox)
        pv_phase_count, pv_phase_source = self._resolve_solar_phase_context(mode, wallbox, effective_pv_strategy)
        dlb_result = self.dlb.calculate_available_current(
            sensors,
            installed_phases,
            wallbox.phase_currents,
            wallbox.active_power_w,
            wallbox.voltage_l1_v,
            wallbox.voltage_l2_v,
            wallbox.voltage_l3_v,
        )

        mode_target, reason = self._mode_target(
            mode,
            sensors,
            pv_phase_count,
            effective_pv_strategy,
            wallbox,
            pv_phase_source,
        )

        final_target, dominant_limit_reason = self._combine_limits(
            wallbox=wallbox,
            mode_target_a=mode_target,
            dlb_limit_a=dlb_result.available_current_a,
        )
        if mode == ChargeMode.SOLAR:
            final_target = self._apply_solar_ramp_limit(final_target, wallbox)

        fallback_active = not dlb_result.valid
        if fallback_active:
            primary_reason = ControlReason.SAFE_CURRENT_FALLBACK
        elif dominant_limit_reason is not None:
            primary_reason = dominant_limit_reason
        else:
            primary_reason = reason

        if final_target is None:
            final_none_reason = (
                ControlReason.SENSOR_UNAVAILABLE
                if reason == ControlReason.SENSOR_UNAVAILABLE
                else ControlReason.BELOW_MIN_CURRENT
            )
            return ControlDecision(
                charging_enabled=False,
                target_current_a=None,
                reason=final_none_reason,
                dlb_limit_a=dlb_result.available_current_a,
                mode_target_a=mode_target,
                final_target_a=None,
                dominant_limit_reason=dominant_limit_reason,
                fallback_active=fallback_active,
                sensor_invalid_reason=sensors.reason_invalid,
                should_write=False,
            )

        should_write = self._should_write_current(
            final_target,
            reported_current_limit_a=wallbox.current_limit_a,
            immediate_if_lower=(
                fallback_active
                or dominant_limit_reason
                in {
                    ControlReason.DLB_LIMITED,
                    ControlReason.CABLE_LIMITED,
                    ControlReason.EV_LIMITED,
                }
            ),
        )

        return ControlDecision(
            charging_enabled=True,
            target_current_a=final_target,
            reason=primary_reason,
            dlb_limit_a=dlb_result.available_current_a,
            mode_target_a=mode_target,
            final_target_a=final_target,
            dominant_limit_reason=dominant_limit_reason,
            fallback_active=fallback_active,
            sensor_invalid_reason=sensors.reason_invalid,
            should_write=should_write,
        )

    def mark_current_written(self, current_a: float) -> None:
        self.write_state.last_written_current_a = current_a
        self.write_state.last_write_monotonic = monotonic()
        self.reset_pending_write_state()

    def reset_pending_write_state(self) -> None:
        self.write_state.pending_stable_cycles = 0
        self.write_state.pending_target_a = None
        self.write_state.pending_started_monotonic = None

    def reset_current_write_state(self) -> None:
        self.write_state.last_written_current_a = None
        self.write_state.last_write_monotonic = 0.0
        self.reset_pending_write_state()

    def reset_solar_state(self) -> None:
        self.solar.reset()

    def reset_session_phase_observation(self) -> None:
        self.observed_session_phase_count = None
        self.session_observed_3p = False
        self._pending_session_phase_count = None
        self._pending_session_phase_polls = 0

    def _update_session_phase_observation(self, wallbox: WallboxState) -> None:
        if not wallbox.vehicle_connected:
            self.reset_session_phase_observation()
            return
        if not wallbox.charging_active or wallbox.phases_in_use not in (1, 3):
            self._pending_session_phase_count = None
            self._pending_session_phase_polls = 0
            return

        if self.observed_session_phase_count == wallbox.phases_in_use:
            self._pending_session_phase_count = None
            self._pending_session_phase_polls = 0
            return

        if self._pending_session_phase_count != wallbox.phases_in_use:
            self._pending_session_phase_count = wallbox.phases_in_use
            self._pending_session_phase_polls = 1
            return

        self._pending_session_phase_polls += 1
        if self._pending_session_phase_polls >= 2:
            self.observed_session_phase_count = wallbox.phases_in_use
            if wallbox.phases_in_use == 3:
                self.session_observed_3p = True
            self._pending_session_phase_count = None
            self._pending_session_phase_polls = 0

    def _resolve_installed_phases(self, wallbox: WallboxState) -> int:
        if wallbox.installed_phases in (1, 3):
            return wallbox.installed_phases
        return 3

    def _resolve_solar_phase_context(
        self,
        mode: ChargeMode,
        wallbox: WallboxState,
        pv_strategy: SolarControlStrategy,
    ) -> tuple[int, str]:
        if wallbox.charging_active and wallbox.phases_in_use in (1, 3):
            return wallbox.phases_in_use, "wallbox_active_phases"
        installed_phases = self._resolve_installed_phases(wallbox)
        if (
            mode == ChargeMode.SOLAR
            and wallbox.vehicle_connected
            and installed_phases == 3
            and self.observed_session_phase_count in (1, 3)
        ):
            return self.observed_session_phase_count, "observed_session_phases"
        if (
            mode == ChargeMode.SOLAR
            and not wallbox.charging_active
            and installed_phases == 3
            and wallbox.phases_in_use not in (1, 3)
        ):
            if normalize_solar_control_strategy(pv_strategy) == SolarControlStrategy.ECO_SOLAR:
                if wallbox.phase_switch_mode_raw == 0:
                    return 1, "phase_switch_mode_1p"
                if wallbox.phase_switch_mode_raw == 1:
                    return 3, "phase_switch_mode_3p"
                return 3, "pre_start_3p_safety"
            # Smart Solar and Solar Boost may use grid support. Keep the
            # conservative 1P assumption so they can start before physical phase
            # observation is available.
            return 1, "pre_start_1p_assumption"
        return installed_phases, "installed_phases"

    def _mode_target(
        self,
        mode: ChargeMode,
        sensors: HaSensorSnapshot,
        installed_phases: int,
        pv_strategy: SolarControlStrategy,
        wallbox: WallboxState,
        phase_source: str | None = None,
    ) -> tuple[float | None, ControlReason]:
        if mode == ChargeMode.NORMAL:
            return self.config.max_current_a, ControlReason.NORMAL_MODE
        if mode == ChargeMode.FIXED_CURRENT:
            return self.config.fixed_current_a, ControlReason.FIXED_CURRENT_MODE
        if mode == ChargeMode.SOLAR:
            pv_result = self.solar.evaluate(
                sensors,
                installed_phases,
                normalize_solar_control_strategy(pv_strategy),
                wallbox,
                phase_source,
            )
            return pv_result.target_current_a, pv_result.reason
        return self.config.max_current_a, ControlReason.NORMAL_MODE

    def _evaluate_solar_mode(
        self,
        sensors: HaSensorSnapshot,
        installed_phases: int,
        pv_strategy: SolarControlStrategy,
        wallbox: WallboxState,
        phase_source: str | None = None,
    ) -> SolarResult:
        return self.solar.evaluate(sensors, installed_phases, pv_strategy, wallbox, phase_source)

    def _evaluate_eco_solar_mode(
        self,
        sensors: HaSensorSnapshot,
        installed_phases: int,
        surplus_w: float | None,
        wallbox: WallboxState,
        phase_source: str | None = None,
    ) -> SolarResult:
        return self.solar._evaluate_eco_solar_mode(sensors, installed_phases, surplus_w, wallbox, phase_source)

    @staticmethod
    def resolve_effective_solar_strategy(
        base_strategy: SolarControlStrategy,
        until_unplug_strategy: SolarOverrideStrategy,
        solar_until_unplug_active: bool,
    ) -> SolarControlStrategy:
        return SolarEngine.resolve_effective_strategy(
            base_strategy,
            until_unplug_strategy,
            solar_until_unplug_active,
        )

    def resolve_surplus_power(
        self,
        sensors: HaSensorSnapshot,
        wallbox: WallboxState | None = None,
    ) -> float | None:
        return self.solar.resolve_surplus_power(sensors, wallbox)

    def _set_solar_calculation_diagnostics(
        self,
        *,
        target_current_a: float | None,
        phase_count: int | None,
        phase_source: str | None,
        voltage_sum_v: float | None,
    ) -> None:
        self.solar._set_calculation_diagnostics(
            target_current_a=target_current_a,
            phase_count=phase_count,
            phase_source=phase_source,
            voltage_sum_v=voltage_sum_v,
        )

    def _clear_solar_calculation_diagnostics(self) -> None:
        self.solar._clear_calculation_diagnostics()

    def _filtered_solar_surplus(self, surplus_w: float) -> float:
        return self.solar._filtered_surplus(surplus_w)

    def _reset_solar_surplus_filter(self) -> None:
        self.solar._reset_surplus_filter()

    def _apply_solar_ramp_limit(
        self,
        target_current_a: float | None,
        wallbox: WallboxState,
    ) -> float | None:
        return self.solar.apply_ramp_limit(
            target_current_a,
            wallbox,
            baseline_current_a=self.write_state.last_written_current_a,
        )

    def _apply_signed_grid_deadband(self, signed_export_w: float) -> float:
        return self.solar._apply_signed_grid_deadband(signed_export_w)

    def _apply_export_deadband(self, surplus_w: float) -> float:
        return self.solar._apply_export_deadband(surplus_w)

    @staticmethod
    def _trusted_charger_power_w(wallbox: WallboxState | None) -> float:
        return SolarEngine._trusted_charger_power_w(wallbox)

    def _combine_limits(
        self,
        wallbox: WallboxState,
        mode_target_a: float | None,
        dlb_limit_a: float | None,
    ) -> tuple[float | None, ControlReason | None]:
        if mode_target_a is None:
            return None, None

        limits: list[tuple[float, ControlReason | None]] = [
            (mode_target_a, None),
            (self.config.max_current_a, None),
        ]

        if dlb_limit_a is not None:
            limits.append((dlb_limit_a, ControlReason.DLB_LIMITED))
        if wallbox.cable_max_current_a is not None:
            limits.append((wallbox.cable_max_current_a, ControlReason.CABLE_LIMITED))
        if wallbox.ev_max_current_a is not None:
            limits.append((wallbox.ev_max_current_a, ControlReason.EV_LIMITED))

        final_target, dominant_limit_reason = min(limits, key=lambda item: item[0])

        minimum_current = self.config.min_current_a
        if wallbox.hardware_min_current_a is not None:
            minimum_current = max(minimum_current, wallbox.hardware_min_current_a)

        if final_target < minimum_current:
            return None, dominant_limit_reason

        return round(final_target, 1), dominant_limit_reason

    def _should_write_current(
        self,
        target_current_a: float,
        *,
        reported_current_limit_a: float | None = None,
        immediate_if_lower: bool = False,
    ) -> bool:
        now = monotonic()
        last = self.write_state.last_written_current_a
        reported_mismatch = False
        if reported_current_limit_a is not None:
            reported_delta = abs(target_current_a - reported_current_limit_a)
            if reported_delta < self.config.min_current_change_a:
                self.write_state.pending_stable_cycles = 0
                self.write_state.pending_target_a = None
                return False
            reported_mismatch = True

        if last is None:
            self._track_pending_write_target(target_current_a, now)
        else:
            if immediate_if_lower and target_current_a < last:
                self._start_pending_write_window_if_needed(now)
                self.write_state.pending_target_a = target_current_a
                self.write_state.pending_stable_cycles = self.config.stable_cycles_before_write
                return True

            delta = abs(target_current_a - last)
            if delta < self.config.min_current_change_a and not reported_mismatch:
                self.write_state.pending_stable_cycles = 0
                self.write_state.pending_target_a = None
                return False

            self._track_pending_write_target(target_current_a, now)

        if (now - self.write_state.last_write_monotonic) < self.config.min_seconds_between_writes:
            return False

        if self.write_state.pending_stable_cycles >= self.config.stable_cycles_before_write:
            return True

        if (
            self.write_state.pending_started_monotonic is not None
            and (now - self.write_state.pending_started_monotonic) >= self.config.pending_stable_max_age_s
        ):
            self.write_state.pending_stable_cycles = self.config.stable_cycles_before_write
            return True

        return False

    def _start_pending_write_window_if_needed(self, now: float) -> None:
        if self.write_state.pending_started_monotonic is None:
            self.write_state.pending_started_monotonic = now

    def _track_pending_write_target(self, target_current_a: float, now: float) -> None:
        self._start_pending_write_window_if_needed(now)
        if self.write_state.pending_target_a == target_current_a:
            self.write_state.pending_stable_cycles += 1
            return
        self.write_state.pending_target_a = target_current_a
        self.write_state.pending_stable_cycles = 1
