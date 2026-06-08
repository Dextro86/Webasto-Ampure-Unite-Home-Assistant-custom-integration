
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.const import EntityCategory, UnitOfElectricCurrent, UnitOfEnergy, UnitOfPower, UnitOfTime, UnitOfElectricPotential
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, PHASE_SWITCHING_MODE_AUTOMATIC_SOLAR, PHASE_SWITCHING_MODE_MANUAL_ONLY
from .control_owner import derive_control_owner_from_snapshot, present_control_owner
from .entity import WebastoUniteCoordinatorEntity
from .evcc import build_evcc_status
from .models import ChargeMode
from .phase_observer import PHASE_SWITCH_VALUE_1P, PHASE_SWITCH_VALUE_3P
from .registers import NUMBER_OF_PHASES, PHASE_SWITCH_MODE


@dataclass(frozen=True, kw_only=True)
class WebastoSensorDescription(SensorEntityDescription):
    value_key: str


SENSORS = (
    WebastoSensorDescription(key="operating_state", name="Charging Behavior", value_key="operating_state"),
    WebastoSensorDescription(key="effective_mode", name="Active Mode", value_key="effective_mode"),
    WebastoSensorDescription(key="charge_point_state_text", name="Charge Point State", value_key="charge_point_state_raw", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="charging_state_text", name="Charging State", value_key="charge_state_raw", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="iec61851_state", name="IEC 61851 State", value_key="iec61851_state", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="equipment_state_text", name="Equipment State", value_key="evse_state_raw", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="cable_state_text", name="Cable State", value_key="cable_state_raw", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="evse_fault_code", name="EVSE Fault Code", value_key="error_code", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="effective_active_phases", name="Effective Active Phases", value_key="phases_in_use", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="charger_reported_phases", name="Charger Phase Register 404", value_key="charge_point_phase_count", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="phase_switch_mode", name="Phase Switch Mode", value_key="phase_switch_mode", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="phase_switch_mode_raw", name="Phase Switch Mode Raw", value_key="phase_switch_mode_raw", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="phase_switch_available", name="Phase Switch Available", value_key="phase_switch_available", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="phase_switch_block_reason", name="Phase Switch Block Reason", value_key="phase_switch_block_reason", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="vehicle_phase_capability", name="Observed Session Phase Usage", value_key="vehicle_phase_capability", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="phase_switching_mode", name="Phase Switching Mode", value_key="phase_switching_mode", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="phase_switch_default_mode", name="Default Phase Mode", value_key="phase_switch_default_mode", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="phase_session_override_active", name="Phase Session Override", value_key="phase_session_override_active", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="phase_session_target", name="Phase Session Target", value_key="phase_session_target", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="phase_restore_pending", name="Phase Restore Pending", value_key="phase_restore_pending", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="phase_policy_decision", name="Phase Policy Decision", value_key="phase_policy_decision", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="phase_policy_block_reason", name="Phase Policy Block Reason", value_key="phase_policy_block_reason", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="phase_policy_target", name="Phase Policy Target", value_key="phase_policy_target", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="phase_policy_required_surplus_1p", name="Phase Policy Required Surplus 1P", value_key="phase_policy_required_surplus_1p_w", native_unit_of_measurement=UnitOfPower.WATT, device_class=SensorDeviceClass.POWER, entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="phase_policy_required_surplus_3p", name="Phase Policy Required Surplus 3P", value_key="phase_policy_required_surplus_3p_w", native_unit_of_measurement=UnitOfPower.WATT, device_class=SensorDeviceClass.POWER, entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="phase_policy_auto_ready", name="Phase Policy Auto Ready", value_key="phase_policy_auto_ready", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="phase_policy_auto_block_reason", name="Phase Policy Auto Block Reason", value_key="phase_policy_auto_block_reason", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="phase_policy_stable_elapsed", name="Phase Policy Stable Target Time", value_key="phase_policy_stable_elapsed_s", native_unit_of_measurement=UnitOfTime.SECONDS, entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="phase_policy_stable_required", name="Phase Policy Required Target Time", value_key="phase_policy_stable_required_s", native_unit_of_measurement=UnitOfTime.SECONDS, entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="phase_policy_cooldown_remaining", name="Phase Policy Cooldown Remaining", value_key="phase_policy_cooldown_remaining_s", native_unit_of_measurement=UnitOfTime.SECONDS, entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="phase_policy_session_switch_count", name="Phase Policy Session Switch Count", value_key="phase_policy_session_switch_count", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="phase_policy_session_switch_limit", name="Phase Policy Session Switch Limit", value_key="phase_policy_session_switch_limit", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="phase_switch_last_result", name="Last Phase Switch Result", value_key="phase_switch_last_result", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="phase_switch_last_block_reason", name="Last Phase Switch Block Reason", value_key="phase_switch_last_block_reason", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="phase_switch_last_target", name="Last Phase Switch Target", value_key="phase_switch_last_target", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="phase_switch_state", name="Phase Switch State", value_key="phase_switch_state", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="active_power", name="Active Power", value_key="active_power_w", native_unit_of_measurement=UnitOfPower.WATT, device_class=SensorDeviceClass.POWER, state_class=SensorStateClass.MEASUREMENT),
    WebastoSensorDescription(key="active_power_l1", name="Active Power L1", value_key="active_power_l1_w", native_unit_of_measurement=UnitOfPower.WATT, device_class=SensorDeviceClass.POWER, state_class=SensorStateClass.MEASUREMENT),
    WebastoSensorDescription(key="active_power_l2", name="Active Power L2", value_key="active_power_l2_w", native_unit_of_measurement=UnitOfPower.WATT, device_class=SensorDeviceClass.POWER, state_class=SensorStateClass.MEASUREMENT),
    WebastoSensorDescription(key="active_power_l3", name="Active Power L3", value_key="active_power_l3_w", native_unit_of_measurement=UnitOfPower.WATT, device_class=SensorDeviceClass.POWER, state_class=SensorStateClass.MEASUREMENT),
    WebastoSensorDescription(key="current_l1", name="Current L1", value_key="current_l1_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE, device_class=SensorDeviceClass.CURRENT, state_class=SensorStateClass.MEASUREMENT),
    WebastoSensorDescription(key="current_l2", name="Current L2", value_key="current_l2_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE, device_class=SensorDeviceClass.CURRENT, state_class=SensorStateClass.MEASUREMENT),
    WebastoSensorDescription(key="current_l3", name="Current L3", value_key="current_l3_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE, device_class=SensorDeviceClass.CURRENT, state_class=SensorStateClass.MEASUREMENT),
    WebastoSensorDescription(key="actual_current", name="Actual Phase Current", value_key="actual_current_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE, device_class=SensorDeviceClass.CURRENT, state_class=SensorStateClass.MEASUREMENT),
    WebastoSensorDescription(key="voltage_l1", name="Voltage L1", value_key="voltage_l1_v", native_unit_of_measurement=UnitOfElectricPotential.VOLT, device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT),
    WebastoSensorDescription(key="voltage_l2", name="Voltage L2", value_key="voltage_l2_v", native_unit_of_measurement=UnitOfElectricPotential.VOLT, device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT),
    WebastoSensorDescription(key="voltage_l3", name="Voltage L3", value_key="voltage_l3_v", native_unit_of_measurement=UnitOfElectricPotential.VOLT, device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT),
    WebastoSensorDescription(key="configured_limit", name="Reported Current Limit", value_key="current_limit_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE, device_class=SensorDeviceClass.CURRENT, state_class=SensorStateClass.MEASUREMENT),
    WebastoSensorDescription(key="safe_current", name="Safe Current", value_key="safe_current_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE, device_class=SensorDeviceClass.CURRENT, state_class=SensorStateClass.MEASUREMENT),
    WebastoSensorDescription(key="session_max_current", name="Session Max Current", value_key="session_max_current_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE, device_class=SensorDeviceClass.CURRENT, state_class=SensorStateClass.MEASUREMENT),
    WebastoSensorDescription(key="session_energy", name="Session Energy", value_key="session_energy_kwh", native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR, device_class=SensorDeviceClass.ENERGY, state_class=SensorStateClass.TOTAL),
    WebastoSensorDescription(key="energy_meter", name="Energy Meter", value_key="energy_meter_kwh", native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR, device_class=SensorDeviceClass.ENERGY, state_class=SensorStateClass.TOTAL_INCREASING),
    WebastoSensorDescription(key="session_duration", name="Session Duration", value_key="session_duration_s", native_unit_of_measurement=UnitOfTime.SECONDS, entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="dlb_limit", name="DLB Limit", value_key="dlb_limit_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE, device_class=SensorDeviceClass.CURRENT, state_class=SensorStateClass.MEASUREMENT),
    WebastoSensorDescription(key="final_target", name="Final Target", value_key="final_target_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE, device_class=SensorDeviceClass.CURRENT, state_class=SensorStateClass.MEASUREMENT),
    WebastoSensorDescription(key="solar_surplus_input", name="Solar Surplus Input", value_key="solar_surplus_w", native_unit_of_measurement=UnitOfPower.WATT, device_class=SensorDeviceClass.POWER, state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="solar_raw_input", name="Solar Raw Input", value_key="solar_raw_surplus_w", native_unit_of_measurement=UnitOfPower.WATT, device_class=SensorDeviceClass.POWER, state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="solar_filtered_input", name="Solar Filtered Input", value_key="solar_filtered_surplus_w", native_unit_of_measurement=UnitOfPower.WATT, device_class=SensorDeviceClass.POWER, state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="solar_target", name="Solar Target", value_key="solar_target_current_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE, device_class=SensorDeviceClass.CURRENT, state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="solar_phase_count", name="Solar Phase Count", value_key="solar_phase_count", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="solar_phase_source", name="Solar Phase Source", value_key="solar_phase_source", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="solar_voltage_sum", name="Solar Voltage Sum", value_key="solar_voltage_sum_v", native_unit_of_measurement=UnitOfElectricPotential.VOLT, device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="solar_input_state", name="Solar Input State", value_key="solar_input_state", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="control_owner", name="Control Owner", value_key="control_owner", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="reason", name="Control Reason", value_key="control_reason", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="control_writes_enabled", name="Control Writes Enabled", value_key="control_writes_enabled", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="last_control_write_value", name="Last Control Write", value_key="last_control_write_value_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE, device_class=SensorDeviceClass.CURRENT, entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="last_control_write_reason", name="Last Control Write Reason", value_key="last_control_write_reason", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="last_control_write_register", name="Last Control Write Register", value_key="last_control_write_register", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="last_control_write_age", name="Last Control Write Age", value_key="last_control_write_age_s", native_unit_of_measurement=UnitOfTime.SECONDS, entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="last_control_write_blocked_reason", name="Last Control Write Blocked Reason", value_key="last_control_write_blocked_reason", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="limit_reason", name="Dominant Limit", value_key="dominant_limit_reason", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="sensor_invalid_reason", name="Sensor Invalid Reason", value_key="sensor_invalid_reason", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="fallback_active", name="Fallback Active", value_key="fallback_active", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="client_error", name="Client Error", value_key="last_client_error", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="evcc_status", name="EVCC Status", value_key="evcc_status", entity_category=EntityCategory.DIAGNOSTIC),
)


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(WebastoSensor(coordinator, description) for description in SENSORS)


class WebastoSensor(WebastoUniteCoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, description: WebastoSensorDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{description.key}"

    @property
    def native_value(self):
        data = self.coordinator.data
        if data is None:
            return None
        if self.entity_description.key == "charge_point_state_text":
            return self._format_charge_point_state(data.wallbox.charge_point_state_raw)
        if self.entity_description.key == "charging_state_text":
            return self._format_charge_state(data.wallbox.charge_state_raw)
        if self.entity_description.key == "iec61851_state":
            return self._derive_iec61851_state(data.wallbox)
        if self.entity_description.key == "evcc_status":
            return build_evcc_status(data, self.coordinator.control_config)["charger_state"]
        if self.entity_description.key == "control_owner":
            return present_control_owner(derive_control_owner_from_snapshot(data))
        if self.entity_description.key == "equipment_state_text":
            return self._format_equipment_state(data.wallbox.evse_state_raw)
        if self.entity_description.key == "cable_state_text":
            return self._format_cable_state(data.wallbox.cable_state_raw)
        if hasattr(data, self.entity_description.value_key):
            return self._present_value(
                getattr(data, self.entity_description.value_key),
                value_key=self.entity_description.value_key,
            )
        if hasattr(data.wallbox, self.entity_description.value_key):
            return self._present_value(
                getattr(data.wallbox, self.entity_description.value_key),
                value_key=self.entity_description.value_key,
            )
        return None

    @property
    def extra_state_attributes(self):
        if self.coordinator.data is None:
            return None
        if self.entity_description.key == "evcc_status":
            return build_evcc_status(self.coordinator.data, self.coordinator.control_config)
        if self.entity_description.key == "phase_switch_mode":
            return {
                "source": "register_405",
                "capability_source": "register_404",
                "capability_register": NUMBER_OF_PHASES.address,
                "read_register": PHASE_SWITCH_MODE.address,
                "write_register": PHASE_SWITCH_MODE.address,
                "write_value_1p": PHASE_SWITCH_VALUE_1P,
                "write_value_3p": PHASE_SWITCH_VALUE_3P,
                "writes_enabled": self.coordinator.data.phase_switching_mode
                in {PHASE_SWITCHING_MODE_MANUAL_ONLY, PHASE_SWITCHING_MODE_AUTOMATIC_SOLAR},
            }
        if self.entity_description.key != "iec61851_state":
            return None
        wallbox = self.coordinator.data.wallbox
        return {
            "source": "derived",
            "charge_point_state": self._format_charge_point_state(wallbox.charge_point_state_raw),
            "charging_state": self._format_charge_state(wallbox.charge_state_raw),
            "cable_state": self._format_cable_state(wallbox.cable_state_raw),
            "charge_point_state_raw": wallbox.charge_point_state_raw,
            "charging_state_raw": wallbox.charge_state_raw,
            "cable_state_raw": wallbox.cable_state_raw,
        }

    @staticmethod
    def _derive_iec61851_state(wallbox) -> str:
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

    @staticmethod
    def _format_charge_point_state(raw_value):
        mapping = {
            0: "Available",
            1: "Preparing",
            2: "Charging",
            3: "SuspendedEVSE",
            4: "SuspendedEV",
            5: "Finishing",
            6: "Reserved",
            7: "Unavailable",
            8: "Faulted",
        }
        return WebastoSensor._format_raw_state_label(raw_value, mapping)

    @staticmethod
    def _format_charge_state(raw_value):
        mapping = {
            0: "Idle",
            1: "Charging",
        }
        return WebastoSensor._format_raw_state_label(raw_value, mapping)

    @staticmethod
    def _format_equipment_state(raw_value):
        mapping = {
            0: "Initializing",
            1: "Running",
            2: "Fault",
            3: "Disabled",
            4: "Updating",
        }
        return WebastoSensor._format_raw_state_label(raw_value, mapping)

    @staticmethod
    def _format_cable_state(raw_value):
        mapping = {
            0: "Cable Not Connected",
            1: "Cable Connected, Vehicle Not Connected",
            2: "Cable Connected, Vehicle Connected",
            3: "Cable Connected, Vehicle Connected, Cable Locked",
        }
        return WebastoSensor._format_raw_state_label(raw_value, mapping)

    @staticmethod
    def _format_raw_state_label(raw_value, mapping):
        if raw_value is None:
            return None
        raw_int = int(raw_value)
        return mapping.get(raw_int, f"Unknown ({raw_int})")

    @staticmethod
    def _present_value(value, *, value_key: str | None = None):
        if isinstance(value, ChargeMode):
            return {
                ChargeMode.OFF: "Off",
                ChargeMode.NORMAL: "Normal",
                ChargeMode.SOLAR: "Solar",
                ChargeMode.FIXED_CURRENT: "Fixed Current",
            }[value]
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, str):
            if value_key == "solar_input_state":
                return {
                    "ready": "Ready",
                    "unavailable": "Solar Input Unavailable",
                    "disabled": "Disabled",
                }.get(value, value)
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
                "validated_with_optional_gaps": "Validated with Optional Gaps",
                "validated": "Validated",
                "monitoring_only_not_writing": "Monitoring Only - Not Writing",
                "monitoring_only": "Monitoring Only",
                "external_controller": "External Controller",
                "external_controller_mode": "External Controller Mode",
                "off_mode": "Off Mode",
                "normal_mode": "Normal Mode",
                "fixed_current_mode": "Fixed Current Mode",
                "phase_switch_pause": "Phase Switch Pause",
                "phase_switch_resume": "Phase Switch Resume",
                "phase_switch_resume_retry_pause": "Phase Switch Resume Retry Pause",
                "phase_switch_resume_retry": "Phase Switch Resume Retry",
                "solar_mode": "Solar Mode",
                "hardware_limited": "Hardware Limited",
                "cable_limited": "Cable Limited",
                "ev_limited": "EV Limited",
                "safe_current_fallback": "Safe Current Fallback",
                "sensor_unavailable": "Sensor Unavailable",
                "communication_loss": "Communication Loss",
                "below_min_current": "Below Minimum Current",
                "no_change": "No Change",
                "wallbox_active_phases": "Wallbox Active Phases",
                "observed_session_phases": "Observed Session Phases",
                "pre_start_1p_assumption": "Pre-start 1P Assumption",
                "installed_phases": "Charger Configuration",
                "normal": "Normal",
                "ready": "Ready",
                "unavailable": "Unavailable",
                "disabled": "Disabled",
                "likely_1p": "Observed 1P",
                "likely_3p": "Observed 3P",
                "observed_1p": "Observed 1P",
                "observed_3p": "Observed 3P",
                "unknown": "Unknown",
                "charger_not_configured_3p": "Charger Not Configured 3P",
                "charger_preconfigured_1p": "Charger Preconfigured 1P",
                "charger_phase_config_unknown": "Charger Phase Configuration Unknown",
                "integration_configured_1p": "Integration Configured 1P",
                "phase_switch_register_unavailable": "Phase Switch Register Unavailable",
                "vehicle_not_connected": "Vehicle Not Connected",
                "manual_only": "Manual Only",
                "automatic_solar": "Automatic Solar",
                "1p": "1P",
                "3p": "3P",
                "manual_phase_switching_disabled": "Manual Phase Switching Disabled",
                "integration_control_disabled": "Integration Control Disabled",
                "phase_switch_in_progress": "Phase Switch in Progress",
                "phase_switching_not_manual_only": "Phase Switching Not Manual Only",
                "phase_switching_off": "Phase Switching Off",
                "automatic_phase_switching_disabled": "Automatic Phase Switching Disabled",
                "charger_not_preconfigured_3p": "Charger Not Preconfigured 3P",
                "3p_not_observed_in_session": "3P Not Observed In Session",
                "phase_restore_pending": "Phase Restore Pending",
                "not_solar_mode": "Not Solar Mode",
                "solar_input_not_ready": "Solar Input Not Ready",
                "dlb_limited": "DLB Limited",
                "would_request_1p": "Would Request 1P",
                "would_request_3p": "Would Request 3P",
                "no_action": "No Action",
                "cooldown_active": "Cooldown Active",
                "session_switch_limit_reached": "Session Switch Limit Reached",
                "waiting_for_stable_surplus": "Waiting For Stable Phase Target",
                "waiting_for_stable_phase_target": "Waiting For Stable Phase Target",
                "invalid_target_phase": "Invalid Target Phase",
                "charger_state_unavailable": "Charger State Unavailable",
                "charger_unavailable": "Charger Unavailable",
                "vehicle_likely_1p": "Observed 1P",
                "already_in_target_phase": "Already In Target Phase",
                "phase_switch_verify_unavailable": "Phase Switch Verification Unavailable",
                "phase_switch_verify_mismatch": "Phase Switch Verification Mismatch",
                "physical_phase_mismatch": "Physical Phase Mismatch",
                "pause_not_confirmed": "Pause Not Confirmed",
                "blocked": "Blocked",
                "failed": "Failed",
                "queued": "Queued",
                "restore_queued": "Restore Queued",
                "requested": "Requested",
                "pausing": "Pausing",
                "waiting_for_pause": "Waiting For Pause",
                "waiting_before_write": "Waiting Before Phase Write",
                "writing_register": "Writing Phase Register",
                "waiting_before_register_verify": "Waiting Before Register Verification",
                "verifying_register": "Verifying Phase Register",
                "waiting_before_resume": "Waiting Before Resume",
                "resuming": "Resuming",
                "retrying_sequence": "Retrying Phase Switch",
                "retry_pausing": "Retry Pausing",
                "retry_writing_register": "Retry Writing Phase Register",
                "retry_waiting_before_resume": "Retry Waiting Before Resume",
                "retry_resuming": "Retry Resuming",
                "resume_retry": "Resume Retry",
                "observing_physical": "Observing Physical Phases",
                "verified": "Verified",
                "unverified": "Unverified",
                "register_verified": "Register Verified",
                "register_unverified": "Register Unverified",
                "register_reverted": "Register Reverted",
                "physical_verified": "Physical Verified",
                "physical_timeout": "Physical Timeout",
                "vehicle_did_not_resume": "Vehicle Did Not Resume",
                "register_verified_physical_mismatch": "Register Verified, Physical Mismatch",
            }.get(value, value)
        return value
