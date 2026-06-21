from __future__ import annotations

from ..const import (
    CONF_COMM_TIMEOUT,
    CONF_CONTROL_MODE,
    CONF_CONTROL_SENSOR_TIMEOUT,
    CONF_DLB_ENABLED,
    CONF_DLB_INPUT_MODEL,
    CONF_DLB_REQUIRE_UNITS,
    CONF_DLB_SENSOR_SCOPE,
    CONF_FIXED_CURRENT,
    CONF_KEEPALIVE_INTERVAL,
    CONF_MAIN_FUSE,
    CONF_MAX_CURRENT,
    CONF_MIN_CURRENT,
    CONF_POLLING_INTERVAL,
    CONF_RETRIES,
    CONF_SAFE_CURRENT,
    CONF_SAFETY_MARGIN,
    CONF_SOLAR_CONTROL_STRATEGY,
    CONF_SOLAR_GRID_POWER_DIRECTION,
    CONF_SOLAR_INPUT_MODEL,
    CONF_SOLAR_MIN_CURRENT,
    CONF_SOLAR_MIN_PAUSE,
    CONF_SOLAR_MIN_RUNTIME,
    CONF_SOLAR_REQUIRE_UNITS,
    CONF_SOLAR_SENSOR_FAILURE_BEHAVIOR,
    CONF_SOLAR_START_DELAY,
    CONF_SOLAR_START_THRESHOLD,
    CONF_SOLAR_STOP_DELAY,
    CONF_SOLAR_STOP_THRESHOLD,
    CONF_SOLAR_UNTIL_UNPLUG_STRATEGY,
    CONF_TIMEOUT,
    CONF_USER_LIMIT,
    DEFAULT_CONTROL_MODE,
    DEFAULT_CONTROL_SENSOR_TIMEOUT_S,
    DEFAULT_FIXED_CURRENT_A,
    DEFAULT_KEEPALIVE_INTERVAL_S,
    DEFAULT_MAIN_FUSE_A,
    DEFAULT_MAX_CURRENT_A,
    DEFAULT_MIN_CURRENT_A,
    DEFAULT_POLL_INTERVAL_S,
    DEFAULT_PV_MIN_PAUSE_S,
    DEFAULT_PV_MIN_RUNTIME_S,
    DEFAULT_PV_START_DELAY_S,
    DEFAULT_PV_STOP_DELAY_S,
    DEFAULT_RETRIES,
    DEFAULT_SAFE_CURRENT_A,
    DEFAULT_SAFETY_MARGIN_A,
    DEFAULT_SOLAR_GRID_POWER_DIRECTION,
    DEFAULT_SOLAR_SENSOR_FAILURE_BEHAVIOR,
    DEFAULT_TIMEOUT_S,
)
from ..models import (
    ControlConfig,
    ControlMode,
    DlbInputModel,
    DlbSensorScope,
    SolarInputModel,
    SolarOverrideStrategy,
    SolarSensorFailureBehavior,
    normalize_solar_control_strategy,
    normalize_solar_override_strategy,
)


def normalize_dlb_input_model(raw_value: str) -> DlbInputModel:
    if raw_value == "grid_power":
        return DlbInputModel.DISABLED
    return DlbInputModel(raw_value)


def resolve_dlb_input_model_from_options(merged: dict) -> DlbInputModel:
    if CONF_DLB_ENABLED in merged:
        return DlbInputModel.PHASE_CURRENTS if bool(merged.get(CONF_DLB_ENABLED)) else DlbInputModel.DISABLED
    return normalize_dlb_input_model(merged.get(CONF_DLB_INPUT_MODEL, DlbInputModel.DISABLED.value))


def resolve_configured_max_current(options: dict) -> float:
    max_current = float(options.get(CONF_MAX_CURRENT, DEFAULT_MAX_CURRENT_A))
    if CONF_USER_LIMIT not in options:
        return max_current
    try:
        return min(max_current, float(options[CONF_USER_LIMIT]))
    except (TypeError, ValueError):
        return max_current


