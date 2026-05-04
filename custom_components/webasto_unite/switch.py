from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import WebastoUniteCoordinatorEntity
from .models import ControlMode, SolarControlStrategy


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        WebastoChargingSwitch(coordinator),
        WebastoSolarUntilUnplugSwitch(coordinator),
        WebastoFixedCurrentUntilUnplugSwitch(coordinator),
    ])


class WebastoChargingSwitch(WebastoUniteCoordinatorEntity, SwitchEntity):
    _attr_name = "Charging On/Off"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_charging_allowed"

    @property
    def available(self) -> bool:
        return self.coordinator.control_config.control_mode == ControlMode.MANAGED_CONTROL

    @property
    def is_on(self):
        return self.coordinator.charging_enabled

    async def async_turn_on(self, **kwargs):
        if not self.available:
            return
        await self.coordinator.async_set_charging_enabled(True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):
        if not self.available:
            return
        await self.coordinator.async_set_charging_enabled(False)
        await self.coordinator.async_request_refresh()


class WebastoSolarUntilUnplugSwitch(WebastoUniteCoordinatorEntity, SwitchEntity):
    _attr_name = "Solar Until Unplug"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_solar_until_unplug"

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.control_config.solar_control_strategy != SolarControlStrategy.DISABLED
        )

    @property
    def is_on(self):
        data = self.coordinator.data
        if data is None:
            return False
        return data.solar_until_unplug_active

    async def async_turn_on(self, **kwargs):
        self.coordinator.set_solar_until_unplug(True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):
        self.coordinator.set_solar_until_unplug(False)
        await self.coordinator.async_request_refresh()


class WebastoFixedCurrentUntilUnplugSwitch(WebastoUniteCoordinatorEntity, SwitchEntity):
    _attr_name = "Fixed Current Until Unplug"

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
