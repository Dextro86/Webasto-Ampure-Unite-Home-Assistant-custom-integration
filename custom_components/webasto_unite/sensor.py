
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
    WebastoSensorDescription(key="operating_state", name="Charging behavior", value_key="operating_state"),
    WebastoSensorDescription(key="effective_mode", name="Active mode", value_key="effective_mode"),
    WebastoSensorDescription(key="capability_summary", name="Capability summary", value_key="capability_summary", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="firmware_version", name="Firmware version", value_key="firmware_version", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="charge_point_phase_count", name="Charge point phases", value_key="charge_point_phase_count", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="active_power", name="Active power", value_key="active_power_w", native_unit_of_measurement=UnitOfPower.WATT),
    WebastoSensorDescription(key="active_power_l1", name="Active power L1", value_key="active_power_l1_w", native_unit_of_measurement=UnitOfPower.WATT),
    WebastoSensorDescription(key="active_power_l2", name="Active power L2", value_key="active_power_l2_w", native_unit_of_measurement=UnitOfPower.WATT),
    WebastoSensorDescription(key="active_power_l3", name="Active power L3", value_key="active_power_l3_w", native_unit_of_measurement=UnitOfPower.WATT),
    WebastoSensorDescription(key="current_l1", name="Current L1", value_key="current_l1_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE),
    WebastoSensorDescription(key="current_l2", name="Current L2", value_key="current_l2_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE),
    WebastoSensorDescription(key="current_l3", name="Current L3", value_key="current_l3_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE),
    WebastoSensorDescription(key="actual_current", name="Actual current", value_key="actual_current_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE),
    WebastoSensorDescription(key="voltage_l1", name="Voltage L1", value_key="voltage_l1_v", native_unit_of_measurement=UnitOfElectricPotential.VOLT),
    WebastoSensorDescription(key="voltage_l2", name="Voltage L2", value_key="voltage_l2_v", native_unit_of_measurement=UnitOfElectricPotential.VOLT),
    WebastoSensorDescription(key="voltage_l3", name="Voltage L3", value_key="voltage_l3_v", native_unit_of_measurement=UnitOfElectricPotential.VOLT),
    WebastoSensorDescription(key="configured_limit", name="Configured limit", value_key="current_limit_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE),
    WebastoSensorDescription(key="safe_current", name="Safe current", value_key="safe_current_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE),
    WebastoSensorDescription(key="hardware_max_current", name="Hardware max current", value_key="hardware_max_current_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE),
    WebastoSensorDescription(key="session_energy", name="Session energy", value_key="session_energy_kwh", native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR),
    WebastoSensorDescription(key="energy_meter", name="Energy meter", value_key="energy_meter_kwh", native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR),
    WebastoSensorDescription(key="session_duration", name="Session duration", value_key="session_duration_s", native_unit_of_measurement=UnitOfTime.SECONDS, entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="dlb_limit", name="DLB limit", value_key="dlb_limit_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE),
    WebastoSensorDescription(key="final_target", name="Final target", value_key="final_target_a", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE),
    WebastoSensorDescription(key="queue_depth", name="Write queue depth", value_key="queue_depth", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="keepalive_age", name="Keepalive age", value_key="keepalive_age_s", native_unit_of_measurement=UnitOfTime.SECONDS, entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="keepalive_sent_count", name="Keepalive sent count", value_key="keepalive_sent_count", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="keepalive_failures", name="Keepalive write failures", value_key="keepalive_write_failures", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="pending_write", name="Pending write", value_key="pending_write_kind", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="reason", name="Control reason", value_key="control_reason", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="limit_reason", name="Dominant limit", value_key="dominant_limit_reason", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="sensor_invalid_reason", name="Sensor invalid reason", value_key="sensor_invalid_reason", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="fallback_active", name="Fallback active", value_key="fallback_active", entity_category=EntityCategory.DIAGNOSTIC),
    WebastoSensorDescription(key="client_error", name="Client error", value_key="last_client_error", entity_category=EntityCategory.DIAGNOSTIC),
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
        return value
