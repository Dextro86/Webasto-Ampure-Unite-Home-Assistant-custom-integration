from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector

from .const import *
from .models import ChargeMode, ControlMode, DlbInputModel, DlbSensorScope, KeepaliveMode, PvControlStrategy, PvInputModel, PvOverrideStrategy, PvPhaseSwitchingMode, normalize_pv_control_strategy, normalize_pv_override_strategy

PHASE_OPTIONS = [PHASE_MODE_1P, PHASE_MODE_3P]
PHASE_SELECTOR_OPTIONS = [
    {"value": PHASE_MODE_1P, "label": "1 Phase"},
    {"value": PHASE_MODE_3P, "label": "3 Phases"},
]
CONTROL_MODE_OPTIONS = [mode.value for mode in ControlMode]
CONTROL_MODE_SELECTOR_OPTIONS = [
    {"value": ControlMode.KEEPALIVE_ONLY.value, "label": "Read-only + Keepalive"},
    {"value": ControlMode.MANAGED_CONTROL.value, "label": "Managed Charging Control"},
]
STARTUP_CHARGE_MODE_SELECTOR_OPTIONS = [
    {"value": ChargeMode.OFF.value, "label": "Off"},
    {"value": ChargeMode.NORMAL.value, "label": "Normal"},
    {"value": ChargeMode.PV.value, "label": "PV"},
    {"value": ChargeMode.FIXED_CURRENT.value, "label": "Fixed Current"},
]
KEEPALIVE_MODE_SELECTOR_OPTIONS = [
    {"value": KeepaliveMode.AUTO.value, "label": "Auto (Recommended)"},
    {"value": KeepaliveMode.FORCED.value, "label": "Always Send Keepalive"},
    {"value": KeepaliveMode.DISABLED.value, "label": "Disable Keepalive"},
]
DLB_INPUT_MODEL_SELECTOR_OPTIONS = [
    {"value": DlbInputModel.DISABLED.value, "label": "Disabled"},
    {"value": DlbInputModel.PHASE_CURRENTS.value, "label": "Phase Current Sensors (Recommended)"},
    {"value": DlbInputModel.GRID_POWER.value, "label": "Grid Power Sensor"},
]
DLB_SENSOR_SCOPE_SELECTOR_OPTIONS = [
    {"value": DlbSensorScope.LOAD_EXCLUDING_CHARGER.value, "label": "Charger Excluded"},
    {"value": DlbSensorScope.TOTAL_INCLUDING_CHARGER.value, "label": "Charger Included"},
]
PV_INPUT_MODEL_SELECTOR_OPTIONS = [
    {"value": PvInputModel.SURPLUS_SENSOR.value, "label": "Use a Surplus Power Sensor"},
    {"value": PvInputModel.GRID_POWER_DERIVED.value, "label": "Use Signed Grid Power Sensor"},
]
PV_CONTROL_STRATEGY_OPTIONS = [
    {"value": PvControlStrategy.DISABLED.value, "label": "Disabled"},
    {"value": PvControlStrategy.SURPLUS.value, "label": "Surplus Only"},
    {"value": PvControlStrategy.MIN_PLUS_SURPLUS.value, "label": "Minimum + Surplus"},
]
PV_OVERRIDE_STRATEGY_OPTIONS = [
    {"value": PvOverrideStrategy.INHERIT.value, "label": "Same as PV Control Strategy"},
    {"value": PvOverrideStrategy.SURPLUS.value, "label": "Surplus Only"},
    {"value": PvOverrideStrategy.MIN_PLUS_SURPLUS.value, "label": "Minimum + Surplus"},
]
PV_PHASE_SWITCHING_MODE_OPTIONS = [
    {"value": PvPhaseSwitchingMode.DISABLED.value, "label": "Disabled"},
    {"value": PvPhaseSwitchingMode.MANUAL_ONLY.value, "label": "Manual Only"},
    {"value": PvPhaseSwitchingMode.AUTOMATIC_1P3P.value, "label": "Automatic 1P/3P"},
]

