from __future__ import annotations

from typing import Any

from .models import ChargeMode, ControlMode, ControlReason

CONTROL_OWNER_MONITORING_ONLY = "monitoring_only"
CONTROL_OWNER_EXTERNAL_CONTROLLER = "external_controller"
CONTROL_OWNER_MANUAL_PAUSE = "manual_pause"
CONTROL_OWNER_FALLBACK = "fallback"
CONTROL_OWNER_DLB = "dlb"
CONTROL_OWNER_SOLAR = "solar"
CONTROL_OWNER_FIXED_CURRENT = "fixed_current"
CONTROL_OWNER_INTEGRATION = "integration"
CONTROL_OWNER_UNKNOWN = "unknown"


def derive_control_owner(
    *,
    control_mode: ControlMode,
    charging_paused: bool,
    effective_mode: ChargeMode,
    fixed_current_until_unplug_active: bool,
    control_reason: str | ControlReason | None,
    dominant_limit_reason: str | ControlReason | None,
    fallback_active: bool,
) -> str:
    """Return the current high-level owner of charger current control."""
    control_reason = _enum_value(control_reason)
    dominant_limit_reason = _enum_value(dominant_limit_reason)

    if control_mode == ControlMode.KEEPALIVE_ONLY:
        return CONTROL_OWNER_MONITORING_ONLY
    if control_mode == ControlMode.EXTERNAL_CONTROLLER:
        return CONTROL_OWNER_EXTERNAL_CONTROLLER
    if charging_paused:
        return CONTROL_OWNER_MANUAL_PAUSE
    if fallback_active or control_reason in {
        ControlReason.SAFE_CURRENT_FALLBACK.value,
        ControlReason.SENSOR_UNAVAILABLE.value,
        ControlReason.COMMUNICATION_LOSS.value,
    }:
        return CONTROL_OWNER_FALLBACK
    if dominant_limit_reason == ControlReason.DLB_LIMITED.value:
        return CONTROL_OWNER_DLB
    if effective_mode == ChargeMode.SOLAR or control_reason == ControlReason.SOLAR_MODE.value:
        return CONTROL_OWNER_SOLAR
    if (
        effective_mode == ChargeMode.FIXED_CURRENT
        or fixed_current_until_unplug_active
        or control_reason == ControlReason.FIXED_CURRENT_MODE.value
    ):
        return CONTROL_OWNER_FIXED_CURRENT
    if control_mode == ControlMode.MANAGED_CONTROL:
        return CONTROL_OWNER_INTEGRATION
    return CONTROL_OWNER_UNKNOWN


def derive_control_owner_from_snapshot(data: Any | None) -> str | None:
    if data is None:
        return None
    return derive_control_owner(
        control_mode=data.control_mode,
        charging_paused=data.charging_paused,
        effective_mode=data.effective_mode,
        fixed_current_until_unplug_active=data.fixed_current_until_unplug_active,
        control_reason=data.control_reason,
        dominant_limit_reason=data.dominant_limit_reason,
        fallback_active=data.fallback_active,
    )


def present_control_owner(owner: str | None) -> str | None:
    if owner is None:
        return None
    return {
        CONTROL_OWNER_MONITORING_ONLY: "Monitoring Only",
        CONTROL_OWNER_EXTERNAL_CONTROLLER: "External Controller",
        CONTROL_OWNER_MANUAL_PAUSE: "Manual Pause",
        CONTROL_OWNER_FALLBACK: "Fallback",
        CONTROL_OWNER_DLB: "DLB",
        CONTROL_OWNER_SOLAR: "Solar",
        CONTROL_OWNER_FIXED_CURRENT: "Fixed Current",
        CONTROL_OWNER_INTEGRATION: "Integration",
        CONTROL_OWNER_UNKNOWN: "Unknown",
    }.get(owner, owner)


def _enum_value(value: str | ControlReason | None) -> str | None:
    if isinstance(value, ControlReason):
        return value.value
    return value
