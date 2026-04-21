
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic
from datetime import timedelta

from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_CONTROL_MODE,
    CONF_DLB_GRID_POWER_SENSOR,
    CONF_COMM_TIMEOUT,
    CONF_DLB_INPUT_MODEL,
    CONF_DLB_SENSOR_SCOPE,
    CONF_DLB_L1_SENSOR,
    CONF_DLB_L2_SENSOR,
    CONF_DLB_L3_SENSOR,
    CONF_INSTALLED_PHASES,
    CONF_KEEPALIVE_INTERVAL,
    CONF_KEEPALIVE_MODE,
    CONF_MAIN_FUSE,
    CONF_MAX_CURRENT,
    CONF_MIN_CURRENT,
    CONF_POLLING_INTERVAL,
    CONF_PV_CONTROL_STRATEGY,
    CONF_PV_UNTIL_UNPLUG_STRATEGY,
    CONF_FIXED_CURRENT,
    CONF_PV_INPUT_MODEL,
    CONF_PV_MIN_CURRENT,
    CONF_PV_START_THRESHOLD,
    CONF_PV_STOP_THRESHOLD,
    CONF_PV_START_DELAY,
    CONF_PV_STOP_DELAY,
    CONF_PV_MIN_RUNTIME,
    CONF_PV_MIN_PAUSE,
    CONF_PV_PHASE_SWITCHING_MODE,
    CONF_PV_PHASE_SWITCHING_HYSTERESIS,
    CONF_PV_PHASE_SWITCHING_MIN_INTERVAL,
    CONF_PV_PHASE_SWITCHING_MAX_PER_SESSION,
    CONF_PV_SURPLUS_SENSOR,
    CONF_RETRIES,
    CONF_SAFE_CURRENT,
    CONF_SAFETY_MARGIN,
    CONF_STARTUP_CHARGE_MODE,
    CONF_TIMEOUT,
    CONF_UNIT_ID,
    CONF_USER_LIMIT,
    DEFAULT_CONTROL_MODE,
    DEFAULT_KEEPALIVE_INTERVAL_S,
    DEFAULT_MAIN_FUSE_A,
    DEFAULT_MAX_CURRENT_A,
    DEFAULT_MIN_CURRENT_A,
    DEFAULT_NAME,
    DEFAULT_POLL_INTERVAL_S,
    DEFAULT_PORT,
    DEFAULT_FIXED_CURRENT_A,
    DEFAULT_PV_START_DELAY_S,
    DEFAULT_PV_STOP_DELAY_S,
    DEFAULT_PV_MIN_RUNTIME_S,
    DEFAULT_PV_MIN_PAUSE_S,
    DEFAULT_PV_PHASE_SWITCHING_MODE,
    DEFAULT_PV_PHASE_SWITCHING_HYSTERESIS_W,
    DEFAULT_PV_PHASE_SWITCHING_MIN_INTERVAL_S,
    DEFAULT_PV_PHASE_SWITCHING_MAX_PER_SESSION,
    DEFAULT_RETRIES,
    DEFAULT_SAFE_CURRENT_A,
    DEFAULT_SAFETY_MARGIN_A,
    DEFAULT_STARTUP_CHARGE_MODE,
    DEFAULT_TIMEOUT_S,
    DEFAULT_UNIT_ID,
    DEFAULT_USER_LIMIT_A,
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
    KeepaliveMode,
    PhaseCurrents,
    PvControlStrategy,
    PvInputModel,
    PvOverrideStrategy,
    PvPhaseSwitchingMode,
    RuntimeSnapshot,
    normalize_pv_control_strategy,
    normalize_pv_override_strategy,
)
from .registers import (
    COMM_TIMEOUT_S,
    LIFE_BIT,
    PHASE_SWITCH_MODE,
    SAFE_CURRENT_A,
    SET_CHARGE_CURRENT_A,
)
from .sensor_adapter import HaSensorAdapter
from .wallbox_reader import WallboxReader
from .write_queue import QueuedWrite, WritePriority, WriteQueueManager

_LOGGER = logging.getLogger(__name__)

PHASE_MISMATCH_MAX_RETRIES_PER_SESSION = 2
PHASE_MISMATCH_RETRY_COOLDOWN_S = 120.0
PHASE_MISMATCH_BOUNCE_STABLE_POLLS = 2
PHASE_MISMATCH_BOUNCE_COOLDOWN_S = 120.0
STARTUP_STABILIZATION_MIN_POLLS = 3
STARTUP_STABILIZATION_MIN_SECONDS = 30.0
STARTUP_MISMATCH_STABLE_POLLS = 3
STARTUP_CHARGING_SETTLE_MIN_POLLS = 2
STARTUP_CHARGING_SETTLE_MIN_SECONDS = 10.0
MIN_PHASE_SWITCH_FIRMWARE = (3, 187, 0)


class PhaseActionReason(StrEnum):
    PV_AUTOMATIC_SWITCH = "pv_automatic_switch"
    NON_PV_RECONCILE = "non_pv_reconcile"
    STARTUP_RESTORE = "startup_restore"
    MISMATCH_RETRY = "mismatch_retry"
    MISMATCH_BOUNCE_RECOVERY = "mismatch_bounce_recovery"


class PhaseBounceStage(StrEnum):
    WRITE_1P = "write_1p"
    WAIT_1P = "wait_1p"
    WRITE_TARGET = "write_target"
    VERIFY = "verify"


class PhaseRecoveryOwner(StrEnum):
    STARTUP = "startup"
    NON_PV = "non_pv"
    PV = "pv"


@dataclass(slots=True)
class PendingPhaseAction:
    target: int | None = None
    force_write: bool = False
    reason: str | None = None

    def clear(self) -> None:
        self.target = None
        self.force_write = False
        self.reason = None

    def start(self, target: int, *, force_write: bool, reason: PhaseActionReason | str) -> None:
        self.target = target
        self.force_write = force_write
        self.reason = str(reason)


@dataclass(slots=True)
class PhaseRecoveryState:
    retry_count: int = 0
    last_retry_monotonic: float = 0.0
    target: int | None = None
    unverified: bool = False
    observation_key: tuple[int, int, int] | None = None
    observation_count: int = 0
    bounce_used: bool = False
    bounce_stage: str | None = None
    bounce_target: int | None = None
    bounce_verify_count: int = 0
    last_bounce_monotonic: float = 0.0

    def reset(self) -> None:
        self.retry_count = 0
        self.last_retry_monotonic = 0.0
        self.target = None
        self.unverified = False
        self.observation_key = None
        self.observation_count = 0
        self.bounce_used = False
        self.bounce_stage = None
        self.bounce_target = None
        self.bounce_verify_count = 0
        self.last_bounce_monotonic = 0.0


