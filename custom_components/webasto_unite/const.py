
from __future__ import annotations

DOMAIN = "webasto_unite"
DEFAULT_NAME = "Webasto Unite"
DEFAULT_PORT = 502
DEFAULT_UNIT_ID = 255
DEFAULT_POLL_INTERVAL_S = 2.0
DEFAULT_TIMEOUT_S = 3.0
DEFAULT_RETRIES = 3
DEFAULT_KEEPALIVE_INTERVAL_S = 10.0
DEFAULT_CONTROL_SENSOR_TIMEOUT_S = 60.0
DEFAULT_CONTROL_MODE = "keepalive_only"
DEFAULT_STARTUP_CHARGE_MODE = "normal"
DEFAULT_SAFE_CURRENT_A = 6.0
DEFAULT_MIN_CURRENT_A = 6.0
DEFAULT_MAX_CURRENT_A = 16.0
DEFAULT_FIXED_CURRENT_A = 6.0
DEFAULT_SOLAR_START_DELAY_S = 0.0
DEFAULT_SOLAR_STOP_DELAY_S = 0.0
DEFAULT_SOLAR_MIN_RUNTIME_S = 0.0
DEFAULT_SOLAR_MIN_PAUSE_S = 0.0
DEFAULT_SOLAR_GRID_POWER_DIRECTION = "negative_export"
DEFAULT_PV_START_DELAY_S = DEFAULT_SOLAR_START_DELAY_S
DEFAULT_PV_STOP_DELAY_S = DEFAULT_SOLAR_STOP_DELAY_S
DEFAULT_PV_MIN_RUNTIME_S = DEFAULT_SOLAR_MIN_RUNTIME_S
DEFAULT_PV_MIN_PAUSE_S = DEFAULT_SOLAR_MIN_PAUSE_S
DEFAULT_MAIN_FUSE_A = 25.0
DEFAULT_SAFETY_MARGIN_A = 2.0

PLATFORMS = ["sensor", "number", "select", "switch", "binary_sensor", "button"]

CONF_UNIT_ID = "unit_id"
CONF_INSTALLED_PHASES = "installed_phases"
PHASE_MODE_1P = "1p"
PHASE_MODE_3P = "3p"
CONF_POLLING_INTERVAL = "polling_interval"
CONF_TIMEOUT = "timeout"
CONF_RETRIES = "retries"
CONF_CONTROL_MODE = "control_mode"
CONF_STARTUP_CHARGE_MODE = "startup_charge_mode"
CONF_KEEPALIVE_INTERVAL = "keepalive_interval"
CONF_CONTROL_SENSOR_TIMEOUT = "control_sensor_timeout"
CONF_SAFE_CURRENT = "safe_current"
CONF_MIN_CURRENT = "min_current"
CONF_MAX_CURRENT = "max_current"
# Legacy option key, migrated to CONF_MAX_CURRENT.
CONF_USER_LIMIT = "user_limit"
CONF_MAIN_FUSE = "main_fuse"
CONF_SAFETY_MARGIN = "safety_margin"
CONF_DLB_ENABLED = "dlb_enabled"
CONF_DLB_INPUT_MODEL = "dlb_input_model"
CONF_DLB_SENSOR_SCOPE = "dlb_sensor_scope"
CONF_DLB_REQUIRE_UNITS = "dlb_require_units"
CONF_DLB_L1_SENSOR = "dlb_l1_sensor"
CONF_DLB_L2_SENSOR = "dlb_l2_sensor"
CONF_DLB_L3_SENSOR = "dlb_l3_sensor"
CONF_SOLAR_GRID_POWER_SENSOR = "solar_grid_power_sensor"
CONF_SOLAR_GRID_POWER_DIRECTION = "solar_grid_power_direction"
# Backward compatibility for old option key.
CONF_DLB_GRID_POWER_SENSOR = "dlb_grid_power_sensor"
CONF_SOLAR_INPUT_MODEL = "solar_input_model"
CONF_SOLAR_CONTROL_STRATEGY = "solar_control_strategy"
CONF_SOLAR_UNTIL_UNPLUG_STRATEGY = "solar_until_unplug_strategy"
CONF_SOLAR_SURPLUS_SENSOR = "solar_surplus_sensor"
CONF_SOLAR_REQUIRE_UNITS = "solar_require_units"
CONF_SOLAR_START_THRESHOLD = "solar_start_threshold"
CONF_SOLAR_STOP_THRESHOLD = "solar_stop_threshold"
CONF_SOLAR_START_DELAY = "solar_start_delay"
CONF_SOLAR_STOP_DELAY = "solar_stop_delay"
CONF_SOLAR_MIN_RUNTIME = "solar_min_runtime"
CONF_SOLAR_MIN_PAUSE = "solar_min_pause"
CONF_SOLAR_MIN_CURRENT = "solar_min_current"
CONF_FIXED_CURRENT = "fixed_current"
CONF_COMM_TIMEOUT = "communication_timeout"
CONF_CHARGE_MODE = "charge_mode"
STORAGE_KEY_CHARGING_STATE = "charging_state"

SERVICE_SET_MODE = "set_mode"
SERVICE_SET_MAX_CURRENT = "set_max_current"
SERVICE_SET_USER_LIMIT = "set_user_limit"
SERVICE_TRIGGER_RECONNECT = "trigger_reconnect"
SERVICE_ENABLE_SOLAR_UNTIL_UNPLUG = "enable_solar_until_unplug"
SERVICE_DISABLE_SOLAR_UNTIL_UNPLUG = "disable_solar_until_unplug"
SERVICE_ENABLE_PV_UNTIL_UNPLUG = "enable_pv_until_unplug"
SERVICE_DISABLE_PV_UNTIL_UNPLUG = "disable_pv_until_unplug"
SERVICE_ENABLE_FIXED_CURRENT_UNTIL_UNPLUG = "enable_fixed_current_until_unplug"
SERVICE_DISABLE_FIXED_CURRENT_UNTIL_UNPLUG = "disable_fixed_current_until_unplug"

RUNTIME_CLIENT = "client"
RUNTIME_CONTROLLER = "controller"
RUNTIME_COORDINATOR = "coordinator"
