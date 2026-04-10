from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import *
from .models import ControlMode, DlbInputModel, KeepaliveMode, PvControlStrategy, PvInputModel, PvOverrideStrategy

PHASE_OPTIONS = [PHASE_MODE_1P, PHASE_MODE_3P]
CONTROL_MODE_OPTIONS = [mode.value for mode in ControlMode]
PV_CONTROL_STRATEGY_OPTIONS = [strategy.value for strategy in PvControlStrategy]
PV_OVERRIDE_STRATEGY_OPTIONS = [strategy.value for strategy in PvOverrideStrategy]

MIN_CURRENT_A = 6.0
MAX_CURRENT_A = 32.0
MIN_POWER_W = 0.0
MAX_POWER_W = 250_000.0
MIN_SECONDS = 0.1
MAX_SECONDS = 300.0
MAX_RETRIES = 10


def _bounded_float(min_value: float, max_value: float, field_name: str):
    def _validate(value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError) as err:
            raise vol.Invalid(f"{field_name} must be a number") from err
        if not min_value <= number <= max_value:
            raise vol.Invalid(f"{field_name} must be between {min_value} and {max_value}")
        return number

    return _validate


def _bounded_int(min_value: int, max_value: int, field_name: str):
    def _validate(value: Any) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError) as err:
            raise vol.Invalid(f"{field_name} must be an integer") from err
        if not min_value <= number <= max_value:
            raise vol.Invalid(f"{field_name} must be between {min_value} and {max_value}")
        return number

    return _validate


def _entity_selector() -> selector.EntitySelector:
    return selector.EntitySelector(selector.EntitySelectorConfig(domain=["sensor"], multiple=False))


def _float_selector(min_value: float, max_value: float, step: float) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=min_value,
            max=max_value,
            step=step,
            mode=selector.NumberSelectorMode.BOX,
        )
    )


def _int_selector(min_value: int, max_value: int, step: int = 1) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=min_value,
            max=max_value,
            step=step,
            mode=selector.NumberSelectorMode.BOX,
        )
    )


def _validate_init_options(options: dict[str, Any]) -> dict[str, Any]:
    min_current = float(options[CONF_MIN_CURRENT])
    max_current = float(options[CONF_MAX_CURRENT])
    user_limit = float(options[CONF_USER_LIMIT])
    safe_current = float(options[CONF_SAFE_CURRENT])

    if min_current > max_current:
        raise vol.Invalid(f"{CONF_MIN_CURRENT} must be less than or equal to {CONF_MAX_CURRENT}")
    if not min_current <= user_limit <= max_current:
        raise vol.Invalid(f"{CONF_USER_LIMIT} must be between {CONF_MIN_CURRENT} and {CONF_MAX_CURRENT}")
    if not min_current <= safe_current <= max_current:
        raise vol.Invalid(f"{CONF_SAFE_CURRENT} must be between {CONF_MIN_CURRENT} and {CONF_MAX_CURRENT}")
    return options


def _validate_dlb_options(options: dict[str, Any], installed_phases: str) -> dict[str, Any]:
    model = options[CONF_DLB_INPUT_MODEL]
    if model == DlbInputModel.PHASE_CURRENTS.value:
        if installed_phases == PHASE_MODE_1P:
            if not options.get(CONF_DLB_L1_SENSOR):
                raise vol.Invalid("A DLB L1 phase current sensor is required for 1p phase_currents mode")
        else:
            missing = [key for key in (CONF_DLB_L1_SENSOR, CONF_DLB_L2_SENSOR, CONF_DLB_L3_SENSOR) if not options.get(key)]
            if missing:
                raise vol.Invalid("DLB L1, L2 and L3 phase current sensors are required for 3p phase_currents mode")
    if model == DlbInputModel.GRID_POWER.value and not options.get(CONF_DLB_GRID_POWER_SENSOR):
        raise vol.Invalid("A DLB grid power sensor is required for grid_power mode")
    return options


