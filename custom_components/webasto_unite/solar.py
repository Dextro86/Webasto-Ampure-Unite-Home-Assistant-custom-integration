from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic
from typing import Callable

from .electrical import voltage_sum_for_phases
from .models import (
    ControlConfig,
    ControlReason,
    HaSensorSnapshot,
    SolarControlStrategy,
    SolarInputModel,
    SolarOverrideStrategy,
    SolarSensorFailureBehavior,
    SolarResult,
    WallboxState,
    normalize_solar_control_strategy,
    normalize_solar_override_strategy,
)


@dataclass(slots=True)
class PvRuntimeState:
    active: bool = False
    start_condition_since: float | None = None
    stop_condition_since: float | None = None
    last_transition_monotonic: float = 0.0
    last_stop_monotonic: float = 0.0
    raw_surplus_w: float | None = None
    filtered_surplus_w: float | None = None
    filtered_surplus_monotonic: float | None = None
    target_current_a: float | None = None
    phase_count: int | None = None
    phase_source: str | None = None
    voltage_sum_v: float | None = None
    last_target_current_a: float | None = None


@dataclass(slots=True)
class SolarEngine:
    config: ControlConfig
    monotonic_fn: Callable[[], float] = monotonic
    state: PvRuntimeState = field(default_factory=PvRuntimeState)

    def reset(self) -> None:
        self.state.active = False
        self.state.start_condition_since = None
        self.state.stop_condition_since = None
        self.state.last_transition_monotonic = 0.0
        self.state.last_stop_monotonic = 0.0
        self.state.raw_surplus_w = None
        self.state.filtered_surplus_w = None
        self.state.filtered_surplus_monotonic = None
        self.state.target_current_a = None
        self.state.phase_count = None
        self.state.phase_source = None
        self.state.voltage_sum_v = None
        self.state.last_target_current_a = None

    def evaluate(
        self,
        sensors: HaSensorSnapshot,
        phase_count: int,
        strategy: SolarControlStrategy,
        wallbox: WallboxState,
        phase_source: str | None = None,
    ) -> SolarResult:
        strategy = normalize_solar_control_strategy(strategy)
        if strategy == SolarControlStrategy.DISABLED:
            self._clear_calculation_diagnostics()
            return SolarResult(
                target_current_a=None,
                valid=True,
                reason=ControlReason.BELOW_MIN_CURRENT,
            )

        surplus_w = self.resolve_surplus_power(sensors, wallbox)

        if strategy in (
            SolarControlStrategy.SMART_SOLAR,
            SolarControlStrategy.SOLAR_BOOST,
        ):
            return self._evaluate_minimum_based_mode(
                surplus_w,
                phase_count,
                strategy,
                wallbox,
                phase_source,
            )

        if strategy == SolarControlStrategy.SURPLUS:
            return self._evaluate_eco_solar_mode(sensors, phase_count, surplus_w, wallbox, phase_source)
        self._clear_calculation_diagnostics()
        return SolarResult(
            target_current_a=None,
            valid=True,
            reason=ControlReason.BELOW_MIN_CURRENT,
        )

    def _evaluate_minimum_based_mode(
        self,
        surplus_w: float | None,
        phase_count: int,
        strategy: SolarControlStrategy,
        wallbox: WallboxState,
        phase_source: str | None,
    ) -> SolarResult:
        if surplus_w is None:
            self._reset_surplus_filter()
            if self.config.solar_sensor_failure_behavior == SolarSensorFailureBehavior.CONTINUE_MINIMUM:
                self._set_calculation_diagnostics(
                    target_current_a=self.config.solar_min_current_a,
                    phase_count=phase_count,
                    phase_source=phase_source,
                    voltage_sum_v=None,
                )
                return SolarResult(
                    target_current_a=self.config.solar_min_current_a,
                    valid=True,
                    reason=ControlReason.SOLAR_MODE,
                )
            self._clear_calculation_diagnostics()
            return SolarResult(
                target_current_a=None,
                valid=False,
                reason=ControlReason.SENSOR_UNAVAILABLE,
            )
        surplus_w = self._filtered_surplus(surplus_w)

        voltage_sum_v = voltage_sum_for_phases(
            phase_count,
            wallbox.voltage_l1_v,
            wallbox.voltage_l2_v,
            wallbox.voltage_l3_v,
        )
        surplus_target = surplus_w / voltage_sum_v
        if strategy == SolarControlStrategy.SMART_SOLAR:
            target_current = max(self.config.solar_min_current_a, surplus_target)
        else:
            target_current = self.config.solar_min_current_a + surplus_target
        self._set_calculation_diagnostics(
            target_current_a=target_current,
            phase_count=phase_count,
            phase_source=phase_source,
            voltage_sum_v=voltage_sum_v,
        )
        return SolarResult(
            target_current_a=target_current,
            valid=True,
            reason=ControlReason.SOLAR_MODE,
        )

    def _evaluate_eco_solar_mode(
        self,
        sensors: HaSensorSnapshot,
        phase_count: int,
        surplus_w: float | None,
        wallbox: WallboxState,
        phase_source: str | None = None,
    ) -> SolarResult:
        now = self.monotonic_fn()

        if surplus_w is None:
            self._reset_surplus_filter()
            self._clear_calculation_diagnostics()
            self.state.start_condition_since = None
            self.state.stop_condition_since = None
            return SolarResult(
                target_current_a=None,
                valid=False,
                reason=ControlReason.SENSOR_UNAVAILABLE,
            )
        surplus_w = self._filtered_surplus(surplus_w)

        voltage_sum_v = voltage_sum_for_phases(
            phase_count,
            wallbox.voltage_l1_v,
            wallbox.voltage_l2_v,
            wallbox.voltage_l3_v,
        )
        target_current = surplus_w / voltage_sum_v
        min_surplus_power_w = self.config.solar_min_current_a * voltage_sum_v
        effective_start_threshold_w = max(self.config.solar_start_threshold_w, min_surplus_power_w)
        effective_stop_threshold_w = max(self.config.solar_stop_threshold_w, min_surplus_power_w)

        if self.state.active:
            self.state.start_condition_since = None
            if surplus_w >= effective_stop_threshold_w:
                self.state.stop_condition_since = None
                self._set_calculation_diagnostics(
                    target_current_a=max(target_current, self.config.solar_min_current_a),
                    phase_count=phase_count,
                    phase_source=phase_source,
                    voltage_sum_v=voltage_sum_v,
                )
                return SolarResult(
                    target_current_a=max(target_current, self.config.solar_min_current_a),
                    valid=True,
                    reason=ControlReason.SOLAR_MODE,
                )

            runtime_elapsed = now - self.state.last_transition_monotonic
            if runtime_elapsed < self.config.solar_min_runtime_s:
                self._set_calculation_diagnostics(
                    target_current_a=self.config.solar_min_current_a,
                    phase_count=phase_count,
                    phase_source=phase_source,
                    voltage_sum_v=voltage_sum_v,
                )
                return SolarResult(
                    target_current_a=self.config.solar_min_current_a,
                    valid=True,
                    reason=ControlReason.SOLAR_MODE,
                )

            if self.state.stop_condition_since is None:
                self.state.stop_condition_since = now

            if (now - self.state.stop_condition_since) < self.config.solar_stop_delay_s:
                self._set_calculation_diagnostics(
                    target_current_a=self.config.solar_min_current_a,
                    phase_count=phase_count,
                    phase_source=phase_source,
                    voltage_sum_v=voltage_sum_v,
                )
                return SolarResult(
                    target_current_a=self.config.solar_min_current_a,
                    valid=True,
                    reason=ControlReason.SOLAR_MODE,
                )

            self.state.active = False
            self.state.stop_condition_since = None
            self.state.last_transition_monotonic = now
            self.state.last_stop_monotonic = now
            self._set_calculation_diagnostics(
                target_current_a=None,
                phase_count=phase_count,
                phase_source=phase_source,
                voltage_sum_v=voltage_sum_v,
            )
            return SolarResult(
                target_current_a=None,
                valid=True,
                reason=ControlReason.BELOW_MIN_CURRENT,
            )

        self.state.stop_condition_since = None
        if surplus_w < effective_start_threshold_w:
            self.state.start_condition_since = None
            self._set_calculation_diagnostics(
                target_current_a=None,
                phase_count=phase_count,
                phase_source=phase_source,
                voltage_sum_v=voltage_sum_v,
            )
            return SolarResult(
                target_current_a=None,
                valid=True,
                reason=ControlReason.BELOW_MIN_CURRENT,
            )

        if (now - self.state.last_stop_monotonic) < self.config.solar_min_pause_s:
            self._set_calculation_diagnostics(
                target_current_a=None,
                phase_count=phase_count,
                phase_source=phase_source,
                voltage_sum_v=voltage_sum_v,
            )
            return SolarResult(
                target_current_a=None,
                valid=True,
                reason=ControlReason.BELOW_MIN_CURRENT,
            )

        if self.state.start_condition_since is None:
            self.state.start_condition_since = now

        if (now - self.state.start_condition_since) < self.config.solar_start_delay_s:
            self._set_calculation_diagnostics(
                target_current_a=None,
                phase_count=phase_count,
                phase_source=phase_source,
                voltage_sum_v=voltage_sum_v,
            )
            return SolarResult(
                target_current_a=None,
                valid=True,
                reason=ControlReason.BELOW_MIN_CURRENT,
            )

        self.state.active = True
        self.state.start_condition_since = None
        self.state.last_transition_monotonic = now
        self._set_calculation_diagnostics(
            target_current_a=max(target_current, self.config.solar_min_current_a),
            phase_count=phase_count,
            phase_source=phase_source,
            voltage_sum_v=voltage_sum_v,
        )
        return SolarResult(
            target_current_a=max(target_current, self.config.solar_min_current_a),
            valid=True,
            reason=ControlReason.SOLAR_MODE,
        )

    @staticmethod
    def resolve_effective_strategy(
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
            raw_surplus_w = max(0.0, sensors.surplus_power_w)
            self.state.raw_surplus_w = raw_surplus_w
            return self._apply_export_deadband(raw_surplus_w)

        if sensors.grid_power_w is None:
            self.state.raw_surplus_w = None
            return None

        charger_power_w = self._trusted_charger_power_w(wallbox)
        if self.config.solar_input_model == SolarInputModel.DSMR_IMPORT_EXPORT:
            # DSMR import/export is normalized by the coordinator as import - export,
            # so export is always the negative direction regardless of UI sign setting.
            signed_export_w = -sensors.grid_power_w
        elif self.config.solar_grid_power_direction == "positive_export":
            signed_export_w = sensors.grid_power_w
        else:
            signed_export_w = -sensors.grid_power_w

        self.state.raw_surplus_w = max(0.0, charger_power_w + signed_export_w)
        signed_export_w = self._apply_signed_grid_deadband(signed_export_w)
        return max(0.0, charger_power_w + signed_export_w)

    def apply_ramp_limit(
        self,
        target_current_a: float | None,
        wallbox: WallboxState,
        *,
        baseline_current_a: float | None = None,
    ) -> float | None:
        if target_current_a is None:
            self.state.last_target_current_a = None
            return None

        ramp_up_a = max(0.0, self.config.solar_ramp_up_current_a)
        if ramp_up_a <= 0.0:
            self.state.last_target_current_a = target_current_a
            return target_current_a

        baseline = self.state.last_target_current_a
        if baseline is None:
            baseline = baseline_current_a
        if baseline is None and wallbox.current_limit_a is not None:
            baseline = wallbox.current_limit_a

        if baseline is None or target_current_a <= baseline:
            limited = target_current_a
        else:
            limited = min(target_current_a, max(self.config.min_current_a, baseline + ramp_up_a))

        self.state.last_target_current_a = limited
        return round(limited, 1)

    def _set_calculation_diagnostics(
        self,
        *,
        target_current_a: float | None,
        phase_count: int | None,
        phase_source: str | None,
        voltage_sum_v: float | None,
    ) -> None:
        self.state.target_current_a = target_current_a
        self.state.phase_count = phase_count
        self.state.phase_source = phase_source
        self.state.voltage_sum_v = voltage_sum_v

    def _clear_calculation_diagnostics(self) -> None:
        self.state.target_current_a = None
        self.state.phase_count = None
        self.state.phase_source = None
        self.state.voltage_sum_v = None

    def _filtered_surplus(self, surplus_w: float) -> float:
        smoothing_time_s = max(0.0, self.config.solar_smoothing_time_s)
        now = self.monotonic_fn()
        previous = self.state.filtered_surplus_w
        previous_time = self.state.filtered_surplus_monotonic
        if smoothing_time_s <= 0.0 or previous is None or previous_time is None:
            self.state.filtered_surplus_w = surplus_w
            self.state.filtered_surplus_monotonic = now
            return surplus_w

        elapsed_s = max(0.0, now - previous_time)
        alpha = elapsed_s / (smoothing_time_s + elapsed_s) if elapsed_s > 0.0 else 0.0
        filtered = previous + alpha * (surplus_w - previous)
        self.state.filtered_surplus_w = filtered
        self.state.filtered_surplus_monotonic = now
        return filtered

    def _reset_surplus_filter(self) -> None:
        self.state.filtered_surplus_w = None
        self.state.filtered_surplus_monotonic = None

    def _apply_signed_grid_deadband(self, signed_export_w: float) -> float:
        if 0.0 < signed_export_w < self.config.solar_export_deadband_w:
            return 0.0
        if -self.config.solar_import_deadband_w < signed_export_w < 0.0:
            return 0.0
        return signed_export_w

    def _apply_export_deadband(self, surplus_w: float) -> float:
        if 0.0 < surplus_w < self.config.solar_export_deadband_w:
            return 0.0
        return surplus_w

    @staticmethod
    def _trusted_charger_power_w(wallbox: WallboxState | None) -> float:
        if wallbox is None or not wallbox.charging_active:
            return 0.0
        return max(0.0, wallbox.active_power_w or 0.0)
