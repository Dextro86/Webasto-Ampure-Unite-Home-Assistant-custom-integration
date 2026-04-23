from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector

from .const import *
from .models import ChargeMode, ControlMode, DlbInputModel, DlbSensorScope, SolarControlStrategy, SolarInputModel, SolarOverrideStrategy, normalize_charge_mode, normalize_solar_control_strategy, normalize_solar_override_strategy


def _solar_mode_label(strategy: str | SolarControlStrategy) -> str:
    normalized = normalize_solar_control_strategy(strategy)
    if normalized == SolarControlStrategy.ECO_SOLAR:
        return "Eco Solar"
    if normalized == SolarControlStrategy.SMART_SOLAR:
        return "Smart Solar"
    return "Solar"

PHASE_OPTIONS = [PHASE_MODE_1P, PHASE_MODE_3P]
PHASE_SELECTOR_OPTIONS = [
    {"value": PHASE_MODE_1P, "label": "1 Phase"},
    {"value": PHASE_MODE_3P, "label": "3 Phases"},
]
CONTROL_MODE_SELECTOR_OPTIONS = [
    {"value": ControlMode.MANAGED_CONTROL.value, "label": "Enabled"},
    {"value": ControlMode.KEEPALIVE_ONLY.value, "label": "Monitoring Only"},
]
STARTUP_CHARGE_MODE_SELECTOR_OPTIONS = [
    {"value": ChargeMode.OFF.value, "label": "Off"},
    {"value": ChargeMode.NORMAL.value, "label": "Normal"},
    {"value": ChargeMode.SOLAR.value, "label": "Solar"},
    {"value": ChargeMode.FIXED_CURRENT.value, "label": "Fixed Current"},
]
DLB_SENSOR_SCOPE_SELECTOR_OPTIONS = [
    {"value": DlbSensorScope.LOAD_EXCLUDING_CHARGER.value, "label": "Exclude Charger Load"},
    {"value": DlbSensorScope.TOTAL_INCLUDING_CHARGER.value, "label": "Include Charger Load"},
]
SOLAR_INPUT_MODEL_SELECTOR_OPTIONS = [
    {"value": SolarInputModel.SURPLUS_SENSOR.value, "label": "Solar Surplus Sensor"},
    {"value": SolarInputModel.GRID_POWER_DERIVED.value, "label": "Signed Grid Power Sensor"},
]
SOLAR_CONTROL_STRATEGY_OPTIONS = [
    {"value": SolarControlStrategy.DISABLED.value, "label": "Disabled"},
    {"value": SolarControlStrategy.ECO_SOLAR.value, "label": "Eco Solar"},
    {"value": SolarControlStrategy.SMART_SOLAR.value, "label": "Smart Solar"},
]
SOLAR_OVERRIDE_STRATEGY_OPTIONS = [
    {"value": SolarOverrideStrategy.INHERIT.value, "label": "Use Solar Strategy"},
    {"value": SolarOverrideStrategy.ECO_SOLAR.value, "label": "Eco Solar"},
    {"value": SolarOverrideStrategy.SMART_SOLAR.value, "label": "Smart Solar"},
]
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
            numeric = float(value)
        except (TypeError, ValueError) as err:
            raise vol.Invalid(f"{field_name} must be an integer") from err
        if not numeric.is_integer():
            raise vol.Invalid(f"{field_name} must be a whole number")
        number = int(numeric)
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


def _optional_field(key: str, field_type, value: Any | None = None):
    if value is None:
        return vol.Optional(key)
    return vol.Optional(key, default=value)


