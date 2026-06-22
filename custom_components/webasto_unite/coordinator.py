from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from time import monotonic

from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_INSTALLED_PHASES,
    CONF_PHASE_SWITCHING_MODE,
    CONF_UNIT_ID,
    DEFAULT_PHASE_SWITCHING_MODE,
    DEFAULT_PORT,
    DEFAULT_UNIT_ID,
    DOMAIN,
    PHASE_SWITCHING_MODE_OFF,
)
from .control.orchestrator import ControlWriteAccess, resolve_control_write_access
from .control.runtime_guards import RuntimeGuards
from .control.write_queue import WriteQueueManager
from .control.write_runtime import BLOCK_REASON_VEHICLE_NOT_CONNECTED, WriteRuntime
from .controller import WallboxController
from .core.config import (
    build_control_config,
    resolve_configured_max_current,
)
from .core.mode import ModeRuntimeState, resolve_startup_mode
from .core.session import SessionRuntimeState
from .features.control_cycle import ControlCycleMixin
from .features.phase_actions import PhaseActionMixin
from .features.phase_switch import (
    PhaseSwitchRuntimeFacade,
)
from .features.phase_runtime import PhaseRuntimeState
from .modbus.client import ModbusClientConfig, WebastoModbusClient
from .models import (
    ChargeMode,
    ControlConfig,
    ControlMode,
    RuntimeSnapshot,
)
from .features.phase_engine import PhaseSwitchManager
from .sensor_adapter import HaSensorAdapter
from .modbus.reader import WallboxReader
from .runtime.rest import RestDiagnosticsRuntime
from .runtime.sensors import SensorListenerRuntime
from .runtime.storage import ChargingStateStorageRuntime
from .runtime.tasks import TaskRuntime

_LOGGER = logging.getLogger(__name__)


PHASE_RUNTIME_ATTRIBUTE_MAP = {
    # Compatibility shim: older tests and feature mixins still access these
    # private coordinator attributes directly. New code should use
    # ``phase_runtime`` / ``mode_runtime`` / ``session_runtime`` explicitly.
    "_phase_switching_mode": "switching_mode",
    "_phase_switch_last_result": "switch_last_result",
    "_phase_switch_last_block_reason": "switch_last_block_reason",
    "_phase_switch_last_target": "switch_last_target",
    "_phase_switch_state": "switch_state",
    "_phase_session_override_active": "session_override_active",
    "_phase_session_target": "session_target",
    "_phase_restore_pending": "restore_pending",
    "_phase_policy_candidate_target": "policy_candidate_target",
    "_phase_policy_candidate_since_monotonic": "policy_candidate_since_monotonic",
    "_phase_policy_last_switch_monotonic": "policy_last_switch_monotonic",
    "_phase_policy_session_switch_count": "policy_session_switch_count",
    "_phase_session_started_monotonic": "session_started_monotonic",
    "_phase_recovery_warning": "recovery_warning",
}
MODE_RUNTIME_ATTRIBUTE_MAP = {
    "_mode": "mode",
    "_active_solar_strategy": "active_solar_strategy",
    "_charging_paused": "charging_paused",
    "_solar_until_unplug_active": "solar_until_unplug_active",
    "_fixed_current_until_unplug_active": "fixed_current_until_unplug_active",
}
SESSION_RUNTIME_ATTRIBUTE_MAP = {
    "_last_vehicle_connected": "last_vehicle_connected",
    "_vehicle_connection_initialized": "vehicle_connection_initialized",
}