def _validate_pv_options(options: dict[str, Any]) -> dict[str, Any]:
    start_threshold = float(options[CONF_PV_START_THRESHOLD])
    stop_threshold = float(options[CONF_PV_STOP_THRESHOLD])
    pv_min_current = float(options[CONF_PV_MIN_CURRENT])
    fixed_current = float(options.get(CONF_FIXED_CURRENT, DEFAULT_FIXED_CURRENT_A))

    if stop_threshold > start_threshold:
        raise vol.Invalid(f"{CONF_PV_STOP_THRESHOLD} must be less than or equal to {CONF_PV_START_THRESHOLD}")
    if pv_min_current > MAX_CURRENT_A:
        raise vol.Invalid(f"{CONF_PV_MIN_CURRENT} must be less than or equal to {MAX_CURRENT_A}")
    if not MIN_CURRENT_A <= fixed_current <= MAX_CURRENT_A:
        raise vol.Invalid(f"{CONF_FIXED_CURRENT} must be between {MIN_CURRENT_A} and {MAX_CURRENT_A}")

    strategy = options.get(CONF_PV_CONTROL_STRATEGY, PvControlStrategy.SURPLUS.value)
    if strategy in (PvControlStrategy.SURPLUS.value, PvControlStrategy.MIN_PLUS_SURPLUS.value):
        model = options[CONF_PV_INPUT_MODEL]
        if model == PvInputModel.SURPLUS_SENSOR.value and not options.get(CONF_PV_SURPLUS_SENSOR):
            raise vol.Invalid("A PV surplus sensor is required for surplus_sensor mode")
        if model == PvInputModel.GRID_POWER_DERIVED.value and not (
            options.get(CONF_DLB_GRID_POWER_SENSOR)
        ):
            raise vol.Invalid("A DLB grid power sensor is required when PV mode derives surplus from grid power")
    return options


def _validation_error_key(err: Exception) -> str:
    message = str(err)
    if CONF_MIN_CURRENT in message and CONF_MAX_CURRENT in message:
        return "min_exceeds_max"
    if CONF_USER_LIMIT in message:
        return "user_limit_out_of_range"
    if CONF_SAFE_CURRENT in message:
        return "safe_current_out_of_range"
    if "phase current sensor" in message:
        return "dlb_phase_sensor_required"
    if "DLB L1, L2 and L3 phase current sensors are required" in message:
        return "dlb_phase_sensor_required"
    if "DLB L1 phase current sensor is required" in message:
        return "dlb_phase_sensor_required"
    if "DLB grid power sensor is required for grid_power mode" in message:
        return "dlb_grid_sensor_required"
    if CONF_PV_STOP_THRESHOLD in message and CONF_PV_START_THRESHOLD in message:
        return "pv_threshold_order"
    if "PV surplus sensor is required" in message:
        return "pv_surplus_sensor_required"
    if "PV mode derives surplus from grid power" in message:
        return "pv_grid_sensor_required"
    if CONF_FIXED_CURRENT in message:
        return "fixed_current_out_of_range"
    return "invalid_config"


class WebastoUniteConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=f"Webasto Unite ({user_input[CONF_HOST]})", data=user_input)
        schema = vol.Schema({
            vol.Required(CONF_HOST): str,
            vol.Optional(CONF_PORT, default=DEFAULT_PORT): _int_selector(1, 65535),
            vol.Optional(CONF_UNIT_ID, default=DEFAULT_UNIT_ID): _int_selector(1, 255),
            vol.Required(CONF_INSTALLED_PHASES, default=PHASE_MODE_3P): selector.SelectSelector(selector.SelectSelectorConfig(options=PHASE_OPTIONS)),
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return WebastoUniteOptionsFlow(config_entry)


class WebastoUniteOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry
        self.options = dict(config_entry.options)

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                user_input[CONF_POLLING_INTERVAL] = _bounded_float(MIN_SECONDS, MAX_SECONDS, CONF_POLLING_INTERVAL)(user_input[CONF_POLLING_INTERVAL])
                user_input[CONF_TIMEOUT] = _bounded_float(MIN_SECONDS, 60.0, CONF_TIMEOUT)(user_input[CONF_TIMEOUT])
                user_input[CONF_RETRIES] = _bounded_int(1, MAX_RETRIES, CONF_RETRIES)(user_input[CONF_RETRIES])
                user_input[CONF_KEEPALIVE_INTERVAL] = _bounded_float(1.0, MAX_SECONDS, CONF_KEEPALIVE_INTERVAL)(user_input[CONF_KEEPALIVE_INTERVAL])
                user_input[CONF_SAFE_CURRENT] = _bounded_float(MIN_CURRENT_A, MAX_CURRENT_A, CONF_SAFE_CURRENT)(user_input[CONF_SAFE_CURRENT])
                user_input[CONF_MIN_CURRENT] = _bounded_float(MIN_CURRENT_A, MAX_CURRENT_A, CONF_MIN_CURRENT)(user_input[CONF_MIN_CURRENT])
                user_input[CONF_MAX_CURRENT] = _bounded_float(MIN_CURRENT_A, MAX_CURRENT_A, CONF_MAX_CURRENT)(user_input[CONF_MAX_CURRENT])
                user_input[CONF_USER_LIMIT] = _bounded_float(MIN_CURRENT_A, MAX_CURRENT_A, CONF_USER_LIMIT)(user_input[CONF_USER_LIMIT])
                self.options.update(_validate_init_options(user_input))
                return await self.async_step_dlb()
            except vol.Invalid as err:
                errors["base"] = _validation_error_key(err)
        schema = vol.Schema({
            vol.Optional(CONF_POLLING_INTERVAL, default=self.options.get(CONF_POLLING_INTERVAL, DEFAULT_POLL_INTERVAL_S)): _float_selector(MIN_SECONDS, MAX_SECONDS, 0.1),
            vol.Optional(CONF_TIMEOUT, default=self.options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT_S)): _float_selector(MIN_SECONDS, 60.0, 0.1),
            vol.Optional(CONF_RETRIES, default=self.options.get(CONF_RETRIES, DEFAULT_RETRIES)): _int_selector(1, MAX_RETRIES),
            vol.Optional(CONF_CONTROL_MODE, default=self.options.get(CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE)): selector.SelectSelector(selector.SelectSelectorConfig(options=CONTROL_MODE_OPTIONS)),
            vol.Optional(CONF_KEEPALIVE_MODE, default=self.options.get(CONF_KEEPALIVE_MODE, KeepaliveMode.AUTO.value)): selector.SelectSelector(selector.SelectSelectorConfig(options=[m.value for m in KeepaliveMode])),
            vol.Optional(CONF_KEEPALIVE_INTERVAL, default=self.options.get(CONF_KEEPALIVE_INTERVAL, DEFAULT_KEEPALIVE_INTERVAL_S)): _float_selector(1.0, MAX_SECONDS, 0.1),
            vol.Optional(CONF_SAFE_CURRENT, default=self.options.get(CONF_SAFE_CURRENT, DEFAULT_SAFE_CURRENT_A)): _float_selector(MIN_CURRENT_A, MAX_CURRENT_A, 0.1),
            vol.Optional(CONF_MIN_CURRENT, default=self.options.get(CONF_MIN_CURRENT, DEFAULT_MIN_CURRENT_A)): _float_selector(MIN_CURRENT_A, MAX_CURRENT_A, 0.1),
            vol.Optional(CONF_MAX_CURRENT, default=self.options.get(CONF_MAX_CURRENT, DEFAULT_MAX_CURRENT_A)): _float_selector(MIN_CURRENT_A, MAX_CURRENT_A, 0.1),
            vol.Optional(CONF_USER_LIMIT, default=self.options.get(CONF_USER_LIMIT, DEFAULT_USER_LIMIT_A)): _float_selector(MIN_CURRENT_A, MAX_CURRENT_A, 0.1),
        })
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

    async def async_step_dlb(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                user_input[CONF_MAIN_FUSE] = _bounded_float(MIN_CURRENT_A, 200.0, CONF_MAIN_FUSE)(user_input[CONF_MAIN_FUSE])
                user_input[CONF_SAFETY_MARGIN] = _bounded_float(0.0, 50.0, CONF_SAFETY_MARGIN)(user_input[CONF_SAFETY_MARGIN])
                installed_phases = self.config_entry.data.get(CONF_INSTALLED_PHASES, PHASE_MODE_3P)
                self.options.update(_validate_dlb_options(user_input, installed_phases))
                return await self.async_step_pv()
            except vol.Invalid as err:
                errors["base"] = _validation_error_key(err)
        schema = vol.Schema({
            vol.Optional(CONF_DLB_INPUT_MODEL, default=self.options.get(CONF_DLB_INPUT_MODEL, DlbInputModel.PHASE_CURRENTS.value)): selector.SelectSelector(selector.SelectSelectorConfig(options=[m.value for m in DlbInputModel])),
            vol.Optional(CONF_MAIN_FUSE, default=self.options.get(CONF_MAIN_FUSE, DEFAULT_MAIN_FUSE_A)): _float_selector(MIN_CURRENT_A, 200.0, 0.1),
            vol.Optional(CONF_SAFETY_MARGIN, default=self.options.get(CONF_SAFETY_MARGIN, DEFAULT_SAFETY_MARGIN_A)): _float_selector(0.0, 50.0, 0.1),
            vol.Optional(CONF_DLB_L1_SENSOR, default=self.options.get(CONF_DLB_L1_SENSOR)): _entity_selector(),
            vol.Optional(CONF_DLB_L2_SENSOR, default=self.options.get(CONF_DLB_L2_SENSOR)): _entity_selector(),
            vol.Optional(CONF_DLB_L3_SENSOR, default=self.options.get(CONF_DLB_L3_SENSOR)): _entity_selector(),
            vol.Optional(CONF_DLB_GRID_POWER_SENSOR, default=self.options.get(CONF_DLB_GRID_POWER_SENSOR)): _entity_selector(),
        })
        return self.async_show_form(step_id="dlb", data_schema=schema, errors=errors)

    async def async_step_pv(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                user_input[CONF_PV_START_THRESHOLD] = _bounded_float(MIN_POWER_W, MAX_POWER_W, CONF_PV_START_THRESHOLD)(user_input[CONF_PV_START_THRESHOLD])
                user_input[CONF_PV_STOP_THRESHOLD] = _bounded_float(MIN_POWER_W, MAX_POWER_W, CONF_PV_STOP_THRESHOLD)(user_input[CONF_PV_STOP_THRESHOLD])
                user_input[CONF_PV_START_DELAY] = _bounded_float(0.0, 3600.0, CONF_PV_START_DELAY)(user_input[CONF_PV_START_DELAY])
                user_input[CONF_PV_STOP_DELAY] = _bounded_float(0.0, 3600.0, CONF_PV_STOP_DELAY)(user_input[CONF_PV_STOP_DELAY])
                user_input[CONF_PV_MIN_RUNTIME] = _bounded_float(0.0, 3600.0, CONF_PV_MIN_RUNTIME)(user_input[CONF_PV_MIN_RUNTIME])
                user_input[CONF_PV_MIN_PAUSE] = _bounded_float(0.0, 3600.0, CONF_PV_MIN_PAUSE)(user_input[CONF_PV_MIN_PAUSE])
                user_input[CONF_PV_MIN_CURRENT] = _bounded_float(MIN_CURRENT_A, MAX_CURRENT_A, CONF_PV_MIN_CURRENT)(user_input[CONF_PV_MIN_CURRENT])
                user_input[CONF_FIXED_CURRENT] = _bounded_float(MIN_CURRENT_A, MAX_CURRENT_A, CONF_FIXED_CURRENT)(user_input[CONF_FIXED_CURRENT])
                combined = {**self.options, **user_input}
                self.options.update(_validate_pv_options(combined))
                return self.async_create_entry(title="", data=self.options)
            except vol.Invalid as err:
                errors["base"] = _validation_error_key(err)
        schema = vol.Schema({
            vol.Optional(CONF_PV_INPUT_MODEL, default=self.options.get(CONF_PV_INPUT_MODEL, PvInputModel.GRID_POWER_DERIVED.value)): selector.SelectSelector(selector.SelectSelectorConfig(options=[m.value for m in PvInputModel])),
            vol.Optional(CONF_PV_CONTROL_STRATEGY, default=self.options.get(CONF_PV_CONTROL_STRATEGY, PvControlStrategy.SURPLUS.value)): selector.SelectSelector(selector.SelectSelectorConfig(options=PV_CONTROL_STRATEGY_OPTIONS)),
            vol.Optional(CONF_PV_UNTIL_UNPLUG_STRATEGY, default=self.options.get(CONF_PV_UNTIL_UNPLUG_STRATEGY, PvOverrideStrategy.INHERIT.value)): selector.SelectSelector(selector.SelectSelectorConfig(options=PV_OVERRIDE_STRATEGY_OPTIONS)),
            vol.Optional(CONF_PV_SURPLUS_SENSOR, default=self.options.get(CONF_PV_SURPLUS_SENSOR)): _entity_selector(),
            vol.Optional(CONF_PV_START_THRESHOLD, default=self.options.get(CONF_PV_START_THRESHOLD, 1800.0)): _float_selector(MIN_POWER_W, MAX_POWER_W, 1.0),
            vol.Optional(CONF_PV_STOP_THRESHOLD, default=self.options.get(CONF_PV_STOP_THRESHOLD, 1200.0)): _float_selector(MIN_POWER_W, MAX_POWER_W, 1.0),
            vol.Optional(CONF_PV_START_DELAY, default=self.options.get(CONF_PV_START_DELAY, DEFAULT_PV_START_DELAY_S)): _float_selector(0.0, 3600.0, 0.1),
            vol.Optional(CONF_PV_STOP_DELAY, default=self.options.get(CONF_PV_STOP_DELAY, DEFAULT_PV_STOP_DELAY_S)): _float_selector(0.0, 3600.0, 0.1),
            vol.Optional(CONF_PV_MIN_RUNTIME, default=self.options.get(CONF_PV_MIN_RUNTIME, DEFAULT_PV_MIN_RUNTIME_S)): _float_selector(0.0, 3600.0, 0.1),
            vol.Optional(CONF_PV_MIN_PAUSE, default=self.options.get(CONF_PV_MIN_PAUSE, DEFAULT_PV_MIN_PAUSE_S)): _float_selector(0.0, 3600.0, 0.1),
            vol.Optional(CONF_PV_MIN_CURRENT, default=self.options.get(CONF_PV_MIN_CURRENT, 6.0)): _float_selector(MIN_CURRENT_A, MAX_CURRENT_A, 0.1),
            vol.Optional(CONF_FIXED_CURRENT, default=self.options.get(CONF_FIXED_CURRENT, DEFAULT_FIXED_CURRENT_A)): _float_selector(MIN_CURRENT_A, MAX_CURRENT_A, 0.1),
        })
        return self.async_show_form(step_id="pv", data_schema=schema, errors=errors)
