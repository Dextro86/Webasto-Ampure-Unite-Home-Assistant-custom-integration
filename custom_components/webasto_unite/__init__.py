
from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigEntryNotReady
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import (
    SERVICE_DISABLE_FIXED_CURRENT_UNTIL_UNPLUG,
    SERVICE_DISABLE_PV_UNTIL_UNPLUG,
    SERVICE_DISABLE_SOLAR_UNTIL_UNPLUG,
    SERVICE_ENABLE_FIXED_CURRENT_UNTIL_UNPLUG,
    SERVICE_ENABLE_PV_UNTIL_UNPLUG,
    SERVICE_ENABLE_SOLAR_UNTIL_UNPLUG,
    DOMAIN,
    PLATFORMS,
    SERVICE_SET_MODE,
    SERVICE_SET_USER_LIMIT,
    SERVICE_TRIGGER_RECONNECT,
)
from .models import ChargeMode, normalize_charge_mode
from .coordinator import WebastoUniteCoordinator
from .modbus_client import ModbusClientError

_SERVICE_SCHEMA_MODE = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Required("mode"): vol.In(sorted({m.value for m in ChargeMode} | {"pv"})),
    }
)
_SERVICE_SCHEMA_RECONNECT = vol.Schema({vol.Required("entry_id"): cv.string})
_SERVICE_SCHEMA_SESSION = vol.Schema({vol.Required("entry_id"): cv.string})
if hasattr(cv, "config_entry_only_config_schema"):
    CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
else:
    CONFIG_SCHEMA = vol.Schema({})


def _coerce_whole_amp(value: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as err:
        raise vol.Invalid("current_a must be a number") from err
    rounded = round(numeric)
    if abs(numeric - rounded) > 1e-6:
        raise vol.Invalid("current_a must be a whole amp value")
    return float(rounded)


_SERVICE_SCHEMA_LIMIT = vol.Schema({vol.Required("entry_id"): cv.string, vol.Required("current_a"): _coerce_whole_amp})


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})

    def _get_coordinator(entry_id: str) -> WebastoUniteCoordinator:
        coordinator = hass.data.get(DOMAIN, {}).get(entry_id)
        if coordinator is None:
            raise HomeAssistantError(f"Unknown Webasto Unite entry_id: {entry_id}")
        return coordinator

    async def handle_set_mode(call: ServiceCall) -> None:
        coordinator = _get_coordinator(call.data["entry_id"])
        coordinator.set_mode(
            normalize_charge_mode(call.data["mode"], coordinator.control_config.solar_control_strategy)
        )
        await coordinator.async_request_refresh()

    async def handle_set_limit(call: ServiceCall) -> None:
        coordinator = _get_coordinator(call.data["entry_id"])
        coordinator.set_user_limit(call.data["current_a"])
        await coordinator.async_request_refresh()

    async def handle_reconnect(call: ServiceCall) -> None:
        coordinator = _get_coordinator(call.data["entry_id"])
        await coordinator.async_trigger_reconnect()

    async def handle_enable_solar_until_unplug(call: ServiceCall) -> None:
        coordinator = _get_coordinator(call.data["entry_id"])
        coordinator.set_solar_until_unplug(True)
        await coordinator.async_request_refresh()

    async def handle_disable_solar_until_unplug(call: ServiceCall) -> None:
        coordinator = _get_coordinator(call.data["entry_id"])
        coordinator.set_solar_until_unplug(False)
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
    hass.services.async_register(
        DOMAIN,
        SERVICE_ENABLE_SOLAR_UNTIL_UNPLUG,
        handle_enable_solar_until_unplug,
        schema=_SERVICE_SCHEMA_SESSION,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DISABLE_SOLAR_UNTIL_UNPLUG,
        handle_disable_solar_until_unplug,
        schema=_SERVICE_SCHEMA_SESSION,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ENABLE_PV_UNTIL_UNPLUG,
        handle_enable_solar_until_unplug,
        schema=_SERVICE_SCHEMA_SESSION,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DISABLE_PV_UNTIL_UNPLUG,
        handle_disable_solar_until_unplug,
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
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    coordinator = WebastoUniteCoordinator(hass, entry)
    try:
        await coordinator.async_setup()
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        await coordinator.async_shutdown()
        raise
    except ModbusClientError as err:
        await coordinator.async_shutdown()
        raise ConfigEntryNotReady(f"Unable to connect to Webasto Unite: {err}") from err
    except Exception as err:
        await coordinator.async_shutdown()
        raise ConfigEntryNotReady(f"Unable to initialize Webasto Unite: {err}") from err
    hass.data[DOMAIN][entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
    return unload_ok