class WebastoUniteCoordinator(ControlCycleMixin, PhaseActionMixin, DataUpdateCoordinator[RuntimeSnapshot]):
    def __getattr__(self, name):
        runtime_attr = PHASE_RUNTIME_ATTRIBUTE_MAP.get(name)
        if runtime_attr is not None:
            return getattr(self._phase_runtime(), runtime_attr)
        runtime_attr = MODE_RUNTIME_ATTRIBUTE_MAP.get(name)
        if runtime_attr is not None:
            return getattr(self._mode_runtime(), runtime_attr)
        runtime_attr = SESSION_RUNTIME_ATTRIBUTE_MAP.get(name)
        if runtime_attr is not None:
            return getattr(self._session_runtime(), runtime_attr)
        raise AttributeError(name)

    def __setattr__(self, name, value) -> None:
        runtime_attr = PHASE_RUNTIME_ATTRIBUTE_MAP.get(name)
        if runtime_attr is not None:
            setattr(self._phase_runtime(), runtime_attr, value)
            return
        runtime_attr = MODE_RUNTIME_ATTRIBUTE_MAP.get(name)
        if runtime_attr is not None:
            setattr(self._mode_runtime(), runtime_attr, value)
            return
        runtime_attr = SESSION_RUNTIME_ATTRIBUTE_MAP.get(name)
        if runtime_attr is not None:
            setattr(self._session_runtime(), runtime_attr, value)
            return
        super().__setattr__(name, value)

    def __init__(self, hass, entry) -> None:
        self.hass = hass
        self.entry = entry
        self.mode_runtime = ModeRuntimeState()
        self.session_runtime = SessionRuntimeState()
        self.phase_runtime = PhaseRuntimeState()
        self.task_runtime = TaskRuntime(self)
        self.phase_switch_manager = PhaseSwitchManager()
        self.phase_switch_runtime = PhaseSwitchRuntimeFacade(self.phase_runtime, self.phase_switch_manager)
        self._external_current_a: float | None = None
        self._pending_external_current_a: float | None = None
        self.sensor_runtime = SensorListenerRuntime(self)
        self.sensor_runtime.initialize()
        self._keepalive_task: asyncio.Task | None = None
        self._phase_switch_task: asyncio.Task | None = None
        self._phase_restore_task: asyncio.Task | None = None
        self._phase_switch_sleep = asyncio.sleep
        self.rest_runtime = RestDiagnosticsRuntime(self)
        self.rest_runtime.initialize()
        self.storage_runtime = ChargingStateStorageRuntime(self)
        self.storage_runtime.initialize()

        merged = {**entry.data, **entry.options}
        self.control_config = build_control_config(merged)
        self.mode_runtime.reset_to_default(
            resolve_startup_mode(merged, self.control_config),
            self.control_config.solar_control_strategy,
        )
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
        return resolve_configured_max_current(options)

    def _phase_runtime(self) -> PhaseRuntimeState:
        runtime = self.__dict__.get("phase_runtime")
        if runtime is None:
            runtime = PhaseRuntimeState()
            super().__setattr__("phase_runtime", runtime)
        return runtime

    def _mode_runtime(self) -> ModeRuntimeState:
        runtime = self.__dict__.get("mode_runtime")
        if runtime is None:
            runtime = ModeRuntimeState()
            super().__setattr__("mode_runtime", runtime)
        return runtime

    def _session_runtime(self) -> SessionRuntimeState:
        runtime = self.__dict__.get("session_runtime")
        if runtime is None:
            runtime = SessionRuntimeState()
            super().__setattr__("session_runtime", runtime)
        return runtime

    def _phase_switch_runtime(self) -> PhaseSwitchRuntimeFacade:
        manager = self.__dict__.get("phase_switch_manager")
        if manager is None:
            manager = PhaseSwitchManager()
            super().__setattr__("phase_switch_manager", manager)
        runtime = self._phase_runtime()
        facade = self.__dict__.get("phase_switch_runtime")
        if facade is None or facade.runtime is not runtime or facade.manager is not manager:
            facade = PhaseSwitchRuntimeFacade(runtime, manager)
            super().__setattr__("phase_switch_runtime", facade)
        return facade

    def _ensure_runtime_defaults(self) -> None:
        # Defensive fallback for partially constructed coordinator instances used
        # in tests or edge-case reload paths. Normal runtime should already have
        # these fields initialized in __init__.
        self._mode_runtime()
        session_runtime = self._session_runtime()
        if session_runtime.last_vehicle_connected and not session_runtime.vehicle_connection_initialized:
            session_runtime.vehicle_connection_initialized = True
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
        if not hasattr(self, "_phase_session_started_monotonic"):
            self._phase_session_started_monotonic = None
        if not hasattr(self, "_phase_recovery_warning"):
            self._phase_recovery_warning = None
        if not hasattr(self._phase_runtime(), "policy_failed_targets"):
            self._phase_runtime().policy_failed_targets = set()
        if not hasattr(self, "phase_switch_manager"):
            self.phase_switch_manager = PhaseSwitchManager()
            self.phase_switch_manager.last_result = self._phase_switch_last_result
            self.phase_switch_manager.last_block_reason = self._phase_switch_last_block_reason
            self.phase_switch_manager.last_target = self._phase_switch_last_target
            self.phase_switch_manager.state = self._phase_switch_state or "idle"
        self._phase_switch_runtime()
        if not hasattr(self, "_external_current_a"):
            self._external_current_a = None
        if not hasattr(self, "_pending_external_current_a"):
            self._pending_external_current_a = None
        if not hasattr(self, "_phase_switch_sleep"):
            self._phase_switch_sleep = asyncio.sleep
        if not hasattr(self, "task_runtime"):
            self.task_runtime = TaskRuntime(self)
        if not hasattr(self, "sensor_runtime"):
            self.sensor_runtime = SensorListenerRuntime(self)
            if not hasattr(self, "_sensor_unsubscribers"):
                self._sensor_unsubscribers = []
            if not hasattr(self, "_sensor_refresh_task"):
                self._sensor_refresh_task = None
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
        if not hasattr(self, "_phase_switch_task"):
            self._phase_switch_task = getattr(self, "_automatic_phase_switch_task", None)
        if not hasattr(self, "_phase_restore_task"):
            self._phase_restore_task = None
        if not hasattr(self, "rest_runtime"):
            self.rest_runtime = RestDiagnosticsRuntime(self)
            self.rest_runtime.initialize()
        if not hasattr(self, "storage_runtime"):
            self.storage_runtime = ChargingStateStorageRuntime(self)
            if (
                not hasattr(self, "_charging_state_store")
                and hasattr(self, "hass")
                and hasattr(self, "entry")
            ):
                self.storage_runtime.initialize()

    def _control_write_access(self) -> ControlWriteAccess:
        return resolve_control_write_access(
            control_mode=self.control_config.control_mode,
            phase_switch_in_progress=self._phase_switch_in_progress(),
        )

    def _resolve_startup_mode(self, merged_options: dict) -> ChargeMode:
        # Backward-compatible wrapper; implementation lives in core/mode.py.
        return resolve_startup_mode(merged_options, self.control_config)

    def _reset_runtime_mode_to_default(self) -> None:
        merged = {**getattr(self.entry, "data", {}), **getattr(self.entry, "options", {})}
        self.mode_runtime.reset_to_default(
            resolve_startup_mode(merged, self.control_config),
            self.control_config.solar_control_strategy,
        )
        self.controller.reset_solar_state()

    async def async_setup(self) -> None:
        await self._async_restore_charging_enabled_state()
        self._setup_sensor_listeners()
        await self._async_setup_rest_diagnostics()
        await self.client.connect()
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    async def async_shutdown(self) -> None:
        await self.task_runtime.cancel_task_attr("_keepalive_task")
        await self.sensor_runtime.shutdown()
        await self.task_runtime.cancel_task_attr("_phase_switch_task")
        await self.task_runtime.cancel_task_attr("_phase_restore_task")
        self.rest_runtime.shutdown()
        await self.client.close()

    @property
    def rest_diagnostics_enabled(self) -> bool:
        self._ensure_runtime_defaults()
        return self.rest_runtime.enabled

    async def _async_setup_rest_diagnostics(self) -> None:
        self._ensure_runtime_defaults()
        await self.rest_runtime.setup()

    async def async_soft_reset_charger(self) -> None:
        """Request an explicit REST restart/reboot of the charger."""
        self._ensure_runtime_defaults()
        _LOGGER.warning("User requested REST restart for Webasto/Ampure Unite charger")
        await self.rest_runtime.restart_charger()

    async def _async_refresh_rest_diagnostics_if_needed(self) -> None:
        self._ensure_runtime_defaults()
        await self.rest_runtime.refresh_if_needed()

    def _rest_diagnostics_snapshot(self):
        self._ensure_runtime_defaults()
        return self.rest_runtime.snapshot()

    @property
    def mode(self) -> ChargeMode:
        self._ensure_runtime_defaults()
        return self.mode_runtime.mode

    @property
    def active_solar_strategy(self) -> SolarControlStrategy:
        self._ensure_runtime_defaults()
        return self.mode_runtime.resolve_active_solar_strategy(self.control_config.solar_control_strategy)

    @property
    def effective_mode(self) -> ChargeMode:
        self._ensure_runtime_defaults()
        return self.mode_runtime.effective_mode()

    @property
    def solar_until_unplug_active(self) -> bool:
        self._ensure_runtime_defaults()
        return self.mode_runtime.solar_until_unplug_active

    @property
    def fixed_current_until_unplug_active(self) -> bool:
        self._ensure_runtime_defaults()
        return self.mode_runtime.fixed_current_until_unplug_active

    @property
    def charging_paused(self) -> bool:
        self._ensure_runtime_defaults()
        return self.mode_runtime.charging_paused

    @property
    def charging_enabled(self) -> bool:
        return not self.charging_paused

    def set_mode(self, mode: ChargeMode, solar_strategy: SolarControlStrategy | None = None) -> None:
        self._ensure_runtime_defaults()
        should_reset_solar = self.mode_runtime.set_mode(
            mode,
            default_solar_strategy=self.control_config.solar_control_strategy,
            solar_strategy=solar_strategy,
        )
        if should_reset_solar:
            self.controller.reset_solar_state()

    def pause_charging(self) -> None:
        self._ensure_runtime_defaults()
        # Pause is current-control state only. The Unite does not expose a
        # separate session-stop register in this integration.
        self.mode_runtime.pause()
        self.controller.reset_solar_state()

    def resume_charging(self) -> None:
        self._ensure_runtime_defaults()
        self.mode_runtime.resume()

    async def async_set_charging_enabled(self, enabled: bool) -> None:
        if enabled:
            self.resume_charging()
        else:
            self.pause_charging()
        await self.storage_runtime.save_charging_enabled(enabled)
        if self.control_config.control_mode == ControlMode.EXTERNAL_CONTROLLER:
            current_a = self._external_current_a or self.control_config.min_current_a
            await self.async_set_external_current_limit(current_a if enabled else 0.0)

    async def _async_restore_charging_enabled_state(self) -> None:
        self._ensure_runtime_defaults()
        charging_enabled = await self.storage_runtime.restore_charging_enabled()
        self.mode_runtime.charging_paused = not charging_enabled

    def set_solar_until_unplug(self, enabled: bool) -> None:
        self._ensure_runtime_defaults()
        self.mode_runtime.set_solar_until_unplug(enabled)
        self.controller.reset_solar_state()

    def set_fixed_current_until_unplug(self, enabled: bool) -> None:
        self._ensure_runtime_defaults()
        self.mode_runtime.set_fixed_current_until_unplug(enabled)
        self.controller.reset_solar_state()

    def set_max_current(self, current_a: float) -> None:
        self.control_config.max_current_a = self._validate_max_current(current_a)

    def set_user_limit(self, current_a: float) -> None:
        # Backward-compatible service alias.
        self.set_max_current(current_a)

    def _create_background_task(self, coro) -> asyncio.Task:
        create_task = getattr(self.hass, "async_create_task", None) or asyncio.create_task
        return create_task(coro)

    def set_fixed_current(self, current_a: float) -> None:
        self.control_config.fixed_current_a = self._validate_runtime_current(current_a, "Fixed Current")

    async def async_set_external_current_limit(self, current_a: float) -> None:
        self._ensure_runtime_defaults()
        current = self._validate_external_current(current_a)
        if current > 0:
            self._external_current_a = current
        if self._phase_switch_in_progress():
            self._pending_external_current_a = current
            self._mark_control_write_blocked("phase_switch_in_progress")
            return
        await self._enqueue_external_current_limit(current)

    async def _flush_pending_external_current_limit(self) -> None:
        pending_current = getattr(self, "_pending_external_current_a", None)
        if pending_current is None:
            return
        if self.control_config.control_mode != ControlMode.EXTERNAL_CONTROLLER:
            self._pending_external_current_a = None
            return
        self._pending_external_current_a = None
        await self._enqueue_external_current_limit(pending_current)

    def _mark_control_write_blocked(self, reason: str) -> None:
        write_runtime_state = getattr(getattr(self, "write_runtime", None), "state", None)
        if write_runtime_state is not None:
            write_runtime_state.last_control_write_blocked_reason = reason
            return
        if hasattr(getattr(self, "write_runtime", None), "last_control_write_blocked_reason"):
            self.write_runtime.last_control_write_blocked_reason = reason

    def _clear_control_write_blocked(self, reason: str | None = None) -> None:
        write_runtime_state = getattr(getattr(self, "write_runtime", None), "state", None)
        if write_runtime_state is not None:
            if reason is None or write_runtime_state.last_control_write_blocked_reason == reason:
                write_runtime_state.last_control_write_blocked_reason = None
            return
        write_runtime = getattr(self, "write_runtime", None)
        if hasattr(write_runtime, "last_control_write_blocked_reason"):
            if reason is None or write_runtime.last_control_write_blocked_reason == reason:
                write_runtime.last_control_write_blocked_reason = None

    async def _enqueue_external_current_limit(self, current_a: float) -> None:
        if current_a > 0 and self._current_snapshot_vehicle_connected() is False:
            self._mark_control_write_blocked(BLOCK_REASON_VEHICLE_NOT_CONNECTED)
            return
        await self.write_runtime.write_current_now(current_a, reason="external_controller")

    def _current_snapshot_vehicle_connected(self) -> bool | None:
        current_snapshot = getattr(self, "data", None)
        wallbox = getattr(current_snapshot, "wallbox", None)
        return getattr(wallbox, "vehicle_connected", None)

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
        self._ensure_runtime_defaults()
        await self.sensor_runtime.debounced_refresh()

    def _schedule_sensor_refresh(self) -> None:
        self._ensure_runtime_defaults()
        self.sensor_runtime.schedule_refresh()

    async def async_trigger_reconnect(self) -> None:
        await self.client.reconnect()
        await self.async_request_refresh()

    def _configured_installed_phases(self) -> str:
        entry = getattr(self, "entry", None)
        return getattr(entry, "data", {}).get(CONF_INSTALLED_PHASES, "3p")

    def _configured_phase_count(self) -> int:
        return 1 if self._configured_installed_phases() == "1p" else 3

    def _allows_control_writes(self) -> bool:
        return self._control_write_access().automatic_control_writes

    def _allows_current_writes(self) -> bool:
        return self._control_write_access().current_writes

    def _control_write_blocked_reason(self) -> str:
        return self._control_write_access().blocked_reason or "monitoring_only"

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
        self._ensure_runtime_defaults()
        self.sensor_runtime.setup_listeners()
