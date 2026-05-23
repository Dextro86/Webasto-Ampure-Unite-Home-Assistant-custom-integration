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
    CONF_SOLAR_SENSOR_FAILURE_BEHAVIOR,
    CONF_SOLAR_START_DELAY,
    CONF_SOLAR_START_THRESHOLD,
    CONF_SOLAR_STOP_DELAY,
    CONF_SOLAR_STOP_THRESHOLD,
    CONF_SOLAR_EXPORT_POWER_SENSOR,
    CONF_SOLAR_GRID_POWER_SENSOR,
    CONF_SOLAR_IMPORT_POWER_SENSOR,
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
    DEFAULT_SOLAR_SENSOR_FAILURE_BEHAVIOR,
    DEFAULT_STARTUP_CHARGE_MODE,
    DEFAULT_TIMEOUT_S,
    DEFAULT_UNIT_ID,
    DOMAIN,
    STORAGE_KEY_CHARGING_STATE,
)
from .capabilities import build_capabilities, build_capability_summary
from .control_inputs import ControlInputReader
from .controller import WallboxController
from .modbus_client import ModbusClientConfig, WebastoModbusClient
from .models import (
    ChargeMode,
    ControlConfig,
    ControlMode,
    ControlReason,
    DlbInputModel,
    DlbSensorScope,
    SolarControlStrategy,
    SolarInputModel,
    SolarOverrideStrategy,
    SolarSensorFailureBehavior,
    RuntimeSnapshot,
    WallboxState,
    normalize_charge_mode,
    normalize_solar_control_strategy,
    normalize_solar_override_strategy,
)
from .operating_status import build_operating_state
from .runtime_guards import RuntimeGuards
from .sensor_adapter import HaSensorAdapter
from .wallbox_reader import WallboxReader
from .write_queue import WriteQueueManager
from .write_runtime import WriteRuntime

_LOGGER = logging.getLogger(__name__)