MIN_CURRENT_A = 6.0
MAX_CURRENT_A = 32.0
MIN_POWER_W = 0.0
MAX_POWER_W = 250_000.0
MAX_PHASE_SWITCHING_HYSTERESIS_W = 10_000.0
MAX_PHASE_SWITCHING_MIN_INTERVAL_S = 7_200.0
MAX_PHASE_SWITCHING_PER_SESSION = 50
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


def _optional_field(key: str, field_type, value: Any | None = None):
    if value is None:
        return vol.Optional(key)
    return vol.Optional(key, default=value)


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
    startup_mode = options.get(CONF_STARTUP_CHARGE_MODE, DEFAULT_STARTUP_CHARGE_MODE)
    if startup_mode not in {mode.value for mode in ChargeMode}:
        raise vol.Invalid(f"{CONF_STARTUP_CHARGE_MODE} must be a supported charge mode")
    options[CONF_STARTUP_CHARGE_MODE] = startup_mode
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
    model = options[CONF_DLB_INPUT_MODEL]
    if model == DlbInputModel.DISABLED.value:
        return options
    if model == DlbInputModel.PHASE_CURRENTS.value:
        if installed_phases == PHASE_MODE_1P:
            if not options.get(CONF_DLB_L1_SENSOR):
                raise vol.Invalid("A DLB L1 phase current sensor is required for 1p phase_currents mode")
        else:
            missing = [key for key in (CONF_DLB_L1_SENSOR, CONF_DLB_L2_SENSOR, CONF_DLB_L3_SENSOR) if not options.get(key)]
            if missing:
                raise vol.Invalid("DLB L1, L2 and L3 phase current sensors are required for 3p phase_currents mode")
    if model == DlbInputModel.GRID_POWER.value:
        if installed_phases == PHASE_MODE_3P:
            raise vol.Invalid("DLB grid power mode is only supported for 1p charger configurations")
        if not options.get(CONF_DLB_GRID_POWER_SENSOR):
            raise vol.Invalid("A DLB grid power sensor is required for grid_power mode")
    return options


