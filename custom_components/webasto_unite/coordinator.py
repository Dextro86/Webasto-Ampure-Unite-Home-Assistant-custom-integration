
from __future__ import annotations

import asyncio
import logging
from time import monotonic
from datetime import timedelta

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event
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
    CONF_PV_SURPLUS_SENSOR,
    CONF_RETRIES,
    CONF_SAFE_CURRENT,
    CONF_SAFETY_MARGIN,
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
    DEFAULT_RETRIES,
    DEFAULT_SAFE_CURRENT_A,
    DEFAULT_SAFETY_MARGIN_A,
    DEFAULT_TIMEOUT_S,
    DEFAULT_UNIT_ID,
    DEFAULT_USER_LIMIT_A,
    DOMAIN,
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


class WebastoUniteCoordinator(DataUpdateCoordinator[RuntimeSnapshot]):
    def __init__(self, hass, entry) -> None:
        self.hass = hass
        self.entry = entry
        self._mode = ChargeMode.NORMAL
        self._charging_paused = False
        self._pv_until_unplug_active = False
        self._fixed_current_until_unplug_active = False
        self._last_vehicle_connected = False
        self._pending_phase_switch_target: int | None = None
        self._sensor_unsubscribers = []
        self._last_keepalive_sent_monotonic = 0.0
        self._keepalive_started_monotonic = monotonic()
        self._keepalive_sent_count = 0
        self._keepalive_write_failures = 0
        self._keepalive_task: asyncio.Task | None = None
        self._flush_lock = asyncio.Lock()

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
            dlb_input_model=DlbInputModel(merged.get(CONF_DLB_INPUT_MODEL, DlbInputModel.PHASE_CURRENTS.value)),
            dlb_sensor_scope=DlbSensorScope(
                merged.get(CONF_DLB_SENSOR_SCOPE, DlbSensorScope.LOAD_EXCLUDING_CHARGER.value)
            ),
            pv_input_model=PvInputModel(merged.get(CONF_PV_INPUT_MODEL, PvInputModel.GRID_POWER_DERIVED.value)),
            pv_control_strategy=PvControlStrategy(merged.get(CONF_PV_CONTROL_STRATEGY, PvControlStrategy.SURPLUS.value)),
            pv_until_unplug_strategy=PvOverrideStrategy(
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
            fixed_current_a=float(merged.get(CONF_FIXED_CURRENT, DEFAULT_FIXED_CURRENT_A)),
            communication_timeout_s=float(merged.get(CONF_COMM_TIMEOUT, 30.0)),
        )
        self.controller = WallboxController(self.control_config)
        self.client = WebastoModbusClient(
            ModbusClientConfig(
                host=entry.data["host"],
                port=int(entry.data.get("port", DEFAULT_PORT)),
                unit_id=int(entry.data.get(CONF_UNIT_ID, DEFAULT_UNIT_ID)),
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

    async def async_setup(self) -> None:
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

    def set_mode(self, mode: ChargeMode) -> None:
        self._mode = mode
        self._pv_until_unplug_active = False
        self._fixed_current_until_unplug_active = False
        if mode != ChargeMode.PV:
            self._reset_pv_runtime_state()
        if mode == ChargeMode.OFF:
            self._charging_paused = False

    def pause_charging(self) -> None:
        self._charging_paused = True
        self._reset_pv_runtime_state()

    def resume_charging(self) -> None:
        self._charging_paused = False

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
        self.control_config.user_limit_a = current_a

    def set_fixed_current(self, current_a: float) -> None:
        self.control_config.fixed_current_a = current_a

    async def async_set_phase_switch_mode(self, phases: int) -> None:
        if phases not in (1, 3):
            raise ValueError("Phase switch mode must be 1 or 3 phases")
        if self.control_config.pv_phase_switching_mode == PvPhaseSwitchingMode.DISABLED:
            raise ValueError("Phase switching is disabled in the integration settings")
        if self.data is None:
            raise ValueError("Phase switching is only allowed after charger state is available")
        if self.data.wallbox.phase_switch_mode_raw not in (0, 1):
            raise ValueError("Phase switch register 405 is unavailable or returned an unsupported value")
        if self.data.wallbox.charging_active:
            raise ValueError("Phase switching is only allowed while charging is inactive")
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
            self._last_vehicle_connected = wallbox.vehicle_connected
            sensors = self._read_sensor_snapshot()
            pv_strategy = self.controller.resolve_effective_pv_strategy(
                self.control_config.pv_control_strategy,
                self.control_config.pv_until_unplug_strategy,
                self._pv_until_unplug_active,
            )
            decision = self.controller.evaluate(self.effective_mode, wallbox, sensors, pv_strategy)
            phase_switch_handled = await self._enqueue_pv_phase_switch_if_needed(wallbox, sensors)
            if not phase_switch_handled:
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
                dominant_limit_reason=decision.dominant_limit_reason.value if decision.dominant_limit_reason is not None else None,
                fallback_active=decision.fallback_active,
                last_client_error=self.client.stats.last_error,
                entry_title=self.entry.title or DEFAULT_NAME,
                capability_summary=self._build_capability_summary(wallbox),
                capabilities=self._build_capabilities(wallbox),
            )
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(str(err)) from err

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
            and self.control_config.pv_control_strategy == PvControlStrategy.MIN_PLUS_SURPLUS
        ):
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
        return self.entry.data.get(CONF_INSTALLED_PHASES, "3p")

    def _reset_pv_runtime_state(self) -> None:
        controller = getattr(self, "controller", None)
        if controller is not None:
            controller.reset_pv_state()

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

        if decision.should_write and decision.target_current_a is not None:
            await self.write_queue.enqueue(
                QueuedWrite("current_limit", SET_CHARGE_CURRENT_A, int(round(decision.target_current_a)), WritePriority.CURRENT)
            )

    async def _enqueue_pv_phase_switch_if_needed(self, wallbox, sensors) -> bool:
        if not self._allows_control_writes():
            return False
        if (
            self.effective_mode != ChargeMode.PV
            or self.control_config.pv_phase_switching_mode != PvPhaseSwitchingMode.AUTOMATIC_1P3P
        ):
            self._pending_phase_switch_target = None
            return False

        current_phases = 1 if wallbox.phase_switch_mode_raw == 0 else 3 if wallbox.phase_switch_mode_raw == 1 else None
        if current_phases is None:
            self._pending_phase_switch_target = None
            return False
        if self._pending_phase_switch_target is None:
            self._pending_phase_switch_target = self.controller.resolve_pv_phase_target(
                self.effective_mode,
                wallbox,
                sensors,
            )

        target_phases = self._pending_phase_switch_target
        if target_phases is None:
            return False
        if current_phases == target_phases:
            self._pending_phase_switch_target = None
            return False

        await self.write_queue.clear()
        await self._enqueue_keepalive_if_needed()
        if wallbox.charging_active:
            await self.write_queue.enqueue(
                QueuedWrite("current_limit", SET_CHARGE_CURRENT_A, 0, WritePriority.CONTROL)
            )
            return True

        await self.write_queue.enqueue(
            QueuedWrite(
                "phase_switch_mode",
                PHASE_SWITCH_MODE,
                0 if target_phases == 1 else 1,
                WritePriority.CONTROL,
            )
        )
        return True

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
