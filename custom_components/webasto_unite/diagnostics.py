
from __future__ import annotations

from homeassistant.components.diagnostics import async_redact_data

from .const import DOMAIN
from .control_owner import derive_control_owner_from_snapshot

TO_REDACT = {
    "host",
    "serial_number",
    "charge_point_id",
    "rest_password",
    "password",
    "authorizationKey",
    "token",
    "access_token",
    "mac",
    "mac_address",
    "ipAddress",
    "centralSystemAddress",
    "rfid",
    "tag",
    "ssid",
}


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
                "control_owner": derive_control_owner_from_snapshot(coordinator.data),
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
                "control_writes_enabled": coordinator.data.control_writes_enabled,
                "last_control_write_value_a": coordinator.data.last_control_write_value_a,
                "last_control_write_reason": coordinator.data.last_control_write_reason,
                "last_control_write_register": coordinator.data.last_control_write_register,
                "last_control_write_age_s": coordinator.data.last_control_write_age_s,
                "last_control_write_blocked_reason": coordinator.data.last_control_write_blocked_reason,
                "last_control_write_verification_status": coordinator.data.last_control_write_verification_status,
                "last_control_write_verification_reported_a": coordinator.data.last_control_write_verification_reported_a,
                "last_control_write_verification_delta_a": coordinator.data.last_control_write_verification_delta_a,
                "mode_target_a": coordinator.data.mode_target_a,
                "dlb_limit_a": coordinator.data.dlb_limit_a,
                "final_target_a": coordinator.data.final_target_a,
                "solar_raw_surplus_w": coordinator.data.solar_raw_surplus_w,
                "solar_filtered_surplus_w": coordinator.data.solar_filtered_surplus_w,
                "solar_target_current_a": coordinator.data.solar_target_current_a,
                "solar_phase_count": coordinator.data.solar_phase_count,
                "solar_phase_source": coordinator.data.solar_phase_source,
                "solar_voltage_sum_v": coordinator.data.solar_voltage_sum_v,
                "phase_switch_mode_raw": coordinator.data.phase_switch_mode_raw,
                "phase_switch_mode": coordinator.data.phase_switch_mode,
                "phase_switch_register_available": coordinator.data.phase_switch_register_available,
                "phase_switch_available": coordinator.data.phase_switch_available,
                "phase_switch_block_reason": coordinator.data.phase_switch_block_reason,
                "observed_session_phase_usage": coordinator.data.observed_session_phase_usage,
                "phase_offer_state": coordinator.data.phase_offer_state,
                "phase_recovery_warning": coordinator.data.phase_recovery_warning,
                "phase_switching_mode": coordinator.data.phase_switching_mode,
                "phase_switch_default_mode": coordinator.data.phase_switch_default_mode,
                "phase_session_override_active": coordinator.data.phase_session_override_active,
                "phase_session_target": coordinator.data.phase_session_target,
                "phase_restore_pending": coordinator.data.phase_restore_pending,
                "phase_policy_decision": coordinator.data.phase_policy_decision,
                "phase_policy_block_reason": coordinator.data.phase_policy_block_reason,
                "phase_policy_target": coordinator.data.phase_policy_target,
                "phase_policy_required_surplus_1p_w": coordinator.data.phase_policy_required_surplus_1p_w,
                "phase_policy_required_surplus_3p_w": coordinator.data.phase_policy_required_surplus_3p_w,
                "phase_policy_auto_ready": coordinator.data.phase_policy_auto_ready,
                "phase_policy_auto_block_reason": coordinator.data.phase_policy_auto_block_reason,
                "phase_policy_stable_elapsed_s": coordinator.data.phase_policy_stable_elapsed_s,
                "phase_policy_stable_required_s": coordinator.data.phase_policy_stable_required_s,
                "phase_policy_cooldown_remaining_s": coordinator.data.phase_policy_cooldown_remaining_s,
                "phase_policy_session_switch_count": coordinator.data.phase_policy_session_switch_count,
                "phase_policy_session_switch_limit": coordinator.data.phase_policy_session_switch_limit,
                "phase_switch_last_result": coordinator.data.phase_switch_last_result,
                "phase_switch_last_block_reason": coordinator.data.phase_switch_last_block_reason,
                "phase_switch_last_target": coordinator.data.phase_switch_last_target,
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
