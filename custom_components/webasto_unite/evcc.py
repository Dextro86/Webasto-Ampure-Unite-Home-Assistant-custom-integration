from __future__ import annotations

from typing import Any

from .models import ControlConfig, RuntimeSnapshot


def derive_iec61851_state(wallbox) -> str:
    if wallbox.charge_point_state_raw == 8 or wallbox.evse_state_raw == 2:
        return "E"
    if wallbox.charge_point_state_raw == 7:
        return "F"
    if wallbox.charge_state_raw == 1 or wallbox.charging_active:
        return "C"
    if not wallbox.vehicle_connected:
        return "A"
    if wallbox.vehicle_connected:
        return "B"
    return "Unknown"


def _format_raw_state_label(raw_value, mapping):
    if raw_value is None:
        return None
    raw_int = int(raw_value)
    return mapping.get(raw_int, f"Unknown ({raw_int})")


def format_charge_point_state(raw_value):
    return _format_raw_state_label(
        raw_value,
        {
            0: "Available",
            1: "Preparing",
            2: "Charging",
            3: "SuspendedEVSE",
            4: "SuspendedEV",
            5: "Finishing",
            6: "Reserved",
            7: "Unavailable",
            8: "Faulted",
        },
    )


def format_charge_state(raw_value):
    return _format_raw_state_label(raw_value, {0: "Idle", 1: "Charging"})


def format_equipment_state(raw_value):
    return _format_raw_state_label(
        raw_value,
        {
            0: "Initializing",
            1: "Running",
            2: "Fault",
            3: "Disabled",
            4: "Updating",
        },
    )


def format_cable_state(raw_value):
    return _format_raw_state_label(
        raw_value,
        {
            0: "Cable Not Connected",
            1: "Cable Connected, Vehicle Not Connected",
            2: "Cable Connected, Vehicle Connected",
            3: "Cable Connected, Vehicle Connected, Cable Locked",
        },
    )


def present_evcc_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return {
        "paused": "Paused",
        "off": "Off",
        "fallback": "Safe Fallback",
        "fixed_current_until_unplug": "Fixed Current Until Unplug",
        "fixed_current": "Fixed Current",
        "waiting_for_solar": "Waiting for Solar",
        "solar_until_unplug": "Solar Until Unplug",
        "eco_solar": "Eco Solar",
        "smart_solar": "Smart Solar",
        "solar_boost": "Solar Boost",
        "solar": "Solar",
        "dlb_limited": "DLB Limited",
        "monitoring_only_not_writing": "Monitoring Only - Not Writing",
        "normal": "Normal",
        "managed_control": "Enabled",
        "keepalive_only": "Monitoring Only",
        "off_mode": "Off Mode",
        "normal_mode": "Normal Mode",
        "fixed_current_mode": "Fixed Current Mode",
        "solar_mode": "Solar Mode",
        "hardware_limited": "Hardware Limited",
        "cable_limited": "Cable Limited",
        "ev_limited": "EV Limited",
        "safe_current_fallback": "Safe Current Fallback",
        "sensor_unavailable": "Sensor Unavailable",
        "communication_loss": "Communication Loss",
        "below_min_current": "Below Minimum Current",
        "no_change": "No Change",
        "ready": "Ready",
        "unavailable": "Unavailable",
        "disabled": "Disabled",
        "charger_unavailable": "Charger Unavailable",
        "no_runtime_data": "No Runtime Data",
    }.get(value, value)


def build_evcc_status(data: RuntimeSnapshot | None, config: ControlConfig | None = None) -> dict[str, Any]:
    """Build a stable, read-only compatibility view for EVCC and automations."""
    if data is None:
        return {
            "charger_state": "unknown",
            "charger_state_label": "Unknown",
            "iec61851_state": "Unknown",
            "enabled": None,
            "charging_enabled": None,
            "vehicle_connected": None,
            "charging": None,
            "faulted": None,
            "unavailable_reason": "no_runtime_data",
            "unavailable_reason_label": "No Runtime Data",
        }

    wallbox = data.wallbox
    faulted = wallbox.charge_point_state_raw == 8 or wallbox.evse_state_raw == 2
    unavailable = not wallbox.available
    unavailable_reason = None
    if unavailable:
        unavailable_reason = data.last_client_error or "charger_unavailable"
    elif data.sensor_invalid_reason:
        unavailable_reason = data.sensor_invalid_reason

    return {
        "charger_state": data.operating_state or "unknown",
        "charger_state_label": present_evcc_value(data.operating_state or "unknown"),
        "iec61851_state": derive_iec61851_state(wallbox),
        "max_current": data.final_target_a,
        "offered_current": wallbox.current_limit_a,
        "actual_current": wallbox.actual_current_a,
        "actual_power": wallbox.active_power_w,
        "session_energy": wallbox.session_energy_kwh,
        "active_phases_observed": wallbox.phases_in_use,
        "phase_count_observed": wallbox.phases_in_use,
        "controllable_current_min": wallbox.hardware_min_current_a,
        "controllable_current_max": wallbox.session_max_current_a,
        "configured_current_min": config.min_current_a if config is not None else None,
        "configured_current_max": config.max_current_a if config is not None else None,
        "enabled": not data.charging_paused,
        "charging_enabled": not data.charging_paused,
        "vehicle_connected": wallbox.vehicle_connected,
        "charging": wallbox.charging_active,
        "faulted": faulted,
        "available": wallbox.available,
        "unavailable_reason": unavailable_reason,
        "unavailable_reason_label": present_evcc_value(unavailable_reason),
        "control_mode": data.control_mode.value,
        "control_mode_label": present_evcc_value(data.control_mode.value),
        "effective_mode": data.effective_mode.value,
        "effective_mode_label": present_evcc_value(data.effective_mode.value),
        "control_reason": data.control_reason,
        "control_reason_label": present_evcc_value(data.control_reason),
        "dominant_limit_reason": data.dominant_limit_reason,
        "dominant_limit_reason_label": present_evcc_value(data.dominant_limit_reason),
        "dlb_limit": data.dlb_limit_a,
        "solar_input_state": data.solar_input_state,
        "solar_input_state_label": present_evcc_value(data.solar_input_state),
        "solar_raw_surplus": data.solar_raw_surplus_w,
        "solar_filtered_surplus": data.solar_filtered_surplus_w,
        "solar_target_current": data.solar_target_current_a,
        "charge_point_state": format_charge_point_state(wallbox.charge_point_state_raw),
        "charging_state": format_charge_state(wallbox.charge_state_raw),
        "equipment_state": format_equipment_state(wallbox.evse_state_raw),
        "cable_state": format_cable_state(wallbox.cable_state_raw),
    }
