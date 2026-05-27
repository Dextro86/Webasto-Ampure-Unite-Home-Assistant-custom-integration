from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import WebastoUniteCoordinatorEntity
from .models import ChargeMode, SolarControlStrategy


def _solar_mode_label(strategy: SolarControlStrategy) -> str:
    strategy = SolarControlStrategy(strategy)
    if strategy == SolarControlStrategy.SMART_SOLAR:
        return "Smart Solar"
    if strategy == SolarControlStrategy.SOLAR_BOOST:
        return "Solar Boost"
    if strategy == SolarControlStrategy.ECO_SOLAR:
        return "Eco Solar"
    return "Solar"


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([WebastoModeSelect(coordinator)])


class WebastoModeSelect(WebastoUniteCoordinatorEntity, SelectEntity):
    _attr_name = "Charge Mode"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_charge_mode"

    def _base_mode_labels(self) -> dict[ChargeMode, str]:
        return {
            ChargeMode.OFF: "Off",
            ChargeMode.NORMAL: "Normal",
            ChargeMode.FIXED_CURRENT: "Fixed Current",
        }

    @property
    def options(self) -> list[str]:
        labels = self._base_mode_labels()
        options = [labels[ChargeMode.OFF], labels[ChargeMode.NORMAL]]
        if self.coordinator.control_config.solar_control_strategy != SolarControlStrategy.DISABLED:
            options.extend(["Eco Solar", "Smart Solar", "Solar Boost"])
        options.append(labels[ChargeMode.FIXED_CURRENT])
        return options

    @property
    def current_option(self) -> str | None:
        labels = self._base_mode_labels()
        if self.coordinator.data is None:
            return labels[ChargeMode.NORMAL]
        current_mode = self.coordinator.data.mode
        if (
            current_mode == ChargeMode.SOLAR
            and self.coordinator.control_config.solar_control_strategy == SolarControlStrategy.DISABLED
        ):
            return labels[ChargeMode.NORMAL]
        if current_mode == ChargeMode.SOLAR:
            return _solar_mode_label(self.coordinator.data.active_solar_strategy or self.coordinator.active_solar_strategy)
        return labels[current_mode]

    async def async_select_option(self, option: str) -> None:
        labels = self._base_mode_labels()
        mode_by_label = {label: mode for mode, label in labels.items()}
        solar_by_label = {
            "Eco Solar": SolarControlStrategy.ECO_SOLAR,
            "Smart Solar": SolarControlStrategy.SMART_SOLAR,
            "Solar Boost": SolarControlStrategy.SOLAR_BOOST,
        }
        if option in solar_by_label:
            self.coordinator.set_mode(ChargeMode.SOLAR, solar_by_label[option])
        else:
            self.coordinator.set_mode(mode_by_label[option])
        await self.coordinator.async_request_refresh()