def _validate_pv_options(options: dict[str, Any]) -> dict[str, Any]:
    options[CONF_PV_CONTROL_STRATEGY] = normalize_pv_control_strategy(
        options.get(CONF_PV_CONTROL_STRATEGY, PvControlStrategy.DISABLED.value)
    ).value
    options[CONF_PV_UNTIL_UNPLUG_STRATEGY] = normalize_pv_override_strategy(
        options.get(CONF_PV_UNTIL_UNPLUG_STRATEGY, PvOverrideStrategy.INHERIT.value)
    ).value
    start_threshold = float(options[CONF_PV_START_THRESHOLD])
    stop_threshold = float(options[CONF_PV_STOP_THRESHOLD])
    pv_min_current = float(options[CONF_PV_MIN_CURRENT])
    fixed_current = float(options.get(CONF_FIXED_CURRENT, DEFAULT_FIXED_CURRENT_A))
    phase_switch_max = int(options.get(CONF_PV_PHASE_SWITCHING_MAX_PER_SESSION, DEFAULT_PV_PHASE_SWITCHING_MAX_PER_SESSION))

    if stop_threshold > start_threshold:
        raise vol.Invalid(f"{CONF_PV_STOP_THRESHOLD} must be less than or equal to {CONF_PV_START_THRESHOLD}")
    if pv_min_current > MAX_CURRENT_A:
        raise vol.Invalid(f"{CONF_PV_MIN_CURRENT} must be less than or equal to {MAX_CURRENT_A}")
    if not MIN_CURRENT_A <= fixed_current <= MAX_CURRENT_A:
        raise vol.Invalid(f"{CONF_FIXED_CURRENT} must be between {MIN_CURRENT_A} and {MAX_CURRENT_A}")
    if not 1 <= phase_switch_max <= MAX_PHASE_SWITCHING_PER_SESSION:
        raise vol.Invalid(f"{CONF_PV_PHASE_SWITCHING_MAX_PER_SESSION} must be between 1 and {MAX_PHASE_SWITCHING_PER_SESSION}")

    strategy = options[CONF_PV_CONTROL_STRATEGY]
    if strategy in (
        PvControlStrategy.SURPLUS.value,
        PvControlStrategy.MIN_PLUS_SURPLUS.value,
    ):
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
    if "DLB grid power sensor is required for grid_power mode" in message:
        return "dlb_grid_sensor_required"
    if "DLB grid power mode is only supported for 1p" in message:
        return "dlb_grid_power_3p_not_supported"
    if CONF_PV_STOP_THRESHOLD in message and CONF_PV_START_THRESHOLD in message:
        return "pv_threshold_order"
    if "PV surplus sensor is required" in message:
        return "pv_surplus_sensor_required"
    if "PV mode derives surplus from grid power" in message:
        return "pv_grid_sensor_required"
    if CONF_FIXED_CURRENT in message:
        return "fixed_current_out_of_range"
    if CONF_PV_PHASE_SWITCHING_MAX_PER_SESSION in message:
        return "phase_switch_limit_out_of_range"
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
        if user_input:
            current.update(self._flatten_section_input(user_input))
        if CONF_PV_CONTROL_STRATEGY in current:
            current[CONF_PV_CONTROL_STRATEGY] = normalize_pv_control_strategy(current[CONF_PV_CONTROL_STRATEGY]).value
        if CONF_PV_UNTIL_UNPLUG_STRATEGY in current:
            current[CONF_PV_UNTIL_UNPLUG_STRATEGY] = normalize_pv_override_strategy(current[CONF_PV_UNTIL_UNPLUG_STRATEGY]).value
        return current

    def _build_init_schema(self, current: dict[str, Any]) -> vol.Schema:
        installed_phases = current.get(CONF_INSTALLED_PHASES, PHASE_MODE_3P)
        control_mode = current.get(CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE)
        managed_control = control_mode == ControlMode.MANAGED_CONTROL.value
        dlb_model = current.get(CONF_DLB_INPUT_MODEL, DlbInputModel.DISABLED.value)
        pv_strategy = current.get(CONF_PV_CONTROL_STRATEGY, PvControlStrategy.DISABLED.value)
        pv_phase_mode = current.get(CONF_PV_PHASE_SWITCHING_MODE, DEFAULT_PV_PHASE_SWITCHING_MODE)
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
                vol.Optional(CONF_STARTUP_CHARGE_MODE, default=current.get(CONF_STARTUP_CHARGE_MODE, DEFAULT_STARTUP_CHARGE_MODE)): selector.SelectSelector(selector.SelectSelectorConfig(options=STARTUP_CHARGE_MODE_SELECTOR_OPTIONS)),
                vol.Optional(CONF_USER_LIMIT, default=current.get(CONF_USER_LIMIT, DEFAULT_USER_LIMIT_A)): _float_selector(MIN_CURRENT_A, MAX_CURRENT_A, 0.1),
                vol.Optional(CONF_SAFE_CURRENT, default=current.get(CONF_SAFE_CURRENT, DEFAULT_SAFE_CURRENT_A)): _float_selector(MIN_CURRENT_A, MAX_CURRENT_A, 0.1),
            }
        )
        session_override_fields: dict[Any, Any] = {}
        session_override_defaults: dict[str, Any] = {}
        if managed_control:
            session_override_defaults = {
                CONF_FIXED_CURRENT: current.get(CONF_FIXED_CURRENT, DEFAULT_FIXED_CURRENT_A),
                CONF_PV_UNTIL_UNPLUG_STRATEGY: current.get(CONF_PV_UNTIL_UNPLUG_STRATEGY, PvOverrideStrategy.INHERIT.value),
            }
            session_override_fields = {
                vol.Optional(CONF_FIXED_CURRENT, default=current.get(CONF_FIXED_CURRENT, DEFAULT_FIXED_CURRENT_A)): _float_selector(MIN_CURRENT_A, MAX_CURRENT_A, 0.1),
                vol.Optional(CONF_PV_UNTIL_UNPLUG_STRATEGY, default=current.get(CONF_PV_UNTIL_UNPLUG_STRATEGY, PvOverrideStrategy.INHERIT.value)): selector.SelectSelector(selector.SelectSelectorConfig(options=PV_OVERRIDE_STRATEGY_OPTIONS)),
            }

        dlb_fields: dict[Any, Any] = {
            vol.Optional(CONF_DLB_INPUT_MODEL, default=current.get(CONF_DLB_INPUT_MODEL, DlbInputModel.DISABLED.value)): selector.SelectSelector(selector.SelectSelectorConfig(options=DLB_INPUT_MODEL_SELECTOR_OPTIONS)),
        }
        dlb_defaults: dict[str, Any] = {
            CONF_DLB_INPUT_MODEL: current.get(CONF_DLB_INPUT_MODEL, DlbInputModel.DISABLED.value),
        }
        if dlb_model != DlbInputModel.DISABLED.value:
            dlb_defaults.update(
                {
                    CONF_DLB_SENSOR_SCOPE: current.get(CONF_DLB_SENSOR_SCOPE, DlbSensorScope.LOAD_EXCLUDING_CHARGER.value),
                    CONF_MAIN_FUSE: current.get(CONF_MAIN_FUSE, DEFAULT_MAIN_FUSE_A),
                    CONF_SAFETY_MARGIN: current.get(CONF_SAFETY_MARGIN, DEFAULT_SAFETY_MARGIN_A),
                }
            )
            dlb_fields[vol.Optional(CONF_DLB_SENSOR_SCOPE, default=current.get(CONF_DLB_SENSOR_SCOPE, DlbSensorScope.LOAD_EXCLUDING_CHARGER.value))] = selector.SelectSelector(selector.SelectSelectorConfig(options=DLB_SENSOR_SCOPE_SELECTOR_OPTIONS))
            dlb_fields[vol.Optional(CONF_MAIN_FUSE, default=current.get(CONF_MAIN_FUSE, DEFAULT_MAIN_FUSE_A))] = _float_selector(MIN_CURRENT_A, 200.0, 0.1)
            dlb_fields[vol.Optional(CONF_SAFETY_MARGIN, default=current.get(CONF_SAFETY_MARGIN, DEFAULT_SAFETY_MARGIN_A))] = _float_selector(0.0, 50.0, 0.1)
            if dlb_model == DlbInputModel.PHASE_CURRENTS.value:
                dlb_defaults[CONF_DLB_L1_SENSOR] = current.get(CONF_DLB_L1_SENSOR)
                dlb_fields[_optional_field(CONF_DLB_L1_SENSOR, _entity_selector(), current.get(CONF_DLB_L1_SENSOR))] = _entity_selector()
                if installed_phases == PHASE_MODE_3P:
                    dlb_defaults[CONF_DLB_L2_SENSOR] = current.get(CONF_DLB_L2_SENSOR)
                    dlb_defaults[CONF_DLB_L3_SENSOR] = current.get(CONF_DLB_L3_SENSOR)
                    dlb_fields[_optional_field(CONF_DLB_L2_SENSOR, _entity_selector(), current.get(CONF_DLB_L2_SENSOR))] = _entity_selector()
                    dlb_fields[_optional_field(CONF_DLB_L3_SENSOR, _entity_selector(), current.get(CONF_DLB_L3_SENSOR))] = _entity_selector()
            if dlb_model == DlbInputModel.GRID_POWER.value:
                dlb_defaults[CONF_DLB_GRID_POWER_SENSOR] = current.get(CONF_DLB_GRID_POWER_SENSOR)
                dlb_fields[_optional_field(CONF_DLB_GRID_POWER_SENSOR, _entity_selector(), current.get(CONF_DLB_GRID_POWER_SENSOR))] = _entity_selector()
        pv_fields: dict[Any, Any] = {
            vol.Optional(CONF_PV_CONTROL_STRATEGY, default=current.get(CONF_PV_CONTROL_STRATEGY, PvControlStrategy.DISABLED.value)): selector.SelectSelector(selector.SelectSelectorConfig(options=PV_CONTROL_STRATEGY_OPTIONS)),
        }
        pv_defaults: dict[str, Any] = {
            CONF_PV_CONTROL_STRATEGY: current.get(CONF_PV_CONTROL_STRATEGY, PvControlStrategy.DISABLED.value),
        }
        if pv_strategy != PvControlStrategy.DISABLED.value:
            pv_defaults.update(
                {
                    CONF_PV_INPUT_MODEL: current.get(CONF_PV_INPUT_MODEL, PvInputModel.GRID_POWER_DERIVED.value),
                    CONF_PV_START_THRESHOLD: current.get(CONF_PV_START_THRESHOLD, 1800.0),
                    CONF_PV_STOP_THRESHOLD: current.get(CONF_PV_STOP_THRESHOLD, 1200.0),
                    CONF_PV_START_DELAY: current.get(CONF_PV_START_DELAY, DEFAULT_PV_START_DELAY_S),
                    CONF_PV_STOP_DELAY: current.get(CONF_PV_STOP_DELAY, DEFAULT_PV_STOP_DELAY_S),
                    CONF_PV_MIN_RUNTIME: current.get(CONF_PV_MIN_RUNTIME, DEFAULT_PV_MIN_RUNTIME_S),
                    CONF_PV_MIN_PAUSE: current.get(CONF_PV_MIN_PAUSE, DEFAULT_PV_MIN_PAUSE_S),
                    CONF_PV_MIN_CURRENT: current.get(CONF_PV_MIN_CURRENT, 6.0),
                }
            )
            pv_fields[vol.Optional(CONF_PV_INPUT_MODEL, default=current.get(CONF_PV_INPUT_MODEL, PvInputModel.GRID_POWER_DERIVED.value))] = selector.SelectSelector(selector.SelectSelectorConfig(options=PV_INPUT_MODEL_SELECTOR_OPTIONS))
            if current.get(CONF_PV_INPUT_MODEL, PvInputModel.GRID_POWER_DERIVED.value) == PvInputModel.SURPLUS_SENSOR.value:
                pv_defaults[CONF_PV_SURPLUS_SENSOR] = current.get(CONF_PV_SURPLUS_SENSOR)
                pv_fields[_optional_field(CONF_PV_SURPLUS_SENSOR, _entity_selector(), current.get(CONF_PV_SURPLUS_SENSOR))] = _entity_selector()
            pv_fields[vol.Optional(CONF_PV_START_THRESHOLD, default=current.get(CONF_PV_START_THRESHOLD, 1800.0))] = _float_selector(MIN_POWER_W, MAX_POWER_W, 1.0)
            pv_fields[vol.Optional(CONF_PV_STOP_THRESHOLD, default=current.get(CONF_PV_STOP_THRESHOLD, 1200.0))] = _float_selector(MIN_POWER_W, MAX_POWER_W, 1.0)
            pv_fields[vol.Optional(CONF_PV_START_DELAY, default=current.get(CONF_PV_START_DELAY, DEFAULT_PV_START_DELAY_S))] = _float_selector(0.0, 3600.0, 0.1)
            pv_fields[vol.Optional(CONF_PV_STOP_DELAY, default=current.get(CONF_PV_STOP_DELAY, DEFAULT_PV_STOP_DELAY_S))] = _float_selector(0.0, 3600.0, 0.1)
            pv_fields[vol.Optional(CONF_PV_MIN_RUNTIME, default=current.get(CONF_PV_MIN_RUNTIME, DEFAULT_PV_MIN_RUNTIME_S))] = _float_selector(0.0, 3600.0, 0.1)
            pv_fields[vol.Optional(CONF_PV_MIN_PAUSE, default=current.get(CONF_PV_MIN_PAUSE, DEFAULT_PV_MIN_PAUSE_S))] = _float_selector(0.0, 3600.0, 0.1)
            pv_fields[vol.Optional(CONF_PV_MIN_CURRENT, default=current.get(CONF_PV_MIN_CURRENT, 6.0))] = _float_selector(MIN_CURRENT_A, MAX_CURRENT_A, 0.1)
        phase_fields: dict[Any, Any] = {}
        phase_defaults: dict[str, Any] = {}
        if installed_phases == PHASE_MODE_3P and pv_strategy != PvControlStrategy.DISABLED.value:
            phase_defaults = {
                CONF_PV_PHASE_SWITCHING_MODE: current.get(CONF_PV_PHASE_SWITCHING_MODE, DEFAULT_PV_PHASE_SWITCHING_MODE),
            }
            phase_fields = {
                vol.Optional(CONF_PV_PHASE_SWITCHING_MODE, default=current.get(CONF_PV_PHASE_SWITCHING_MODE, DEFAULT_PV_PHASE_SWITCHING_MODE)): selector.SelectSelector(selector.SelectSelectorConfig(options=PV_PHASE_SWITCHING_MODE_OPTIONS)),
            }
            if pv_phase_mode == PvPhaseSwitchingMode.AUTOMATIC_1P3P.value:
                phase_defaults.update(
                    {
                        CONF_PV_PHASE_SWITCHING_HYSTERESIS: current.get(CONF_PV_PHASE_SWITCHING_HYSTERESIS, DEFAULT_PV_PHASE_SWITCHING_HYSTERESIS_W),
                        CONF_PV_PHASE_SWITCHING_MIN_INTERVAL: current.get(CONF_PV_PHASE_SWITCHING_MIN_INTERVAL, DEFAULT_PV_PHASE_SWITCHING_MIN_INTERVAL_S),
                        CONF_PV_PHASE_SWITCHING_MAX_PER_SESSION: current.get(CONF_PV_PHASE_SWITCHING_MAX_PER_SESSION, DEFAULT_PV_PHASE_SWITCHING_MAX_PER_SESSION),
                    }
                )
                phase_fields[vol.Optional(CONF_PV_PHASE_SWITCHING_HYSTERESIS, default=current.get(CONF_PV_PHASE_SWITCHING_HYSTERESIS, DEFAULT_PV_PHASE_SWITCHING_HYSTERESIS_W))] = _float_selector(MIN_POWER_W, MAX_PHASE_SWITCHING_HYSTERESIS_W, 1.0)
                phase_fields[vol.Optional(CONF_PV_PHASE_SWITCHING_MIN_INTERVAL, default=current.get(CONF_PV_PHASE_SWITCHING_MIN_INTERVAL, DEFAULT_PV_PHASE_SWITCHING_MIN_INTERVAL_S))] = _float_selector(0.0, MAX_PHASE_SWITCHING_MIN_INTERVAL_S, 1.0)
                phase_fields[vol.Optional(CONF_PV_PHASE_SWITCHING_MAX_PER_SESSION, default=current.get(CONF_PV_PHASE_SWITCHING_MAX_PER_SESSION, DEFAULT_PV_PHASE_SWITCHING_MAX_PER_SESSION))] = _int_selector(1, MAX_PHASE_SWITCHING_PER_SESSION)

        schema: dict[Any, Any] = {
            vol.Optional("connection", default=connection_defaults): section(connection_schema, {"collapsed": False}),
            vol.Optional("general_charging", default=general_defaults): section(general_schema, {"collapsed": False}),
            vol.Optional("dynamic_load_balancing", default=dlb_defaults): section(vol.Schema(dlb_fields), {"collapsed": True}),
            vol.Optional("pv_charging", default=pv_defaults): section(vol.Schema(pv_fields), {"collapsed": True}),
        }
        if session_override_fields:
            schema[vol.Optional("session_overrides", default=session_override_defaults)] = section(vol.Schema(session_override_fields), {"collapsed": True})
        if phase_fields:
            schema[vol.Optional("phase_switching", default=phase_defaults)] = section(vol.Schema(phase_fields), {"collapsed": True})
        return vol.Schema(schema)

    def _validate_all_options(self, user_input: dict[str, Any]) -> dict[str, Any]:
        validated = self._current_values(user_input)
        validated.setdefault(CONF_POLLING_INTERVAL, DEFAULT_POLL_INTERVAL_S)
        validated.setdefault(CONF_TIMEOUT, DEFAULT_TIMEOUT_S)
        validated.setdefault(CONF_RETRIES, DEFAULT_RETRIES)
        validated.setdefault(CONF_KEEPALIVE_MODE, KeepaliveMode.AUTO.value)
        validated.setdefault(CONF_KEEPALIVE_INTERVAL, DEFAULT_KEEPALIVE_INTERVAL_S)
        validated.setdefault(CONF_SAFE_CURRENT, DEFAULT_SAFE_CURRENT_A)
        validated.setdefault(CONF_MIN_CURRENT, DEFAULT_MIN_CURRENT_A)
        validated.setdefault(CONF_MAX_CURRENT, DEFAULT_MAX_CURRENT_A)
        validated.setdefault(CONF_USER_LIMIT, DEFAULT_USER_LIMIT_A)
        validated.setdefault(CONF_FIXED_CURRENT, DEFAULT_FIXED_CURRENT_A)
        validated.setdefault(CONF_DLB_SENSOR_SCOPE, DlbSensorScope.LOAD_EXCLUDING_CHARGER.value)
        validated.setdefault(CONF_MAIN_FUSE, DEFAULT_MAIN_FUSE_A)
        validated.setdefault(CONF_SAFETY_MARGIN, DEFAULT_SAFETY_MARGIN_A)
        validated.setdefault(CONF_PV_INPUT_MODEL, PvInputModel.GRID_POWER_DERIVED.value)
        validated.setdefault(CONF_PV_UNTIL_UNPLUG_STRATEGY, PvOverrideStrategy.INHERIT.value)
        validated.setdefault(CONF_PV_START_THRESHOLD, 1800.0)
        validated.setdefault(CONF_PV_STOP_THRESHOLD, 1200.0)
        validated.setdefault(CONF_PV_START_DELAY, DEFAULT_PV_START_DELAY_S)
        validated.setdefault(CONF_PV_STOP_DELAY, DEFAULT_PV_STOP_DELAY_S)
        validated.setdefault(CONF_PV_MIN_RUNTIME, DEFAULT_PV_MIN_RUNTIME_S)
        validated.setdefault(CONF_PV_MIN_PAUSE, DEFAULT_PV_MIN_PAUSE_S)
        validated.setdefault(CONF_PV_MIN_CURRENT, 6.0)
        validated.setdefault(CONF_PV_PHASE_SWITCHING_MODE, DEFAULT_PV_PHASE_SWITCHING_MODE)
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
        validated[CONF_SAFE_CURRENT] = _bounded_float(MIN_CURRENT_A, MAX_CURRENT_A, CONF_SAFE_CURRENT)(validated[CONF_SAFE_CURRENT])
        validated[CONF_MIN_CURRENT] = _bounded_float(MIN_CURRENT_A, MAX_CURRENT_A, CONF_MIN_CURRENT)(validated[CONF_MIN_CURRENT])
        validated[CONF_MAX_CURRENT] = _bounded_float(MIN_CURRENT_A, MAX_CURRENT_A, CONF_MAX_CURRENT)(validated[CONF_MAX_CURRENT])
        validated[CONF_USER_LIMIT] = _bounded_float(MIN_CURRENT_A, MAX_CURRENT_A, CONF_USER_LIMIT)(validated[CONF_USER_LIMIT])
        validated[CONF_MAIN_FUSE] = _bounded_float(MIN_CURRENT_A, 200.0, CONF_MAIN_FUSE)(validated[CONF_MAIN_FUSE])
        validated[CONF_SAFETY_MARGIN] = _bounded_float(0.0, 50.0, CONF_SAFETY_MARGIN)(validated[CONF_SAFETY_MARGIN])
        validated[CONF_PV_START_THRESHOLD] = _bounded_float(MIN_POWER_W, MAX_POWER_W, CONF_PV_START_THRESHOLD)(validated[CONF_PV_START_THRESHOLD])
        validated[CONF_PV_STOP_THRESHOLD] = _bounded_float(MIN_POWER_W, MAX_POWER_W, CONF_PV_STOP_THRESHOLD)(validated[CONF_PV_STOP_THRESHOLD])
        validated[CONF_PV_START_DELAY] = _bounded_float(0.0, 3600.0, CONF_PV_START_DELAY)(validated[CONF_PV_START_DELAY])
        validated[CONF_PV_STOP_DELAY] = _bounded_float(0.0, 3600.0, CONF_PV_STOP_DELAY)(validated[CONF_PV_STOP_DELAY])
        validated[CONF_PV_MIN_RUNTIME] = _bounded_float(0.0, 3600.0, CONF_PV_MIN_RUNTIME)(validated[CONF_PV_MIN_RUNTIME])
        validated[CONF_PV_MIN_PAUSE] = _bounded_float(0.0, 3600.0, CONF_PV_MIN_PAUSE)(validated[CONF_PV_MIN_PAUSE])
        validated[CONF_PV_MIN_CURRENT] = _bounded_float(MIN_CURRENT_A, MAX_CURRENT_A, CONF_PV_MIN_CURRENT)(validated[CONF_PV_MIN_CURRENT])
        validated[CONF_FIXED_CURRENT] = _bounded_float(MIN_CURRENT_A, MAX_CURRENT_A, CONF_FIXED_CURRENT)(validated[CONF_FIXED_CURRENT])
        validated[CONF_PV_CONTROL_STRATEGY] = normalize_pv_control_strategy(validated[CONF_PV_CONTROL_STRATEGY]).value
        validated[CONF_PV_UNTIL_UNPLUG_STRATEGY] = normalize_pv_override_strategy(validated[CONF_PV_UNTIL_UNPLUG_STRATEGY]).value
        if CONF_PV_PHASE_SWITCHING_HYSTERESIS in validated:
            validated[CONF_PV_PHASE_SWITCHING_HYSTERESIS] = _bounded_float(MIN_POWER_W, MAX_PHASE_SWITCHING_HYSTERESIS_W, CONF_PV_PHASE_SWITCHING_HYSTERESIS)(validated[CONF_PV_PHASE_SWITCHING_HYSTERESIS])
        else:
            validated[CONF_PV_PHASE_SWITCHING_HYSTERESIS] = float(self.options.get(CONF_PV_PHASE_SWITCHING_HYSTERESIS, DEFAULT_PV_PHASE_SWITCHING_HYSTERESIS_W))
        if CONF_PV_PHASE_SWITCHING_MIN_INTERVAL in validated:
            validated[CONF_PV_PHASE_SWITCHING_MIN_INTERVAL] = _bounded_float(0.0, MAX_PHASE_SWITCHING_MIN_INTERVAL_S, CONF_PV_PHASE_SWITCHING_MIN_INTERVAL)(validated[CONF_PV_PHASE_SWITCHING_MIN_INTERVAL])
        else:
            validated[CONF_PV_PHASE_SWITCHING_MIN_INTERVAL] = float(self.options.get(CONF_PV_PHASE_SWITCHING_MIN_INTERVAL, DEFAULT_PV_PHASE_SWITCHING_MIN_INTERVAL_S))
        if CONF_PV_PHASE_SWITCHING_MAX_PER_SESSION in validated:
            validated[CONF_PV_PHASE_SWITCHING_MAX_PER_SESSION] = _bounded_int(1, MAX_PHASE_SWITCHING_PER_SESSION, CONF_PV_PHASE_SWITCHING_MAX_PER_SESSION)(validated[CONF_PV_PHASE_SWITCHING_MAX_PER_SESSION])
        else:
            validated[CONF_PV_PHASE_SWITCHING_MAX_PER_SESSION] = int(self.options.get(CONF_PV_PHASE_SWITCHING_MAX_PER_SESSION, DEFAULT_PV_PHASE_SWITCHING_MAX_PER_SESSION))
        validated = _validate_init_options(validated)
        validated = _validate_dlb_options(validated, self.entry_data.get(CONF_INSTALLED_PHASES, PHASE_MODE_3P))
        validated = _validate_pv_options(validated)
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