SENSOR_REFRESH_DEBOUNCE_S = 0.4


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
        self._sensor_unsubscribers = []
        self._keepalive_task: asyncio.Task | None = None
        self._sensor_refresh_task: asyncio.Task | None = None
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
        self._mode = self._resolve_startup_mode(merged)
        self.controller = WallboxController(self.control_config)
        self.runtime_guards = RuntimeGuards(self.control_config, monotonic_fn=lambda: monotonic())
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
        self.write_runtime = WriteRuntime(
            self.control_config,
            write_queue=self.write_queue,
            client=self.client,
            controller=self.controller,
            monotonic_fn=lambda: monotonic(),
        )
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
        if not hasattr(self, "_last_vehicle_connected"):
            self._last_vehicle_connected = False
        if not hasattr(self, "write_queue"):
            self.write_queue = WriteQueueManager()
        if not hasattr(self, "control_config"):
            self.control_config = ControlConfig()
        if not hasattr(self, "controller"):
            self.controller = WallboxController(self.control_config)
        if not hasattr(self, "runtime_guards"):
            self.runtime_guards = RuntimeGuards(self.control_config, monotonic_fn=lambda: monotonic())
        if not hasattr(self, "write_runtime"):
            self.write_runtime = WriteRuntime(
                self.control_config,
                write_queue=self.write_queue,
                client=getattr(self, "client", None),
                controller=getattr(self, "controller", None),
                monotonic_fn=lambda: monotonic(),
            )
        if not hasattr(self, "_sensor_refresh_task"):
            self._sensor_refresh_task = None

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
        await self.write_runtime.sync_static_registers(allows_static_sync=self._allows_static_sync())
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
            self.controller.reset_solar_state()

    def pause_charging(self) -> None:
        self._ensure_runtime_defaults()
        self._charging_paused = True
        self.controller.reset_solar_state()

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
        self.controller.reset_solar_state()

    def set_fixed_current_until_unplug(self, enabled: bool) -> None:
        self._ensure_runtime_defaults()
        self._fixed_current_until_unplug_active = enabled
        if enabled:
            self._solar_until_unplug_active = False
        self.controller.reset_solar_state()

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
        await self.write_runtime.sync_static_registers(allows_static_sync=self._allows_static_sync())
        await self.async_request_refresh()

    async def _async_update_data(self) -> RuntimeSnapshot:
        self._ensure_runtime_defaults()
        try:
            wallbox = await self.wallbox_reader.read_wallbox_state(self._configured_installed_phases())
            self.runtime_guards.record_startup_refresh()

            # Runtime-only session overrides end when the vehicle is unplugged.
            if (
                (self._solar_until_unplug_active or self._fixed_current_until_unplug_active)
                and self._last_vehicle_connected
                and not wallbox.vehicle_connected
            ):
                self._solar_until_unplug_active = False
                self._fixed_current_until_unplug_active = False
                self.controller.reset_solar_state()
                self.controller.reset_session_phase_observation()

            if not self._last_vehicle_connected and wallbox.vehicle_connected:
                self.controller.reset_current_write_state()

            self._last_vehicle_connected = wallbox.vehicle_connected

            # Read Home Assistant sensor inputs only after wallbox/session state
            # has been updated for this poll, so the controller sees one
            # consistent view of the current cycle.
            sensors = ControlInputReader(
                options=self.entry.options,
                config=self.control_config,
                sensor_adapter=self.sensor_adapter,
                surplus_resolver=self.controller.resolve_surplus_power,
                configured_phase_count=self._configured_phase_count,
            ).read(wallbox)
            solar_surplus_w = self.controller.resolve_surplus_power(sensors, wallbox)
            solar_strategy = self.controller.resolve_effective_solar_strategy(
                self.control_config.solar_control_strategy,
                self.control_config.solar_until_unplug_strategy,
                self._solar_until_unplug_active,
            )
            decision = self.controller.evaluate(self.effective_mode, wallbox, sensors, solar_strategy)

            # Apply transient/startup guards after the controller decision is
            # built, but before anything is enqueued for writing.
            if self.runtime_guards.should_defer_startup_safe_current_fallback_write(
                wallbox=wallbox,
                sensors=sensors,
                decision=decision,
            ):
                decision.should_write = False
            self.runtime_guards.apply_dlb_start_transient_guard(wallbox=wallbox, decision=decision)
            self.runtime_guards.apply_solar_start_transient_guard(
                effective_mode=self.effective_mode,
                wallbox=wallbox,
                decision=decision,
                sensors=sensors,
            )
            await self.write_runtime.enqueue_decision(
                decision,
                effective_mode=self.effective_mode,
                current_snapshot=getattr(self, "data", None),
                allows_control_writes=self._allows_control_writes(),
                enqueue_keepalive=self.write_runtime.enqueue_keepalive_if_needed,
            )
            await self.write_runtime.flush_write_queue()
            keepalive_age_s = self.write_runtime.keepalive_age_seconds()
            return RuntimeSnapshot(
                wallbox=wallbox,
                mode=self._mode,
                effective_mode=self.effective_mode,
                operating_state=build_operating_state(
                    effective_mode=self.effective_mode,
                    charging_paused=self._charging_paused,
                    fixed_current_until_unplug_active=self._fixed_current_until_unplug_active,
                    solar_until_unplug_active=self._solar_until_unplug_active,
                    control_config=self.control_config,
                    decision=decision,
                ),
                control_mode=self.control_config.control_mode,
                control_reason=decision.reason.value,
                charging_paused=self._charging_paused,
                solar_until_unplug_active=self._solar_until_unplug_active,
                fixed_current_until_unplug_active=self._fixed_current_until_unplug_active,
                keepalive_age_s=keepalive_age_s,
                keepalive_interval_s=self.control_config.keepalive_interval_s,
                keepalive_overdue=self.write_runtime.is_keepalive_overdue(keepalive_age_s),
                keepalive_sent_count=self.write_runtime.keepalive_sent_count,
                keepalive_write_failures=self.write_runtime.keepalive_write_failures,
                sensor_snapshot_valid=sensors.valid,
                sensor_invalid_reason=sensors.reason_invalid,
                queue_depth=await self.write_queue.size(),
                pending_write_kind=await self.write_queue.peek_next_kind(),
                dlb_limit_a=decision.dlb_limit_a,
                final_target_a=decision.final_target_a,
                mode_target_a=decision.mode_target_a,
                solar_surplus_w=solar_surplus_w,
                solar_raw_surplus_w=self.controller.solar_state.raw_surplus_w,
                solar_filtered_surplus_w=self.controller.solar_state.filtered_surplus_w,
                solar_target_current_a=self.controller.solar_state.target_current_a,
                solar_phase_count=self.controller.solar_state.phase_count,
                solar_phase_source=self.controller.solar_state.phase_source,
                solar_voltage_sum_v=self.controller.solar_state.voltage_sum_v,
                solar_input_state=sensors.solar_input_state,
                dominant_limit_reason=decision.dominant_limit_reason.value if decision.dominant_limit_reason is not None else None,
                fallback_active=decision.fallback_active,
                last_client_error=self.client.stats.last_error,
                entry_title=self.entry.title or DEFAULT_NAME,
                capability_summary=build_capability_summary(wallbox),
                capabilities=build_capabilities(wallbox),
            )
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(str(err)) from err

    def _configured_installed_phases(self) -> str:
        entry = getattr(self, "entry", None)
        return getattr(entry, "data", {}).get(CONF_INSTALLED_PHASES, "3p")

    def _configured_phase_count(self) -> int:
        return 1 if self._configured_installed_phases() == "1p" else 3

    def _allows_control_writes(self) -> bool:
        return self.control_config.control_mode == ControlMode.MANAGED_CONTROL

    def _allows_static_sync(self) -> bool:
        return self.control_config.control_mode == ControlMode.MANAGED_CONTROL

    async def _keepalive_loop(self) -> None:
        sleep_s = max(1.0, min(self.control_config.keepalive_interval_s / 2.0, 5.0))
        while True:
            try:
                await self.write_runtime.enqueue_keepalive_if_needed()
                await self.write_runtime.flush_write_queue()
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
            self.entry.options.get(CONF_SOLAR_IMPORT_POWER_SENSOR),
            self.entry.options.get(CONF_SOLAR_EXPORT_POWER_SENSOR),
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
