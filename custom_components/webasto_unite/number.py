from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.const import UnitOfElectricCurrent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import WebastoUniteCoordinatorEntity
from .models import ControlMode


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            WebastoMaximumCurrentNumber(coordinator),
            WebastoRequestedCurrentNumber(coordinator),
            WebastoFixedCurrentNumber(coordinator),
        ]
    )


class WebastoMaximumCurrentNumber(WebastoUniteCoordinatorEntity, NumberEntity):
    _attr_name = "Maximum Current"
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        # Keep the old unique ID so existing HA entity IDs are not orphaned.
        self._attr_unique_id = f"{coordinator.entry.entry_id}_current_limit"

    @property
    def native_value(self):
        return self.coordinator.control_config.max_current_a

    @property
    def native_min_value(self) -> float:
        return self.coordinator.control_config.min_current_a

    @property
    def native_max_value(self) -> float:
        return 32.0

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.set_max_current(float(value))
        await self.coordinator.async_request_refresh()


class WebastoRequestedCurrentNumber(WebastoUniteCoordinatorEntity, NumberEntity):
    _attr_name = "Requested Current"
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_requested_current"

    @property
    def available(self) -> bool:
        return self.coordinator.control_config.control_mode == ControlMode.EXTERNAL_CONTROLLER

    @property
    def native_value(self):
        return getattr(self.coordinator, "_external_current_a", None) or self.coordinator.control_config.min_current_a

    @property
    def native_min_value(self) -> float:
        return self.coordinator.control_config.min_current_a

    @property
    def native_max_value(self) -> float:
        return self.coordinator.control_config.max_current_a

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_external_current_limit(float(value))
        await self.coordinator.async_request_refresh()


class WebastoFixedCurrentNumber(WebastoUniteCoordinatorEntity, NumberEntity):
    _attr_name = "Fixed Current"
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_fixed_current"

    @property
    def native_value(self):
        return self.coordinator.control_config.fixed_current_a

    @property
    def native_min_value(self) -> float:
        return self.coordinator.control_config.min_current_a

    @property
    def native_max_value(self) -> float:
        return self.coordinator.control_config.max_current_a

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.set_fixed_current(float(value))
        await self.coordinator.async_request_refresh()
