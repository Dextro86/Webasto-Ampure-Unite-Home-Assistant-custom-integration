
from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import (
    SERVICE_DISABLE_FIXED_CURRENT_UNTIL_UNPLUG,
    SERVICE_DISABLE_PV_UNTIL_UNPLUG,
    SERVICE_ENABLE_FIXED_CURRENT_UNTIL_UNPLUG,
    SERVICE_ENABLE_PV_UNTIL_UNPLUG,
    DOMAIN,
    PLATFORMS,
    SERVICE_CANCEL_SESSION,
    SERVICE_SET_MODE,
    SERVICE_SET_USER_LIMIT,
    SERVICE_START_SESSION,
    SERVICE_TRIGGER_RECONNECT,
)
from .models import ChargeMode
from .coordinator import WebastoUniteCoordinator

_SERVICE_SCHEMA_MODE = vol.Schema({vol.Required("entry_id"): cv.string, vol.Required("mode"): vol.In([m.value for m in ChargeMode])})
_SERVICE_SCHEMA_LIMIT = vol.Schema({vol.Required("entry_id"): cv.string, vol.Required("current_a"): vol.Coerce(float)})
_SERVICE_SCHEMA_RECONNECT = vol.Schema({vol.Required("entry_id"): cv.string})
_SERVICE_SCHEMA_SESSION = vol.Schema({vol.Required("entry_id"): cv.string})


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})

    def _get_coordinator(entry_id: str) -> WebastoUniteCoordinator:
        return hass.data[DOMAIN][entry_id]

    async def handle_set_mode(call: ServiceCall) -> None:
        coordinator = _get_coordinator(call.data["entry_id"])
        coordinator.set_mode(ChargeMode(call.data["mode"]))
        await coordinator.async_request_refresh()

    async def handle_set_limit(call: ServiceCall) -> None:
        coordinator = _get_coordinator(call.data["entry_id"])
        coordinator.set_user_limit(call.data["current_a"])
        await coordinator.async_request_refresh()

    async def handle_reconnect(call: ServiceCall) -> None:
        coordinator = _get_coordinator(call.data["entry_id"])
        await coordinator.async_trigger_reconnect()

    async def handle_start_session(call: ServiceCall) -> None:
        coordinator = _get_coordinator(call.data["entry_id"])
        await coordinator.async_start_session()
        await coordinator.async_request_refresh()

    async def handle_cancel_session(call: ServiceCall) -> None:
        coordinator = _get_coordinator(call.data["entry_id"])
        await coordinator.async_cancel_session()
        await coordinator.async_request_refresh()

    async def handle_enable_pv_until_unplug(call: ServiceCall) -> None:
        coordinator = _get_coordinator(call.data["entry_id"])
        coordinator.set_pv_until_unplug(True)
        await coordinator.async_request_refresh()

    async def handle_disable_pv_until_unplug(call: ServiceCall) -> None:
        coordinator = _get_coordinator(call.data["entry_id"])
        coordinator.set_pv_until_unplug(False)
        await coordinator.async_request_refresh()

    async def handle_enable_fixed_current_until_unplug(call: ServiceCall) -> None:
        coordinator = _get_coordinator(call.data["entry_id"])
        coordinator.set_fixed_current_until_unplug(True)
        await coordinator.async_request_refresh()

    async def handle_disable_fixed_current_until_unplug(call: ServiceCall) -> None:
        coordinator = _get_coordinator(call.data["entry_id"])
        coordinator.set_fixed_current_until_unplug(False)
        await coordinator.async_request_refresh()

    hass.services.async_register(DOMAIN, SERVICE_SET_MODE, handle_set_mode, schema=_SERVICE_SCHEMA_MODE)
    hass.services.async_register(DOMAIN, SERVICE_SET_USER_LIMIT, handle_set_limit, schema=_SERVICE_SCHEMA_LIMIT)
    hass.services.async_register(DOMAIN, SERVICE_TRIGGER_RECONNECT, handle_reconnect, schema=_SERVICE_SCHEMA_RECONNECT)
    hass.services.async_register(DOMAIN, SERVICE_START_SESSION, handle_start_session, schema=_SERVICE_SCHEMA_SESSION)
    hass.services.async_register(DOMAIN, SERVICE_CANCEL_SESSION, handle_cancel_session, schema=_SERVICE_SCHEMA_SESSION)
    hass.services.async_register(
        DOMAIN,
        SERVICE_ENABLE_PV_UNTIL_UNPLUG,
        handle_enable_pv_until_unplug,
        schema=_SERVICE_SCHEMA_SESSION,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DISABLE_PV_UNTIL_UNPLUG,
        handle_disable_pv_until_unplug,
        schema=_SERVICE_SCHEMA_SESSION,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ENABLE_FIXED_CURRENT_UNTIL_UNPLUG,
        handle_enable_fixed_current_until_unplug,
        schema=_SERVICE_SCHEMA_SESSION,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DISABLE_FIXED_CURRENT_UNTIL_UNPLUG,
        handle_disable_fixed_current_until_unplug,
        schema=_SERVICE_SCHEMA_SESSION,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    coordinator = WebastoUniteCoordinator(hass, entry)
    await coordinator.async_setup()
    await coordinator.async_config_entry_first_refresh()
    hass.data[DOMAIN][entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
    return unload_ok