def _compact_section_defaults(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _validate_init_options(options: dict[str, Any]) -> dict[str, Any]:
    min_current = int(options[CONF_MIN_CURRENT])
    max_current = int(options[CONF_MAX_CURRENT])
    user_limit = int(options[CONF_USER_LIMIT])
    safe_current = int(options[CONF_SAFE_CURRENT])

    if min_current > max_current:
        raise vol.Invalid(f"{CONF_MIN_CURRENT} must be less than or equal to {CONF_MAX_CURRENT}")
    if not min_current <= user_limit <= max_current:
        raise vol.Invalid(f"{CONF_USER_LIMIT} must be between {CONF_MIN_CURRENT} and {CONF_MAX_CURRENT}")
    if not min_current <= safe_current <= max_current:
        raise vol.Invalid(f"{CONF_SAFE_CURRENT} must be between {CONF_MIN_CURRENT} and {CONF_MAX_CURRENT}")
    options[CONF_STARTUP_CHARGE_MODE] = normalize_charge_mode(
        options.get(CONF_STARTUP_CHARGE_MODE, DEFAULT_STARTUP_CHARGE_MODE),
        options.get(CONF_SOLAR_CONTROL_STRATEGY, SolarControlStrategy.DISABLED.value),
    ).value
    return options


def _validate_connection_data(data: dict[str, Any]) -> dict[str, Any]:
    host = str(data[CONF_HOST]).strip()
    if not host:
        raise vol.Invalid(f"{CONF_HOST} is required")
    port = _bounded_int(1, 65535, CONF_PORT)(data[CONF_PORT])
    unit_id = _bounded_int(1, 255, CONF_UNIT_ID)(data[CONF_UNIT_ID])
    installed_phases = data[CONF_INSTALLED_PHASES]
    if installed_phases not in PHASE_OPTIONS:
        raise vol.Invalid(f"{CONF_INSTALLED_PHASES} must be 1p or 3p")
    return {
        CONF_HOST: host,
        CONF_PORT: port,
        CONF_UNIT_ID: unit_id,
        CONF_INSTALLED_PHASES: installed_phases,
    }


def _validate_dlb_options(options: dict[str, Any], installed_phases: str) -> dict[str, Any]:
    dlb_enabled_raw = options.get(CONF_DLB_ENABLED)
    if dlb_enabled_raw is None:
        dlb_enabled = options.get(CONF_DLB_INPUT_MODEL, DlbInputModel.DISABLED.value) == DlbInputModel.PHASE_CURRENTS.value
    else:
        dlb_enabled = bool(dlb_enabled_raw)
    if not dlb_enabled:
        return options
    if installed_phases == PHASE_MODE_1P:
        if not options.get(CONF_DLB_L1_SENSOR):
            raise vol.Invalid("A DLB L1 phase current sensor is required for 1p DLB")
    else:
        missing = [key for key in (CONF_DLB_L1_SENSOR, CONF_DLB_L2_SENSOR, CONF_DLB_L3_SENSOR) if not options.get(key)]
        if missing:
            raise vol.Invalid("DLB L1, L2 and L3 phase current sensors are required for 3p DLB")
    return options


def _validate_solar_options(options: dict[str, Any]) -> dict[str, Any]:
    options[CONF_SOLAR_CONTROL_STRATEGY] = normalize_solar_control_strategy(
        options.get(CONF_SOLAR_CONTROL_STRATEGY, SolarControlStrategy.DISABLED.value)
    ).value
    options[CONF_SOLAR_UNTIL_UNPLUG_STRATEGY] = normalize_solar_override_strategy(
        options.get(CONF_SOLAR_UNTIL_UNPLUG_STRATEGY, SolarOverrideStrategy.INHERIT.value)
    ).value
    start_threshold = float(options[CONF_SOLAR_START_THRESHOLD])
    stop_threshold = float(options[CONF_SOLAR_STOP_THRESHOLD])
    solar_min_current = int(options[CONF_SOLAR_MIN_CURRENT])
    fixed_current = int(options.get(CONF_FIXED_CURRENT, DEFAULT_FIXED_CURRENT_A))
    max_current = int(options.get(CONF_MAX_CURRENT, MAX_CURRENT_A))

    if stop_threshold > start_threshold:
        raise vol.Invalid(f"{CONF_SOLAR_STOP_THRESHOLD} must be less than or equal to {CONF_SOLAR_START_THRESHOLD}")
    if solar_min_current > MAX_CURRENT_A:
        raise vol.Invalid(f"{CONF_SOLAR_MIN_CURRENT} must be less than or equal to {MAX_CURRENT_A}")
    if solar_min_current > max_current:
        raise vol.Invalid(f"{CONF_SOLAR_MIN_CURRENT} must be less than or equal to {CONF_MAX_CURRENT}")
    if not MIN_CURRENT_A <= fixed_current <= MAX_CURRENT_A:
        raise vol.Invalid(f"{CONF_FIXED_CURRENT} must be between {MIN_CURRENT_A} and {MAX_CURRENT_A}")
    if fixed_current > max_current:
        raise vol.Invalid(f"{CONF_FIXED_CURRENT} must be less than or equal to {CONF_MAX_CURRENT}")

    strategy = options[CONF_SOLAR_CONTROL_STRATEGY]
    if strategy in (
        SolarControlStrategy.SURPLUS.value,
        SolarControlStrategy.MIN_PLUS_SURPLUS.value,
    ):
        model = options[CONF_SOLAR_INPUT_MODEL]
        if model == SolarInputModel.SURPLUS_SENSOR.value and not options.get(CONF_SOLAR_SURPLUS_SENSOR):
            raise vol.Invalid("A solar surplus sensor is required for surplus_sensor mode")
        if model == SolarInputModel.GRID_POWER_DERIVED.value and not (
            options.get(CONF_SOLAR_GRID_POWER_SENSOR) or options.get(CONF_DLB_GRID_POWER_SENSOR)
        ):
            raise vol.Invalid("A grid power sensor is required when solar mode derives surplus from grid power")
    return options


_validate_pv_options = _validate_solar_options


def _validation_error_key(err: Exception) -> str:
    message = str(err)
    if CONF_USER_LIMIT in message:
        return "user_limit_out_of_range"
    if CONF_SAFE_CURRENT in message:
        return "safe_current_out_of_range"
    if CONF_SOLAR_MIN_CURRENT in message and CONF_MAX_CURRENT in message:
        return "solar_min_current_out_of_range"
    if CONF_MIN_CURRENT in message and CONF_MAX_CURRENT in message:
        return "min_exceeds_max"
    if CONF_HOST in message:
        return "host_required"
    if CONF_PORT in message:
        return "port_out_of_range"
    if CONF_UNIT_ID in message:
        return "unit_id_out_of_range"
    if CONF_INSTALLED_PHASES in message:
        return "installed_phases_invalid"
    if "phase current sensor" in message:
        return "dlb_phase_sensor_required"
    if "DLB L1, L2 and L3 phase current sensors are required" in message:
        return "dlb_phase_sensor_required"
    if "DLB L1 phase current sensor is required" in message:
        return "dlb_phase_sensor_required"
    if CONF_SOLAR_STOP_THRESHOLD in message and CONF_SOLAR_START_THRESHOLD in message:
        return "solar_threshold_order"
    if "solar surplus sensor is required" in message:
        return "solar_surplus_sensor_required"
    if "grid power sensor is required when solar mode derives surplus" in message:
        return "solar_grid_sensor_required"
    if CONF_FIXED_CURRENT in message:
        return "fixed_current_out_of_range"
    return "invalid_config"


class WebastoUniteConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                data = _validate_connection_data(user_input)
                await self.async_set_unique_id(f"{data[CONF_HOST]}:{data[CONF_PORT]}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=f"Webasto Unite ({data[CONF_HOST]})", data=data)
            except vol.Invalid as err:
                errors["base"] = _validation_error_key(err)
        schema = vol.Schema({
            vol.Required(CONF_HOST): str,
            vol.Optional(CONF_PORT, default=DEFAULT_PORT): _int_selector(1, 65535),
            vol.Optional(CONF_UNIT_ID, default=DEFAULT_UNIT_ID): _int_selector(1, 255),
            vol.Required(CONF_INSTALLED_PHASES, default=PHASE_MODE_3P): selector.SelectSelector(selector.SelectSelectorConfig(options=PHASE_SELECTOR_OPTIONS)),
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return WebastoUniteOptionsFlow(config_entry)


class WebastoUniteOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry
        self.options = dict(config_entry.options)
        self.entry_data = dict(config_entry.data)

    def _flatten_section_input(self, user_input: dict[str, Any] | None) -> dict[str, Any]:
        if not user_input:
            return {}
        flattened: dict[str, Any] = {}
        for key, value in user_input.items():
            if isinstance(value, dict):
                flattened.update(value)
            else:
                flattened[key] = value
        return flattened

    def _current_values(self, user_input: dict[str, Any] | None = None) -> dict[str, Any]:
        current = {**self._config_entry.data, **self.options}
        flattened: dict[str, Any] = {}
        if user_input:
            flattened = self._flatten_section_input(user_input)
            current.update(flattened)
        if CONF_SOLAR_GRID_POWER_SENSOR not in current and CONF_DLB_GRID_POWER_SENSOR in current:
            current[CONF_SOLAR_GRID_POWER_SENSOR] = current.get(CONF_DLB_GRID_POWER_SENSOR)
        if current.get(CONF_DLB_INPUT_MODEL) == "grid_power":
            current[CONF_DLB_INPUT_MODEL] = DlbInputModel.DISABLED.value
        if CONF_DLB_ENABLED not in flattened and CONF_DLB_INPUT_MODEL in flattened:
            current[CONF_DLB_ENABLED] = (
                flattened[CONF_DLB_INPUT_MODEL] == DlbInputModel.PHASE_CURRENTS.value
            )
        if CONF_DLB_ENABLED not in current:
            model = current.get(CONF_DLB_INPUT_MODEL, DlbInputModel.DISABLED.value)
            current[CONF_DLB_ENABLED] = model == DlbInputModel.PHASE_CURRENTS.value
        if CONF_SOLAR_CONTROL_STRATEGY in current:
            current[CONF_SOLAR_CONTROL_STRATEGY] = normalize_solar_control_strategy(current[CONF_SOLAR_CONTROL_STRATEGY]).value
        if CONF_SOLAR_UNTIL_UNPLUG_STRATEGY in current:
            current[CONF_SOLAR_UNTIL_UNPLUG_STRATEGY] = normalize_solar_override_strategy(current[CONF_SOLAR_UNTIL_UNPLUG_STRATEGY]).value
        return current

    def _build_init_schema(self, current: dict[str, Any]) -> vol.Schema:
        control_mode = current.get(CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE)
        managed_control = control_mode == ControlMode.MANAGED_CONTROL.value
        connection_defaults = {
            CONF_HOST: current.get(CONF_HOST, ""),
            CONF_PORT: current.get(CONF_PORT, DEFAULT_PORT),
            CONF_UNIT_ID: current.get(CONF_UNIT_ID, DEFAULT_UNIT_ID),
            CONF_POLLING_INTERVAL: current.get(CONF_POLLING_INTERVAL, DEFAULT_POLL_INTERVAL_S),
        }
        connection_schema = vol.Schema(
            {
                vol.Optional(CONF_HOST, default=current.get(CONF_HOST, "")): str,
                vol.Optional(CONF_PORT, default=current.get(CONF_PORT, DEFAULT_PORT)): _int_selector(1, 65535),
                vol.Optional(CONF_UNIT_ID, default=current.get(CONF_UNIT_ID, DEFAULT_UNIT_ID)): _int_selector(1, 255),
                vol.Optional(CONF_POLLING_INTERVAL, default=current.get(CONF_POLLING_INTERVAL, DEFAULT_POLL_INTERVAL_S)): _float_selector(MIN_SECONDS, MAX_SECONDS, 0.1),
            }
        )
        general_defaults = {
            CONF_INSTALLED_PHASES: current.get(CONF_INSTALLED_PHASES, PHASE_MODE_3P),
            CONF_CONTROL_MODE: current.get(CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE),
            CONF_STARTUP_CHARGE_MODE: current.get(CONF_STARTUP_CHARGE_MODE, DEFAULT_STARTUP_CHARGE_MODE),
            CONF_USER_LIMIT: current.get(CONF_USER_LIMIT, DEFAULT_USER_LIMIT_A),
            CONF_SAFE_CURRENT: current.get(CONF_SAFE_CURRENT, DEFAULT_SAFE_CURRENT_A),
        }
        general_schema = vol.Schema(
            {
                vol.Optional(CONF_INSTALLED_PHASES, default=current.get(CONF_INSTALLED_PHASES, PHASE_MODE_3P)): selector.SelectSelector(selector.SelectSelectorConfig(options=PHASE_SELECTOR_OPTIONS)),
                vol.Optional(CONF_CONTROL_MODE, default=current.get(CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE)): selector.SelectSelector(selector.SelectSelectorConfig(options=CONTROL_MODE_SELECTOR_OPTIONS)),
                vol.Optional(
                    CONF_STARTUP_CHARGE_MODE,
                    default=current.get(CONF_STARTUP_CHARGE_MODE, DEFAULT_STARTUP_CHARGE_MODE),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": ChargeMode.OFF.value, "label": "Off"},
                            {"value": ChargeMode.NORMAL.value, "label": "Normal"},
                            {
                                "value": ChargeMode.SOLAR.value,
                                "label": _solar_mode_label(
                                    current.get(CONF_SOLAR_CONTROL_STRATEGY, SolarControlStrategy.DISABLED.value)
                                ),
                            },
                            {"value": ChargeMode.FIXED_CURRENT.value, "label": "Fixed Current"},
                        ]
                    )
                ),
                vol.Optional(CONF_USER_LIMIT, default=current.get(CONF_USER_LIMIT, DEFAULT_USER_LIMIT_A)): _int_selector(int(MIN_CURRENT_A), int(MAX_CURRENT_A)),
                vol.Optional(CONF_SAFE_CURRENT, default=current.get(CONF_SAFE_CURRENT, DEFAULT_SAFE_CURRENT_A)): _int_selector(int(MIN_CURRENT_A), int(MAX_CURRENT_A)),
            }
        )
        session_override_fields: dict[Any, Any] = {}
        session_override_defaults: dict[str, Any] = {}
        if managed_control:
            session_override_defaults = {
                CONF_FIXED_CURRENT: current.get(CONF_FIXED_CURRENT, DEFAULT_FIXED_CURRENT_A),
                CONF_SOLAR_UNTIL_UNPLUG_STRATEGY: current.get(CONF_SOLAR_UNTIL_UNPLUG_STRATEGY, SolarOverrideStrategy.INHERIT.value),
            }
            session_override_fields = {
                vol.Optional(CONF_FIXED_CURRENT, default=current.get(CONF_FIXED_CURRENT, DEFAULT_FIXED_CURRENT_A)): _int_selector(int(MIN_CURRENT_A), int(MAX_CURRENT_A)),
                vol.Optional(CONF_SOLAR_UNTIL_UNPLUG_STRATEGY, default=current.get(CONF_SOLAR_UNTIL_UNPLUG_STRATEGY, SolarOverrideStrategy.INHERIT.value)): selector.SelectSelector(selector.SelectSelectorConfig(options=SOLAR_OVERRIDE_STRATEGY_OPTIONS)),
            }

        dlb_fields: dict[Any, Any] = {
            vol.Optional(CONF_DLB_ENABLED, default=current.get(CONF_DLB_ENABLED, False)): bool,
            vol.Optional(CONF_DLB_SENSOR_SCOPE, default=current.get(CONF_DLB_SENSOR_SCOPE, DlbSensorScope.LOAD_EXCLUDING_CHARGER.value)): selector.SelectSelector(selector.SelectSelectorConfig(options=DLB_SENSOR_SCOPE_SELECTOR_OPTIONS)),
            vol.Optional(CONF_DLB_REQUIRE_UNITS, default=current.get(CONF_DLB_REQUIRE_UNITS, False)): bool,
            vol.Optional(CONF_MAIN_FUSE, default=current.get(CONF_MAIN_FUSE, DEFAULT_MAIN_FUSE_A)): _float_selector(MIN_CURRENT_A, 200.0, 0.1),
            vol.Optional(CONF_SAFETY_MARGIN, default=current.get(CONF_SAFETY_MARGIN, DEFAULT_SAFETY_MARGIN_A)): _float_selector(0.0, 50.0, 0.1),
            _optional_field(CONF_DLB_L1_SENSOR, _entity_selector(), current.get(CONF_DLB_L1_SENSOR)): _entity_selector(),
            _optional_field(CONF_DLB_L2_SENSOR, _entity_selector(), current.get(CONF_DLB_L2_SENSOR)): _entity_selector(),
            _optional_field(CONF_DLB_L3_SENSOR, _entity_selector(), current.get(CONF_DLB_L3_SENSOR)): _entity_selector(),
        }
        dlb_defaults: dict[str, Any] = {
            CONF_DLB_ENABLED: current.get(CONF_DLB_ENABLED, False),
            CONF_DLB_SENSOR_SCOPE: current.get(CONF_DLB_SENSOR_SCOPE, DlbSensorScope.LOAD_EXCLUDING_CHARGER.value),
            CONF_DLB_REQUIRE_UNITS: current.get(CONF_DLB_REQUIRE_UNITS, False),
            CONF_MAIN_FUSE: current.get(CONF_MAIN_FUSE, DEFAULT_MAIN_FUSE_A),
            CONF_SAFETY_MARGIN: current.get(CONF_SAFETY_MARGIN, DEFAULT_SAFETY_MARGIN_A),
            CONF_DLB_L1_SENSOR: current.get(CONF_DLB_L1_SENSOR),
            CONF_DLB_L2_SENSOR: current.get(CONF_DLB_L2_SENSOR),
            CONF_DLB_L3_SENSOR: current.get(CONF_DLB_L3_SENSOR),
        }
        dlb_defaults = _compact_section_defaults(dlb_defaults)
        solar_fields: dict[Any, Any] = {
            vol.Optional(CONF_SOLAR_CONTROL_STRATEGY, default=current.get(CONF_SOLAR_CONTROL_STRATEGY, SolarControlStrategy.DISABLED.value)): selector.SelectSelector(selector.SelectSelectorConfig(options=SOLAR_CONTROL_STRATEGY_OPTIONS)),
            vol.Optional(CONF_SOLAR_INPUT_MODEL, default=current.get(CONF_SOLAR_INPUT_MODEL, SolarInputModel.GRID_POWER_DERIVED.value)): selector.SelectSelector(selector.SelectSelectorConfig(options=SOLAR_INPUT_MODEL_SELECTOR_OPTIONS)),
            vol.Optional(CONF_SOLAR_REQUIRE_UNITS, default=current.get(CONF_SOLAR_REQUIRE_UNITS, False)): bool,
            _optional_field(CONF_SOLAR_SURPLUS_SENSOR, _entity_selector(), current.get(CONF_SOLAR_SURPLUS_SENSOR)): _entity_selector(),
            _optional_field(CONF_SOLAR_GRID_POWER_SENSOR, _entity_selector(), current.get(CONF_SOLAR_GRID_POWER_SENSOR)): _entity_selector(),
            vol.Optional(CONF_SOLAR_START_THRESHOLD, default=current.get(CONF_SOLAR_START_THRESHOLD, 1800.0)): _float_selector(MIN_POWER_W, MAX_POWER_W, 1.0),
            vol.Optional(CONF_SOLAR_STOP_THRESHOLD, default=current.get(CONF_SOLAR_STOP_THRESHOLD, 1200.0)): _float_selector(MIN_POWER_W, MAX_POWER_W, 1.0),
            vol.Optional(CONF_SOLAR_START_DELAY, default=current.get(CONF_SOLAR_START_DELAY, DEFAULT_PV_START_DELAY_S)): _float_selector(0.0, 3600.0, 0.1),
            vol.Optional(CONF_SOLAR_STOP_DELAY, default=current.get(CONF_SOLAR_STOP_DELAY, DEFAULT_PV_STOP_DELAY_S)): _float_selector(0.0, 3600.0, 0.1),
            vol.Optional(CONF_SOLAR_MIN_RUNTIME, default=current.get(CONF_SOLAR_MIN_RUNTIME, DEFAULT_PV_MIN_RUNTIME_S)): _float_selector(0.0, 3600.0, 0.1),
            vol.Optional(CONF_SOLAR_MIN_PAUSE, default=current.get(CONF_SOLAR_MIN_PAUSE, DEFAULT_PV_MIN_PAUSE_S)): _float_selector(0.0, 3600.0, 0.1),
            vol.Optional(CONF_SOLAR_MIN_CURRENT, default=current.get(CONF_SOLAR_MIN_CURRENT, 6.0)): _int_selector(int(MIN_CURRENT_A), int(MAX_CURRENT_A)),
        }
        solar_defaults: dict[str, Any] = {
            CONF_SOLAR_CONTROL_STRATEGY: current.get(CONF_SOLAR_CONTROL_STRATEGY, SolarControlStrategy.DISABLED.value),
            CONF_SOLAR_INPUT_MODEL: current.get(CONF_SOLAR_INPUT_MODEL, SolarInputModel.GRID_POWER_DERIVED.value),
            CONF_SOLAR_REQUIRE_UNITS: current.get(CONF_SOLAR_REQUIRE_UNITS, False),
            CONF_SOLAR_SURPLUS_SENSOR: current.get(CONF_SOLAR_SURPLUS_SENSOR),
            CONF_SOLAR_GRID_POWER_SENSOR: current.get(CONF_SOLAR_GRID_POWER_SENSOR),
            CONF_SOLAR_START_THRESHOLD: current.get(CONF_SOLAR_START_THRESHOLD, 1800.0),
            CONF_SOLAR_STOP_THRESHOLD: current.get(CONF_SOLAR_STOP_THRESHOLD, 1200.0),
            CONF_SOLAR_START_DELAY: current.get(CONF_SOLAR_START_DELAY, DEFAULT_PV_START_DELAY_S),
            CONF_SOLAR_STOP_DELAY: current.get(CONF_SOLAR_STOP_DELAY, DEFAULT_PV_STOP_DELAY_S),
            CONF_SOLAR_MIN_RUNTIME: current.get(CONF_SOLAR_MIN_RUNTIME, DEFAULT_PV_MIN_RUNTIME_S),
            CONF_SOLAR_MIN_PAUSE: current.get(CONF_SOLAR_MIN_PAUSE, DEFAULT_PV_MIN_PAUSE_S),
            CONF_SOLAR_MIN_CURRENT: current.get(CONF_SOLAR_MIN_CURRENT, 6.0),
        }
        solar_defaults = _compact_section_defaults(solar_defaults)
        advanced_defaults = {
            CONF_KEEPALIVE_INTERVAL: current.get(CONF_KEEPALIVE_INTERVAL, DEFAULT_KEEPALIVE_INTERVAL_S),
            CONF_TIMEOUT: current.get(CONF_TIMEOUT, DEFAULT_TIMEOUT_S),
            CONF_RETRIES: current.get(CONF_RETRIES, DEFAULT_RETRIES),
        }
        advanced_schema = vol.Schema(
            {
                vol.Optional(CONF_KEEPALIVE_INTERVAL, default=current.get(CONF_KEEPALIVE_INTERVAL, DEFAULT_KEEPALIVE_INTERVAL_S)): _float_selector(1.0, MAX_SECONDS, 0.1),
                vol.Optional(CONF_TIMEOUT, default=current.get(CONF_TIMEOUT, DEFAULT_TIMEOUT_S)): _float_selector(MIN_SECONDS, 60.0, 0.1),
                vol.Optional(CONF_RETRIES, default=current.get(CONF_RETRIES, DEFAULT_RETRIES)): _int_selector(1, MAX_RETRIES),
            }
        )

        schema: dict[Any, Any] = {
            vol.Optional("connection", default=connection_defaults): section(connection_schema, {"collapsed": False}),
            vol.Optional("general_charging", default=general_defaults): section(general_schema, {"collapsed": False}),
            vol.Optional("dynamic_load_balancing", default=dlb_defaults): section(vol.Schema(dlb_fields), {"collapsed": True}),
            vol.Optional("solar_charging", default=solar_defaults): section(vol.Schema(solar_fields), {"collapsed": True}),
            vol.Optional("advanced", default=advanced_defaults): section(advanced_schema, {"collapsed": True}),
        }
        if session_override_fields:
            schema[vol.Optional("session_overrides", default=session_override_defaults)] = section(vol.Schema(session_override_fields), {"collapsed": True})
        return vol.Schema(schema)

    def _validate_all_options(self, user_input: dict[str, Any]) -> dict[str, Any]:
        validated = self._current_values(user_input)
        validated.setdefault(CONF_POLLING_INTERVAL, DEFAULT_POLL_INTERVAL_S)
        validated.setdefault(CONF_TIMEOUT, DEFAULT_TIMEOUT_S)
        validated.setdefault(CONF_RETRIES, DEFAULT_RETRIES)
        validated.setdefault(CONF_KEEPALIVE_INTERVAL, DEFAULT_KEEPALIVE_INTERVAL_S)
        validated.setdefault(CONF_SAFE_CURRENT, DEFAULT_SAFE_CURRENT_A)
        validated.setdefault(CONF_MIN_CURRENT, DEFAULT_MIN_CURRENT_A)
        validated.setdefault(CONF_MAX_CURRENT, DEFAULT_MAX_CURRENT_A)
        validated.setdefault(CONF_USER_LIMIT, DEFAULT_USER_LIMIT_A)
        validated.setdefault(CONF_FIXED_CURRENT, DEFAULT_FIXED_CURRENT_A)
        validated.setdefault(CONF_DLB_ENABLED, False)
        validated.setdefault(CONF_DLB_SENSOR_SCOPE, DlbSensorScope.LOAD_EXCLUDING_CHARGER.value)
        validated.setdefault(CONF_DLB_REQUIRE_UNITS, False)
        validated.setdefault(CONF_MAIN_FUSE, DEFAULT_MAIN_FUSE_A)
        validated.setdefault(CONF_SAFETY_MARGIN, DEFAULT_SAFETY_MARGIN_A)
        validated.setdefault(CONF_SOLAR_INPUT_MODEL, SolarInputModel.GRID_POWER_DERIVED.value)
        validated.setdefault(CONF_SOLAR_REQUIRE_UNITS, False)
        validated.setdefault(CONF_SOLAR_UNTIL_UNPLUG_STRATEGY, SolarOverrideStrategy.INHERIT.value)
        validated.setdefault(CONF_SOLAR_START_THRESHOLD, 1800.0)
        validated.setdefault(CONF_SOLAR_STOP_THRESHOLD, 1200.0)
        validated.setdefault(CONF_SOLAR_START_DELAY, DEFAULT_PV_START_DELAY_S)
        validated.setdefault(CONF_SOLAR_STOP_DELAY, DEFAULT_PV_STOP_DELAY_S)
        validated.setdefault(CONF_SOLAR_MIN_RUNTIME, DEFAULT_PV_MIN_RUNTIME_S)
        validated.setdefault(CONF_SOLAR_MIN_PAUSE, DEFAULT_PV_MIN_PAUSE_S)
        validated.setdefault(CONF_SOLAR_MIN_CURRENT, 6.0)
        connection_input = {
            CONF_HOST: validated.pop(CONF_HOST),
            CONF_PORT: validated.pop(CONF_PORT),
            CONF_UNIT_ID: validated.pop(CONF_UNIT_ID),
            CONF_INSTALLED_PHASES: validated.pop(CONF_INSTALLED_PHASES),
        }
        self.entry_data = _validate_connection_data(connection_input)
        validated.update(self.entry_data)
        validated[CONF_POLLING_INTERVAL] = _bounded_float(MIN_SECONDS, MAX_SECONDS, CONF_POLLING_INTERVAL)(validated[CONF_POLLING_INTERVAL])
        validated[CONF_TIMEOUT] = _bounded_float(MIN_SECONDS, 60.0, CONF_TIMEOUT)(validated[CONF_TIMEOUT])
        validated[CONF_RETRIES] = _bounded_int(1, MAX_RETRIES, CONF_RETRIES)(validated[CONF_RETRIES])
        validated[CONF_KEEPALIVE_INTERVAL] = _bounded_float(1.0, MAX_SECONDS, CONF_KEEPALIVE_INTERVAL)(validated[CONF_KEEPALIVE_INTERVAL])
        validated[CONF_SAFE_CURRENT] = _bounded_int(int(MIN_CURRENT_A), int(MAX_CURRENT_A), CONF_SAFE_CURRENT)(validated[CONF_SAFE_CURRENT])
        validated[CONF_MIN_CURRENT] = _bounded_int(int(MIN_CURRENT_A), int(MAX_CURRENT_A), CONF_MIN_CURRENT)(validated[CONF_MIN_CURRENT])
        validated[CONF_MAX_CURRENT] = _bounded_int(int(MIN_CURRENT_A), int(MAX_CURRENT_A), CONF_MAX_CURRENT)(validated[CONF_MAX_CURRENT])
        validated[CONF_USER_LIMIT] = _bounded_int(int(MIN_CURRENT_A), int(MAX_CURRENT_A), CONF_USER_LIMIT)(validated[CONF_USER_LIMIT])
        validated[CONF_MAIN_FUSE] = _bounded_float(MIN_CURRENT_A, 200.0, CONF_MAIN_FUSE)(validated[CONF_MAIN_FUSE])
        validated[CONF_SAFETY_MARGIN] = _bounded_float(0.0, 50.0, CONF_SAFETY_MARGIN)(validated[CONF_SAFETY_MARGIN])
        validated[CONF_SOLAR_START_THRESHOLD] = _bounded_float(MIN_POWER_W, MAX_POWER_W, CONF_SOLAR_START_THRESHOLD)(validated[CONF_SOLAR_START_THRESHOLD])
        validated[CONF_SOLAR_STOP_THRESHOLD] = _bounded_float(MIN_POWER_W, MAX_POWER_W, CONF_SOLAR_STOP_THRESHOLD)(validated[CONF_SOLAR_STOP_THRESHOLD])
        validated[CONF_SOLAR_START_DELAY] = _bounded_float(0.0, 3600.0, CONF_SOLAR_START_DELAY)(validated[CONF_SOLAR_START_DELAY])
        validated[CONF_SOLAR_STOP_DELAY] = _bounded_float(0.0, 3600.0, CONF_SOLAR_STOP_DELAY)(validated[CONF_SOLAR_STOP_DELAY])
        validated[CONF_SOLAR_MIN_RUNTIME] = _bounded_float(0.0, 3600.0, CONF_SOLAR_MIN_RUNTIME)(validated[CONF_SOLAR_MIN_RUNTIME])
        validated[CONF_SOLAR_MIN_PAUSE] = _bounded_float(0.0, 3600.0, CONF_SOLAR_MIN_PAUSE)(validated[CONF_SOLAR_MIN_PAUSE])
        validated[CONF_SOLAR_MIN_CURRENT] = _bounded_int(int(MIN_CURRENT_A), int(MAX_CURRENT_A), CONF_SOLAR_MIN_CURRENT)(validated[CONF_SOLAR_MIN_CURRENT])
        validated[CONF_FIXED_CURRENT] = _bounded_int(int(MIN_CURRENT_A), int(MAX_CURRENT_A), CONF_FIXED_CURRENT)(validated[CONF_FIXED_CURRENT])
        validated[CONF_DLB_INPUT_MODEL] = (
            DlbInputModel.PHASE_CURRENTS.value
            if bool(validated.get(CONF_DLB_ENABLED))
            else DlbInputModel.DISABLED.value
        )
        if CONF_DLB_GRID_POWER_SENSOR in validated and CONF_SOLAR_GRID_POWER_SENSOR not in validated:
            validated[CONF_SOLAR_GRID_POWER_SENSOR] = validated[CONF_DLB_GRID_POWER_SENSOR]
        if CONF_SOLAR_GRID_POWER_SENSOR in validated:
            validated.pop(CONF_DLB_GRID_POWER_SENSOR, None)
        validated[CONF_SOLAR_CONTROL_STRATEGY] = normalize_solar_control_strategy(validated[CONF_SOLAR_CONTROL_STRATEGY]).value
        validated[CONF_SOLAR_UNTIL_UNPLUG_STRATEGY] = normalize_solar_override_strategy(validated[CONF_SOLAR_UNTIL_UNPLUG_STRATEGY]).value
        validated = _validate_init_options(validated)
        validated = _validate_dlb_options(validated, self.entry_data.get(CONF_INSTALLED_PHASES, PHASE_MODE_3P))
        validated = _validate_solar_options(validated)
        return validated

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self.options.update(self._validate_all_options(dict(user_input)))
                return self.async_create_entry(title="", data=self.options)
            except vol.Invalid as err:
                errors["base"] = _validation_error_key(err)
        current = self._current_values(user_input)
        return self.async_show_form(step_id="init", data_schema=self._build_init_schema(current), errors=errors)