class WebastoUniteCoordinator(DataUpdateCoordinator[RuntimeSnapshot]):
    def __init__(self, hass, entry) -> None:
        self.hass = hass
        self.entry = entry
        self._mode = ChargeMode.NORMAL
        self._charging_paused = False
        self._pv_until_unplug_active = False
        self._fixed_current_until_unplug_active = False
        self._last_vehicle_connected = False
        self._pending_phase_action = PendingPhaseAction()
        self._phase_recovery = PhaseRecoveryState()
        self._startup_started_monotonic = monotonic()
        self._startup_refresh_count = 0
        self._startup_consistency_checked = False
        self._startup_mismatch_observations: list[tuple[int, int, int | None]] = []
        self._startup_charging_active_since_monotonic: float | None = None
        self._startup_charging_active_polls = 0
        self._last_phase_switch_monotonic = 0.0
        self._phase_switch_up_condition_since: float | None = None
        self._phase_switch_count_this_session = 0
        self._phase_switch_decision: str | None = None
        self._phase_switch_firmware_warning_emitted = False
        self._sensor_unsubscribers = []
        self._last_keepalive_sent_monotonic = 0.0
        self._keepalive_started_monotonic = monotonic()
        self._keepalive_sent_count = 0
        self._keepalive_write_failures = 0
        self._keepalive_task: asyncio.Task | None = None
        self._flush_lock = asyncio.Lock()
        entry_id = getattr(entry, "entry_id", "default")
        self._charging_state_store = Store(hass, 1, f"{DOMAIN}.{entry_id}.{STORAGE_KEY_CHARGING_STATE}")

        merged = {**entry.data, **entry.options}
        self.control_config = ControlConfig(
            polling_interval_s=float(merged.get(CONF_POLLING_INTERVAL, DEFAULT_POLL_INTERVAL_S)),
            timeout_s=float(merged.get(CONF_TIMEOUT, DEFAULT_TIMEOUT_S)),
            retries=int(merged.get(CONF_RETRIES, DEFAULT_RETRIES)),
            control_mode=ControlMode(merged.get(CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE)),
            keepalive_mode=KeepaliveMode(merged.get(CONF_KEEPALIVE_MODE, KeepaliveMode.AUTO.value)),
            keepalive_interval_s=float(merged.get(CONF_KEEPALIVE_INTERVAL, DEFAULT_KEEPALIVE_INTERVAL_S)),
            safe_current_a=float(merged.get(CONF_SAFE_CURRENT, DEFAULT_SAFE_CURRENT_A)),
            min_current_a=float(merged.get(CONF_MIN_CURRENT, DEFAULT_MIN_CURRENT_A)),
            max_current_a=float(merged.get(CONF_MAX_CURRENT, DEFAULT_MAX_CURRENT_A)),
            user_limit_a=float(merged.get(CONF_USER_LIMIT, DEFAULT_USER_LIMIT_A)),
            main_fuse_a=float(merged.get(CONF_MAIN_FUSE, DEFAULT_MAIN_FUSE_A)),
            safety_margin_a=float(merged.get(CONF_SAFETY_MARGIN, DEFAULT_SAFETY_MARGIN_A)),
            dlb_input_model=DlbInputModel(merged.get(CONF_DLB_INPUT_MODEL, DlbInputModel.DISABLED.value)),
            dlb_sensor_scope=DlbSensorScope(
                merged.get(CONF_DLB_SENSOR_SCOPE, DlbSensorScope.LOAD_EXCLUDING_CHARGER.value)
            ),
            pv_input_model=PvInputModel(merged.get(CONF_PV_INPUT_MODEL, PvInputModel.GRID_POWER_DERIVED.value)),
            pv_control_strategy=normalize_pv_control_strategy(
                merged.get(CONF_PV_CONTROL_STRATEGY, PvControlStrategy.DISABLED.value)
            ),
            pv_until_unplug_strategy=normalize_pv_override_strategy(
                merged.get(CONF_PV_UNTIL_UNPLUG_STRATEGY, PvOverrideStrategy.INHERIT.value)
            ),
            pv_start_threshold_w=float(merged.get(CONF_PV_START_THRESHOLD, 1800.0)),
            pv_stop_threshold_w=float(merged.get(CONF_PV_STOP_THRESHOLD, 1200.0)),
            pv_start_delay_s=float(merged.get(CONF_PV_START_DELAY, DEFAULT_PV_START_DELAY_S)),
            pv_stop_delay_s=float(merged.get(CONF_PV_STOP_DELAY, DEFAULT_PV_STOP_DELAY_S)),
            pv_min_runtime_s=float(merged.get(CONF_PV_MIN_RUNTIME, DEFAULT_PV_MIN_RUNTIME_S)),
            pv_min_pause_s=float(merged.get(CONF_PV_MIN_PAUSE, DEFAULT_PV_MIN_PAUSE_S)),
            pv_min_current_a=float(merged.get(CONF_PV_MIN_CURRENT, 6.0)),
            pv_phase_switching_mode=PvPhaseSwitchingMode(
                merged.get(CONF_PV_PHASE_SWITCHING_MODE, DEFAULT_PV_PHASE_SWITCHING_MODE)
            ),
            pv_phase_switching_hysteresis_w=float(
                merged.get(CONF_PV_PHASE_SWITCHING_HYSTERESIS, DEFAULT_PV_PHASE_SWITCHING_HYSTERESIS_W)
            ),
            pv_phase_switching_min_interval_s=float(
                merged.get(CONF_PV_PHASE_SWITCHING_MIN_INTERVAL, DEFAULT_PV_PHASE_SWITCHING_MIN_INTERVAL_S)
            ),
            pv_phase_switching_max_per_session=int(
                merged.get(CONF_PV_PHASE_SWITCHING_MAX_PER_SESSION, DEFAULT_PV_PHASE_SWITCHING_MAX_PER_SESSION)
            ),
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

    def _resolve_startup_mode(self, merged_options: dict) -> ChargeMode:
        try:
            mode = ChargeMode(merged_options.get(CONF_STARTUP_CHARGE_MODE, DEFAULT_STARTUP_CHARGE_MODE))
        except ValueError:
            return ChargeMode.NORMAL
        if mode == ChargeMode.PV and self.control_config.pv_control_strategy == PvControlStrategy.DISABLED:
            return ChargeMode.NORMAL
        return mode

    async def async_setup(self) -> None:
        await self._async_restore_charging_enabled_state()
        self._setup_sensor_listeners()
        await self.client.connect()
        await self._sync_static_registers()
        if self._allows_keepalive():
            self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    async def async_shutdown(self) -> None:
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except asyncio.CancelledError:
                pass
            self._keepalive_task = None
        for unsub in self._sensor_unsubscribers:
            unsub()
        self._sensor_unsubscribers.clear()
        await self.client.close()

    @property
    def mode(self) -> ChargeMode:
        return self._mode

    @property
    def effective_mode(self) -> ChargeMode:
        if self._mode == ChargeMode.OFF or self._charging_paused:
            return ChargeMode.OFF
        if self._fixed_current_until_unplug_active:
            return ChargeMode.FIXED_CURRENT
        if self._pv_until_unplug_active:
            return ChargeMode.PV
        return self._mode

    @property
    def pv_until_unplug_active(self) -> bool:
        return self._pv_until_unplug_active

    @property
    def fixed_current_until_unplug_active(self) -> bool:
        return self._fixed_current_until_unplug_active

    @property
    def charging_paused(self) -> bool:
        return self._charging_paused

    @property
    def charging_enabled(self) -> bool:
        return not self._charging_paused

    def _ensure_phase_runtime_state(self) -> None:
        if not hasattr(self, "_pending_phase_action"):
            self._pending_phase_action = PendingPhaseAction()
        if not hasattr(self, "_phase_recovery"):
            self._phase_recovery = PhaseRecoveryState()

    @property
    def _pending_phase_switch_target(self) -> int | None:
        self._ensure_phase_runtime_state()
        return self._pending_phase_action.target

    @_pending_phase_switch_target.setter
    def _pending_phase_switch_target(self, value: int | None) -> None:
        self._ensure_phase_runtime_state()
        self._pending_phase_action.target = value

    @property
    def _pending_phase_switch_force_write(self) -> bool:
        self._ensure_phase_runtime_state()
        return self._pending_phase_action.force_write

    @_pending_phase_switch_force_write.setter
    def _pending_phase_switch_force_write(self, value: bool) -> None:
        self._ensure_phase_runtime_state()
        self._pending_phase_action.force_write = bool(value)

    @property
    def _pending_phase_switch_reason(self) -> str | None:
        self._ensure_phase_runtime_state()
        return self._pending_phase_action.reason

    @_pending_phase_switch_reason.setter
    def _pending_phase_switch_reason(self, value: str | None) -> None:
        self._ensure_phase_runtime_state()
        self._pending_phase_action.reason = value

    @property
    def _phase_mismatch_retry_count(self) -> int:
        self._ensure_phase_runtime_state()
        return self._phase_recovery.retry_count

    @_phase_mismatch_retry_count.setter
    def _phase_mismatch_retry_count(self, value: int) -> None:
        self._ensure_phase_runtime_state()
        self._phase_recovery.retry_count = value

    @property
    def _last_phase_mismatch_retry_monotonic(self) -> float:
        self._ensure_phase_runtime_state()
        return self._phase_recovery.last_retry_monotonic

    @_last_phase_mismatch_retry_monotonic.setter
    def _last_phase_mismatch_retry_monotonic(self, value: float) -> None:
        self._ensure_phase_runtime_state()
        self._phase_recovery.last_retry_monotonic = value

    @property
    def _phase_mismatch_target(self) -> int | None:
        self._ensure_phase_runtime_state()
        return self._phase_recovery.target

    @_phase_mismatch_target.setter
    def _phase_mismatch_target(self, value: int | None) -> None:
        self._ensure_phase_runtime_state()
        self._phase_recovery.target = value

    @property
    def _phase_mismatch_unverified(self) -> bool:
        self._ensure_phase_runtime_state()
        return self._phase_recovery.unverified

    @_phase_mismatch_unverified.setter
    def _phase_mismatch_unverified(self, value: bool) -> None:
        self._ensure_phase_runtime_state()
        self._phase_recovery.unverified = bool(value)

    @property
    def _phase_mismatch_observation_key(self) -> tuple[int, int, int] | None:
        self._ensure_phase_runtime_state()
        return self._phase_recovery.observation_key

    @_phase_mismatch_observation_key.setter
    def _phase_mismatch_observation_key(self, value: tuple[int, int, int] | None) -> None:
        self._ensure_phase_runtime_state()
        self._phase_recovery.observation_key = value

    @property
    def _phase_mismatch_observation_count(self) -> int:
        self._ensure_phase_runtime_state()
        return self._phase_recovery.observation_count

    @_phase_mismatch_observation_count.setter
    def _phase_mismatch_observation_count(self, value: int) -> None:
        self._ensure_phase_runtime_state()
        self._phase_recovery.observation_count = value

    @property
    def _phase_mismatch_bounce_used(self) -> bool:
        self._ensure_phase_runtime_state()
        return self._phase_recovery.bounce_used

    @_phase_mismatch_bounce_used.setter
    def _phase_mismatch_bounce_used(self, value: bool) -> None:
        self._ensure_phase_runtime_state()
        self._phase_recovery.bounce_used = bool(value)

    @property
    def _phase_mismatch_bounce_stage(self) -> str | None:
        self._ensure_phase_runtime_state()
        return self._phase_recovery.bounce_stage

    @_phase_mismatch_bounce_stage.setter
    def _phase_mismatch_bounce_stage(self, value: str | None) -> None:
        self._ensure_phase_runtime_state()
        self._phase_recovery.bounce_stage = value

    @property
    def _phase_mismatch_bounce_target(self) -> int | None:
        self._ensure_phase_runtime_state()
        return self._phase_recovery.bounce_target

    @_phase_mismatch_bounce_target.setter
    def _phase_mismatch_bounce_target(self, value: int | None) -> None:
        self._ensure_phase_runtime_state()
        self._phase_recovery.bounce_target = value

    @property
    def _phase_mismatch_bounce_verify_count(self) -> int:
        self._ensure_phase_runtime_state()
        return self._phase_recovery.bounce_verify_count

    @_phase_mismatch_bounce_verify_count.setter
    def _phase_mismatch_bounce_verify_count(self, value: int) -> None:
        self._ensure_phase_runtime_state()
        self._phase_recovery.bounce_verify_count = value

    @property
    def _last_phase_mismatch_bounce_monotonic(self) -> float:
        self._ensure_phase_runtime_state()
        return self._phase_recovery.last_bounce_monotonic

    @_last_phase_mismatch_bounce_monotonic.setter
    def _last_phase_mismatch_bounce_monotonic(self, value: float) -> None:
        self._ensure_phase_runtime_state()
        self._phase_recovery.last_bounce_monotonic = value

    def set_mode(self, mode: ChargeMode) -> None:
        self._mode = mode
        self._pv_until_unplug_active = False
        self._fixed_current_until_unplug_active = False
        if mode != ChargeMode.PV:
            self._reset_pv_runtime_state()

    def pause_charging(self) -> None:
        self._charging_paused = True
        self._reset_pv_runtime_state()

    def resume_charging(self) -> None:
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

    def set_pv_until_unplug(self, enabled: bool) -> None:
        self._pv_until_unplug_active = enabled
        if enabled:
            self._fixed_current_until_unplug_active = False
        self._reset_pv_runtime_state()

    def set_fixed_current_until_unplug(self, enabled: bool) -> None:
        self._fixed_current_until_unplug_active = enabled
        if enabled:
            self._pv_until_unplug_active = False
        self._reset_pv_runtime_state()

    def set_user_limit(self, current_a: float) -> None:
        self.control_config.user_limit_a = self._validate_runtime_current(current_a, "Current Limit")

    def set_fixed_current(self, current_a: float) -> None:
        self.control_config.fixed_current_a = self._validate_runtime_current(current_a, "Fixed Current")

    def _validate_runtime_current(self, current_a: float, label: str) -> float:
        current = float(current_a)
        if not self.control_config.min_current_a <= current <= self.control_config.max_current_a:
            raise ValueError(
                f"{label} must be between {self.control_config.min_current_a:g} A "
                f"and {self.control_config.max_current_a:g} A"
            )
        return current

    async def async_set_phase_switch_mode(self, phases: int) -> None:
        if phases not in (1, 3):
            raise ValueError("Phase Switch Mode must be 1 or 3 phases")
        if self.control_config.pv_phase_switching_mode == PvPhaseSwitchingMode.DISABLED:
            raise ValueError("Phase switching is disabled in the integration settings")
        if self.data is None:
            raise ValueError("Phase switching is only allowed after charger state is available")
        if not self._phase_switch_runtime_allowed(self.data.wallbox):
            raise ValueError("Phase switching is not supported by the detected charger firmware")
        if self.data.wallbox.phase_switch_mode_raw not in (0, 1):
            raise ValueError("Phase switch register 405 is unavailable or returned an unsupported value")
        if self.data.wallbox.charging_active:
            raise ValueError("Phase switching is only allowed while charging is inactive")
        self._reset_phase_mismatch_state()
        await self.write_queue.enqueue(
            QueuedWrite("phase_switch_mode", PHASE_SWITCH_MODE, 0 if phases == 1 else 1, WritePriority.CONTROL)
        )
        await self._flush_write_queue()
        await self.async_request_refresh()

    async def async_trigger_reconnect(self) -> None:
        await self.client.reconnect()
        await self._sync_static_registers()
        await self.async_request_refresh()

    async def _async_update_data(self) -> RuntimeSnapshot:
        try:
            wallbox = await self.wallbox_reader.read_wallbox_state(self._configured_installed_phases())
            if (
                (self._pv_until_unplug_active or self._fixed_current_until_unplug_active)
                and self._last_vehicle_connected
                and not wallbox.vehicle_connected
            ):
                self._pv_until_unplug_active = False
                self._fixed_current_until_unplug_active = False
                self._reset_pv_runtime_state()
                self._phase_switch_count_this_session = 0
                self._clear_pending_phase_action()
                self._phase_switch_up_condition_since = None
            if not self._last_vehicle_connected and wallbox.vehicle_connected:
                self._phase_switch_count_this_session = 0
                self._phase_switch_up_condition_since = None
                self._clear_pending_phase_action()
                self._reset_phase_mismatch_state()
                self.controller.reset_current_write_state()
            self._last_vehicle_connected = wallbox.vehicle_connected
            sensors = self._read_sensor_snapshot()
            pv_surplus_w = self.controller._resolve_surplus_power(sensors)
            pv_strategy = self.controller.resolve_effective_pv_strategy(
                self.control_config.pv_control_strategy,
                self.control_config.pv_until_unplug_strategy,
                self._pv_until_unplug_active,
            )
            decision = self.controller.evaluate(self.effective_mode, wallbox, sensors, pv_strategy)
            if self._should_defer_startup_safe_current_fallback_write(
                wallbox=wallbox,
                sensors=sensors,
                decision=decision,
            ):
                decision.should_write = False
            recovery_owner = self._phase_recovery_owner()
            startup_phase_handled = await self._enqueue_startup_consistency_recovery_if_needed(wallbox, sensors)
            phase_switch_handled = False
            if not startup_phase_handled:
                if recovery_owner == PhaseRecoveryOwner.NON_PV:
                    reconcile_required = self._non_pv_phase_reconcile_required(wallbox)
                    if reconcile_required:
                        phase_switch_handled = await self._enqueue_non_pv_phase_reconcile_if_needed(wallbox)
                    if reconcile_required:
                        phase_switch_handled = True
                elif recovery_owner == PhaseRecoveryOwner.PV:
                    phase_switch_handled = await self._enqueue_pv_phase_switch_if_needed(wallbox, sensors)
            if not startup_phase_handled and not phase_switch_handled:
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
                pv_until_unplug_active=self._pv_until_unplug_active,
                fixed_current_until_unplug_active=self._fixed_current_until_unplug_active,
                keepalive_age_s=keepalive_age_s,
                keepalive_interval_s=self.control_config.keepalive_interval_s if self._allows_keepalive() else None,
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
                pv_surplus_w=pv_surplus_w,
                phase_switch_decision=self._phase_switch_decision,
                phase_switch_count=self._phase_switch_count_this_session,
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

    def _phase_recovery_owner(self) -> PhaseRecoveryOwner | None:
        if not getattr(self, "_startup_consistency_checked", True):
            return PhaseRecoveryOwner.STARTUP
        if self.effective_mode in (ChargeMode.NORMAL, ChargeMode.FIXED_CURRENT):
            return PhaseRecoveryOwner.NON_PV
        if self.effective_mode == ChargeMode.PV:
            return PhaseRecoveryOwner.PV
        return None

    def _normalized_phase_switch_firmware(self, wallbox) -> tuple[int, int, int] | tuple[()] | None:
        raw = (getattr(wallbox, "firmware_version", None) or "").strip()
        if not raw:
            return None
        primary = raw.split("-", 1)[0].strip().lstrip("vV")
        parts = primary.split(".")
        if len(parts) != 3:
            return ()
        try:
            return tuple(int(part) for part in parts)  # type: ignore[return-value]
        except ValueError:
            return ()

    def _phase_switch_firmware_supported(self, wallbox) -> bool:
        version = self._normalized_phase_switch_firmware(wallbox)
        if version is None:
            return True
        if version == ():
            return False
        return version >= MIN_PHASE_SWITCH_FIRMWARE

    def _phase_switch_runtime_allowed(self, wallbox) -> bool:
        return self._phase_switch_firmware_supported(wallbox)

    def _mark_phase_switch_firmware_unsupported(self, wallbox) -> None:
        self._clear_pending_phase_action()
        self._reset_phase_mismatch_state()
        self._phase_switch_up_condition_since = None
        self._phase_switch_decision = "phase_switch_firmware_unsupported"
        if not getattr(self, "_phase_switch_firmware_warning_emitted", False):
            firmware = getattr(wallbox, "firmware_version", None) or "unknown"
            _LOGGER.warning(
                "Webasto Unite phase switching is disabled at runtime because firmware %s does not support reliable Modbus phase switching",
                firmware,
            )
            self._phase_switch_firmware_warning_emitted = True

    def _build_capabilities(self, wallbox: WallboxState) -> dict[str, str]:
        ev_max_state = CapabilityState.CONFIRMED if wallbox.ev_max_current_a is not None else CapabilityState.OPTIONAL_ABSENT
        return {
            "core_measurements": CapabilityState.CONFIRMED.value,
            "phase_count_404": CapabilityState.CONFIRMED.value,
            "phase_switch_405": CapabilityState.UNCONFIRMED.value,
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
        if self.effective_mode == ChargeMode.OFF and self._charging_paused:
            return "paused"
        if self.effective_mode == ChargeMode.OFF:
            return "off"
        if decision.fallback_active:
            return "fallback"
        if self.effective_mode == ChargeMode.FIXED_CURRENT and self._fixed_current_until_unplug_active:
            return "fixed_current_until_unplug"
        if self.effective_mode == ChargeMode.FIXED_CURRENT:
            return "fixed_current"
        if (
            self.effective_mode == ChargeMode.PV
            and self._pv_until_unplug_active
            and decision.reason == ControlReason.BELOW_MIN_CURRENT
        ):
            return "waiting_for_surplus"
        if (
            self.effective_mode == ChargeMode.PV
            and self._pv_until_unplug_active
            and WallboxController.resolve_effective_pv_strategy(
                self.control_config.pv_control_strategy,
                self.control_config.pv_until_unplug_strategy,
                True,
            )
            == PvControlStrategy.MIN_PLUS_SURPLUS
        ):
            return "pv_until_unplug"
        if self.effective_mode == ChargeMode.PV and self._pv_until_unplug_active:
            return "pv_until_unplug"
        if (
            self.effective_mode == ChargeMode.PV
            and decision.reason == ControlReason.BELOW_MIN_CURRENT
        ):
            return "waiting_for_surplus"
        if (
            self.effective_mode == ChargeMode.PV
            and decision.reason == ControlReason.SENSOR_UNAVAILABLE
        ):
            return "fallback"
        if self.effective_mode == ChargeMode.PV and self.control_config.pv_control_strategy == PvControlStrategy.MIN_PLUS_SURPLUS:
            return "min_plus_surplus"
        if decision.dominant_limit_reason == ControlReason.DLB_LIMITED:
            return "dlb_limited"
        if self.effective_mode == ChargeMode.PV:
            return "pv"
        return "normal"

    def _read_sensor_snapshot(self) -> HaSensorSnapshot:
        options = self.entry.options
        snapshot = HaSensorSnapshot(valid=True)

        if self.control_config.dlb_input_model == DlbInputModel.PHASE_CURRENTS:
            snapshot.phase_currents = PhaseCurrents(
                l1=self.sensor_adapter.state_as_current_a(options.get(CONF_DLB_L1_SENSOR)),
                l2=self.sensor_adapter.state_as_current_a(options.get(CONF_DLB_L2_SENSOR)),
                l3=self.sensor_adapter.state_as_current_a(options.get(CONF_DLB_L3_SENSOR)),
            )
        else:
            snapshot.grid_power_w = self.sensor_adapter.state_as_power_w(options.get(CONF_DLB_GRID_POWER_SENSOR))

        if self.control_config.pv_input_model == PvInputModel.SURPLUS_SENSOR:
            snapshot.surplus_power_w = self.sensor_adapter.state_as_power_w(options.get(CONF_PV_SURPLUS_SENSOR))
        else:
            if snapshot.grid_power_w is None:
                snapshot.grid_power_w = self.sensor_adapter.state_as_power_w(options.get(CONF_DLB_GRID_POWER_SENSOR))

        if (
            self.control_config.dlb_input_model == DlbInputModel.PHASE_CURRENTS
            and all(v is None for v in (snapshot.phase_currents.l1, snapshot.phase_currents.l2, snapshot.phase_currents.l3))
        ):
            snapshot.valid = False
            snapshot.reason_invalid = "No DLB phase sensors available"

        if self.control_config.dlb_input_model == DlbInputModel.GRID_POWER and snapshot.grid_power_w is None:
            snapshot.valid = False
            snapshot.reason_invalid = "No DLB grid power sensor available"

        return snapshot

    def _configured_installed_phases(self) -> str:
        entry = getattr(self, "entry", None)
        return getattr(entry, "data", {}).get(CONF_INSTALLED_PHASES, "3p")

    def _configured_phase_count(self) -> int:
        return 1 if self._configured_installed_phases() == "1p" else 3

    def _reset_pv_runtime_state(self) -> None:
        controller = getattr(self, "controller", None)
        if controller is not None:
            controller.reset_pv_state()

    def _reset_phase_mismatch_state(self) -> None:
        self._ensure_phase_runtime_state()
        self._phase_recovery.reset()

    def _clear_pending_phase_action(self) -> None:
        self._ensure_phase_runtime_state()
        self._pending_phase_action.clear()

    def _start_pending_phase_action(
        self,
        target: int,
        *,
        force_write: bool,
        reason: PhaseActionReason | str,
    ) -> None:
        self._ensure_phase_runtime_state()
        self._pending_phase_action.start(target, force_write=force_write, reason=reason)

    def _has_active_phase_recovery_for_target(self, target_phases: int) -> bool:
        observation_key = getattr(self, "_phase_mismatch_observation_key", None)
        return (
            getattr(self, "_phase_mismatch_target", None) == target_phases
            or getattr(self, "_pending_phase_switch_target", None) == target_phases
            or (
                observation_key is not None
                and observation_key[0] == target_phases
                and getattr(self, "_phase_mismatch_observation_count", 0) > 0
            )
            or getattr(self, "_phase_mismatch_bounce_stage", None) is not None
        )

    def _requested_phase_mode(self, wallbox) -> int | None:
        if wallbox.phase_switch_mode_raw == 0:
            return 1
        if wallbox.phase_switch_mode_raw == 1:
            return 3
        return None

    def _observed_active_phases(self, wallbox) -> int | None:
        if wallbox.charging_active and wallbox.phases_in_use in (1, 3):
            return wallbox.phases_in_use
        return None

    def _non_pv_phase_reconcile_required(self, wallbox) -> bool:
        if self.effective_mode not in (ChargeMode.NORMAL, ChargeMode.FIXED_CURRENT):
            return False
        if getattr(self, "_phase_mismatch_unverified", False):
            return False

        target_phases = self._configured_phase_count()
        if target_phases not in (1, 3):
            return False

        requested_phases = self._requested_phase_mode(wallbox)
        observed_phases = self._observed_active_phases(wallbox)
        active_recovery = self._has_active_phase_recovery_for_target(target_phases)

        if not getattr(self, "_startup_consistency_checked", True) and not active_recovery and wallbox.charging_active:
            if observed_phases is None:
                return False
            if not self._startup_charging_settled(wallbox):
                return False
            if self._is_startup_observed_only_phase_mismatch(
                requested_phases=requested_phases,
                observed_phases=observed_phases,
                target_phases=target_phases,
            ):
                return False

        if requested_phases is None:
            return active_recovery
        if observed_phases == target_phases:
            return False
        if observed_phases is not None:
            if (
                not active_recovery
                and self._is_non_pv_observed_only_phase_mismatch(
                    requested_phases=requested_phases,
                    observed_phases=observed_phases,
                    target_phases=target_phases,
                )
            ):
                return False
            return True
        return requested_phases != target_phases

    def _startup_stabilization_ready(self) -> bool:
        poll_count = getattr(self, "_startup_refresh_count", 0)
        started = getattr(self, "_startup_started_monotonic", monotonic())
        return (
            poll_count >= STARTUP_STABILIZATION_MIN_POLLS
            and (monotonic() - started) >= STARTUP_STABILIZATION_MIN_SECONDS
        )

    def _startup_charging_settled(self, wallbox) -> bool:
        if not wallbox.charging_active:
            self._startup_charging_active_since_monotonic = None
            self._startup_charging_active_polls = 0
            return False

        now = monotonic()
        active_since = getattr(self, "_startup_charging_active_since_monotonic", None)
        if active_since is None:
            self._startup_charging_active_since_monotonic = now
            self._startup_charging_active_polls = 1
            return False

        self._startup_charging_active_polls = getattr(self, "_startup_charging_active_polls", 0) + 1
        return (
            self._startup_charging_active_polls >= STARTUP_CHARGING_SETTLE_MIN_POLLS
            and (now - active_since) >= STARTUP_CHARGING_SETTLE_MIN_SECONDS
        )

    def _is_startup_observed_only_phase_mismatch(
        self,
        *,
        requested_phases: int | None,
        observed_phases: int | None,
        target_phases: int | None,
    ) -> bool:
        return (
            not getattr(self, "_startup_consistency_checked", True)
            and requested_phases in (1, 3)
            and observed_phases in (1, 3)
            and target_phases in (1, 3)
            and requested_phases == target_phases
            and observed_phases != target_phases
        )

    def _is_non_pv_observed_only_phase_mismatch(
        self,
        *,
        requested_phases: int | None,
        observed_phases: int | None,
        target_phases: int | None,
    ) -> bool:
        return (
            requested_phases in (1, 3)
            and observed_phases in (1, 3)
            and target_phases in (1, 3)
            and requested_phases == target_phases
            and observed_phases != target_phases
        )

    def _is_pv_observed_only_phase_mismatch(
        self,
        *,
        requested_phases: int | None,
        observed_phases: int | None,
        target_phases: int | None,
    ) -> bool:
        return (
            requested_phases in (1, 3)
            and observed_phases in (1, 3)
            and target_phases in (1, 3)
            and requested_phases == target_phases
            and observed_phases != target_phases
        )

    def _startup_expected_phase_target(self, wallbox, sensors) -> int | None:
        mode = self.effective_mode
        if mode == ChargeMode.PV:
            if self.control_config.pv_phase_switching_mode == PvPhaseSwitchingMode.DISABLED:
                return None
            return self.controller.resolve_pv_phase_target(
                mode,
                wallbox,
                sensors,
            )
        if mode in (ChargeMode.NORMAL, ChargeMode.FIXED_CURRENT):
            configured_phases = self._configured_phase_count()
            return configured_phases if configured_phases in (1, 3) else None
        return None

    async def _enqueue_startup_consistency_recovery_if_needed(self, wallbox, sensors) -> bool:
        if getattr(self, "_startup_consistency_checked", True):
            return False

        self._startup_refresh_count = getattr(self, "_startup_refresh_count", 0) + 1
        if not self._allows_control_writes():
            self._startup_consistency_checked = True
            return False

        requested_phases = self._requested_phase_mode(wallbox)
        observed_phases = self._observed_active_phases(wallbox)
        target_phases = self._startup_expected_phase_target(wallbox, sensors)

        if not self._startup_stabilization_ready():
            if target_phases in (1, 3) and observed_phases is not None and observed_phases != target_phases:
                self._phase_switch_decision = "startup_stabilizing"
            return False

        if wallbox.charging_active and not self._startup_charging_settled(wallbox):
            self._phase_switch_decision = "startup_waiting_for_stable_charging"
            return False

        if observed_phases is None and wallbox.charging_active:
            self._phase_switch_decision = "startup_waiting_for_observed_phases"
            return False

        if requested_phases not in (1, 3) or target_phases not in (1, 3) or observed_phases is None:
            self._startup_consistency_checked = True
            self._startup_mismatch_observations.clear()
            return False

        if observed_phases == target_phases:
            self._startup_consistency_checked = True
            self._startup_mismatch_observations.clear()
            self._reset_phase_mismatch_state()
            return False

        observation = (target_phases, observed_phases, requested_phases)
        observations = getattr(self, "_startup_mismatch_observations", [])
        if observations and observations[-1] != observation:
            observations.clear()
        observations.append(observation)
        self._startup_mismatch_observations = observations[-STARTUP_MISMATCH_STABLE_POLLS:]
        self._phase_switch_decision = "startup_consistency_observing"

        if len(self._startup_mismatch_observations) < STARTUP_MISMATCH_STABLE_POLLS:
            return False

        if not self._phase_switch_runtime_allowed(wallbox):
            self._startup_consistency_checked = True
            self._startup_mismatch_observations.clear()
            self._mark_phase_switch_firmware_unsupported(wallbox)
            return False

        self._startup_consistency_checked = True
        self._startup_mismatch_observations.clear()
        self._pending_phase_switch_reason = PhaseActionReason.STARTUP_RESTORE
        return await self._handle_phase_mismatch_recovery(
            wallbox=wallbox,
            requested_phases=requested_phases or target_phases,
            observed_phases=observed_phases,
            target_phases=target_phases,
        )

    async def _enqueue_keepalive_if_needed(self) -> None:
        if not self._allows_keepalive():
            return
        if self.control_config.keepalive_mode == KeepaliveMode.DISABLED:
            return
        now = monotonic()
        elapsed = now - self._last_keepalive_sent_monotonic if self._last_keepalive_sent_monotonic else now - self._keepalive_started_monotonic
        if elapsed < self.control_config.keepalive_interval_s:
            return
        await self.write_queue.enqueue(
            QueuedWrite("keepalive", LIFE_BIT, 1, WritePriority.KEEPALIVE)
        )

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
            in {
                ControlReason.DLB_LIMITED,
                ControlReason.CABLE_LIMITED,
                ControlReason.EV_LIMITED,
            }
        ):
            await self.write_queue.clear()
            await self._enqueue_keepalive_if_needed()
            await self.write_queue.enqueue(
                QueuedWrite("current_limit", SET_CHARGE_CURRENT_A, 0, WritePriority.CONTROL)
            )
            return

        if (
            not decision.charging_enabled
            and
            self.effective_mode == ChargeMode.PV
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
                QueuedWrite("current_limit", SET_CHARGE_CURRENT_A, int(round(decision.target_current_a)), WritePriority.CURRENT)
            )

    async def _enqueue_pv_phase_switch_if_needed(self, wallbox, sensors) -> bool:
        if not self._allows_control_writes():
            self._phase_switch_decision = "control_writes_disabled"
            return False
        if not self._phase_switch_runtime_allowed(wallbox):
            self._mark_phase_switch_firmware_unsupported(wallbox)
            return False
        requested_phases = self._requested_phase_mode(wallbox)
        observed_phases = self._observed_active_phases(wallbox)
        if requested_phases is None:
            self._clear_pending_phase_action()
            self._phase_switch_up_condition_since = None
            self._phase_switch_decision = "phase_switch_register_unavailable"
            return False
        current_phases = requested_phases

        automatic_pv_phase_switching = (
            self.effective_mode == ChargeMode.PV
            and self.control_config.pv_phase_switching_mode == PvPhaseSwitchingMode.AUTOMATIC_1P3P
        )
        if not automatic_pv_phase_switching:
            self._clear_pending_phase_action()
            self._phase_switch_up_condition_since = None
            if self.effective_mode != ChargeMode.PV:
                self._reset_phase_mismatch_state()
            self._phase_switch_decision = (
                "outside_pv_mode"
                if self.effective_mode != ChargeMode.PV
                else "automatic_phase_switching_disabled"
            )
            return False

        target = None
        if automatic_pv_phase_switching:
            target = self.controller.resolve_pv_phase_target(
                self.effective_mode,
                wallbox,
                sensors,
            )
        active_recovery = self._has_active_phase_recovery_for_target(target) if target in (1, 3) else False

        if (
            observed_phases is not None
            and getattr(self, "_phase_mismatch_target", None) is not None
            and observed_phases == self._phase_mismatch_target
        ):
            _LOGGER.debug(
                "Webasto Unite phase switch success; register_405=%s phases_in_use=%s target_phases=%s charging_active=%s",
                requested_phases,
                observed_phases,
                self._phase_mismatch_target,
                wallbox.charging_active,
            )
            self._reset_phase_mismatch_state()
            self._phase_switch_decision = "phase_switch_success"
            return False

        mismatch_detected = (
            target in (1, 3)
            and requested_phases == target
            and observed_phases is not None
            and observed_phases != target
        )
        if (
            mismatch_detected
            and not active_recovery
            and self._pending_phase_switch_target is None
            and self._is_pv_observed_only_phase_mismatch(
                requested_phases=requested_phases,
                observed_phases=observed_phases,
                target_phases=target,
            )
        ):
            self._phase_switch_decision = "pv_observed_phases_differ_but_requested_matches"
            return False
        if mismatch_detected:
            return await self._handle_phase_mismatch_recovery(
                wallbox=wallbox,
                requested_phases=requested_phases,
                observed_phases=observed_phases,
                target_phases=target,
            )

        if (
            self._pending_phase_switch_target is not None
            and current_phases == self._pending_phase_switch_target
            and not getattr(self, "_pending_phase_switch_force_write", False)
        ):
            self._clear_pending_phase_action()
            self._phase_switch_decision = "phase_switch_complete"
            return False
        if (
            automatic_pv_phase_switching
            and self._pending_phase_switch_target is not None
            and target != self._pending_phase_switch_target
            and not getattr(self, "_pending_phase_switch_force_write", False)
        ):
            self._clear_pending_phase_action()
            if target is None:
                self._phase_switch_up_condition_since = None
                self._phase_switch_decision = "phase_switch_cancelled"
                return False
        if self._pending_phase_switch_target is None:
            if target is None:
                self._phase_switch_up_condition_since = None
                self._phase_switch_decision = "no_phase_switch_needed"
                return False
            phase_switch_count = getattr(self, "_phase_switch_count_this_session", 0)
            if target == 3 and phase_switch_count >= self.control_config.pv_phase_switching_max_per_session:
                self._phase_switch_decision = "phase_switch_session_limit_reached"
                return False
            last_phase_switch = getattr(self, "_last_phase_switch_monotonic", 0.0)
            elapsed = monotonic() - last_phase_switch if last_phase_switch else None
            if target == 3:
                stable_since = getattr(self, "_phase_switch_up_condition_since", None)
                now = monotonic()
                if stable_since is None:
                    self._phase_switch_up_condition_since = now
                    self._phase_switch_decision = "waiting_for_stable_3p_surplus"
                    return False
                if (now - stable_since) < self.control_config.pv_phase_switching_min_interval_s:
                    self._phase_switch_decision = "waiting_for_stable_3p_surplus"
                    return False
            else:
                self._phase_switch_up_condition_since = None
            if target == 3 and elapsed is not None and elapsed < self.control_config.pv_phase_switching_min_interval_s:
                self._phase_switch_decision = "phase_switch_rate_limited"
                return False
            self._start_pending_phase_action(
                target,
                force_write=(
                    observed_phases is not None
                    and observed_phases != requested_phases
                    and target == requested_phases
                ),
                reason=PhaseActionReason.PV_AUTOMATIC_SWITCH,
            )
            self._phase_switch_decision = "phase_switch_requested"

        target_phases = self._pending_phase_switch_target
        if target_phases is None:
            self._clear_pending_phase_action()
            self._phase_switch_decision = "no_phase_switch_needed"
            return False
        await self.write_queue.clear()
        await self._enqueue_keepalive_if_needed()
        if wallbox.charging_active:
            self._phase_switch_decision = "pausing_before_phase_switch"
            await self.write_queue.enqueue(
                QueuedWrite("current_limit", SET_CHARGE_CURRENT_A, 0, WritePriority.CONTROL)
            )
            return True

        if getattr(self, "_pending_phase_switch_force_write", False):
            retry_count = getattr(self, "_phase_mismatch_retry_count", 0)
            self._phase_mismatch_retry_count = retry_count + 1
            self._last_phase_mismatch_retry_monotonic = monotonic()
            self._pending_phase_switch_reason = PhaseActionReason.MISMATCH_RETRY
            self._phase_switch_decision = "phase_switch_retry"
            _LOGGER.debug(
                "Webasto Unite phase switch retry; register_405=%s phases_in_use=%s target_phases=%s charging_active=%s retry=%s",
                requested_phases,
                observed_phases,
                target_phases,
                wallbox.charging_active,
                self._phase_mismatch_retry_count,
            )
        else:
            self._phase_switch_decision = "writing_phase_switch_mode"
        self._pending_phase_switch_force_write = False
        await self.write_queue.enqueue(
            QueuedWrite(
                "phase_switch_mode",
                PHASE_SWITCH_MODE,
                0 if target_phases == 1 else 1,
                WritePriority.CONTROL,
            )
        )
        return True

    async def _enqueue_pending_phase_switch_write(
        self,
        *,
        wallbox,
        requested_phases: int,
        observed_phases: int | None,
    ) -> bool:
        target_phases = self._pending_phase_switch_target
        if target_phases is None:
            self._phase_switch_decision = "no_phase_switch_needed"
            return False

        await self.write_queue.clear()
        await self._enqueue_keepalive_if_needed()
        if wallbox.charging_active:
            self._phase_switch_decision = "pausing_before_phase_switch"
            await self.write_queue.enqueue(
                QueuedWrite("current_limit", SET_CHARGE_CURRENT_A, 0, WritePriority.CONTROL)
            )
            return True

        if getattr(self, "_pending_phase_switch_force_write", False):
            retry_count = getattr(self, "_phase_mismatch_retry_count", 0)
            self._phase_mismatch_retry_count = retry_count + 1
            self._last_phase_mismatch_retry_monotonic = monotonic()
            self._phase_switch_decision = "phase_switch_retry"
            _LOGGER.debug(
                "Webasto Unite phase switch retry; register_405=%s phases_in_use=%s target_phases=%s charging_active=%s retry=%s",
                requested_phases,
                observed_phases,
                target_phases,
                wallbox.charging_active,
                self._phase_mismatch_retry_count,
            )
        else:
            self._phase_switch_decision = "writing_phase_switch_mode"
        self._pending_phase_switch_force_write = False
        await self.write_queue.enqueue(
            QueuedWrite(
                "phase_switch_mode",
                PHASE_SWITCH_MODE,
                0 if target_phases == 1 else 1,
                WritePriority.CONTROL,
            )
        )
        return True

    async def _enqueue_non_pv_phase_reconcile_if_needed(self, wallbox) -> bool:
        if not self._allows_control_writes():
            self._phase_switch_decision = "control_writes_disabled"
            return False
        if not self._phase_switch_runtime_allowed(wallbox):
            self._mark_phase_switch_firmware_unsupported(wallbox)
            return False

        requested_phases = self._requested_phase_mode(wallbox)
        observed_phases = self._observed_active_phases(wallbox)
        target_phases = self._configured_phase_count()
        active_recovery = self._has_active_phase_recovery_for_target(target_phases)

        if not getattr(self, "_startup_consistency_checked", True) and not active_recovery and wallbox.charging_active:
            if observed_phases is None:
                self._phase_switch_decision = "startup_waiting_for_observed_phases"
                return False
            if not self._startup_charging_settled(wallbox):
                self._phase_switch_decision = "startup_waiting_for_stable_charging"
                return False
            if self._is_startup_observed_only_phase_mismatch(
                requested_phases=requested_phases,
                observed_phases=observed_phases,
                target_phases=target_phases,
            ):
                self._phase_switch_decision = "startup_consistency_observing"
                return False

        if requested_phases is None or target_phases not in (1, 3):
            self._phase_switch_decision = "phase_switch_register_unavailable"
            return False

        if (
            not active_recovery
            and self._pending_phase_switch_target is None
            and self._is_non_pv_observed_only_phase_mismatch(
                requested_phases=requested_phases,
                observed_phases=observed_phases,
                target_phases=target_phases,
            )
        ):
            self._phase_switch_decision = "observed_phases_differ_but_requested_matches"
            return False

        if getattr(self, "_phase_mismatch_bounce_stage", None) is not None:
            return await self._advance_phase_mismatch_bounce(
                wallbox=wallbox,
                requested_phases=requested_phases,
                observed_phases=observed_phases,
                target_phases=target_phases,
            )

        if (
            observed_phases is not None
            and getattr(self, "_phase_mismatch_target", None) is not None
            and observed_phases == self._phase_mismatch_target
        ):
            _LOGGER.debug(
                "Webasto Unite phase switch success; register_405=%s phases_in_use=%s target_phases=%s charging_active=%s",
                requested_phases,
                observed_phases,
                self._phase_mismatch_target,
                wallbox.charging_active,
            )
            self._clear_pending_phase_action()
            self._reset_phase_mismatch_state()
            self._phase_switch_decision = "phase_switch_success"
            return False

        if (
            self._pending_phase_switch_target is not None
            and requested_phases == self._pending_phase_switch_target
            and not getattr(self, "_pending_phase_switch_force_write", False)
            and (
                observed_phases == self._pending_phase_switch_target
                or (
                    observed_phases is None
                    and not self._has_active_phase_recovery_for_target(self._pending_phase_switch_target)
                )
            )
        ):
            self._clear_pending_phase_action()
            self._phase_switch_decision = "phase_switch_complete"
            return False

        if self._pending_phase_switch_target is not None:
            if observed_phases is not None and observed_phases != self._pending_phase_switch_target:
                self._pending_phase_switch_reason = self._pending_phase_switch_reason or "non_pv_reconcile"
                return await self._handle_phase_mismatch_recovery(
                    wallbox=wallbox,
                    requested_phases=requested_phases,
                    observed_phases=observed_phases,
                    target_phases=self._pending_phase_switch_target,
                )
            if (
                observed_phases is None
                and getattr(self, "_last_phase_switch_monotonic", 0.0)
                and not getattr(self, "_pending_phase_switch_force_write", False)
            ):
                self._phase_switch_decision = (
                    "waiting_for_phase_switch_feedback"
                    if requested_phases != self._pending_phase_switch_target
                    else "waiting_for_observed_phases"
                )
                return True
            return await self._enqueue_pending_phase_switch_write(
                wallbox=wallbox,
                requested_phases=requested_phases,
                observed_phases=observed_phases,
            )

        if observed_phases is None:
            if requested_phases != target_phases:
                self._start_pending_phase_action(
                    target_phases,
                    force_write=False,
                    reason=PhaseActionReason.NON_PV_RECONCILE,
                )
                self._phase_switch_decision = "phase_switch_requested"
                return await self._enqueue_pending_phase_switch_write(
                    wallbox=wallbox,
                    requested_phases=requested_phases,
                    observed_phases=observed_phases,
                )
            if active_recovery:
                if requested_phases == target_phases:
                    self._phase_switch_decision = "waiting_for_observed_phases"
                    return True
                self._start_pending_phase_action(
                    target_phases,
                    force_write=True,
                    reason=PhaseActionReason.MISMATCH_RETRY,
                )
                self._phase_mismatch_target = target_phases
                return await self._enqueue_pending_phase_switch_write(
                    wallbox=wallbox,
                    requested_phases=requested_phases,
                    observed_phases=observed_phases,
                )
            self._phase_switch_decision = "no_phase_switch_needed"
            return False

        if observed_phases == target_phases:
            self._reset_phase_mismatch_state()
            self._phase_switch_decision = "no_phase_switch_needed"
            return False

        self._pending_phase_switch_reason = getattr(self, "_pending_phase_switch_reason", None) or "non_pv_reconcile"
        return await self._handle_phase_mismatch_recovery(
            wallbox=wallbox,
            requested_phases=requested_phases,
            observed_phases=observed_phases,
            target_phases=target_phases,
        )

    def _record_phase_mismatch_observation(
        self,
        *,
        requested_phases: int,
        observed_phases: int,
        target_phases: int,
    ) -> int:
        key = (target_phases, requested_phases, observed_phases)
        if getattr(self, "_phase_mismatch_observation_key", None) == key:
            self._phase_mismatch_observation_count = getattr(self, "_phase_mismatch_observation_count", 0) + 1
        else:
            self._phase_mismatch_observation_key = key
            self._phase_mismatch_observation_count = 1
        return self._phase_mismatch_observation_count

    def _can_start_phase_bounce(
        self,
        *,
        requested_phases: int,
        target_phases: int,
    ) -> bool:
        if getattr(self, "_phase_mismatch_bounce_used", False):
            return False
        if getattr(self, "_phase_mismatch_bounce_stage", None) is not None:
            return True
        if self._configured_phase_count() != 3:
            return False
        if target_phases != 3:
            return False
        if requested_phases != target_phases:
            return False
        last_bounce = getattr(self, "_last_phase_mismatch_bounce_monotonic", 0.0)
        return not last_bounce or (monotonic() - last_bounce) >= PHASE_MISMATCH_BOUNCE_COOLDOWN_S

    async def _start_phase_mismatch_bounce(
        self,
        *,
        wallbox,
        requested_phases: int,
        observed_phases: int,
        target_phases: int,
    ) -> bool:
        self._phase_mismatch_bounce_used = True
        self._phase_mismatch_bounce_stage = PhaseBounceStage.WRITE_1P
        self._phase_mismatch_bounce_target = target_phases
        self._phase_mismatch_bounce_verify_count = 0
        self._pending_phase_switch_reason = PhaseActionReason.MISMATCH_BOUNCE_RECOVERY
        self._phase_switch_decision = "phase_switch_bounce_recovery"
        return await self._advance_phase_mismatch_bounce(
            wallbox=wallbox,
            requested_phases=requested_phases,
            observed_phases=observed_phases,
            target_phases=target_phases,
        )

    async def _advance_phase_mismatch_bounce(
        self,
        *,
        wallbox,
        requested_phases: int,
        observed_phases: int | None,
        target_phases: int,
    ) -> bool:
        stage = getattr(self, "_phase_mismatch_bounce_stage", None)
        bounce_target = getattr(self, "_phase_mismatch_bounce_target", None) or target_phases
        if stage is None:
            return False

        if bounce_target != 3 or self._configured_phase_count() != 3:
            self._mark_phase_switch_unverified(
                wallbox=wallbox,
                requested_phases=requested_phases,
                observed_phases=observed_phases or 0,
                target_phases=target_phases,
            )
            return False

        if stage == PhaseBounceStage.WRITE_1P:
            await self.write_queue.clear()
            await self._enqueue_keepalive_if_needed()
            if wallbox.charging_active:
                self._phase_switch_decision = "phase_switch_bounce_pausing"
                await self.write_queue.enqueue(
                    QueuedWrite("current_limit", SET_CHARGE_CURRENT_A, 0, WritePriority.CONTROL)
                )
                return True
            self._phase_mismatch_bounce_stage = PhaseBounceStage.WAIT_1P
            self._last_phase_mismatch_bounce_monotonic = monotonic()
            self._phase_switch_decision = "phase_switch_bounce_to_1p"
            await self.write_queue.enqueue(
                QueuedWrite("phase_switch_mode", PHASE_SWITCH_MODE, 0, WritePriority.CONTROL)
            )
            return True

        if stage == PhaseBounceStage.WAIT_1P:
            if requested_phases != 1:
                self._phase_switch_decision = "phase_switch_bounce_waiting_1p"
                return True
            self._phase_mismatch_bounce_stage = PhaseBounceStage.WRITE_TARGET
            self._phase_switch_decision = "phase_switch_bounce_settled_1p"
            return True

        if stage == PhaseBounceStage.WRITE_TARGET:
            await self.write_queue.clear()
            await self._enqueue_keepalive_if_needed()
            if wallbox.charging_active:
                self._phase_switch_decision = "phase_switch_bounce_pausing"
                await self.write_queue.enqueue(
                    QueuedWrite("current_limit", SET_CHARGE_CURRENT_A, 0, WritePriority.CONTROL)
                )
                return True
            self._phase_mismatch_bounce_stage = PhaseBounceStage.VERIFY
            self._phase_switch_decision = "phase_switch_bounce_to_target"
            await self.write_queue.enqueue(
                QueuedWrite("phase_switch_mode", PHASE_SWITCH_MODE, 1, WritePriority.CONTROL)
            )
            return True

        if stage == PhaseBounceStage.VERIFY:
            if requested_phases == bounce_target and observed_phases == bounce_target:
                self._clear_pending_phase_action()
                self._reset_phase_mismatch_state()
                self._phase_switch_decision = "phase_switch_success"
                return False
            if requested_phases == bounce_target and observed_phases is not None and observed_phases != bounce_target:
                self._phase_mismatch_bounce_verify_count = getattr(self, "_phase_mismatch_bounce_verify_count", 0) + 1
                if self._phase_mismatch_bounce_verify_count >= PHASE_MISMATCH_BOUNCE_STABLE_POLLS:
                    self._mark_phase_switch_unverified(
                        wallbox=wallbox,
                        requested_phases=requested_phases,
                        observed_phases=observed_phases,
                        target_phases=bounce_target,
                    )
                    return False
            self._phase_switch_decision = "phase_switch_bounce_verifying"
            return False

        self._mark_phase_switch_unverified(
            wallbox=wallbox,
            requested_phases=requested_phases,
            observed_phases=observed_phases or 0,
            target_phases=target_phases,
        )
        return False

    async def _handle_phase_mismatch_recovery(
        self,
        *,
        wallbox,
        requested_phases: int,
        observed_phases: int,
        target_phases: int,
    ) -> bool:
        if not self._phase_switch_runtime_allowed(wallbox):
            self._mark_phase_switch_firmware_unsupported(wallbox)
            return False
        self._phase_switch_decision = "phase_switch_mismatch_detected"

        stable_mismatch_count = self._record_phase_mismatch_observation(
            requested_phases=requested_phases,
            observed_phases=observed_phases,
            target_phases=target_phases,
        )

        if getattr(self, "_phase_mismatch_unverified", False):
            self._phase_switch_decision = "phase_switch_unverified"
            return False

        if getattr(self, "_phase_mismatch_bounce_stage", None) is not None:
            return await self._advance_phase_mismatch_bounce(
                wallbox=wallbox,
                requested_phases=requested_phases,
                observed_phases=observed_phases,
                target_phases=target_phases,
            )

        retry_count = getattr(self, "_phase_mismatch_retry_count", 0)

        bounce_candidate = (
            retry_count >= 1
            and stable_mismatch_count >= PHASE_MISMATCH_BOUNCE_STABLE_POLLS
            and self._can_start_phase_bounce(
                requested_phases=requested_phases,
                target_phases=target_phases,
            )
        )
        if bounce_candidate:
            return await self._start_phase_mismatch_bounce(
                wallbox=wallbox,
                requested_phases=requested_phases,
                observed_phases=observed_phases,
                target_phases=target_phases,
            )
        if (
            retry_count >= 1
            and retry_count < PHASE_MISMATCH_MAX_RETRIES_PER_SESSION
            and stable_mismatch_count < PHASE_MISMATCH_BOUNCE_STABLE_POLLS
            and self._can_start_phase_bounce(
                requested_phases=requested_phases,
                target_phases=target_phases,
            )
        ):
            return False

        if retry_count >= PHASE_MISMATCH_MAX_RETRIES_PER_SESSION:
            self._mark_phase_switch_unverified(
                wallbox=wallbox,
                requested_phases=requested_phases,
                observed_phases=observed_phases,
                target_phases=target_phases,
            )
            return False

        now = monotonic()
        last_retry = getattr(self, "_last_phase_mismatch_retry_monotonic", 0.0)
        if last_retry and (now - last_retry) < PHASE_MISMATCH_RETRY_COOLDOWN_S:
            return False

        self._start_pending_phase_action(
            target_phases,
            force_write=True,
            reason=PhaseActionReason.MISMATCH_RETRY,
        )
        self._phase_mismatch_target = target_phases
        await self.write_queue.clear()
        await self._enqueue_keepalive_if_needed()

        if wallbox.charging_active:
            self._phase_switch_decision = "phase_switch_mismatch_detected"
            _LOGGER.debug(
                "Webasto Unite phase switch attempt; register_405=%s phases_in_use=%s target_phases=%s charging_active=%s",
                requested_phases,
                observed_phases,
                target_phases,
                wallbox.charging_active,
            )
            await self.write_queue.enqueue(
                QueuedWrite("current_limit", SET_CHARGE_CURRENT_A, 0, WritePriority.CONTROL)
            )
            return True

        self._phase_switch_decision = "phase_switch_retry"
        self._last_phase_mismatch_retry_monotonic = now
        self._phase_mismatch_retry_count = retry_count + 1
        self._pending_phase_switch_force_write = False
        _LOGGER.debug(
            "Webasto Unite phase switch retry; register_405=%s phases_in_use=%s target_phases=%s charging_active=%s retry=%s",
            requested_phases,
            observed_phases,
            target_phases,
            wallbox.charging_active,
            self._phase_mismatch_retry_count,
        )
        await self.write_queue.enqueue(
            QueuedWrite(
                "phase_switch_mode",
                PHASE_SWITCH_MODE,
                0 if target_phases == 1 else 1,
                WritePriority.CONTROL,
            )
        )
        return True

    def _mark_phase_switch_unverified(
        self,
        *,
        wallbox,
        requested_phases: int,
        observed_phases: int,
        target_phases: int,
    ) -> None:
        self._phase_mismatch_unverified = True
        self._clear_pending_phase_action()
        self._phase_mismatch_bounce_stage = None
        self._phase_mismatch_bounce_target = None
        self._phase_mismatch_bounce_verify_count = 0
        self._phase_switch_decision = "phase_switch_unverified"
        _LOGGER.warning(
            "Webasto Unite phase switch unverified after mismatch retries; "
            "register_405=%s phases_in_use=%s target_phases=%s charging_active=%s",
            requested_phases,
            observed_phases,
            target_phases,
            wallbox.charging_active,
        )

    async def _sync_static_registers(self) -> None:
        if not self._allows_static_sync():
            return
        await self.write_queue.enqueue(
            QueuedWrite("safe_current", SAFE_CURRENT_A, int(round(self.control_config.safe_current_a)), WritePriority.SAFETY)
        )
        await self.write_queue.enqueue(
            QueuedWrite("communication_timeout", COMM_TIMEOUT_S, int(round(self.control_config.communication_timeout_s)), WritePriority.SAFETY)
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
                if item.key == "phase_switch_mode":
                    self._last_phase_switch_monotonic = monotonic()
                    self._phase_switch_count_this_session = getattr(self, "_phase_switch_count_this_session", 0) + 1

    def _allows_keepalive(self) -> bool:
        return self.control_config.control_mode in (ControlMode.KEEPALIVE_ONLY, ControlMode.MANAGED_CONTROL)

    def _allows_control_writes(self) -> bool:
        return self.control_config.control_mode == ControlMode.MANAGED_CONTROL

    def _allows_static_sync(self) -> bool:
        return self.control_config.control_mode == ControlMode.MANAGED_CONTROL

    def _keepalive_age_seconds(self) -> float | None:
        if not self._allows_keepalive() or self.control_config.keepalive_mode == KeepaliveMode.DISABLED:
            return None
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
            self.entry.options.get(CONF_DLB_GRID_POWER_SENSOR),
            self.entry.options.get(CONF_PV_SURPLUS_SENSOR),
        ]
        entities = [e for e in entities if e]
        if not entities:
            return

        @callback
        def _handle_state_change(_event):
            self.async_set_updated_data(self.data)
            self.hass.async_create_task(self.async_request_refresh())

        self._sensor_unsubscribers.append(async_track_state_change_event(self.hass, entities, _handle_state_change))
