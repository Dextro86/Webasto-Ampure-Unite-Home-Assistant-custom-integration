from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from time import monotonic

from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_COMM_TIMEOUT,
    CONF_CONTROL_MODE,
    CONF_CONTROL_SENSOR_TIMEOUT,
    CONF_DLB_ENABLED,
    CONF_DLB_GRID_POWER_SENSOR,
    CONF_DLB_INPUT_MODEL,
    CONF_DLB_L1_SENSOR,
    CONF_DLB_L2_SENSOR,
    CONF_DLB_L3_SENSOR,
    CONF_DLB_REQUIRE_UNITS,
    CONF_DLB_SENSOR_SCOPE,
    CONF_FIXED_CURRENT,
    CONF_INSTALLED_PHASES,
    CONF_KEEPALIVE_INTERVAL,
    CONF_MAIN_FUSE,
    CONF_MAX_CURRENT,
    CONF_MIN_CURRENT,
    CONF_POLLING_INTERVAL,
    CONF_SOLAR_CONTROL_STRATEGY,
    CONF_SOLAR_GRID_POWER_DIRECTION,
    CONF_SOLAR_INPUT_MODEL,
    CONF_SOLAR_MIN_CURRENT,
    CONF_SOLAR_MIN_PAUSE,
    CONF_SOLAR_MIN_RUNTIME,
    CONF_SOLAR_REQUIRE_UNITS,
    CONF_SOLAR_START_DELAY,
    CONF_SOLAR_START_THRESHOLD,
    CONF_SOLAR_STOP_DELAY,
    CONF_SOLAR_STOP_THRESHOLD,
    CONF_SOLAR_GRID_POWER_SENSOR,
    CONF_SOLAR_SURPLUS_SENSOR,
    CONF_SOLAR_UNTIL_UNPLUG_STRATEGY,
    CONF_RETRIES,
    CONF_SAFE_CURRENT,
    CONF_SAFETY_MARGIN,
    CONF_STARTUP_CHARGE_MODE,
    CONF_TIMEOUT,
    CONF_UNIT_ID,
    CONF_USER_LIMIT,
    DEFAULT_CONTROL_MODE,
    DEFAULT_CONTROL_SENSOR_TIMEOUT_S,
    DEFAULT_FIXED_CURRENT_A,
    DEFAULT_KEEPALIVE_INTERVAL_S,
    DEFAULT_MAIN_FUSE_A,
    DEFAULT_MAX_CURRENT_A,
    DEFAULT_MIN_CURRENT_A,
    DEFAULT_NAME,
    DEFAULT_POLL_INTERVAL_S,
    DEFAULT_PORT,
    DEFAULT_PV_MIN_PAUSE_S,
    DEFAULT_PV_MIN_RUNTIME_S,
    DEFAULT_PV_START_DELAY_S,
    DEFAULT_PV_STOP_DELAY_S,
    DEFAULT_RETRIES,
    DEFAULT_SAFE_CURRENT_A,
    DEFAULT_SAFETY_MARGIN_A,
    DEFAULT_SOLAR_GRID_POWER_DIRECTION,
    DEFAULT_STARTUP_CHARGE_MODE,
    DEFAULT_TIMEOUT_S,
    DEFAULT_UNIT_ID,
    DOMAIN,
    STORAGE_KEY_CHARGING_STATE,
)
from .controller import WallboxController
from .modbus_client import ModbusClientConfig, WebastoModbusClient
from .models import (
    CapabilityState,
    ChargeMode,
    ControlConfig,
    ControlMode,
    ControlReason,
    DlbInputModel,
    DlbSensorScope,
    HaSensorSnapshot,
    PhaseCurrents,
    SolarControlStrategy,
    SolarInputModel,
    SolarOverrideStrategy,
    RuntimeSnapshot,
    WallboxState,
    normalize_charge_mode,
    normalize_solar_control_strategy,
    normalize_solar_override_strategy,
)
from .registers import COMM_TIMEOUT_S, LIFE_BIT, SAFE_CURRENT_A, SET_CHARGE_CURRENT_A
from .sensor_adapter import HaSensorAdapter
from .wallbox_reader import WallboxReader
from .write_queue import QueuedWrite, WritePriority, WriteQueueManager

_LOGGER = logging.getLogger(__name__)

STARTUP_STABILIZATION_MIN_POLLS = 3
STARTUP_STABILIZATION_MIN_SECONDS = 30.0
DLB_START_GUARD_SECONDS = 4.0
DLB_START_GUARD_CONFIRM_SAMPLES = 2
SENSOR_REFRESH_DEBOUNCE_S = 0.4
SOLAR_START_GUARD_SECONDS = 8.0
SOLAR_START_GUARD_CONFIRM_SAMPLES = 2


def _normalize_dlb_input_model(raw_value: str) -> DlbInputModel:
    if raw_value == "grid_power":
        return DlbInputModel.DISABLED
    return DlbInputModel(raw_value)


