from __future__ import annotations

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from ..const import (
    DOMAIN,
    SERVICE_DISABLE_FIXED_CURRENT_UNTIL_UNPLUG,
    SERVICE_DISABLE_PV_UNTIL_UNPLUG,
    SERVICE_DISABLE_SOLAR_UNTIL_UNPLUG,
    SERVICE_ENABLE_FIXED_CURRENT_UNTIL_UNPLUG,
    SERVICE_ENABLE_PV_UNTIL_UNPLUG,
    SERVICE_ENABLE_SOLAR_UNTIL_UNPLUG,
    SERVICE_REQUEST_PHASE_1P,
    SERVICE_REQUEST_PHASE_3P,
    SERVICE_RESTORE_DEFAULT_PHASE,
    SERVICE_RESET_PHASE_SWITCH_STATE,
    SERVICE_SET_CURRENT,
    SERVICE_SET_MAX_CURRENT,
    SERVICE_SET_MODE,
    SERVICE_SET_USER_LIMIT,
    SERVICE_TRIGGER_RECONNECT,
)
from ..coordinator import WebastoUniteCoordinator
from ..models import (
    ChargeMode,
    ControlMode,
    SolarControlStrategy,
    normalize_charge_mode,
    normalize_solar_control_strategy,
)

_SERVICE_SCHEMA_MODE = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Required("mode"): vol.In(
            sorted(
                {mode.value for mode in ChargeMode}
                | {"pv", "eco_solar", "smart_solar", "solar_boost", "surplus", "min_plus_surplus"}
            )
        ),
    }
)
_SERVICE_SCHEMA_ENTRY = vol.Schema({vol.Required("entry_id"): cv.string})


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


def _coerce_current_number(value: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as err:
        raise vol.Invalid("current_a must be a number") from err


_SERVICE_SCHEMA_SET_CURRENT = vol.Schema(
    {vol.Required("entry_id"): cv.string, vol.Required("current_a"): _coerce_current_number}
)


def _get_coordinator(hass: HomeAssistant, entry_id: str) -> WebastoUniteCoordinator:
    coordinator = hass.data.get(DOMAIN, {}).get(entry_id)
    if coordinator is None:
        raise HomeAssistantError(f"Unknown Webasto Unite entry_id: {entry_id}")
    return coordinator


async def async_setup_services(hass: HomeAssistant) -> None:
    async def handle_set_mode(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass, call.data["entry_id"])
        raw_mode = call.data["mode"]
        try:
            solar_strategy = normalize_solar_control_strategy(raw_mode)
        except ValueError:
            solar_strategy = None
        if solar_strategy is not None and solar_strategy != SolarControlStrategy.DISABLED:
            coordinator.set_mode(ChargeMode.SOLAR, solar_strategy)
        else:
            coordinator.set_mode(
                normalize_charge_mode(raw_mode, coordinator.control_config.solar_control_strategy)
            )
        await coordinator.async_request_refresh()

    async def handle_set_limit(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass, call.data["entry_id"])
        coordinator.set_max_current(call.data["current_a"])
        await coordinator.async_request_refresh()

    async def handle_set_current(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass, call.data["entry_id"])
        if coordinator.control_config.control_mode != ControlMode.EXTERNAL_CONTROLLER:
            raise HomeAssistantError("set_current requires Integration Charging Control = External Controller")
        await coordinator.async_set_external_current_limit(call.data["current_a"])
        await coordinator.async_request_refresh()

    async def handle_reconnect(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass, call.data["entry_id"])
        await coordinator.async_trigger_reconnect()

    async def handle_enable_solar_until_unplug(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass, call.data["entry_id"])
        coordinator.set_solar_until_unplug(True)
        await coordinator.async_request_refresh()

    async def handle_disable_solar_until_unplug(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass, call.data["entry_id"])
        coordinator.set_solar_until_unplug(False)
        await coordinator.async_request_refresh()

    async def handle_enable_fixed_current_until_unplug(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass, call.data["entry_id"])
        coordinator.set_fixed_current_until_unplug(True)
        await coordinator.async_request_refresh()

    async def handle_disable_fixed_current_until_unplug(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass, call.data["entry_id"])
        coordinator.set_fixed_current_until_unplug(False)
        await coordinator.async_request_refresh()

    async def handle_request_phase_1p(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass, call.data["entry_id"])
        try:
            await coordinator.async_schedule_phase_switch(1)
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(str(err)) from err

    async def handle_request_phase_3p(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass, call.data["entry_id"])
        try:
            await coordinator.async_schedule_phase_switch(3)
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(str(err)) from err

    async def handle_reset_phase_switch_state(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass, call.data["entry_id"])
        coordinator.reset_phase_switch_state()
        await coordinator.async_request_refresh()

    async def handle_restore_default_phase(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass, call.data["entry_id"])
        try:
            await coordinator.async_schedule_restore_default_phase_mode()
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(str(err)) from err

    hass.services.async_register(DOMAIN, SERVICE_SET_MODE, handle_set_mode, schema=_SERVICE_SCHEMA_MODE)
    hass.services.async_register(DOMAIN, SERVICE_SET_CURRENT, handle_set_current, schema=_SERVICE_SCHEMA_SET_CURRENT)
    hass.services.async_register(DOMAIN, SERVICE_SET_MAX_CURRENT, handle_set_limit, schema=_SERVICE_SCHEMA_LIMIT)
    # Legacy alias kept for existing automations; hidden from services.yaml.
    hass.services.async_register(DOMAIN, SERVICE_SET_USER_LIMIT, handle_set_limit, schema=_SERVICE_SCHEMA_LIMIT)
    hass.services.async_register(DOMAIN, SERVICE_TRIGGER_RECONNECT, handle_reconnect, schema=_SERVICE_SCHEMA_ENTRY)
    hass.services.async_register(
        DOMAIN,
        SERVICE_ENABLE_SOLAR_UNTIL_UNPLUG,
        handle_enable_solar_until_unplug,
        schema=_SERVICE_SCHEMA_ENTRY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DISABLE_SOLAR_UNTIL_UNPLUG,
        handle_disable_solar_until_unplug,
        schema=_SERVICE_SCHEMA_ENTRY,
    )
    # Legacy PV-named service aliases stay registered for existing automations.
    hass.services.async_register(
        DOMAIN,
        SERVICE_ENABLE_PV_UNTIL_UNPLUG,
        handle_enable_solar_until_unplug,
        schema=_SERVICE_SCHEMA_ENTRY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DISABLE_PV_UNTIL_UNPLUG,
        handle_disable_solar_until_unplug,
        schema=_SERVICE_SCHEMA_ENTRY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ENABLE_FIXED_CURRENT_UNTIL_UNPLUG,
        handle_enable_fixed_current_until_unplug,
        schema=_SERVICE_SCHEMA_ENTRY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DISABLE_FIXED_CURRENT_UNTIL_UNPLUG,
        handle_disable_fixed_current_until_unplug,
        schema=_SERVICE_SCHEMA_ENTRY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REQUEST_PHASE_1P,
        handle_request_phase_1p,
        schema=_SERVICE_SCHEMA_ENTRY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REQUEST_PHASE_3P,
        handle_request_phase_3p,
        schema=_SERVICE_SCHEMA_ENTRY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RESET_PHASE_SWITCH_STATE,
        handle_reset_phase_switch_state,
        schema=_SERVICE_SCHEMA_ENTRY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RESTORE_DEFAULT_PHASE,
        handle_restore_default_phase,
        schema=_SERVICE_SCHEMA_ENTRY,
    )
