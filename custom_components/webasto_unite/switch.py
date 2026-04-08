from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import WebastoUniteCoordinatorEntity
from .models import ChargeMode


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        WebastoChargingSwitch(coordinator),
        WebastoPvUntilUnplugSwitch(coordinator),
        WebastoFixedCurrentUntilUnplugSwitch(coordinator),
    ])


class WebastoChargingSwitch(WebastoUniteCoordinatorEntity, SwitchEntity):
    _attr_name = "Charging allowed"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_charging_allowed"

    @property
    def is_on(self):
        data = self.coordinator.data
        if data is None:
            return False
        return not data.charging_paused and data.mode != ChargeMode.OFF

    async def async_turn_on(self, **kwargs):
        self.coordinator.resume_charging()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):
        self.coordinator.pause_charging()
        await self.coordinator.async_request_refresh()


class WebastoPvUntilUnplugSwitch(WebastoUniteCoordinatorEntity, SwitchEntity):
    _attr_name = "PV until unplug"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_pv_until_unplug"

    @property
    def is_on(self):
        data = self.coordinator.data
        if data is None:
            return False
        return data.pv_until_unplug_active

    async def async_turn_on(self, **kwargs):
        self.coordinator.set_pv_until_unplug(True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):
        self.coordinator.set_pv_until_unplug(False)
        await self.coordinator.async_request_refresh()


class WebastoFixedCurrentUntilUnplugSwitch(WebastoUniteCoordinatorEntity, SwitchEntity):
    _attr_name = "Fixed current until unplug"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_fixed_current_until_unplug"

    @property
    def is_on(self):
        data = self.coordinator.data
        if data is None:
            return False
        return data.fixed_current_until_unplug_active

    async def async_turn_on(self, **kwargs):
        self.coordinator.set_fixed_current_until_unplug(True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):
        self.coordinator.set_fixed_current_until_unplug(False)
        await self.coordinator.async_request_refresh()