def _resolve_dlb_input_model_from_options(merged: dict) -> DlbInputModel:
    if CONF_DLB_ENABLED in merged:
        return DlbInputModel.PHASE_CURRENTS if bool(merged.get(CONF_DLB_ENABLED)) else DlbInputModel.DISABLED
    return _normalize_dlb_input_model(merged.get(CONF_DLB_INPUT_MODEL, DlbInputModel.DISABLED.value))


class WebastoUniteCoordinator(DataUpdateCoordinator[RuntimeSnapshot]):
    def __init__(self, hass, entry) -> None:
        self.hass = hass
        self.entry = entry
        self._mode = ChargeMode.NORMAL
        self._charging_paused = False
        self._solar_until_unplug_active = False
        self._fixed_current_until_unplug_active = False
        self._last_vehicle_connected = False
        self._last_charging_active = False
        self._dlb_start_guard_until_monotonic = 0.0
        self._dlb_start_guard_downscale_samples = 0
        self._solar_start_guard_until_monotonic = 0.0
        self._solar_start_guard_pause_samples = 0
        self._last_solar_charging_active = False
        self._startup_started_monotonic = monotonic()
        self._startup_refresh_count = 0
        self._sensor_unsubscribers = []
        self._last_keepalive_sent_monotonic = 0.0
        self._keepalive_started_monotonic = monotonic()
        self._keepalive_sent_count = 0
        self._keepalive_write_failures = 0
        self._keepalive_task: asyncio.Task | None = None
        self._sensor_refresh_task: asyncio.Task | None = None
        self._flush_lock = asyncio.Lock()
        entry_id = getattr(entry, "entry_id", "default")
        self._charging_state_store = Store(hass, 1, f"{DOMAIN}.{entry_id}.{STORAGE_KEY_CHARGING_STATE}")

        merged = {**entry.data, **entry.options}
        self.control_config = ControlConfig(
            polling_interval_s=float(merged.get(CONF_POLLING_INTERVAL, DEFAULT_POLL_INTERVAL_S)),
            timeout_s=float(merged.get(CONF_TIMEOUT, DEFAULT_TIMEOUT_S)),
            retries=int(merged.get(CONF_RETRIES, DEFAULT_RETRIES)),
            control_mode=ControlMode(merged.get(CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE)),
            keepalive_interval_s=float(merged.get(CONF_KEEPALIVE_INTERVAL, DEFAULT_KEEPALIVE_INTERVAL_S)),
            control_sensor_timeout_s=float(
                merged.get(CONF_CONTROL_SENSOR_TIMEOUT, DEFAULT_CONTROL_SENSOR_TIMEOUT_S)
            ),
            safe_current_a=float(merged.get(CONF_SAFE_CURRENT, DEFAULT_SAFE_CURRENT_A)),
            min_current_a=float(merged.get(CONF_MIN_CURRENT, DEFAULT_MIN_CURRENT_A)),
            max_current_a=self._resolve_configured_max_current(merged),
            main_fuse_a=float(merged.get(CONF_MAIN_FUSE, DEFAULT_MAIN_FUSE_A)),
            safety_margin_a=float(merged.get(CONF_SAFETY_MARGIN, DEFAULT_SAFETY_MARGIN_A)),
            dlb_input_model=_resolve_dlb_input_model_from_options(merged),
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
                merged.get(CONF_SOLAR_CONTROL_STRATEGY, SolarControlStrategy.DISABLED.value)
            ),
            solar_until_unplug_strategy=normalize_solar_override_strategy(
                merged.get(CONF_SOLAR_UNTIL_UNPLUG_STRATEGY, SolarOverrideStrategy.INHERIT.value)
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
        self._mode = self._resolve_startup_mode(merged)
        self.controller = WallboxController(self.control_config)
        self.client = WebastoModbusClient(
            ModbusClientConfig(
                host=merged[CONF_HOST],
                port=int(merged.get(CONF_PORT, DEFAULT_PORT)),
                unit_id=int(merged.get(CONF_UNIT_ID, DEFAULT_UNIT_ID)),
                timeout_s=self.control_config.timeout_s,
                retries=self.control_config.retries,
            )
        )
        self.write_queue = WriteQueueManager()
        self.sensor_adapter = HaSensorAdapter(hass)
        self.wallbox_reader = WallboxReader(self.client)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=self.control_config.polling_interval_s),
        )

    @staticmethod
    def _resolve_configured_max_current(options: dict) -> float:
        max_current = float(options.get(CONF_MAX_CURRENT, DEFAULT_MAX_CURRENT_A))
        if CONF_USER_LIMIT not in options:
            return max_current
        try:
            return min(max_current, float(options[CONF_USER_LIMIT]))
        except (TypeError, ValueError):
            return max_current

    def _ensure_runtime_defaults(self) -> None:
        # Defensive fallback for partially constructed coordinator instances used
        # in tests or edge-case reload paths. Normal runtime should already have
        # these fields initialized in __init__.
        if not hasattr(self, "_mode"):
            self._mode = ChargeMode.NORMAL
        if not hasattr(self, "_charging_paused"):
            self._charging_paused = False
        if not hasattr(self, "_solar_until_unplug_active"):
            self._solar_until_unplug_active = False
        if not hasattr(self, "_fixed_current_until_unplug_active"):
            self._fixed_current_until_unplug_active = False
        if not hasattr(self, "_startup_started_monotonic"):
            self._startup_started_monotonic = monotonic()
        if not hasattr(self, "_startup_refresh_count"):
            self._startup_refresh_count = 0
        if not hasattr(self, "_last_vehicle_connected"):
            self._last_vehicle_connected = False
        if not hasattr(self, "_last_charging_active"):
            self._last_charging_active = False
        if not hasattr(self, "_dlb_start_guard_until_monotonic"):
            self._dlb_start_guard_until_monotonic = 0.0
        if not hasattr(self, "_dlb_start_guard_downscale_samples"):
            self._dlb_start_guard_downscale_samples = 0
        if not hasattr(self, "_solar_start_guard_until_monotonic"):
            self._solar_start_guard_until_monotonic = 0.0
        if not hasattr(self, "_solar_start_guard_pause_samples"):
            self._solar_start_guard_pause_samples = 0
        if not hasattr(self, "_last_solar_charging_active"):
            self._last_solar_charging_active = False
        if not hasattr(self, "_last_keepalive_sent_monotonic"):
            self._last_keepalive_sent_monotonic = 0.0
        if not hasattr(self, "_keepalive_started_monotonic"):
            self._keepalive_started_monotonic = monotonic()
        if not hasattr(self, "_keepalive_sent_count"):
            self._keepalive_sent_count = 0
        if not hasattr(self, "_keepalive_write_failures"):
            self._keepalive_write_failures = 0
        if not hasattr(self, "_sensor_refresh_task"):
            self._sensor_refresh_task = None
        if not hasattr(self, "_flush_lock"):
            self._flush_lock = asyncio.Lock()

    def _resolve_startup_mode(self, merged_options: dict) -> ChargeMode:
        try:
            mode = normalize_charge_mode(
                merged_options.get(CONF_STARTUP_CHARGE_MODE, DEFAULT_STARTUP_CHARGE_MODE),
                self.control_config.solar_control_strategy,
            )
        except ValueError:
            return ChargeMode.NORMAL
        if mode == ChargeMode.SOLAR and self.control_config.solar_control_strategy == SolarControlStrategy.DISABLED:
            return ChargeMode.NORMAL
        return mode

    async def async_setup(self) -> None:
        await self._async_restore_charging_enabled_state()
        self._setup_sensor_listeners()
        await self.client.connect()
        await self._sync_static_registers()
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    async def async_shutdown(self) -> None:
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except asyncio.CancelledError:
                pass
            self._keepalive_task = None
        if self._sensor_refresh_task is not None:
            self._sensor_refresh_task.cancel()
            try:
                await self._sensor_refresh_task
            except asyncio.CancelledError:
                pass
            self._sensor_refresh_task = None
        for unsub in self._sensor_unsubscribers:
            unsub()
        self._sensor_unsubscribers.clear()
        await self.client.close()

    @property
    def mode(self) -> ChargeMode:
        self._ensure_runtime_defaults()
        return self._mode

    @property
    def effective_mode(self) -> ChargeMode:
        self._ensure_runtime_defaults()
        if self._mode == ChargeMode.OFF or self._charging_paused:
            return ChargeMode.OFF
        if self._fixed_current_until_unplug_active:
            return ChargeMode.FIXED_CURRENT
        if self._solar_until_unplug_active:
            return ChargeMode.SOLAR
        return self._mode

    @property
    def solar_until_unplug_active(self) -> bool:
        self._ensure_runtime_defaults()
        return self._solar_until_unplug_active

    @property
    def fixed_current_until_unplug_active(self) -> bool:
        self._ensure_runtime_defaults()
        return self._fixed_current_until_unplug_active

    @property
    def charging_paused(self) -> bool:
        self._ensure_runtime_defaults()
        return self._charging_paused

    @property
    def charging_enabled(self) -> bool:
        return not self.charging_paused

    def set_mode(self, mode: ChargeMode) -> None:
        self._ensure_runtime_defaults()
        self._mode = mode
        self._solar_until_unplug_active = False
        self._fixed_current_until_unplug_active = False
        if mode != ChargeMode.SOLAR:
            self._reset_solar_runtime_state()

    def pause_charging(self) -> None:
        self._ensure_runtime_defaults()
        self._charging_paused = True
        self._reset_solar_runtime_state()

    def resume_charging(self) -> None:
        self._ensure_runtime_defaults()
        self._charging_paused = False

    async def async_set_charging_enabled(self, enabled: bool) -> None:
        if enabled:
            self.resume_charging()
        else:
            self.pause_charging()
        await self._charging_state_store.async_save({"charging_enabled": enabled})

    async def _async_restore_charging_enabled_state(self) -> None:
        stored = await self._charging_state_store.async_load()
        charging_enabled = True
        if isinstance(stored, dict):
            charging_enabled = bool(stored.get("charging_enabled", True))
        self._charging_paused = not charging_enabled

    def set_solar_until_unplug(self, enabled: bool) -> None:
        self._ensure_runtime_defaults()
        self._solar_until_unplug_active = enabled
        if enabled:
            self._fixed_current_until_unplug_active = False
        self._reset_solar_runtime_state()

    def set_fixed_current_until_unplug(self, enabled: bool) -> None:
        self._ensure_runtime_defaults()
        self._fixed_current_until_unplug_active = enabled
        if enabled:
            self._solar_until_unplug_active = False
        self._reset_solar_runtime_state()

    def set_max_current(self, current_a: float) -> None:
        self.control_config.max_current_a = self._validate_max_current(current_a)

    def set_user_limit(self, current_a: float) -> None:
        # Backward-compatible service alias.
        self.set_max_current(current_a)

    def set_fixed_current(self, current_a: float) -> None:
        self.control_config.fixed_current_a = self._validate_runtime_current(current_a, "Fixed Current")

    def _validate_runtime_current(self, current_a: float, label: str) -> float:
        current = float(current_a)
        rounded = round(current)
        if abs(current - rounded) > 1e-6:
            raise ValueError(f"{label} must be a whole amp value")
        if not self.control_config.min_current_a <= current <= self.control_config.max_current_a:
            raise ValueError(
                f"{label} must be between {self.control_config.min_current_a:g} A "
                f"and {self.control_config.max_current_a:g} A"
            )
        return float(rounded)

    def _validate_max_current(self, current_a: float) -> float:
        current = float(current_a)
        rounded = round(current)
        if abs(current - rounded) > 1e-6:
            raise ValueError("Maximum Current must be a whole amp value")
        if not self.control_config.min_current_a <= current <= 32.0:
            raise ValueError(
                f"Maximum Current must be between {self.control_config.min_current_a:g} A and 32 A"
            )
        return float(rounded)

    async def _debounced_sensor_refresh(self) -> None:
        await asyncio.sleep(SENSOR_REFRESH_DEBOUNCE_S)
        await self.async_request_refresh()

    def _schedule_sensor_refresh(self) -> None:
        if self._sensor_refresh_task is not None and not self._sensor_refresh_task.done():
            self._sensor_refresh_task.cancel()
        self._sensor_refresh_task = self.hass.async_create_task(self._debounced_sensor_refresh())

    async def async_trigger_reconnect(self) -> None:
        await self.client.reconnect()
        await self._sync_static_registers()
        await self.async_request_refresh()

    async def _async_update_data(self) -> RuntimeSnapshot:
        self._ensure_runtime_defaults()
        try:
            wallbox = await self.wallbox_reader.read_wallbox_state(self._configured_installed_phases())
            self._startup_refresh_count += 1

            # Runtime-only session overrides end when the vehicle is unplugged.
            if (
                (self._solar_until_unplug_active or self._fixed_current_until_unplug_active)
                and self._last_vehicle_connected
                and not wallbox.vehicle_connected
            ):
                self._solar_until_unplug_active = False
                self._fixed_current_until_unplug_active = False
                self._reset_solar_runtime_state()
                self.controller.reset_session_phase_observation()

            if not self._last_vehicle_connected and wallbox.vehicle_connected:
                self.controller.reset_current_write_state()

            self._last_vehicle_connected = wallbox.vehicle_connected

            # Read Home Assistant sensor inputs only after wallbox/session state
            # has been updated for this poll, so the controller sees one
            # consistent view of the current cycle.
            sensors = self._read_sensor_snapshot(wallbox)
            solar_surplus_w = self.controller.resolve_surplus_power(sensors, wallbox)
            solar_strategy = self.controller.resolve_effective_solar_strategy(
                self.control_config.solar_control_strategy,
                self.control_config.solar_until_unplug_strategy,
                self._solar_until_unplug_active,
            )
            decision = self.controller.evaluate(self.effective_mode, wallbox, sensors, solar_strategy)

            # Apply transient/startup guards after the controller decision is
            # built, but before anything is enqueued for writing.
            if self._should_defer_startup_safe_current_fallback_write(
                wallbox=wallbox,
                sensors=sensors,
                decision=decision,
            ):
                decision.should_write = False
            self._apply_dlb_start_transient_guard(wallbox=wallbox, decision=decision)
            self._apply_solar_start_transient_guard(
                wallbox=wallbox,
                decision=decision,
                sensors=sensors,
            )
            await self._enqueue_decision(decision)
            await self._flush_write_queue()
            keepalive_age_s = self._keepalive_age_seconds()
            return RuntimeSnapshot(
                wallbox=wallbox,
                mode=self._mode,
                effective_mode=self.effective_mode,
                operating_state=self._build_operating_state(decision),
                control_mode=self.control_config.control_mode,
                control_reason=decision.reason.value,
                charging_paused=self._charging_paused,
                solar_until_unplug_active=self._solar_until_unplug_active,
                fixed_current_until_unplug_active=self._fixed_current_until_unplug_active,
                keepalive_age_s=keepalive_age_s,
                keepalive_interval_s=self.control_config.keepalive_interval_s,
                keepalive_overdue=self._is_keepalive_overdue(keepalive_age_s),
                keepalive_sent_count=self._keepalive_sent_count,
                keepalive_write_failures=self._keepalive_write_failures,
                sensor_snapshot_valid=sensors.valid,
                sensor_invalid_reason=sensors.reason_invalid,
                queue_depth=await self.write_queue.size(),
                pending_write_kind=await self.write_queue.peek_next_kind(),
                dlb_limit_a=decision.dlb_limit_a,
                final_target_a=decision.final_target_a,
                mode_target_a=decision.mode_target_a,
                solar_surplus_w=solar_surplus_w,
                solar_input_state=sensors.solar_input_state,
                dominant_limit_reason=decision.dominant_limit_reason.value if decision.dominant_limit_reason is not None else None,
                fallback_active=decision.fallback_active,
                last_client_error=self.client.stats.last_error,
                entry_title=self.entry.title or DEFAULT_NAME,
                capability_summary=self._build_capability_summary(wallbox),
                capabilities=self._build_capabilities(wallbox),
            )
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(str(err)) from err

    def _should_defer_startup_safe_current_fallback_write(self, *, wallbox, sensors, decision) -> bool:
        if self.control_config.dlb_input_model == DlbInputModel.DISABLED:
            return False
        if self._startup_stabilization_ready():
            return False
        if not wallbox.charging_active:
            return False
        if sensors.valid:
            return False
        if not decision.fallback_active or decision.reason != ControlReason.SAFE_CURRENT_FALLBACK:
            return False
        if decision.target_current_a is None:
            return False
        if abs(decision.target_current_a - self.control_config.safe_current_a) > 0.01:
            return False
        if wallbox.current_limit_a is None:
            return False
        return wallbox.current_limit_a > (self.control_config.safe_current_a + 0.01)

    def _apply_dlb_start_transient_guard(self, *, wallbox, decision, now_monotonic: float | None = None) -> None:
        self._ensure_runtime_defaults()
        now = now_monotonic if now_monotonic is not None else monotonic()

        if self.control_config.dlb_sensor_scope != DlbSensorScope.TOTAL_INCLUDING_CHARGER:
            self._dlb_start_guard_until_monotonic = 0.0
            self._dlb_start_guard_downscale_samples = 0
            self._last_charging_active = wallbox.charging_active
            return

        if wallbox.charging_active and not self._last_charging_active:
            self._dlb_start_guard_until_monotonic = now + DLB_START_GUARD_SECONDS
            self._dlb_start_guard_downscale_samples = 0

        within_guard = wallbox.charging_active and now <= self._dlb_start_guard_until_monotonic
        if not within_guard:
            self._dlb_start_guard_downscale_samples = 0
            self._last_charging_active = wallbox.charging_active
            return

        downscale_requested = (
            decision.should_write
            and decision.target_current_a is not None
            and wallbox.current_limit_a is not None
            and decision.target_current_a + 0.01 < wallbox.current_limit_a
            and decision.dominant_limit_reason == ControlReason.DLB_LIMITED
        )
        if not downscale_requested:
            self._dlb_start_guard_downscale_samples = 0
            self._last_charging_active = wallbox.charging_active
            return

        # Safety first: do not delay hard reductions down to minimum current.
        if decision.target_current_a <= (self.control_config.min_current_a + 0.01):
            self._last_charging_active = wallbox.charging_active
            return

        self._dlb_start_guard_downscale_samples += 1
        if self._dlb_start_guard_downscale_samples < DLB_START_GUARD_CONFIRM_SAMPLES:
            decision.should_write = False

        self._last_charging_active = wallbox.charging_active

    def _apply_solar_start_transient_guard(self, *, wallbox, decision, sensors, now_monotonic: float | None = None) -> None:
        self._ensure_runtime_defaults()
        now = now_monotonic if now_monotonic is not None else monotonic()

        if self.effective_mode != ChargeMode.SOLAR:
            self._solar_start_guard_until_monotonic = 0.0
            self._solar_start_guard_pause_samples = 0
            self._last_solar_charging_active = wallbox.charging_active
            return

        if wallbox.charging_active and not self._last_solar_charging_active:
            self._solar_start_guard_until_monotonic = now + SOLAR_START_GUARD_SECONDS
            self._solar_start_guard_pause_samples = 0

        within_start_guard = wallbox.charging_active and now <= self._solar_start_guard_until_monotonic
        within_startup_guard = wallbox.charging_active and not self._startup_stabilization_ready()
        within_guard = within_start_guard or within_startup_guard
        if not within_guard:
            self._solar_start_guard_pause_samples = 0
            self._last_solar_charging_active = wallbox.charging_active
            return

        solar_pause_requested = (
            decision.should_write
            and not decision.charging_enabled
            and decision.target_current_a is not None
            and decision.target_current_a <= 0.01
            and decision.reason in {ControlReason.BELOW_MIN_CURRENT, ControlReason.SENSOR_UNAVAILABLE}
            and decision.dominant_limit_reason is None
        )
        if not solar_pause_requested:
            self._solar_start_guard_pause_samples = 0
            self._last_solar_charging_active = wallbox.charging_active
            return

        # If input is explicitly unavailable, require a short confirmation window
        # before writing 0A during startup/charge-start transients.
        if sensors.solar_input_state != "ready":
            self._solar_start_guard_pause_samples += 1
            if self._solar_start_guard_pause_samples < SOLAR_START_GUARD_CONFIRM_SAMPLES:
                decision.should_write = False

        self._last_solar_charging_active = wallbox.charging_active

    def _build_capabilities(self, wallbox: WallboxState) -> dict[str, str]:
        ev_max_state = CapabilityState.CONFIRMED if wallbox.ev_max_current_a is not None else CapabilityState.OPTIONAL_ABSENT
        return {
            "core_measurements": CapabilityState.CONFIRMED.value,
            "phase_count_404": CapabilityState.CONFIRMED.value,
            "failsafe_2000_2002": CapabilityState.CONFIRMED.value,
            "current_control_5004": CapabilityState.CONFIRMED.value,
            "keepalive_6000": CapabilityState.CONFIRMED.value,
            "ev_max_current_1108": ev_max_state.value,
        }

    def _build_capability_summary(self, wallbox: WallboxState) -> str:
        capabilities = self._build_capabilities(wallbox)
        if CapabilityState.UNCONFIRMED.value in capabilities.values():
            return "partially_validated"
        if CapabilityState.OPTIONAL_ABSENT.value in capabilities.values():
            return "validated_with_optional_gaps"
        return "validated"

    def _build_operating_state(self, decision) -> str:
        effective_mode = self.effective_mode
        if effective_mode == ChargeMode.OFF and self._charging_paused:
            return "paused"
        if effective_mode == ChargeMode.OFF:
            return "off"
        if decision.fallback_active:
            return "fallback"
        if effective_mode == ChargeMode.FIXED_CURRENT and self._fixed_current_until_unplug_active:
            return "fixed_current_until_unplug"
        if effective_mode == ChargeMode.FIXED_CURRENT:
            return "fixed_current"
        if (
            effective_mode == ChargeMode.SOLAR
            and self._solar_until_unplug_active
            and decision.reason == ControlReason.BELOW_MIN_CURRENT
        ):
            return "waiting_for_solar"
        if (
            effective_mode == ChargeMode.SOLAR
            and self._solar_until_unplug_active
            and WallboxController.resolve_effective_solar_strategy(
                self.control_config.solar_control_strategy,
                self.control_config.solar_until_unplug_strategy,
                True,
            )
            == SolarControlStrategy.MIN_PLUS_SURPLUS
        ):
            return "solar_until_unplug"
        if effective_mode == ChargeMode.SOLAR and self._solar_until_unplug_active:
            return "solar_until_unplug"
        if effective_mode == ChargeMode.SOLAR and decision.reason == ControlReason.BELOW_MIN_CURRENT:
            return "waiting_for_solar"
        if effective_mode == ChargeMode.SOLAR and decision.reason == ControlReason.SENSOR_UNAVAILABLE:
            return "fallback"
        if decision.dominant_limit_reason == ControlReason.DLB_LIMITED:
            return "dlb_limited"
        if effective_mode == ChargeMode.SOLAR:
            if self.control_config.solar_control_strategy == SolarControlStrategy.ECO_SOLAR:
                return "eco_solar"
            if self.control_config.solar_control_strategy == SolarControlStrategy.SMART_SOLAR:
                return "smart_solar"
            return "solar"
        return "normal"

    def _read_sensor_snapshot(self, wallbox: WallboxState | None = None) -> HaSensorSnapshot:
        options = self.entry.options
        snapshot = HaSensorSnapshot(valid=True)
        snapshot.solar_input_state = "disabled"

        if self.control_config.dlb_input_model == DlbInputModel.PHASE_CURRENTS:
            snapshot.phase_currents = PhaseCurrents(
                l1=self.sensor_adapter.state_as_current_a(
                    options.get(CONF_DLB_L1_SENSOR),
                    require_supported_unit=self.control_config.dlb_require_units,
                    max_age_s=self.control_config.control_sensor_timeout_s,
                ),
                l2=self.sensor_adapter.state_as_current_a(
                    options.get(CONF_DLB_L2_SENSOR),
                    require_supported_unit=self.control_config.dlb_require_units,
                    max_age_s=self.control_config.control_sensor_timeout_s,
                ),
                l3=self.sensor_adapter.state_as_current_a(
                    options.get(CONF_DLB_L3_SENSOR),
                    require_supported_unit=self.control_config.dlb_require_units,
                    max_age_s=self.control_config.control_sensor_timeout_s,
                ),
            )

        if self.control_config.solar_input_model == SolarInputModel.SURPLUS_SENSOR:
            snapshot.surplus_power_w = self.sensor_adapter.state_as_power_w(
                options.get(CONF_SOLAR_SURPLUS_SENSOR),
                require_supported_unit=self.control_config.solar_require_units,
                max_age_s=self.control_config.control_sensor_timeout_s,
            )
        elif snapshot.grid_power_w is None:
            snapshot.grid_power_w = self.sensor_adapter.state_as_power_w(
                options.get(CONF_SOLAR_GRID_POWER_SENSOR) or options.get(CONF_DLB_GRID_POWER_SENSOR),
                require_supported_unit=self.control_config.solar_require_units,
                max_age_s=self.control_config.control_sensor_timeout_s,
            )

        if self.control_config.dlb_input_model == DlbInputModel.PHASE_CURRENTS:
            required_indices = self._required_dlb_sensor_indices(wallbox)
            phase_values = (
                snapshot.phase_currents.l1,
                snapshot.phase_currents.l2,
                snapshot.phase_currents.l3,
            )
            phase_entities = (
                options.get(CONF_DLB_L1_SENSOR),
                options.get(CONF_DLB_L2_SENSOR),
                options.get(CONF_DLB_L3_SENSOR),
            )
            required_values = (
                tuple(phase_values[idx] for idx in required_indices)
            )
            if any(value is None for value in required_values):
                stale_entities = self._stale_sensor_entities(
                    tuple(phase_entities[idx] for idx in required_indices)
                )
                snapshot.valid = False
                snapshot.reason_invalid = self._control_sensor_invalid_reason(
                    "Required DLB phase sensors",
                    stale=bool(stale_entities),
                    require_units=self.control_config.dlb_require_units,
                )

        if (
            snapshot.reason_invalid is None
            and self.control_config.solar_control_strategy != SolarControlStrategy.DISABLED
            and self.controller.resolve_surplus_power(snapshot, wallbox) is None
        ):
            solar_entity = (
                options.get(CONF_SOLAR_SURPLUS_SENSOR)
                if self.control_config.solar_input_model == SolarInputModel.SURPLUS_SENSOR
                else options.get(CONF_SOLAR_GRID_POWER_SENSOR) or options.get(CONF_DLB_GRID_POWER_SENSOR)
            )
            snapshot.solar_input_state = "unavailable"
            snapshot.reason_invalid = self._control_sensor_invalid_reason(
                "Required Solar sensor",
                stale=self.sensor_adapter.state_is_stale(
                    solar_entity,
                    max_age_s=self.control_config.control_sensor_timeout_s,
                ),
                require_units=self.control_config.solar_require_units,
            )
        elif self.control_config.solar_control_strategy != SolarControlStrategy.DISABLED:
            snapshot.solar_input_state = "ready"

        return snapshot

    def _required_dlb_sensor_indices(self, wallbox: WallboxState | None) -> tuple[int, ...]:
        if self._configured_phase_count() == 1:
            return (0,)
        if wallbox is None or not wallbox.charging_active:
            return (0, 1, 2)
        active_indices = tuple(
            idx
            for idx, value in enumerate(
                (
                    wallbox.phase_currents.l1,
                    wallbox.phase_currents.l2,
                    wallbox.phase_currents.l3,
                )
            )
            if value is not None and value >= 0.5
        )
        return active_indices or (0, 1, 2)

    @staticmethod
    def _control_sensor_invalid_reason(prefix: str, *, stale: bool, require_units: bool) -> str:
        if stale:
            return f"{prefix} stale"
        if require_units:
            return f"{prefix} unavailable or invalid unit"
        return f"{prefix} unavailable"

    def _stale_sensor_entities(self, entity_ids: tuple[str | None, ...]) -> list[str]:
        return [
            entity_id
            for entity_id in entity_ids
            if self.sensor_adapter.state_is_stale(
                entity_id,
                max_age_s=self.control_config.control_sensor_timeout_s,
            )
        ]

    def _configured_installed_phases(self) -> str:
        entry = getattr(self, "entry", None)
        return getattr(entry, "data", {}).get(CONF_INSTALLED_PHASES, "3p")

    def _configured_phase_count(self) -> int:
        return 1 if self._configured_installed_phases() == "1p" else 3

    def _reset_solar_runtime_state(self) -> None:
        controller = getattr(self, "controller", None)
        if controller is not None:
            controller.reset_solar_state()

    def _reset_pv_runtime_state(self) -> None:
        self._reset_solar_runtime_state()

    def _startup_stabilization_ready(self) -> bool:
        return (
            self._startup_refresh_count >= STARTUP_STABILIZATION_MIN_POLLS
            and (monotonic() - self._startup_started_monotonic) >= STARTUP_STABILIZATION_MIN_SECONDS
        )

    async def _enqueue_keepalive_if_needed(self) -> None:
        now = monotonic()
        elapsed = (
            now - self._last_keepalive_sent_monotonic
            if self._last_keepalive_sent_monotonic
            else now - self._keepalive_started_monotonic
        )
        if elapsed < self.control_config.keepalive_interval_s:
            return
        await self.write_queue.enqueue(QueuedWrite("keepalive", LIFE_BIT, 1, WritePriority.KEEPALIVE))

    async def _enqueue_decision(self, decision) -> None:
        if not decision.charging_enabled and decision.reason == ControlReason.OFF_MODE:
            await self.write_queue.clear()
            await self._enqueue_keepalive_if_needed()

        if not self._allows_control_writes():
            return

        if (
            not decision.charging_enabled
            and decision.reason == ControlReason.BELOW_MIN_CURRENT
            and getattr(decision, "dominant_limit_reason", None)
            in {ControlReason.DLB_LIMITED, ControlReason.CABLE_LIMITED, ControlReason.EV_LIMITED}
        ):
            await self.write_queue.clear()
            await self._enqueue_keepalive_if_needed()
            await self.write_queue.enqueue(
                QueuedWrite("current_limit", SET_CHARGE_CURRENT_A, 0, WritePriority.CONTROL)
            )
            return

        if (
            not decision.charging_enabled
            and self.effective_mode == ChargeMode.SOLAR
            and decision.reason in (ControlReason.BELOW_MIN_CURRENT, ControlReason.SENSOR_UNAVAILABLE)
        ) and (
            self.data is None
            or self.data.wallbox.charging_active
            or self.data.wallbox.vehicle_connected
            or (self.data.wallbox.current_limit_a is not None and self.data.wallbox.current_limit_a > 0)
        ):
            await self.write_queue.clear()
            await self._enqueue_keepalive_if_needed()
            await self.write_queue.enqueue(
                QueuedWrite("current_limit", SET_CHARGE_CURRENT_A, 0, WritePriority.CONTROL)
            )
            return

        if decision.should_write and decision.target_current_a is not None:
            await self.write_queue.enqueue(
                QueuedWrite(
                    "current_limit",
                    SET_CHARGE_CURRENT_A,
                    int(round(decision.target_current_a)),
                    WritePriority.CURRENT,
                )
            )

    async def _sync_static_registers(self) -> None:
        if not self._allows_static_sync():
            return
        await self.write_queue.enqueue(
            QueuedWrite("safe_current", SAFE_CURRENT_A, int(round(self.control_config.safe_current_a)), WritePriority.SAFETY)
        )
        await self.write_queue.enqueue(
            QueuedWrite(
                "communication_timeout",
                COMM_TIMEOUT_S,
                int(round(self.control_config.communication_timeout_s)),
                WritePriority.SAFETY,
            )
        )
        await self._flush_write_queue()

    async def _flush_write_queue(self) -> None:
        async with self._flush_lock:
            while True:
                item = await self.write_queue.dequeue_next()
                if item is None:
                    break
                try:
                    await self.client.write(item.register, item.value)
                except Exception:
                    if item.key == "keepalive":
                        self._keepalive_write_failures += 1
                    raise
                if item.key == "keepalive":
                    self._last_keepalive_sent_monotonic = monotonic()
                    self._keepalive_sent_count += 1
                if item.key == "current_limit":
                    self.controller.mark_current_written(float(item.value))

    def _allows_keepalive(self) -> bool:
        return True

    def _allows_control_writes(self) -> bool:
        return self.control_config.control_mode == ControlMode.MANAGED_CONTROL

    def _allows_static_sync(self) -> bool:
        return self.control_config.control_mode == ControlMode.MANAGED_CONTROL

    def _keepalive_age_seconds(self) -> float | None:
        reference = self._last_keepalive_sent_monotonic or self._keepalive_started_monotonic
        return round(max(0.0, monotonic() - reference), 1)

    def _is_keepalive_overdue(self, age_s: float | None) -> bool:
        if age_s is None:
            return False
        return age_s > (self.control_config.keepalive_interval_s * 1.5)

    async def _keepalive_loop(self) -> None:
        sleep_s = max(1.0, min(self.control_config.keepalive_interval_s / 2.0, 5.0))
        while True:
            try:
                await self._enqueue_keepalive_if_needed()
                await self._flush_write_queue()
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Keepalive loop error: %s", err)
            await asyncio.sleep(sleep_s)

    def _setup_sensor_listeners(self) -> None:
        entities = [
            self.entry.options.get(CONF_DLB_L1_SENSOR),
            self.entry.options.get(CONF_DLB_L2_SENSOR),
            self.entry.options.get(CONF_DLB_L3_SENSOR),
            self.entry.options.get(CONF_SOLAR_GRID_POWER_SENSOR),
            self.entry.options.get(CONF_DLB_GRID_POWER_SENSOR),
            self.entry.options.get(CONF_SOLAR_SURPLUS_SENSOR),
        ]
        entities = [entity_id for entity_id in entities if entity_id]
        if not entities:
            return

        @callback
        def _handle_state_change(_event):
            self.async_set_updated_data(self.data)
            self._schedule_sensor_refresh()

        self._sensor_unsubscribers.append(
            async_track_state_change_event(self.hass, entities, _handle_state_change)
        )
