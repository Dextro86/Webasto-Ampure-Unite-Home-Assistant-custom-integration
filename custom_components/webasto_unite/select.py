from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import WebastoUniteCoordinatorEntity
from .models import ChargeMode


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([WebastoModeSelect(coordinator)])


class WebastoModeSelect(WebastoUniteCoordinatorEntity, SelectEntity):
    _attr_name = "Charge mode"
    _attr_options = [mode.value for mode in ChargeMode]

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_charge_mode"

    @property
    def current_option(self) -> str | None:
        if self.coordinator.data is None:
            return ChargeMode.NORMAL.value
        return self.coordinator.data.mode.value

    async def async_select_option(self, option: str) -> None:
        self.coordinator.set_mode(ChargeMode(option))
        await self.coordinator.async_request_refresh()
