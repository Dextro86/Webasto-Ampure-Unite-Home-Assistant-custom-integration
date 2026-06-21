
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
from .features.phase_observer import PHASE_SWITCH_VALUE_1P, PHASE_SWITCH_VALUE_3P
from .modbus.registers import NUMBER_OF_PHASES, PHASE_SWITCH_MODE


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
    WebastoSensorDescription(key="phase_requested", name="Requested Phase", value_key="phase_requested", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="phase_observed", name="Observed Phase", value_key="phase_observed", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="phase_recovery_state", name="Phase Recovery State", value_key="phase_recovery_state", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="active_power", name="Active Power", value_key="active_power_w", native_unit_of_measurement=UnitOfPower.WATT, device_class=SensorDeviceClass.POWER, state_class=SensorStateClass.MEASUREMENT),
    WebastoSensorDescription(key="active_power_l1", name="Active Power L1", value_key="active_power_l1_w", native_unit_of_measurement=UnitOfPower.WATT, device_class=SensorDeviceClass.POWER, state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="active_power_l2", name="Active Power L2", value_key="active_power_l2_w", native_unit_of_measurement=UnitOfPower.WATT, device_class=SensorDeviceClass.POWER, state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="active_power_l3", name="Active Power L3", value_key="active_power_l3_w", native_unit_of_measurement=UnitOfPower.WATT, device_class=SensorDeviceClass.POWER, state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="current_l1", name="Current L1", value_key="current_l1_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE, device_class=SensorDeviceClass.CURRENT, state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="current_l2", name="Current L2", value_key="current_l2_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE, device_class=SensorDeviceClass.CURRENT, state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="current_l3", name="Current L3", value_key="current_l3_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE, device_class=SensorDeviceClass.CURRENT, state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="actual_current", name="Actual Phase Current", value_key="actual_current_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE, device_class=SensorDeviceClass.CURRENT, state_class=SensorStateClass.MEASUREMENT),
    WebastoSensorDescription(key="voltage_l1", name="Voltage L1", value_key="voltage_l1_v", native_unit_of_measurement=UnitOfElectricPotential.VOLT, device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="voltage_l2", name="Voltage L2", value_key="voltage_l2_v", native_unit_of_measurement=UnitOfElectricPotential.VOLT, device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="voltage_l3", name="Voltage L3", value_key="voltage_l3_v", native_unit_of_measurement=UnitOfElectricPotential.VOLT, device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="configured_limit", name="Reported Current Limit", value_key="current_limit_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE, device_class=SensorDeviceClass.CURRENT, state_class=SensorStateClass.MEASUREMENT),
    WebastoSensorDescription(key="safe_current", name="Safe Current", value_key="safe_current_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE, device_class=SensorDeviceClass.CURRENT, state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="session_max_current", name="Session Max Current", value_key="session_max_current_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE, device_class=SensorDeviceClass.CURRENT, state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="session_energy", name="Session Energy", value_key="session_energy_kwh", native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR, device_class=SensorDeviceClass.ENERGY, state_class=SensorStateClass.TOTAL),
    WebastoSensorDescription(key="energy_meter", name="Energy Meter", value_key="energy_meter_kwh", native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR, device_class=SensorDeviceClass.ENERGY, state_class=SensorStateClass.TOTAL_INCREASING),
    WebastoSensorDescription(key="session_duration", name="Session Duration", value_key="session_duration_s", native_unit_of_measurement=UnitOfTime.SECONDS, entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="dlb_limit", name="DLB Limit", value_key="dlb_limit_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE, device_class=SensorDeviceClass.CURRENT, state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="final_target", name="Final Target", value_key="final_target_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE, device_class=SensorDeviceClass.CURRENT, state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC),
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
        if self.entity_description.key == "phase_requested":
            return self._present_value(self._phase_requested(data))
        if self.entity_description.key == "phase_observed":
            return self._present_value(self._phase_observed(data))
        if self.entity_description.key == "phase_recovery_state":
            return self._present_value(self._phase_recovery_state(data))
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
        if self.entity_description.key == "phase_requested":
            data = self.coordinator.data
            return {
                "source": "register_405",
                "capability_source": "register_404",
                "capability_register": NUMBER_OF_PHASES.address,
                "read_register": PHASE_SWITCH_MODE.address,
                "write_register": PHASE_SWITCH_MODE.address,
                "write_value_1p": PHASE_SWITCH_VALUE_1P,
                "write_value_3p": PHASE_SWITCH_VALUE_3P,
                "writes_enabled": data.phase_switching_mode
                in {PHASE_SWITCHING_MODE_MANUAL_ONLY, PHASE_SWITCHING_MODE_AUTOMATIC_SOLAR},
                "phase_switching_mode": self._present_value(data.phase_switching_mode),
                "default_phase": self._present_value(data.phase_switch_default_mode),
                "register_405_raw": data.phase_switch_mode_raw,
                "register_405_mode": self._present_value(data.phase_switch_mode),
                "register_available": data.phase_switch_register_available,
                "session_override": data.phase_session_override_active,
                "session_target": self._present_value(data.phase_session_target),
                "restore_pending": data.phase_restore_pending,
            }
        if self.entity_description.key == "phase_observed":
            data = self.coordinator.data
            return {
                "effective_active_phases": data.wallbox.phases_in_use,
                "observed_session_phase_usage": self._present_value(data.observed_session_phase_usage),
                "offer_state": self._present_value(data.phase_offer_state),
                "consistency": self._present_value(data.phase_consistency),
                "charger_phase_capability_register_404": data.wallbox.charge_point_phase_count,
                "register_405_mode": self._present_value(data.phase_switch_mode),
                "register_405_raw": data.phase_switch_mode_raw,
            }
        if self.entity_description.key == "phase_recovery_state":
            data = self.coordinator.data
            return {
                "reason": self._present_value(self._phase_status_reason(data)),
                "recovery_warning": self._present_value(data.phase_recovery_warning),
                "switch_state": self._present_value(data.phase_switch_state),
                "last_result": self._present_value(data.phase_switch_last_result),
                "last_target": self._present_value(data.phase_switch_last_target),
                "last_block_reason": self._present_value(data.phase_switch_last_block_reason),
                "switch_available": data.phase_switch_available,
                "switch_block_reason": self._present_value(data.phase_switch_block_reason),
                "policy_decision": self._present_value(data.phase_policy_decision),
                "policy_target": self._present_value(data.phase_policy_target),
                "policy_block_reason": self._present_value(data.phase_policy_block_reason),
                "policy_auto_ready": data.phase_policy_auto_ready,
                "policy_auto_block_reason": self._present_value(data.phase_policy_auto_block_reason),
                "policy_stable_elapsed_s": data.phase_policy_stable_elapsed_s,
                "policy_stable_required_s": data.phase_policy_stable_required_s,
                "policy_cooldown_remaining_s": data.phase_policy_cooldown_remaining_s,
                "policy_session_switch_count": data.phase_policy_session_switch_count,
                "policy_session_switch_limit": data.phase_policy_session_switch_limit,
                "required_surplus_1p_w": data.phase_policy_required_surplus_1p_w,
                "required_surplus_3p_w": data.phase_policy_required_surplus_3p_w,
            }
        if self.entity_description.key == "last_control_write_value":
            data = self.coordinator.data
            return {
                "verification_status": self._present_value(data.last_control_write_verification_status),
                "verification_reported_current_a": data.last_control_write_verification_reported_a,
                "verification_delta_a": data.last_control_write_verification_delta_a,
                "blocked_reason": self._present_value(data.last_control_write_blocked_reason),
                "reason": self._present_value(data.last_control_write_reason),
                "register": self._present_value(data.last_control_write_register),
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
    def _phase_requested(data) -> str | None:
        return data.phase_switch_mode

    @staticmethod
    def _phase_observed(data) -> str:
        if not data.wallbox.charging_active:
            return "not_charging"
        if data.wallbox.phases_in_use == 1:
            return "1P"
        if data.wallbox.phases_in_use == 3:
            return "3P"
        return "unknown"

    @staticmethod
    def _phase_recovery_state(data) -> str:
        if data.phase_switch_state and data.phase_switch_state != "idle":
            return data.phase_switch_state
        if data.phase_recovery_warning:
            return data.phase_recovery_warning
        if data.phase_restore_pending:
            return "phase_restore_pending"
        return "idle"

    @staticmethod
    def _phase_status_reason(data) -> str | None:
        for value in (
            data.phase_switch_last_block_reason,
            data.phase_recovery_warning,
            data.phase_switch_block_reason,
            data.phase_policy_block_reason,
            data.phase_policy_auto_block_reason,
        ):
            if value:
                return value
        return None

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
                "vehicle_disconnected": "Vehicle Disconnected",
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
                "pre_start_3p_safety": "Pre-start 3P Safety",
                "phase_switch_mode_1p": "Requested 1P Phase Mode",
                "phase_switch_mode_3p": "Requested 3P Phase Mode",
                "installed_phases": "Charger Configuration",
                "normal": "Normal",
                "ready": "Ready",
                "unavailable": "Unavailable",
                "disabled": "Disabled",
                "observed_1p": "Observed 1P",
                "observed_3p": "Observed 3P",
                "unknown": "Unknown",
                "charger_not_configured_3p": "Charger Not Configured 3P",
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
                "phase_switching_off": "Phase Switching Off",
                "automatic_phase_switching_disabled": "Automatic Phase Switching Disabled",
                "charger_not_preconfigured_3p": "Charger Not Preconfigured 3P",
                "3p_not_yet_observed": "3P Not Yet Observed",
                "phase_restore_pending": "Phase Restore Pending",
                "not_solar_mode": "Not Solar Mode",
                "solar_input_not_ready": "Solar Input Not Ready",
                "dlb_limited": "DLB Limited",
                "would_request_1p": "Would Request 1P",
                "would_request_3p": "Would Request 3P",
                "no_action": "No Action",
                "cooldown_active": "Cooldown Active",
                "automatic_phase_switch_failed_this_session": "Automatic Phase Switch Failed This Session",
                "session_switch_limit_reached": "Session Switch Limit Reached",
                "waiting_for_stable_surplus": "Waiting For Stable Phase Target",
                "waiting_for_stable_phase_target": "Waiting For Stable Phase Target",
                "invalid_target_phase": "Invalid Target Phase",
                "charger_state_unavailable": "Charger State Unavailable",
                "charger_unavailable": "Charger Unavailable",
                "already_in_target_phase": "Already In Target Phase",
                "blocked": "Blocked",
                "failed": "Failed",
                "queued": "Queued",
                "restore_queued": "Restore Queued",
                "requested": "Requested",
                "writing_register": "Writing Phase Register",
                "register_written": "Phase Register Written",
                "phase_switch_settling": "Phase Switch Settling",
                "verified": "Verified",
                "unverified": "Unverified",
                "register_verified_physical_mismatch": "Register Verified, Physical Mismatch",
                "register_and_physical_match": "Register and Physical Match",
                "register_3p_physical_1p": "Register 3P, Physical 1P",
                "register_1p_physical_3p": "Register 1P, Physical 3P",
                "offering_1p": "Offering 1P",
                "offering_3p": "Offering 3P",
                "requested_3p_observed_1p": "Requested 3P, Observed 1P",
                "requested_1p_observed_3p": "Requested 1P, Observed 3P",
                "possible_1p_vehicle_or_charger_stuck": "Possible 1P Vehicle Or Charger Stuck",
                "not_charging": "Not Charging",
            }.get(value, value)
        return value
