from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import WebastoUniteCoordinatorEntity
from .models import ChargeMode, PvControlStrategy

CHARGE_MODE_LABELS = {
    ChargeMode.OFF: "Off",
    ChargeMode.NORMAL: "Normal",
    ChargeMode.PV: "PV",
    ChargeMode.FIXED_CURRENT: "Fixed Current",
}
CHARGE_MODE_BY_LABEL = {label: mode for mode, label in CHARGE_MODE_LABELS.items()}


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([WebastoModeSelect(coordinator)])


class WebastoModeSelect(WebastoUniteCoordinatorEntity, SelectEntity):
    _attr_name = "Charge mode"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_charge_mode"

    @property
    def options(self) -> list[str]:
        modes = [ChargeMode.OFF, ChargeMode.NORMAL, ChargeMode.FIXED_CURRENT]
        if self.coordinator.control_config.pv_control_strategy != PvControlStrategy.DISABLED:
            modes.insert(2, ChargeMode.PV)
        return [CHARGE_MODE_LABELS[mode] for mode in modes]

    @property
    def current_option(self) -> str | None:
        if self.coordinator.data is None:
            return CHARGE_MODE_LABELS[ChargeMode.NORMAL]
        current_mode = self.coordinator.data.mode
        if (
            current_mode == ChargeMode.PV
            and self.coordinator.control_config.pv_control_strategy == PvControlStrategy.DISABLED
        ):
            return CHARGE_MODE_LABELS[ChargeMode.NORMAL]
        return CHARGE_MODE_LABELS[current_mode]

    async def async_select_option(self, option: str) -> None:
        self.coordinator.set_mode(CHARGE_MODE_BY_LABEL[option])
        await self.coordinator.async_request_refresh()
