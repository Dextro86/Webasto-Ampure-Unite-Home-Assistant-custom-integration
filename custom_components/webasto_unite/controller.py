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
    PvControlStrategy,
    PvOverrideStrategy,
    PvPhaseSwitchingMode,
    PvResult,
    WallboxState,
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
    pv_state: PvRuntimeState = field(default_factory=PvRuntimeState)

    def __post_init__(self) -> None:
        self.dlb = DlbEngine(self.config)

    def evaluate(
        self,
        mode: ChargeMode,
        wallbox: WallboxState,
        sensors: HaSensorSnapshot,
        pv_strategy: PvControlStrategy | None = None,
    ) -> ControlDecision:
        effective_pv_strategy = pv_strategy or self.config.pv_control_strategy

        if mode == ChargeMode.OFF:
            self.reset_pv_state()
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

        if mode != ChargeMode.PV:
            self.reset_pv_state()

        installed_phases = self._resolve_installed_phases(wallbox)
        pv_phase_count = self._resolve_pv_phase_count(wallbox)
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
            return ControlDecision(
                charging_enabled=False,
                target_current_a=None,
                reason=ControlReason.BELOW_MIN_CURRENT,
                dlb_limit_a=dlb_result.available_current_a,
                mode_target_a=mode_target,
                final_target_a=None,
                dominant_limit_reason=dominant_limit_reason,
                fallback_active=fallback_active,
                sensor_invalid_reason=sensors.reason_invalid,
                should_write=False,
            )

        should_write = self._should_write_current(final_target)

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

    def reset_pv_state(self) -> None:
        self.pv_state.active = False
        self.pv_state.start_condition_since = None
        self.pv_state.stop_condition_since = None
        self.pv_state.last_transition_monotonic = 0.0
        self.pv_state.last_stop_monotonic = 0.0

    def _resolve_installed_phases(self, wallbox: WallboxState) -> int:
        if wallbox.installed_phases in (1, 3):
            return wallbox.installed_phases
        return 3

    def _resolve_pv_phase_count(self, wallbox: WallboxState) -> int:
        if wallbox.charging_active and wallbox.phases_in_use in (1, 3):
            return wallbox.phases_in_use
        return self._resolve_installed_phases(wallbox)

    def _mode_target(
        self,
        mode: ChargeMode,
        sensors: HaSensorSnapshot,
        installed_phases: int,
        pv_strategy: PvControlStrategy,
        wallbox: WallboxState,
    ) -> tuple[float | None, ControlReason]:
        if mode == ChargeMode.NORMAL:
            return self.config.user_limit_a, ControlReason.NORMAL_MODE
        if mode == ChargeMode.FIXED_CURRENT:
            return self.config.fixed_current_a, ControlReason.FIXED_CURRENT_MODE
        if mode == ChargeMode.PV:
            pv_result = self._evaluate_pv_mode(sensors, installed_phases, pv_strategy, wallbox)
            return pv_result.target_current_a, pv_result.reason
        return self.config.user_limit_a, ControlReason.NORMAL_MODE

    def _evaluate_pv_mode(
        self,
        sensors: HaSensorSnapshot,
        installed_phases: int,
        pv_strategy: PvControlStrategy,
        wallbox: WallboxState,
    ) -> PvResult:
        if pv_strategy == PvControlStrategy.DISABLED:
            return PvResult(
                target_current_a=None,
                valid=True,
                reason=ControlReason.BELOW_MIN_CURRENT,
            )

        surplus_w = self._resolve_surplus_power(sensors)

        if pv_strategy == PvControlStrategy.MIN_PLUS_SURPLUS:
            if surplus_w is None:
                return PvResult(
                    target_current_a=self.config.pv_min_current_a,
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
            return PvResult(
                target_current_a=max(self.config.pv_min_current_a, surplus_target),
                valid=True,
                reason=ControlReason.PV_MODE,
            )

        if pv_strategy == PvControlStrategy.SURPLUS:
            return self._evaluate_surplus_pv_mode(sensors, installed_phases, surplus_w, wallbox)
        return PvResult(
            target_current_a=None,
            valid=True,
            reason=ControlReason.BELOW_MIN_CURRENT,
        )

    def _evaluate_surplus_pv_mode(
        self,
        sensors: HaSensorSnapshot,
        installed_phases: int,
        surplus_w: float | None,
        wallbox: WallboxState,
    ) -> PvResult:
        now = monotonic()

        if surplus_w is None:
            self.pv_state.start_condition_since = None
            self.pv_state.stop_condition_since = None
            return PvResult(
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

        if self.pv_state.active:
            self.pv_state.start_condition_since = None
            if surplus_w >= self.config.pv_stop_threshold_w:
                self.pv_state.stop_condition_since = None
                return PvResult(
                    target_current_a=max(target_current, self.config.pv_min_current_a),
                    valid=True,
                    reason=ControlReason.PV_MODE,
                )

            runtime_elapsed = now - self.pv_state.last_transition_monotonic
            if runtime_elapsed < self.config.pv_min_runtime_s:
                return PvResult(
                    target_current_a=self.config.pv_min_current_a,
                    valid=True,
                    reason=ControlReason.PV_MODE,
                )

            if self.pv_state.stop_condition_since is None:
                self.pv_state.stop_condition_since = now

            if (now - self.pv_state.stop_condition_since) < self.config.pv_stop_delay_s:
                return PvResult(
                    target_current_a=self.config.pv_min_current_a,
                    valid=True,
                    reason=ControlReason.PV_MODE,
                )

            self.pv_state.active = False
            self.pv_state.stop_condition_since = None
            self.pv_state.last_transition_monotonic = now
            self.pv_state.last_stop_monotonic = now
            return PvResult(
                target_current_a=None,
                valid=True,
                reason=ControlReason.BELOW_MIN_CURRENT,
            )

        self.pv_state.stop_condition_since = None
        if surplus_w < self.config.pv_start_threshold_w:
            self.pv_state.start_condition_since = None
            return PvResult(
                target_current_a=None,
                valid=True,
                reason=ControlReason.BELOW_MIN_CURRENT,
            )

        if (now - self.pv_state.last_stop_monotonic) < self.config.pv_min_pause_s:
            return PvResult(
                target_current_a=None,
                valid=True,
                reason=ControlReason.BELOW_MIN_CURRENT,
            )

        if self.pv_state.start_condition_since is None:
            self.pv_state.start_condition_since = now

        if (now - self.pv_state.start_condition_since) < self.config.pv_start_delay_s:
            return PvResult(
                target_current_a=None,
                valid=True,
                reason=ControlReason.BELOW_MIN_CURRENT,
            )

        self.pv_state.active = True
        self.pv_state.start_condition_since = None
        self.pv_state.last_transition_monotonic = now
        return PvResult(
            target_current_a=max(target_current, self.config.pv_min_current_a),
            valid=True,
            reason=ControlReason.PV_MODE,
        )

    @staticmethod
    def resolve_effective_pv_strategy(
        base_strategy: PvControlStrategy,
        until_unplug_strategy: PvOverrideStrategy,
        pv_until_unplug_active: bool,
    ) -> PvControlStrategy:
        if not pv_until_unplug_active or until_unplug_strategy == PvOverrideStrategy.INHERIT:
            return base_strategy
        return PvControlStrategy(until_unplug_strategy.value)

    def resolve_pv_phase_target(
        self,
        mode: ChargeMode,
        wallbox: WallboxState,
        sensors: HaSensorSnapshot,
    ) -> int | None:
        if mode != ChargeMode.PV:
            return None
        if self.config.pv_phase_switching_mode != PvPhaseSwitchingMode.AUTOMATIC_1P3P:
            return None
        if wallbox.phase_switch_mode_raw not in (0, 1):
            return None

        surplus_w = self._resolve_surplus_power(sensors)
        if surplus_w is None:
            return None

        current_phases = 1 if wallbox.phase_switch_mode_raw == 0 else 3
        min_1p_w = self.config.pv_min_current_a * voltage_sum_for_phases(
            1,
            wallbox.voltage_l1_v,
            wallbox.voltage_l2_v,
            wallbox.voltage_l3_v,
        )
        min_3p_w = self.config.pv_min_current_a * voltage_sum_for_phases(
            3,
            wallbox.voltage_l1_v,
            wallbox.voltage_l2_v,
            wallbox.voltage_l3_v,
        )
        hysteresis_w = self.config.pv_phase_switching_hysteresis_w
        switch_up_w = min_3p_w + hysteresis_w
        switch_down_w = max(min_1p_w, min_3p_w - hysteresis_w)

        if current_phases == 1 and surplus_w >= switch_up_w:
            return 3
        if current_phases == 3 and min_1p_w <= surplus_w <= switch_down_w:
            return 1
        return None

    def _resolve_surplus_power(self, sensors: HaSensorSnapshot) -> float | None:
        if sensors.surplus_power_w is not None:
            return max(0.0, sensors.surplus_power_w)

        if sensors.grid_power_w is None:
            return None

        if sensors.grid_power_w < 0:
            return abs(sensors.grid_power_w)

        return 0.0

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
            (self.config.user_limit_a, None),
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

    def _should_write_current(self, target_current_a: float) -> bool:
        now = monotonic()
        last = self.write_state.last_written_current_a

        if last is None:
            self.write_state.pending_target_a = target_current_a
            self.write_state.pending_stable_cycles += 1
        else:
            delta = abs(target_current_a - last)
            if delta < self.config.min_current_change_a:
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
