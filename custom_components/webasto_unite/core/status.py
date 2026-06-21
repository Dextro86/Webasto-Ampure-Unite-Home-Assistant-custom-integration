from __future__ import annotations

from ..controller import WallboxController
from ..models import ChargeMode, ControlConfig, ControlMode, ControlReason, SolarControlStrategy


def build_operating_state(
    *,
    effective_mode: ChargeMode,
    charging_paused: bool,
    fixed_current_until_unplug_active: bool,
    solar_until_unplug_active: bool,
    control_config: ControlConfig,
    decision,
    solar_strategy: SolarControlStrategy | None = None,
) -> str:
    solar_strategy = solar_strategy or control_config.solar_control_strategy
    if control_config.control_mode == ControlMode.KEEPALIVE_ONLY:
        return "monitoring_only_not_writing"
    if control_config.control_mode == ControlMode.EXTERNAL_CONTROLLER:
        return "external_controller"
    if effective_mode == ChargeMode.OFF and charging_paused:
        return "paused"
    if effective_mode == ChargeMode.OFF:
        return "off"
    if decision.fallback_active:
        return "fallback"
    if effective_mode == ChargeMode.FIXED_CURRENT and fixed_current_until_unplug_active:
        return "fixed_current_until_unplug"
    if effective_mode == ChargeMode.FIXED_CURRENT:
        return "fixed_current"
    if (
        effective_mode == ChargeMode.SOLAR
        and solar_until_unplug_active
        and decision.reason == ControlReason.BELOW_MIN_CURRENT
    ):
        return "waiting_for_solar"
    if (
        effective_mode == ChargeMode.SOLAR
        and solar_until_unplug_active
        and WallboxController.resolve_effective_solar_strategy(
            solar_strategy,
            control_config.solar_until_unplug_strategy,
            True,
        )
        == SolarControlStrategy.MIN_PLUS_SURPLUS
    ):
        return "solar_until_unplug"
    if effective_mode == ChargeMode.SOLAR and solar_until_unplug_active:
        return "solar_until_unplug"
    if effective_mode == ChargeMode.SOLAR and decision.reason == ControlReason.BELOW_MIN_CURRENT:
        return "waiting_for_solar"
    if effective_mode == ChargeMode.SOLAR and decision.reason == ControlReason.SENSOR_UNAVAILABLE:
        return "fallback"
    if decision.dominant_limit_reason == ControlReason.DLB_LIMITED:
        return "dlb_limited"
    if effective_mode == ChargeMode.SOLAR:
        if solar_strategy == SolarControlStrategy.ECO_SOLAR:
            return "eco_solar"
        if solar_strategy == SolarControlStrategy.SMART_SOLAR:
            return "smart_solar"
        if solar_strategy == SolarControlStrategy.SOLAR_BOOST:
            return "solar_boost"
        return "solar"
    return "normal"
