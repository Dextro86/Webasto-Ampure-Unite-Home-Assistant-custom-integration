
from __future__ import annotations

from homeassistant.components.diagnostics import async_redact_data

from .const import DOMAIN

TO_REDACT = {"host", "serial_number", "charge_point_id"}


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": async_redact_data(dict(entry.options), TO_REDACT),
        "runtime": async_redact_data(coordinator.data.as_dict(), TO_REDACT) if coordinator.data is not None else None,
        "wallbox_summary": async_redact_data(coordinator.data.as_dict().get("wallbox"), TO_REDACT) if coordinator.data is not None else None,
        "identity_summary": (
            async_redact_data(
                {
                    "serial_number": coordinator.data.wallbox.serial_number,
                    "charge_point_id": coordinator.data.wallbox.charge_point_id,
                    "brand": coordinator.data.wallbox.brand,
                    "model_name": coordinator.data.wallbox.model_name,
                    "firmware_version": coordinator.data.wallbox.firmware_version,
                    "charge_point_phase_count": coordinator.data.wallbox.charge_point_phase_count,
                },
                TO_REDACT,
            )
            if coordinator.data is not None
            else None
        ),
        "control_summary": (
            {
                "mode": coordinator.data.mode.value,
                "effective_mode": coordinator.data.effective_mode.value,
                "operating_state": coordinator.data.operating_state,
                "control_mode": coordinator.data.control_mode.value,
                "capability_summary": coordinator.data.capability_summary,
                "control_reason": coordinator.data.control_reason,
                "charging_paused": coordinator.data.charging_paused,
                "solar_until_unplug_active": coordinator.data.solar_until_unplug_active,
                "fixed_current_until_unplug_active": coordinator.data.fixed_current_until_unplug_active,
                "keepalive_age_s": coordinator.data.keepalive_age_s,
                "keepalive_interval_s": coordinator.data.keepalive_interval_s,
                "keepalive_overdue": coordinator.data.keepalive_overdue,
                "keepalive_sent_count": coordinator.data.keepalive_sent_count,
                "keepalive_write_failures": coordinator.data.keepalive_write_failures,
                "dominant_limit_reason": coordinator.data.dominant_limit_reason,
                "fallback_active": coordinator.data.fallback_active,
                "sensor_snapshot_valid": coordinator.data.sensor_snapshot_valid,
                "sensor_invalid_reason": coordinator.data.sensor_invalid_reason,
                "mode_target_a": coordinator.data.mode_target_a,
                "dlb_limit_a": coordinator.data.dlb_limit_a,
                "final_target_a": coordinator.data.final_target_a,
                "pending_write_kind": coordinator.data.pending_write_kind,
            }
            if coordinator.data is not None
            else None
        ),
        "capabilities": (dict(coordinator.data.capabilities) if coordinator.data is not None else None),
        "session_summary": (
            {
                "session_energy_kwh": coordinator.data.wallbox.session_energy_kwh,
                "energy_meter_kwh": coordinator.data.wallbox.energy_meter_kwh,
                "session_start_time": coordinator.data.wallbox.session_start_time,
                "session_duration_s": coordinator.data.wallbox.session_duration_s,
                "session_end_time": coordinator.data.wallbox.session_end_time,
            }
            if coordinator.data is not None
            else None
        ),
        "client_stats": {
            "connected": coordinator.client.stats.connected,
            "connect_attempts": coordinator.client.stats.connect_attempts,
            "read_failures": coordinator.client.stats.read_failures,
            "write_failures": coordinator.client.stats.write_failures,
            "reconnects": coordinator.client.stats.reconnects,
            "last_error": coordinator.client.stats.last_error,
        },
    }
