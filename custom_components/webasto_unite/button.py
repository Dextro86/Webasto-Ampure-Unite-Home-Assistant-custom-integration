
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import WebastoUniteCoordinatorEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            WebastoRefreshButton(coordinator),
            WebastoReconnectButton(coordinator),
            WebastoStartSessionButton(coordinator),
            WebastoCancelSessionButton(coordinator),
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


class WebastoStartSessionButton(WebastoUniteCoordinatorEntity, ButtonEntity):
    _attr_name = "Start session"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_start_session"

    async def async_press(self) -> None:
        await self.coordinator.async_start_session()
        await self.coordinator.async_request_refresh()


class WebastoCancelSessionButton(WebastoUniteCoordinatorEntity, ButtonEntity):
    _attr_name = "Cancel session"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_cancel_session"

    async def async_press(self) -> None:
        await self.coordinator.async_cancel_session()
        await self.coordinator.async_request_refresh()