def build_control_config(merged: dict) -> ControlConfig:
    return ControlConfig(
        polling_interval_s=float(merged.get(CONF_POLLING_INTERVAL, DEFAULT_POLL_INTERVAL_S)),
        timeout_s=float(merged.get(CONF_TIMEOUT, DEFAULT_TIMEOUT_S)),
        retries=int(merged.get(CONF_RETRIES, DEFAULT_RETRIES)),
        control_mode=ControlMode(merged.get(CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE)),
        keepalive_interval_s=float(merged.get(CONF_KEEPALIVE_INTERVAL, DEFAULT_KEEPALIVE_INTERVAL_S)),
        control_sensor_timeout_s=float(merged.get(CONF_CONTROL_SENSOR_TIMEOUT, DEFAULT_CONTROL_SENSOR_TIMEOUT_S)),
        safe_current_a=float(merged.get(CONF_SAFE_CURRENT, DEFAULT_SAFE_CURRENT_A)),
        min_current_a=float(merged.get(CONF_MIN_CURRENT, DEFAULT_MIN_CURRENT_A)),
        max_current_a=resolve_configured_max_current(merged),
        main_fuse_a=float(merged.get(CONF_MAIN_FUSE, DEFAULT_MAIN_FUSE_A)),
        safety_margin_a=float(merged.get(CONF_SAFETY_MARGIN, DEFAULT_SAFETY_MARGIN_A)),
        dlb_input_model=resolve_dlb_input_model_from_options(merged),
        dlb_sensor_scope=DlbSensorScope(
            merged.get(CONF_DLB_SENSOR_SCOPE, DlbSensorScope.LOAD_EXCLUDING_CHARGER.value)
        ),
        dlb_require_units=bool(merged.get(CONF_DLB_REQUIRE_UNITS, False)),
        solar_input_model=SolarInputModel(merged.get(CONF_SOLAR_INPUT_MODEL, SolarInputModel.GRID_POWER_DERIVED.value)),
        solar_grid_power_direction=merged.get(
            CONF_SOLAR_GRID_POWER_DIRECTION,
            DEFAULT_SOLAR_GRID_POWER_DIRECTION,
        ),
        solar_control_strategy=normalize_solar_control_strategy(
            merged.get(CONF_SOLAR_CONTROL_STRATEGY, "disabled")
        ),
        solar_until_unplug_strategy=normalize_solar_override_strategy(
            merged.get(CONF_SOLAR_UNTIL_UNPLUG_STRATEGY, SolarOverrideStrategy.INHERIT.value)
        ),
        solar_sensor_failure_behavior=SolarSensorFailureBehavior(
            merged.get(CONF_SOLAR_SENSOR_FAILURE_BEHAVIOR, DEFAULT_SOLAR_SENSOR_FAILURE_BEHAVIOR)
        ),
        solar_require_units=bool(merged.get(CONF_SOLAR_REQUIRE_UNITS, False)),
        solar_start_threshold_w=float(merged.get(CONF_SOLAR_START_THRESHOLD, 1800.0)),
        solar_stop_threshold_w=float(merged.get(CONF_SOLAR_STOP_THRESHOLD, 1200.0)),
        solar_start_delay_s=float(merged.get(CONF_SOLAR_START_DELAY, DEFAULT_PV_START_DELAY_S)),
        solar_stop_delay_s=float(merged.get(CONF_SOLAR_STOP_DELAY, DEFAULT_PV_STOP_DELAY_S)),
        solar_min_runtime_s=float(merged.get(CONF_SOLAR_MIN_RUNTIME, DEFAULT_PV_MIN_RUNTIME_S)),
        solar_min_pause_s=float(merged.get(CONF_SOLAR_MIN_PAUSE, DEFAULT_PV_MIN_PAUSE_S)),
        solar_min_current_a=float(merged.get(CONF_SOLAR_MIN_CURRENT, 6.0)),
        fixed_current_a=float(merged.get(CONF_FIXED_CURRENT, DEFAULT_FIXED_CURRENT_A)),
        communication_timeout_s=float(merged.get(CONF_COMM_TIMEOUT, 30.0)),
    )
