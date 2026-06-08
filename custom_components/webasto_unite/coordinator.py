from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
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
    CONF_PHASE_SWITCHING_MODE,
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
    DEFAULT_PHASE_SWITCHING_MODE,
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
    PHASE_MODE_1P,
    PHASE_SWITCHING_MODE_AUTOMATIC_SOLAR,
    PHASE_SWITCHING_MODE_OFF,
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
from .phase_engine import REGISTER_ACCEPTED_RESULTS, PhaseSwitchManager
from .phase_observer import build_phase_observability
from .phase_policy import (
    AUTO_PHASE_MAX_SWITCHES_PER_SESSION,
    AUTO_PHASE_STABLE_TO_1P_S,
    AUTO_PHASE_STABLE_TO_3P_S,
    AUTO_PHASE_SWITCH_COOLDOWN_S,
    PhasePolicyDecision,
    evaluate_phase_policy,
)
from .registers import SET_CHARGE_CURRENT_A
from .runtime_guards import RuntimeGuards
from .sensor_adapter import HaSensorAdapter
from .wallbox_reader import WallboxReader
from .write_queue import QueuedWrite, WritePriority, WriteQueueManager
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
        self._active_solar_strategy: SolarControlStrategy | None = None
        self._charging_paused = False
        self._solar_until_unplug_active = False
        self._fixed_current_until_unplug_active = False
        self._last_vehicle_connected = False
        self._phase_switching_mode = PHASE_SWITCHING_MODE_OFF
        self._phase_switch_last_result: str | None = None
        self._phase_switch_last_block_reason: str | None = None
        self._phase_switch_last_target: str | None = None
        self._phase_switch_state: str | None = "idle"
        self._phase_session_override_active = False
        self._phase_session_target: str | None = None
        self._phase_restore_pending = False
        self._phase_policy_candidate_target: str | None = None
        self._phase_policy_candidate_since_monotonic: float | None = None
        self._phase_policy_last_switch_monotonic: float | None = None
        self._phase_policy_session_switch_count = 0
        self.phase_switch_manager = PhaseSwitchManager()
        self._external_current_a: float | None = None
        self._sensor_unsubscribers = []
        self._keepalive_task: asyncio.Task | None = None
        self._sensor_refresh_task: asyncio.Task | None = None
        self._phase_switch_task: asyncio.Task | None = None
        self._phase_restore_task: asyncio.Task | None = None
        self._phase_switch_sleep = asyncio.sleep
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
        self._active_solar_strategy = self.control_config.solar_control_strategy
        self._phase_switching_mode = str(
            merged.get(CONF_PHASE_SWITCHING_MODE, DEFAULT_PHASE_SWITCHING_MODE)
        )
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
        if not hasattr(self, "_phase_switching_mode"):
            self._phase_switching_mode = PHASE_SWITCHING_MODE_OFF
        if not hasattr(self, "_phase_switch_last_result"):
            self._phase_switch_last_result = None
        if not hasattr(self, "_phase_switch_last_block_reason"):
            self._phase_switch_last_block_reason = None
        if not hasattr(self, "_phase_switch_last_target"):
            self._phase_switch_last_target = None
        if not hasattr(self, "_phase_switch_state"):
            self._phase_switch_state = "idle"
        if not hasattr(self, "_phase_session_override_active"):
            self._phase_session_override_active = False
        if not hasattr(self, "_phase_session_target"):
            self._phase_session_target = None
        if not hasattr(self, "_phase_restore_pending"):
            self._phase_restore_pending = False
        if not hasattr(self, "_phase_policy_candidate_target"):
            self._phase_policy_candidate_target = None
        if not hasattr(self, "_phase_policy_candidate_since_monotonic"):
            self._phase_policy_candidate_since_monotonic = None
        if not hasattr(self, "_phase_policy_last_switch_monotonic"):
            self._phase_policy_last_switch_monotonic = None
        if not hasattr(self, "_phase_policy_session_switch_count"):
            self._phase_policy_session_switch_count = 0
        if not hasattr(self, "phase_switch_manager"):
            self.phase_switch_manager = PhaseSwitchManager()
            self.phase_switch_manager.last_result = self._phase_switch_last_result
            self.phase_switch_manager.last_block_reason = self._phase_switch_last_block_reason
            self.phase_switch_manager.last_target = self._phase_switch_last_target
            self.phase_switch_manager.state = self._phase_switch_state or "idle"
        if not hasattr(self, "_external_current_a"):
            self._external_current_a = None
        if not hasattr(self, "_phase_switch_sleep"):
            self._phase_switch_sleep = asyncio.sleep
        if not hasattr(self, "write_queue"):
            self.write_queue = WriteQueueManager()
        if not hasattr(self, "control_config"):
            self.control_config = ControlConfig()
        if not hasattr(self, "_active_solar_strategy"):
            self._active_solar_strategy = self.control_config.solar_control_strategy
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
        if not hasattr(self, "_phase_switch_task"):
            self._phase_switch_task = getattr(self, "_automatic_phase_switch_task", None)
        if not hasattr(self, "_phase_restore_task"):
            self._phase_restore_task = None

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
        if self._phase_switch_task is not None:
            self._phase_switch_task.cancel()
            try:
                await self._phase_switch_task
            except asyncio.CancelledError:
                pass
            self._phase_switch_task = None
        if self._phase_restore_task is not None:
            self._phase_restore_task.cancel()
            try:
                await self._phase_restore_task
            except asyncio.CancelledError:
                pass
            self._phase_restore_task = None
        for unsub in self._sensor_unsubscribers:
            unsub()
        self._sensor_unsubscribers.clear()
        await self.client.close()

    @property
    def mode(self) -> ChargeMode:
        self._ensure_runtime_defaults()
        return self._mode

    @property
    def active_solar_strategy(self) -> SolarControlStrategy:
        self._ensure_runtime_defaults()
        strategy = self._active_solar_strategy or self.control_config.solar_control_strategy
        if strategy == SolarControlStrategy.DISABLED:
            return self.control_config.solar_control_strategy
        return strategy

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

    def set_mode(self, mode: ChargeMode, solar_strategy: SolarControlStrategy | None = None) -> None:
        self._ensure_runtime_defaults()
        self._mode = mode
        self._solar_until_unplug_active = False
        self._fixed_current_until_unplug_active = False
        if mode == ChargeMode.SOLAR:
            self._active_solar_strategy = solar_strategy or self.control_config.solar_control_strategy
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
        if self.control_config.control_mode == ControlMode.EXTERNAL_CONTROLLER:
            current_a = self._external_current_a or self.control_config.min_current_a
            await self._enqueue_external_current_limit(current_a if enabled else 0.0)

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

    def _create_background_task(self, coro) -> asyncio.Task:
        create_task = getattr(self.hass, "async_create_task", None) or asyncio.create_task
        return create_task(coro)

    async def async_schedule_phase_switch(self, target_phases: int, *, request_refresh: bool = True) -> None:
        self._ensure_runtime_defaults()
        if self._phase_switch_in_progress():
            raise ValueError("Phase switch blocked: phase_switch_in_progress")
        self._schedule_phase_switch_task(target_phases, source="manual")
        if request_refresh:
            await self.async_request_refresh()

    def _schedule_phase_switch_task(self, target_phases: int, *, source: str) -> None:
        self.phase_switch_manager.last_target = f"{target_phases}P"
        self.phase_switch_manager.last_block_reason = None
        self.phase_switch_manager.state = "queued"
        self._sync_phase_switch_diagnostics()
        self._phase_switch_task = self._create_background_task(
            self._run_scheduled_phase_switch(target_phases, source=source)
        )

    async def _run_scheduled_phase_switch(self, target_phases: int, *, source: str) -> None:
        failed_before_accept = True
        try:
            await self.async_request_phase_switch(target_phases, request_refresh=False)
            failed_before_accept = self._phase_switch_last_result not in REGISTER_ACCEPTED_RESULTS
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("%s phase switch to %sP failed: %s", source.capitalize(), target_phases, err)
        finally:
            if source == "automatic" and failed_before_accept:
                self._record_phase_policy_failed_attempt()
            self._sync_phase_switch_diagnostics()
            await self.async_request_refresh()

    async def async_request_phase_switch(self, target_phases: int, *, request_refresh: bool = True) -> None:
        self._ensure_runtime_defaults()
        current_snapshot = getattr(self, "data", None)
        wallbox = getattr(current_snapshot, "wallbox", None)
        try:
            await self.phase_switch_manager.request(
                phase_switching_mode=self._phase_switching_mode,
                wallbox=wallbox,
                target_phases=target_phases,
                config=self.control_config,
                client=self.client,
                write_queue=self.write_queue,
                flush_lock=self.write_runtime.flush_lock,
                sleep=self._phase_switch_sleep,
                read_wallbox=self._read_wallbox_for_phase_switch,
                pause_charging=self._phase_switch_pause_charging,
                resume_charging=self._phase_switch_resume_charging,
            )
        finally:
            self._sync_phase_switch_diagnostics()
        if self._phase_switch_last_result in REGISTER_ACCEPTED_RESULTS:
            self._record_phase_policy_switch_attempt()
            self._update_phase_session_override(target_phases)
        if request_refresh:
            await self.async_request_refresh()

    async def async_schedule_restore_default_phase_mode(
        self,
        wallbox: WallboxState | None = None,
        *,
        request_refresh: bool = True,
    ) -> None:
        self._ensure_runtime_defaults()
        if self._phase_switch_in_progress():
            raise ValueError("Phase restore blocked: phase_switch_in_progress")
        self._schedule_phase_restore_task(wallbox)
        if request_refresh:
            await self.async_request_refresh()

    def _schedule_phase_restore_task(self, wallbox: WallboxState | None = None) -> None:
        target_phases = 1 if self._configured_installed_phases() == PHASE_MODE_1P else 3
        self._phase_restore_pending = True
        self.phase_switch_manager.last_target = f"{target_phases}P"
        self.phase_switch_manager.last_block_reason = None
        self.phase_switch_manager.state = "restore_queued"
        self._sync_phase_switch_diagnostics()
        self._phase_restore_task = self._create_background_task(
            self._run_scheduled_phase_restore(wallbox)
        )

    async def _run_scheduled_phase_restore(self, wallbox: WallboxState | None = None) -> None:
        try:
            await self.async_restore_default_phase_mode(wallbox, request_refresh=False)
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Default phase restore failed: %s", err)
            self._phase_restore_pending = True
        finally:
            self._sync_phase_switch_diagnostics()
            await self.async_request_refresh()

    async def async_restore_default_phase_mode(
        self,
        wallbox: WallboxState | None = None,
        *,
        request_refresh: bool = True,
    ) -> None:
        self._ensure_runtime_defaults()
        target_phases = 1 if self._configured_installed_phases() == PHASE_MODE_1P else 3
        current_snapshot = getattr(self, "data", None)
        wallbox = wallbox or getattr(current_snapshot, "wallbox", None)
        if (
            wallbox is not None
            and wallbox.phase_switch_mode_raw == (0 if target_phases == 1 else 1)
            and self._observed_phases_match_target(wallbox, target_phases)
        ):
            self.phase_switch_manager.last_target = f"{target_phases}P"
            self.phase_switch_manager.last_result = "already_in_target_phase"
            self.phase_switch_manager.last_block_reason = None
            self.phase_switch_manager.state = "already_in_target_phase"
            self._sync_phase_switch_diagnostics()
            self._clear_phase_session_override()
            if request_refresh:
                await self.async_request_refresh()
            return
        try:
            await self.phase_switch_manager.request(
                phase_switching_mode=self._phase_switching_mode,
                wallbox=wallbox,
                target_phases=target_phases,
                config=self.control_config,
                client=self.client,
                write_queue=self.write_queue,
                flush_lock=self.write_runtime.flush_lock,
                sleep=self._phase_switch_sleep,
                read_wallbox=self._read_wallbox_for_phase_switch,
                pause_charging=self._phase_switch_pause_charging,
                resume_charging=self._phase_switch_resume_charging,
                require_vehicle=False,
            )
        finally:
            self._sync_phase_switch_diagnostics()
        if wallbox is not None and wallbox.vehicle_connected and self._phase_switch_last_result in REGISTER_ACCEPTED_RESULTS:
            self._record_phase_policy_switch_attempt()
        self._clear_phase_session_override()
        if request_refresh:
            await self.async_request_refresh()

    def reset_phase_switch_state(self) -> None:
        self._ensure_runtime_defaults()
        self.phase_switch_manager.reset()
        self._sync_phase_switch_diagnostics()

    def _reset_phase_policy_dry_run_state(self) -> None:
        self._phase_policy_candidate_target = None
        self._phase_policy_candidate_since_monotonic = None
        self._phase_policy_last_switch_monotonic = None
        self._phase_policy_session_switch_count = 0

    def _record_phase_policy_switch_attempt(self) -> None:
        self._phase_policy_last_switch_monotonic = monotonic()
        self._phase_policy_session_switch_count += 1
        self._phase_policy_candidate_target = None
        self._phase_policy_candidate_since_monotonic = None

    def _record_phase_policy_failed_attempt(self) -> None:
        self._phase_policy_last_switch_monotonic = monotonic()
        self._phase_policy_candidate_target = None
        self._phase_policy_candidate_since_monotonic = None

    def _apply_phase_policy_dry_run(self, phase_policy: PhasePolicyDecision) -> PhasePolicyDecision:
        now = monotonic()
        cooldown_remaining_s = 0.0
        if self._phase_policy_last_switch_monotonic is not None:
            cooldown_remaining_s = max(
                0.0,
                AUTO_PHASE_SWITCH_COOLDOWN_S - (now - self._phase_policy_last_switch_monotonic),
            )

        target = phase_policy.target if phase_policy.decision in {"would_request_1p", "would_request_3p"} else None
        if target is None:
            self._phase_policy_candidate_target = None
            self._phase_policy_candidate_since_monotonic = None
            return replace(
                phase_policy,
                auto_ready=False,
                auto_block_reason=phase_policy.block_reason,
                stable_elapsed_s=None,
                stable_required_s=None,
                cooldown_remaining_s=round(cooldown_remaining_s, 1),
                session_switch_count=self._phase_policy_session_switch_count,
                session_switch_limit=AUTO_PHASE_MAX_SWITCHES_PER_SESSION,
            )

        if self._phase_policy_candidate_target != target:
            self._phase_policy_candidate_target = target
            self._phase_policy_candidate_since_monotonic = now

        stable_elapsed_s = max(0.0, now - (self._phase_policy_candidate_since_monotonic or now))
        stable_required_s = AUTO_PHASE_STABLE_TO_1P_S if target == "1P" else AUTO_PHASE_STABLE_TO_3P_S
        auto_block_reason = None
        if getattr(self, "_phase_switching_mode", PHASE_SWITCHING_MODE_OFF) != PHASE_SWITCHING_MODE_AUTOMATIC_SOLAR:
            auto_block_reason = "automatic_phase_switching_disabled"
        elif self.control_config.control_mode != ControlMode.MANAGED_CONTROL:
            auto_block_reason = "external_controller_mode"
        elif cooldown_remaining_s > 0:
            auto_block_reason = "cooldown_active"
        elif self._phase_policy_session_switch_count >= AUTO_PHASE_MAX_SWITCHES_PER_SESSION:
            auto_block_reason = "session_switch_limit_reached"
        elif stable_elapsed_s < stable_required_s:
            auto_block_reason = "waiting_for_stable_phase_target"

        return replace(
            phase_policy,
            auto_ready=auto_block_reason is None,
            auto_block_reason=auto_block_reason,
            stable_elapsed_s=round(stable_elapsed_s, 1),
            stable_required_s=stable_required_s,
            cooldown_remaining_s=round(cooldown_remaining_s, 1),
            session_switch_count=self._phase_policy_session_switch_count,
            session_switch_limit=AUTO_PHASE_MAX_SWITCHES_PER_SESSION,
        )

    async def _maybe_execute_automatic_phase_policy(self, phase_policy: PhasePolicyDecision) -> bool:
        if self._phase_switching_mode != PHASE_SWITCHING_MODE_AUTOMATIC_SOLAR:
            return False
        if self.control_config.control_mode != ControlMode.MANAGED_CONTROL:
            return False
        if not phase_policy.auto_ready or phase_policy.target not in {"1P", "3P"}:
            return False
        if self._phase_switch_in_progress():
            return False
        target_phases = int(phase_policy.target[0])
        self._schedule_phase_switch_task(target_phases, source="automatic")
        return True

    def _sync_phase_switch_diagnostics(self) -> None:
        self._phase_switch_last_result = self.phase_switch_manager.last_result
        self._phase_switch_last_block_reason = self.phase_switch_manager.last_block_reason
        self._phase_switch_last_target = self.phase_switch_manager.last_target
        self._phase_switch_state = self.phase_switch_manager.state

    async def _read_wallbox_for_phase_switch(self) -> WallboxState | None:
        if not hasattr(self, "wallbox_reader"):
            current_snapshot = getattr(self, "data", None)
            return getattr(current_snapshot, "wallbox", None)
        return await self.wallbox_reader.read_wallbox_state(self._configured_installed_phases())

    async def _phase_switch_pause_charging(self) -> None:
        self.pause_charging()
        await self.write_runtime.write_current_now(0.0, reason="phase_switch_pause")

    async def _phase_switch_resume_charging(self, current_a: float) -> None:
        self.resume_charging()
        await self.write_runtime.write_current_now(current_a, reason="phase_switch_resume")

    @staticmethod
    def _observed_phases_match_target(wallbox: WallboxState, target_phases: int) -> bool:
        if not wallbox.charging_active:
            return True
        return wallbox.phases_in_use == target_phases

    def _update_phase_session_override(self, target_phases: int) -> None:
        default_target = 1 if self._configured_installed_phases() == PHASE_MODE_1P else 3
        if target_phases == default_target:
            self._clear_phase_session_override()
            return
        self._phase_session_override_active = True
        self._phase_session_target = f"{target_phases}P"
        self._phase_restore_pending = False

    def _clear_phase_session_override(self) -> None:
        self._phase_session_override_active = False
        self._phase_session_target = None
        self._phase_restore_pending = False

    def _default_phase_switch_raw_value(self) -> int:
        return 0 if self._configured_installed_phases() == PHASE_MODE_1P else 1

    async def _async_handle_phase_restore_state(self, wallbox: WallboxState) -> None:
        if wallbox.phase_switch_mode_raw not in (0, 1):
            return
        default_raw = self._default_phase_switch_raw_value()
        if wallbox.phase_switch_mode_raw == default_raw:
            self._clear_phase_session_override()
            return
        if (
            self._phase_session_override_active
            and self._phase_session_target == ("1P" if wallbox.phase_switch_mode_raw == 0 else "3P")
        ):
            self._phase_restore_pending = False
            return
        self._phase_restore_pending = True
        self._phase_session_override_active = True
        self._phase_session_target = "1P" if wallbox.phase_switch_mode_raw == 0 else "3P"
        if wallbox.vehicle_connected:
            return
        if not self._phase_switch_in_progress():
            self._schedule_phase_restore_task(wallbox)

    def set_fixed_current(self, current_a: float) -> None:
        self.control_config.fixed_current_a = self._validate_runtime_current(current_a, "Fixed Current")

    async def async_set_external_current_limit(self, current_a: float) -> None:
        self._ensure_runtime_defaults()
        current = self._validate_external_current(current_a)
        if current > 0:
            self._external_current_a = current
        await self._enqueue_external_current_limit(current)

    async def _enqueue_external_current_limit(self, current_a: float) -> None:
        await self.write_queue.enqueue(
            QueuedWrite(
                "current_limit",
                SET_CHARGE_CURRENT_A,
                int(round(current_a)),
                WritePriority.CONTROL,
                reason="external_controller",
            )
        )
        await self.write_runtime.flush_write_queue()

    def _validate_external_current(self, current_a: float) -> float:
        current = float(current_a)
        rounded = round(current)
        if rounded == 0:
            return 0.0
        if not self.control_config.min_current_a <= rounded <= self.control_config.max_current_a:
            raise ValueError(
                f"External current must be 0 A or between {self.control_config.min_current_a:g} A "
                f"and {self.control_config.max_current_a:g} A"
            )
        return float(rounded)

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
            phase_restore_attempted = False

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
                self._reset_phase_policy_dry_run_state()

            if self._phase_session_override_active and self._last_vehicle_connected and not wallbox.vehicle_connected:
                self._phase_restore_pending = True
                if not self._phase_switch_in_progress():
                    self._schedule_phase_restore_task(wallbox)
                phase_restore_attempted = True

            if not phase_restore_attempted:
                await self._async_handle_phase_restore_state(wallbox)

            if not self._last_vehicle_connected and wallbox.vehicle_connected:
                self.controller.reset_current_write_state()
                self._reset_phase_policy_dry_run_state()

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
            phase_observability = build_phase_observability(wallbox)
            solar_strategy = self.controller.resolve_effective_solar_strategy(
                self.active_solar_strategy,
                self.control_config.solar_until_unplug_strategy,
                self._solar_until_unplug_active,
            )
            decision = self.controller.evaluate(self.effective_mode, wallbox, sensors, solar_strategy)
            phase_policy = evaluate_phase_policy(
                effective_mode=self.effective_mode,
                solar_strategy=solar_strategy,
                phase_switching_mode=self._phase_switching_mode,
                configured_installed_phases=self._configured_installed_phases(),
                wallbox=wallbox,
                control_decision=decision,
                solar_input_state=sensors.solar_input_state,
                filtered_surplus_w=self.controller.solar_state.filtered_surplus_w,
                phase_restore_pending=self._phase_restore_pending,
                solar_min_current_a=self.control_config.solar_min_current_a,
                session_observed_3p=self.controller.session_observed_3p,
            )
            phase_policy = self._apply_phase_policy_dry_run(phase_policy)
            automatic_phase_switch_executed = await self._maybe_execute_automatic_phase_policy(phase_policy)

            # Apply transient/startup guards after the controller decision is
            # built, but before anything is enqueued for writing.
            if automatic_phase_switch_executed:
                decision.should_write = False
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
                blocked_reason=self._control_write_blocked_reason(),
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
                    solar_strategy=solar_strategy,
                ),
                control_mode=self.control_config.control_mode,
                control_reason=decision.reason.value,
                active_solar_strategy=solar_strategy,
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
                control_writes_enabled=self._allows_current_writes(),
                last_control_write_value_a=self.write_runtime.last_control_write_value_a,
                last_control_write_reason=self.write_runtime.last_control_write_reason,
                last_control_write_register=self.write_runtime.last_control_write_register,
                last_control_write_age_s=self.write_runtime.last_control_write_age_seconds(),
                last_control_write_blocked_reason=self.write_runtime.last_control_write_blocked_reason,
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
                phase_switch_mode_raw=phase_observability.phase_switch_mode_raw,
                phase_switch_mode=phase_observability.phase_switch_mode,
                phase_switch_register_available=phase_observability.phase_switch_register_available,
                phase_switch_available=phase_observability.phase_switch_available,
                phase_switch_block_reason=phase_observability.phase_switch_block_reason,
                vehicle_phase_capability=phase_observability.vehicle_phase_capability,
                phase_switching_mode=self._phase_switching_mode,
                phase_switch_default_mode=self._configured_installed_phases(),
                phase_session_override_active=self._phase_session_override_active,
                phase_session_target=self._phase_session_target,
                phase_restore_pending=self._phase_restore_pending,
                phase_policy_decision=phase_policy.decision,
                phase_policy_block_reason=phase_policy.block_reason,
                phase_policy_target=phase_policy.target,
                phase_policy_required_surplus_1p_w=phase_policy.required_surplus_1p_w,
                phase_policy_required_surplus_3p_w=phase_policy.required_surplus_3p_w,
                phase_policy_auto_ready=phase_policy.auto_ready,
                phase_policy_auto_block_reason=phase_policy.auto_block_reason,
                phase_policy_stable_elapsed_s=phase_policy.stable_elapsed_s,
                phase_policy_stable_required_s=phase_policy.stable_required_s,
                phase_policy_cooldown_remaining_s=phase_policy.cooldown_remaining_s,
                phase_policy_session_switch_count=phase_policy.session_switch_count,
                phase_policy_session_switch_limit=phase_policy.session_switch_limit,
                phase_switch_last_result=self._phase_switch_last_result,
                phase_switch_last_block_reason=self._phase_switch_last_block_reason,
                phase_switch_last_target=self._phase_switch_last_target,
                phase_switch_state=self._phase_switch_state,
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
        return self.control_config.control_mode == ControlMode.MANAGED_CONTROL and not self._phase_switch_in_progress()

    def _allows_current_writes(self) -> bool:
        return self.control_config.control_mode in {
            ControlMode.MANAGED_CONTROL,
            ControlMode.EXTERNAL_CONTROLLER,
        } and not self._phase_switch_in_progress()

    def _allows_static_sync(self) -> bool:
        return self.control_config.control_mode in {
            ControlMode.MANAGED_CONTROL,
            ControlMode.EXTERNAL_CONTROLLER,
        }

    def _control_write_blocked_reason(self) -> str:
        if self._phase_switch_in_progress():
            return "phase_switch_in_progress"
        if self.control_config.control_mode == ControlMode.EXTERNAL_CONTROLLER:
            return "external_controller_mode"
        return "monitoring_only"

    def _phase_switch_in_progress(self) -> bool:
        switch_task = getattr(self, "_phase_switch_task", None)
        if switch_task is not None and not switch_task.done():
            return True
        restore_task = getattr(self, "_phase_restore_task", None)
        if restore_task is not None and not restore_task.done():
            return True
        manager = getattr(self, "phase_switch_manager", None)
        return bool(getattr(manager, "active", False))

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
