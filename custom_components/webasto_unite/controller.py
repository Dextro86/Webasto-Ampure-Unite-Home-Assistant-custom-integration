from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic

from .dlb import DlbEngine
from .electrical import voltage_sum_for_phases
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
    normalize_solar_override_strategy,
)


@dataclass(slots=True)
class WriteState:
    last_written_current_a: float | None = None
    last_write_monotonic: float = 0.0
    pending_stable_cycles: int = 0
    pending_target_a: float | None = None


@dataclass(slots=True)
class PvRuntimeState:
    active: bool = False
    start_condition_since: float | None = None
    stop_condition_since: float | None = None
    last_transition_monotonic: float = 0.0
    last_stop_monotonic: float = 0.0


@dataclass(slots=True)
class WallboxController:
    config: ControlConfig
    dlb: DlbEngine = field(init=False)
    write_state: WriteState = field(default_factory=WriteState)
    solar_state: PvRuntimeState = field(default_factory=PvRuntimeState)
    observed_session_phase_count: int | None = None
    _pending_session_phase_count: int | None = None
    _pending_session_phase_polls: int = 0

    def __post_init__(self) -> None:
        self.dlb = DlbEngine(self.config)

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
        pv_phase_count = self._resolve_solar_phase_count(mode, wallbox)
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
        )

        final_target, dominant_limit_reason = self._combine_limits(
            wallbox=wallbox,
            mode_target_a=mode_target,
            dlb_limit_a=dlb_result.available_current_a,
        )

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

    def reset_current_write_state(self) -> None:
        self.write_state.last_written_current_a = None
        self.write_state.last_write_monotonic = 0.0
        self.reset_pending_write_state()

    def reset_solar_state(self) -> None:
        self.solar_state.active = False
        self.solar_state.start_condition_since = None
        self.solar_state.stop_condition_since = None
        self.solar_state.last_transition_monotonic = 0.0
        self.solar_state.last_stop_monotonic = 0.0

    def reset_session_phase_observation(self) -> None:
        self.observed_session_phase_count = None
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
            self._pending_session_phase_count = None
            self._pending_session_phase_polls = 0

    def _resolve_installed_phases(self, wallbox: WallboxState) -> int:
        if wallbox.installed_phases in (1, 3):
            return wallbox.installed_phases
        return 3

    def _resolve_solar_phase_count(self, mode: ChargeMode, wallbox: WallboxState) -> int:
        if wallbox.charging_active and wallbox.phases_in_use in (1, 3):
            return wallbox.phases_in_use
        installed_phases = self._resolve_installed_phases(wallbox)
        if (
            mode == ChargeMode.SOLAR
            and wallbox.vehicle_connected
            and installed_phases == 3
            and self.observed_session_phase_count in (1, 3)
        ):
            return self.observed_session_phase_count
        if (
            mode == ChargeMode.SOLAR
            and not wallbox.charging_active
            and installed_phases == 3
            and wallbox.phases_in_use not in (1, 3)
        ):
            # Pre-start phase count is unknown; use a conservative 1P assumption
            # so 1P vehicles on 3P installations can still start on PV.
            return 1
        return installed_phases

    def _mode_target(
        self,
        mode: ChargeMode,
        sensors: HaSensorSnapshot,
        installed_phases: int,
        pv_strategy: SolarControlStrategy,
        wallbox: WallboxState,
    ) -> tuple[float | None, ControlReason]:
        if mode == ChargeMode.NORMAL:
            return self.config.max_current_a, ControlReason.NORMAL_MODE
        if mode == ChargeMode.FIXED_CURRENT:
            return self.config.fixed_current_a, ControlReason.FIXED_CURRENT_MODE
        if mode == ChargeMode.SOLAR:
            pv_result = self._evaluate_solar_mode(
                sensors,
                installed_phases,
                normalize_solar_control_strategy(pv_strategy),
                wallbox,
            )
            return pv_result.target_current_a, pv_result.reason
        return self.config.max_current_a, ControlReason.NORMAL_MODE

    def _evaluate_solar_mode(
        self,
        sensors: HaSensorSnapshot,
        installed_phases: int,
        pv_strategy: SolarControlStrategy,
        wallbox: WallboxState,
    ) -> SolarResult:
        if pv_strategy == SolarControlStrategy.DISABLED:
            return SolarResult(
                target_current_a=None,
                valid=True,
                reason=ControlReason.BELOW_MIN_CURRENT,
            )

        surplus_w = self.resolve_surplus_power(sensors, wallbox)

        if pv_strategy == SolarControlStrategy.MIN_PLUS_SURPLUS:
            if surplus_w is None:
                return SolarResult(
                    target_current_a=None,
                    valid=False,
                    reason=ControlReason.SENSOR_UNAVAILABLE,
                )

            voltage_sum_v = voltage_sum_for_phases(
                installed_phases,
                wallbox.voltage_l1_v,
                wallbox.voltage_l2_v,
                wallbox.voltage_l3_v,
            )
            surplus_target = surplus_w / voltage_sum_v
            return SolarResult(
                target_current_a=max(self.config.solar_min_current_a, surplus_target),
                valid=True,
                reason=ControlReason.SOLAR_MODE,
            )

        if pv_strategy == SolarControlStrategy.SURPLUS:
            return self._evaluate_eco_solar_mode(sensors, installed_phases, surplus_w, wallbox)
        return SolarResult(
            target_current_a=None,
            valid=True,
            reason=ControlReason.BELOW_MIN_CURRENT,
        )

    def _evaluate_eco_solar_mode(
        self,
        sensors: HaSensorSnapshot,
        installed_phases: int,
        surplus_w: float | None,
        wallbox: WallboxState,
    ) -> SolarResult:
        now = monotonic()

        if surplus_w is None:
            self.solar_state.start_condition_since = None
            self.solar_state.stop_condition_since = None
            return SolarResult(
                target_current_a=None,
                valid=False,
                reason=ControlReason.SENSOR_UNAVAILABLE,
            )

        voltage_sum_v = voltage_sum_for_phases(
            installed_phases,
            wallbox.voltage_l1_v,
            wallbox.voltage_l2_v,
            wallbox.voltage_l3_v,
        )
        target_current = surplus_w / voltage_sum_v
        min_surplus_power_w = self.config.solar_min_current_a * voltage_sum_v
        effective_start_threshold_w = max(self.config.solar_start_threshold_w, min_surplus_power_w)
        effective_stop_threshold_w = max(self.config.solar_stop_threshold_w, min_surplus_power_w)

        if self.solar_state.active:
            self.solar_state.start_condition_since = None
            if surplus_w >= effective_stop_threshold_w:
                self.solar_state.stop_condition_since = None
                return SolarResult(
                    target_current_a=max(target_current, self.config.solar_min_current_a),
                    valid=True,
                    reason=ControlReason.SOLAR_MODE,
                )

            runtime_elapsed = now - self.solar_state.last_transition_monotonic
            if runtime_elapsed < self.config.solar_min_runtime_s:
                return SolarResult(
                    target_current_a=self.config.solar_min_current_a,
                    valid=True,
                    reason=ControlReason.SOLAR_MODE,
                )

            if self.solar_state.stop_condition_since is None:
                self.solar_state.stop_condition_since = now

            if (now - self.solar_state.stop_condition_since) < self.config.solar_stop_delay_s:
                return SolarResult(
                    target_current_a=self.config.solar_min_current_a,
                    valid=True,
                    reason=ControlReason.SOLAR_MODE,
                )

            self.solar_state.active = False
            self.solar_state.stop_condition_since = None
            self.solar_state.last_transition_monotonic = now
            self.solar_state.last_stop_monotonic = now
            return SolarResult(
                target_current_a=None,
                valid=True,
                reason=ControlReason.BELOW_MIN_CURRENT,
            )

        self.solar_state.stop_condition_since = None
        if surplus_w < effective_start_threshold_w:
            self.solar_state.start_condition_since = None
            return SolarResult(
                target_current_a=None,
                valid=True,
                reason=ControlReason.BELOW_MIN_CURRENT,
            )

        if (now - self.solar_state.last_stop_monotonic) < self.config.solar_min_pause_s:
            return SolarResult(
                target_current_a=None,
                valid=True,
                reason=ControlReason.BELOW_MIN_CURRENT,
            )

        if self.solar_state.start_condition_since is None:
            self.solar_state.start_condition_since = now

        if (now - self.solar_state.start_condition_since) < self.config.solar_start_delay_s:
            return SolarResult(
                target_current_a=None,
                valid=True,
                reason=ControlReason.BELOW_MIN_CURRENT,
            )

        self.solar_state.active = True
        self.solar_state.start_condition_since = None
        self.solar_state.last_transition_monotonic = now
        return SolarResult(
            target_current_a=max(target_current, self.config.solar_min_current_a),
            valid=True,
            reason=ControlReason.SOLAR_MODE,
        )

    @staticmethod
    def resolve_effective_solar_strategy(
        base_strategy: SolarControlStrategy,
        until_unplug_strategy: SolarOverrideStrategy,
        solar_until_unplug_active: bool,
    ) -> SolarControlStrategy:
        base_strategy = normalize_solar_control_strategy(base_strategy)
        until_unplug_strategy = normalize_solar_override_strategy(until_unplug_strategy)
        if not solar_until_unplug_active or until_unplug_strategy == SolarOverrideStrategy.INHERIT:
            return base_strategy
        return SolarControlStrategy(until_unplug_strategy.value)

    def resolve_surplus_power(
        self,
        sensors: HaSensorSnapshot,
        wallbox: WallboxState | None = None,
    ) -> float | None:
        if sensors.surplus_power_w is not None:
            return max(0.0, sensors.surplus_power_w)

        if sensors.grid_power_w is None:
            return None

        charger_power_w = self._trusted_charger_power_w(wallbox)
        if self.config.solar_grid_power_direction == "positive_export":
            return max(0.0, sensors.grid_power_w + charger_power_w)

        return max(0.0, charger_power_w - sensors.grid_power_w)

    @staticmethod
    def _trusted_charger_power_w(wallbox: WallboxState | None) -> float:
        if wallbox is None or not wallbox.charging_active:
            return 0.0
        return max(0.0, wallbox.active_power_w or 0.0)

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
            self.write_state.pending_target_a = target_current_a
            self.write_state.pending_stable_cycles += 1
        else:
            if immediate_if_lower and target_current_a < last:
                self.write_state.pending_target_a = target_current_a
                self.write_state.pending_stable_cycles = self.config.stable_cycles_before_write
                return True

            delta = abs(target_current_a - last)
            if delta < self.config.min_current_change_a and not reported_mismatch:
                self.write_state.pending_stable_cycles = 0
                self.write_state.pending_target_a = None
                return False

            if self.write_state.pending_target_a == target_current_a:
                self.write_state.pending_stable_cycles += 1
            else:
                self.write_state.pending_target_a = target_current_a
                self.write_state.pending_stable_cycles = 1

        if (now - self.write_state.last_write_monotonic) < self.config.min_seconds_between_writes:
            return False

        return self.write_state.pending_stable_cycles >= self.config.stable_cycles_before_write
