
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, PHASE_SWITCHING_MODE_MANUAL_ONLY
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
            WebastoRequestPhase1PButton(coordinator),
            WebastoRequestPhase3PButton(coordinator),
            WebastoRestoreDefaultPhaseButton(coordinator),
            WebastoResetPhaseSwitchStateButton(coordinator),
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
        return self.coordinator.control_config.control_mode in {
            ControlMode.MANAGED_CONTROL,
            ControlMode.EXTERNAL_CONTROLLER,
        }

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
        return self.coordinator.control_config.control_mode in {
            ControlMode.MANAGED_CONTROL,
            ControlMode.EXTERNAL_CONTROLLER,
        }

    async def async_press(self) -> None:
        if not self.available:
            return
        await self.coordinator.async_set_charging_enabled(True)
        await self.coordinator.async_request_refresh()


class _WebastoPhaseSwitchButton(WebastoUniteCoordinatorEntity, ButtonEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        data = getattr(self.coordinator, "data", None)
        return (
            getattr(self.coordinator, "_phase_switching_mode", None) == PHASE_SWITCHING_MODE_MANUAL_ONLY
            and data is not None
            and data.phase_switch_available is True
        )


class WebastoRequestPhase1PButton(_WebastoPhaseSwitchButton):
    _attr_name = "Request 1P Phase Mode"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_request_phase_1p"

    async def async_press(self) -> None:
        if not self.available:
            return
        await self.coordinator.async_request_phase_switch(1)


class WebastoRequestPhase3PButton(_WebastoPhaseSwitchButton):
    _attr_name = "Request 3P Phase Mode"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_request_phase_3p"

    async def async_press(self) -> None:
        if not self.available:
            return
        await self.coordinator.async_request_phase_switch(3)


class WebastoRestoreDefaultPhaseButton(_WebastoPhaseSwitchButton):
    _attr_name = "Restore Default Phase Mode"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_restore_default_phase"

    @property
    def available(self) -> bool:
        data = getattr(self.coordinator, "data", None)
        return (
            getattr(self.coordinator, "_phase_switching_mode", None) == PHASE_SWITCHING_MODE_MANUAL_ONLY
            and data is not None
            and data.phase_switch_register_available is True
        )

    async def async_press(self) -> None:
        if not self.available:
            return
        await self.coordinator.async_restore_default_phase_mode()


class WebastoResetPhaseSwitchStateButton(WebastoUniteCoordinatorEntity, ButtonEntity):
    _attr_name = "Reset Phase Switch State"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_reset_phase_switch_state"

    async def async_press(self) -> None:
        self.coordinator.reset_phase_switch_state()
        await self.coordinator.async_request_refresh()
