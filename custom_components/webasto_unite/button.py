
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import CONF_HOST, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_REST_DIAGNOSTICS_ENABLED,
    CONF_REST_PASSWORD,
    CONF_REST_USERNAME,
    DEFAULT_REST_USERNAME,
    DOMAIN,
    PHASE_SWITCHING_MODE_OFF,
)
from .entity import WebastoUniteCoordinatorEntity
from .features.phase_switch import phase_register_control_available
from .models import ControlMode


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        WebastoRefreshButton(coordinator),
        WebastoReconnectButton(coordinator),
    ]
    if _rest_actions_configured(coordinator):
        entities.append(WebastoSoftResetChargerButton(coordinator))
    if _phase_controls_configured(coordinator):
        entities.extend(
            [
                WebastoRequestPhase1PButton(coordinator),
                WebastoRequestPhase3PButton(coordinator),
                WebastoRestoreDefaultPhaseButton(coordinator),
                WebastoResetPhaseSwitchStateButton(coordinator),
            ]
        )
    async_add_entities(entities)


def _phase_controls_configured(coordinator) -> bool:
    return (
        getattr(coordinator, "_phase_switching_mode", PHASE_SWITCHING_MODE_OFF) != PHASE_SWITCHING_MODE_OFF
        and getattr(getattr(coordinator, "control_config", None), "control_mode", None)
        in {ControlMode.MANAGED_CONTROL, ControlMode.EXTERNAL_CONTROLLER}
    )


def _rest_actions_configured(coordinator) -> bool:
    merged = {**getattr(coordinator.entry, "data", {}), **getattr(coordinator.entry, "options", {})}
    return (
        bool(merged.get(CONF_REST_DIAGNOSTICS_ENABLED, False))
        and bool(str(merged.get(CONF_HOST, "") or "").strip())
        and bool(str(merged.get(CONF_REST_USERNAME, DEFAULT_REST_USERNAME) or "").strip())
        and bool(str(merged.get(CONF_REST_PASSWORD, "") or "").strip())
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


class WebastoSoftResetChargerButton(WebastoUniteCoordinatorEntity, ButtonEntity):
    _attr_name = "Restart Charger"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_soft_reset_charger"

    @property
    def available(self) -> bool:
        return _rest_actions_configured(self.coordinator)

    async def async_press(self) -> None:
        if not self.available:
            return
        await self.coordinator.async_soft_reset_charger()


class WebastoRequestPhase1PButton(WebastoUniteCoordinatorEntity, ButtonEntity):
    _attr_name = "Switch to 1P"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_request_phase_1p"

    @property
    def available(self) -> bool:
        return _manual_phase_request_available(self.coordinator)

    async def async_press(self) -> None:
        if not self.available:
            return
        await self.coordinator.async_schedule_phase_switch(1)


class WebastoRequestPhase3PButton(WebastoUniteCoordinatorEntity, ButtonEntity):
    _attr_name = "Switch to 3P"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_request_phase_3p"

    @property
    def available(self) -> bool:
        return _manual_phase_request_available(self.coordinator)

    async def async_press(self) -> None:
        if not self.available:
            return
        await self.coordinator.async_schedule_phase_switch(3)


def _manual_phase_request_available(coordinator) -> bool:
    return _phase_controls_configured(coordinator) and phase_register_control_available(
        phase_switching_mode=getattr(coordinator, "_phase_switching_mode", None),
        data=getattr(coordinator, "data", None),
    )


class WebastoRestoreDefaultPhaseButton(WebastoUniteCoordinatorEntity, ButtonEntity):
    _attr_name = "Restore Configured Phase"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_restore_default_phase"

    @property
    def available(self) -> bool:
        return _phase_controls_configured(self.coordinator) and phase_register_control_available(
            phase_switching_mode=getattr(self.coordinator, "_phase_switching_mode", None),
            data=getattr(self.coordinator, "data", None),
        )

    async def async_press(self) -> None:
        if not self.available:
            return
        await self.coordinator.async_schedule_restore_default_phase_mode()


class WebastoResetPhaseSwitchStateButton(WebastoUniteCoordinatorEntity, ButtonEntity):
    _attr_name = "Clear Phase Switch Status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_reset_phase_switch_state"

    async def async_press(self) -> None:
        self.coordinator.reset_phase_switch_state()
        await self.coordinator.async_request_refresh()
