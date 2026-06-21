from __future__ import annotations

from dataclasses import dataclass

from ..const import CONF_STARTUP_CHARGE_MODE, DEFAULT_STARTUP_CHARGE_MODE
from ..models import (
    ChargeMode,
    ControlConfig,
    SolarControlStrategy,
    normalize_charge_mode,
)


@dataclass(slots=True)
class ModeRuntimeState:
    """Runtime charge-mode state independent from Home Assistant plumbing."""

    mode: ChargeMode = ChargeMode.NORMAL
    active_solar_strategy: SolarControlStrategy | None = None
    charging_paused: bool = False
    solar_until_unplug_active: bool = False
    fixed_current_until_unplug_active: bool = False

    def resolve_active_solar_strategy(self, default_strategy: SolarControlStrategy) -> SolarControlStrategy:
        strategy = self.active_solar_strategy or default_strategy
        if strategy == SolarControlStrategy.DISABLED:
            return default_strategy
        return strategy

    def effective_mode(self) -> ChargeMode:
        if self.mode == ChargeMode.OFF or self.charging_paused:
            return ChargeMode.OFF
        if self.fixed_current_until_unplug_active:
            return ChargeMode.FIXED_CURRENT
        if self.solar_until_unplug_active:
            return ChargeMode.SOLAR
        return self.mode

    def set_mode(
        self,
        mode: ChargeMode,
        *,
        default_solar_strategy: SolarControlStrategy,
        solar_strategy: SolarControlStrategy | None = None,
    ) -> bool:
        self.mode = mode
        self.solar_until_unplug_active = False
        self.fixed_current_until_unplug_active = False
        if mode == ChargeMode.SOLAR:
            self.active_solar_strategy = solar_strategy or default_solar_strategy
        return mode != ChargeMode.SOLAR

    def reset_to_default(self, mode: ChargeMode, default_solar_strategy: SolarControlStrategy) -> None:
        self.mode = mode
        self.active_solar_strategy = default_solar_strategy
        self.solar_until_unplug_active = False
        self.fixed_current_until_unplug_active = False

    def pause(self) -> None:
        self.charging_paused = True

    def resume(self) -> None:
        self.charging_paused = False

    def set_solar_until_unplug(self, enabled: bool) -> None:
        self.solar_until_unplug_active = enabled
        if enabled:
            self.fixed_current_until_unplug_active = False

    def set_fixed_current_until_unplug(self, enabled: bool) -> None:
        self.fixed_current_until_unplug_active = enabled
        if enabled:
            self.solar_until_unplug_active = False


def resolve_startup_mode(merged_options: dict, control_config: ControlConfig) -> ChargeMode:
    try:
        mode = normalize_charge_mode(
            merged_options.get(CONF_STARTUP_CHARGE_MODE, DEFAULT_STARTUP_CHARGE_MODE),
            control_config.solar_control_strategy,
        )
    except ValueError:
        return ChargeMode.NORMAL
    if mode == ChargeMode.SOLAR and control_config.solar_control_strategy == SolarControlStrategy.DISABLED:
        return ChargeMode.NORMAL
    return mode
