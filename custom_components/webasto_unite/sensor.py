
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.const import EntityCategory, UnitOfElectricCurrent, UnitOfEnergy, UnitOfPower, UnitOfTime, UnitOfElectricPotential
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import WebastoUniteCoordinatorEntity
from .models import ChargeMode


@dataclass(frozen=True, kw_only=True)
class WebastoSensorDescription(SensorEntityDescription):
    value_key: str


SENSORS = (
    WebastoSensorDescription(key="operating_state", name="Charging Behavior", value_key="operating_state"),
    WebastoSensorDescription(key="effective_mode", name="Active Mode", value_key="effective_mode"),
    WebastoSensorDescription(key="capability_summary", name="Capability Summary", value_key="capability_summary", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="firmware_version", name="Firmware Version", value_key="firmware_version", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="charge_point_state_raw", name="Charge Point State Code", value_key="charge_point_state_raw", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="charging_state_raw", name="Charging State Code", value_key="charge_state_raw", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="equipment_state_raw", name="Equipment State Code", value_key="evse_state_raw", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="cable_state_raw", name="Cable State Code", value_key="cable_state_raw", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="evse_fault_code", name="EVSE Fault Code", value_key="error_code", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="charge_point_phase_count", name="Charger Configured Phases", value_key="charge_point_phase_count", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="phase_switch_mode_raw", name="Phase Switch Mode Code", value_key="phase_switch_mode_raw", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="active_power", name="Active Power", value_key="active_power_w", native_unit_of_measurement=UnitOfPower.WATT),
    WebastoSensorDescription(key="active_power_l1", name="Active Power L1", value_key="active_power_l1_w", native_unit_of_measurement=UnitOfPower.WATT),
    WebastoSensorDescription(key="active_power_l2", name="Active Power L2", value_key="active_power_l2_w", native_unit_of_measurement=UnitOfPower.WATT),
    WebastoSensorDescription(key="active_power_l3", name="Active Power L3", value_key="active_power_l3_w", native_unit_of_measurement=UnitOfPower.WATT),
    WebastoSensorDescription(key="current_l1", name="Current L1", value_key="current_l1_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE),
    WebastoSensorDescription(key="current_l2", name="Current L2", value_key="current_l2_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE),
    WebastoSensorDescription(key="current_l3", name="Current L3", value_key="current_l3_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE),
    WebastoSensorDescription(key="actual_current", name="Max Phase Current", value_key="actual_current_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE),
    WebastoSensorDescription(key="voltage_l1", name="Voltage L1", value_key="voltage_l1_v", native_unit_of_measurement=UnitOfElectricPotential.VOLT),
    WebastoSensorDescription(key="voltage_l2", name="Voltage L2", value_key="voltage_l2_v", native_unit_of_measurement=UnitOfElectricPotential.VOLT),
    WebastoSensorDescription(key="voltage_l3", name="Voltage L3", value_key="voltage_l3_v", native_unit_of_measurement=UnitOfElectricPotential.VOLT),
    WebastoSensorDescription(key="configured_limit", name="Reported Current Limit", value_key="current_limit_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE),
    WebastoSensorDescription(key="safe_current", name="Safe Current", value_key="safe_current_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE),
    WebastoSensorDescription(key="session_max_current", name="Session Max Current", value_key="session_max_current_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE),
    WebastoSensorDescription(key="session_energy", name="Session Energy", value_key="session_energy_kwh", native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR),
    WebastoSensorDescription(key="energy_meter", name="Energy Meter", value_key="energy_meter_kwh", native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR),
    WebastoSensorDescription(key="session_duration", name="Session Duration", value_key="session_duration_s", native_unit_of_measurement=UnitOfTime.SECONDS, entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="dlb_limit", name="DLB Limit", value_key="dlb_limit_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE),
    WebastoSensorDescription(key="final_target", name="Final Target", value_key="final_target_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE),
    WebastoSensorDescription(key="queue_depth", name="Write Queue Depth", value_key="queue_depth", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="keepalive_age", name="Keepalive Age", value_key="keepalive_age_s", native_unit_of_measurement=UnitOfTime.SECONDS, entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="keepalive_sent_count", name="Keepalive Sent Count", value_key="keepalive_sent_count", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="keepalive_failures", name="Keepalive Write Failures", value_key="keepalive_write_failures", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="pending_write", name="Pending Write", value_key="pending_write_kind", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="reason", name="Control Reason", value_key="control_reason", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="limit_reason", name="Dominant Limit", value_key="dominant_limit_reason", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="sensor_invalid_reason", name="Sensor Invalid Reason", value_key="sensor_invalid_reason", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="fallback_active", name="Fallback Active", value_key="fallback_active", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="client_error", name="Client Error", value_key="last_client_error", entity_category=EntityCategory.DIAGNOSTIC),
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
        if hasattr(data, self.entity_description.value_key):
            return self._present_value(getattr(data, self.entity_description.value_key))
        if hasattr(data.wallbox, self.entity_description.value_key):
            return self._present_value(getattr(data.wallbox, self.entity_description.value_key))
        return None

    @staticmethod
    def _present_value(value):
        if isinstance(value, ChargeMode):
            return {
                ChargeMode.OFF: "Off",
                ChargeMode.NORMAL: "Normal",
                ChargeMode.PV: "PV",
                ChargeMode.FIXED_CURRENT: "Fixed Current",
            }[value]
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, str):
            return {
                "paused": "Paused",
                "off": "Off",
                "fallback": "Fallback",
                "fixed_current_until_unplug": "Fixed Current Until Unplug",
                "fixed_current": "Fixed Current",
                "waiting_for_surplus": "Waiting for Surplus",
                "pv_until_unplug": "PV Until Unplug",
                "min_plus_surplus": "Min + Surplus",
                "dlb_limited": "DLB Limited",
                "partially_validated": "Partially Validated",
                "validated_with_optional_gaps": "Validated with Optional Gaps",
                "validated": "Validated",
                "off_mode": "Off Mode",
                "normal_mode": "Normal Mode",
                "fixed_current_mode": "Fixed Current Mode",
                "pv_mode": "PV Mode",
                "hardware_limited": "Hardware Limited",
                "cable_limited": "Cable Limited",
                "ev_limited": "EV Limited",
                "safe_current_fallback": "Safe Current Fallback",
                "sensor_unavailable": "Sensor Unavailable",
                "communication_loss": "Communication Loss",
                "below_min_current": "Below Minimum Current",
                "no_change": "No Change",
                "pv": "PV",
                "normal": "Normal",
            }.get(value, value)
        return value
