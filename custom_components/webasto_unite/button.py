
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import WebastoUniteCoordinatorEntity
from .models import ControlMode


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            WebastoRefreshButton(coordinator),
            WebastoReconnectButton(coordinator),
            WebastoPauseChargingButton(coordinator),
            WebastoResumeChargingButton(coordinator),
        ]
    )


class WebastoRefreshButton(WebastoUniteCoordinatorEntity, ButtonEntity):
    _attr_name = "Refresh"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_refresh"

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()


class WebastoReconnectButton(WebastoUniteCoordinatorEntity, ButtonEntity):
    _attr_name = "Reconnect"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_reconnect"

    async def async_press(self) -> None:
        await self.coordinator.async_trigger_reconnect()


class WebastoPauseChargingButton(WebastoUniteCoordinatorEntity, ButtonEntity):
    _attr_name = "Pause Charging"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_pause_charging"

    @property
    def available(self) -> bool:
        return self.coordinator.control_config.control_mode == ControlMode.MANAGED_CONTROL

    async def async_press(self) -> None:
        if not self.available:
            return
        await self.coordinator.async_set_charging_enabled(False)
        await self.coordinator.async_request_refresh()


class WebastoResumeChargingButton(WebastoUniteCoordinatorEntity, ButtonEntity):
    _attr_name = "Resume Charging"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_resume_charging"

    @property
    def available(self) -> bool:
        return self.coordinator.control_config.control_mode == ControlMode.MANAGED_CONTROL

    async def async_press(self) -> None:
        if not self.available:
            return
        await self.coordinator.async_set_charging_enabled(True)
        await self.coordinator.async_request_refresh()
