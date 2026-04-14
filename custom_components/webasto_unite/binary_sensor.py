from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import WebastoUniteCoordinatorEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        WebastoBinarySensor(coordinator, "Vehicle Connected", "vehicle_connected"),
        WebastoBinarySensor(coordinator, "Charging Active", "charging_active"),
        WebastoConnectionBinarySensor(coordinator),
        WebastoKeepaliveOverdueBinarySensor(coordinator),
    ])


class WebastoBinarySensor(WebastoUniteCoordinatorEntity, BinarySensorEntity):
    def __init__(self, coordinator, name: str, field_name: str) -> None:
        super().__init__(coordinator)
        self._attr_name = name
        self._field_name = field_name
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{field_name}"

    @property
    def is_on(self):
        data = self.coordinator.data
        if data is None:
            return None
        return getattr(data.wallbox, self._field_name)


class WebastoConnectionBinarySensor(WebastoUniteCoordinatorEntity, BinarySensorEntity):
    _attr_name = "Connected"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_connected"

    @property
    def is_on(self):
        data = self.coordinator.data
        if data is None:
            return False
        return data.wallbox.available


class WebastoKeepaliveOverdueBinarySensor(WebastoUniteCoordinatorEntity, BinarySensorEntity):
    _attr_name = "Keepalive Overdue"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_keepalive_overdue"

    @property
    def is_on(self):
        data = self.coordinator.data
        if data is None:
            return False
        return data.keepalive_overdue
