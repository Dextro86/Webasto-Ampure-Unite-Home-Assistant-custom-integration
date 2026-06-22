from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.const import CONF_HOST, CONF_PORT

from .const import (
    CONF_DLB_ENABLED,
    CONF_DLB_GRID_POWER_SENSOR,
    CONF_DLB_INPUT_MODEL,
    CONF_DLB_L1_SENSOR,
    CONF_DLB_L2_SENSOR,
    CONF_DLB_L3_SENSOR,
    CONF_FIXED_CURRENT,
    CONF_INSTALLED_PHASES,
    CONF_MAX_CURRENT,
    CONF_MIN_CURRENT,
    CONF_SAFE_CURRENT,
    CONF_SOLAR_CONTROL_STRATEGY,
    CONF_SOLAR_EXPORT_POWER_SENSOR,
    CONF_SOLAR_GRID_POWER_SENSOR,
    CONF_SOLAR_IMPORT_POWER_SENSOR,
    CONF_SOLAR_INPUT_MODEL,
    CONF_SOLAR_MIN_CURRENT,
    CONF_SOLAR_SENSOR_FAILURE_BEHAVIOR,
    CONF_SOLAR_START_THRESHOLD,
    CONF_SOLAR_STOP_THRESHOLD,
    CONF_SOLAR_SURPLUS_SENSOR,
    CONF_SOLAR_UNTIL_UNPLUG_STRATEGY,
    CONF_STARTUP_CHARGE_MODE,
    CONF_UNIT_ID,
    CONF_USER_LIMIT,
    DEFAULT_FIXED_CURRENT_A,
    DEFAULT_MAX_CURRENT_A,
    DEFAULT_SOLAR_SENSOR_FAILURE_BEHAVIOR,
    DEFAULT_STARTUP_CHARGE_MODE,
    PHASE_MODE_1P,
    PHASE_MODE_3P,
)
from .models import (
    DlbInputModel,
    SolarControlStrategy,
    SolarInputModel,
    SolarOverrideStrategy,
    SolarSensorFailureBehavior,
    normalize_charge_mode,
    normalize_solar_control_strategy,
    normalize_solar_override_strategy,
)

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


def _migrate_legacy_user_limit(values: dict[str, Any]) -> dict[str, Any]:
    """Fold the old separate Current Limit into Maximum Current."""
    if CONF_USER_LIMIT not in values:
        return values
    migrated = dict(values)
    try:
        old_max_current = float(migrated.get(CONF_MAX_CURRENT, DEFAULT_MAX_CURRENT_A))
        old_user_limit = float(migrated[CONF_USER_LIMIT])
        if CONF_MAX_CURRENT not in values or old_max_current == DEFAULT_MAX_CURRENT_A:
            migrated[CONF_MAX_CURRENT] = old_user_limit
        else:
            migrated[CONF_MAX_CURRENT] = min(old_max_current, old_user_limit)
    except (TypeError, ValueError):
        pass
    migrated.pop(CONF_USER_LIMIT, None)
    return migrated


def _validate_init_options(options: dict[str, Any]) -> dict[str, Any]:
    min_current = int(options[CONF_MIN_CURRENT])
    max_current = int(options[CONF_MAX_CURRENT])
    safe_current = int(options[CONF_SAFE_CURRENT])

    if min_current > max_current:
        raise vol.Invalid(f"{CONF_MIN_CURRENT} must be less than or equal to {CONF_MAX_CURRENT}")
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
    if installed_phases not in (PHASE_MODE_1P, PHASE_MODE_3P):
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
    options[CONF_SOLAR_SENSOR_FAILURE_BEHAVIOR] = SolarSensorFailureBehavior(
        options.get(CONF_SOLAR_SENSOR_FAILURE_BEHAVIOR, DEFAULT_SOLAR_SENSOR_FAILURE_BEHAVIOR)
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
        SolarControlStrategy.SMART_SOLAR.value,
        SolarControlStrategy.MIN_PLUS_SURPLUS.value,
    ):
        model = options[CONF_SOLAR_INPUT_MODEL]
        if model == SolarInputModel.SURPLUS_SENSOR.value and not options.get(CONF_SOLAR_SURPLUS_SENSOR):
            raise vol.Invalid("A solar surplus sensor is required for surplus_sensor mode")
        if model == SolarInputModel.GRID_POWER_DERIVED.value and not (
            options.get(CONF_SOLAR_GRID_POWER_SENSOR) or options.get(CONF_DLB_GRID_POWER_SENSOR)
        ):
            raise vol.Invalid("A grid power sensor is required when solar mode derives surplus from grid power")
        if model == SolarInputModel.DSMR_IMPORT_EXPORT.value and not (
            options.get(CONF_SOLAR_IMPORT_POWER_SENSOR) and options.get(CONF_SOLAR_EXPORT_POWER_SENSOR)
        ):
            raise vol.Invalid("Solar import and export power sensors are required for DSMR import/export mode")
    return options


_validate_pv_options = _validate_solar_options


def _validation_error_key(err: Exception) -> str:
    message = str(err)
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
    if "Solar import and export power sensors are required" in message:
        return "solar_import_export_sensor_required"
    if CONF_FIXED_CURRENT in message:
        return "fixed_current_out_of_range"
    return "invalid_config"
