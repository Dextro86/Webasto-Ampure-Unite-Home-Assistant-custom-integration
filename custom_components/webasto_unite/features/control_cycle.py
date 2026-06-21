from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from homeassistant.helpers.update_coordinator import UpdateFailed

from ..control.inputs import read_control_inputs
from ..control.orchestrator import ControlWriteAccess
from ..core.session import SessionTransition
from ..core.snapshot import SnapshotBuildInput, build_runtime_snapshot
from ..models import (
    ControlDecision,
    HaSensorSnapshot,
    RuntimeSnapshot,
    SolarControlStrategy,
    WallboxState,
)
from .phase_observer import PhaseObservability, build_phase_observability
from .phase_policy import PhasePolicyDecision, evaluate_phase_policy


@dataclass(slots=True)
class ControlCycleState:
    wallbox: WallboxState
    sensors: HaSensorSnapshot
    solar_surplus_w: float | None
    phase_observability: PhaseObservability
    solar_strategy: SolarControlStrategy
    decision: ControlDecision
    phase_policy: PhasePolicyDecision


class ControlCycleMixin:
    """Coordinator update-cycle steps kept separate from setup/service code."""

    def _handle_session_transition(self, wallbox: WallboxState) -> tuple[SessionTransition, bool]:
        session_transition = self.session_runtime.observe_vehicle_connection(wallbox.vehicle_connected)

        # A new plug-in session should start from the configured default mode,
        # not from a runtime mode selected during the previous session.
        if session_transition.vehicle_disconnected:
            # Do not write phase register 405 while the charger is closing
            # the vehicle session. Phase changes must stay explicit.
            self._reset_runtime_mode_to_default()
            self.controller.reset_session_phase_observation()
            self._phase_runtime().reset_session_transient_state()
            self._clear_phase_session_override()
            self._phase_recovery_warning = None

        if session_transition.vehicle_connected:
            self.controller.reset_current_write_state()
            self.controller.reset_session_phase_observation()
            self._phase_runtime().reset_session_transient_state()
            self._phase_runtime().mark_session_started()

        phase_session_settling = wallbox.vehicle_connected and self._phase_session_start_settling()
        self.write_runtime.update_current_write_verification(wallbox.current_limit_a)
        return session_transition, phase_session_settling

    def _read_control_inputs(self, wallbox: WallboxState) -> HaSensorSnapshot:
        # Read Home Assistant sensor inputs only after wallbox/session state
        # has been updated for this poll, so the controller sees one
        # consistent view of the current cycle.
        return read_control_inputs(
            options=self.entry.options,
            config=self.control_config,
            sensor_adapter=self.sensor_adapter,
            surplus_resolver=self.controller.resolve_surplus_power,
            configured_phase_count=self._configured_phase_count,
            wallbox=wallbox,
        )

    def _resolve_cycle_solar_strategy(self) -> SolarControlStrategy:
        return self.controller.resolve_effective_solar_strategy(
            self.active_solar_strategy,
            self.control_config.solar_until_unplug_strategy,
            self._solar_until_unplug_active,
        )

    def _build_phase_policy(
        self,
        *,
        wallbox: WallboxState,
        sensors: HaSensorSnapshot,
        solar_strategy: SolarControlStrategy,
        decision: ControlDecision,
    ) -> PhasePolicyDecision:
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
        return self._apply_phase_policy_runtime_state(phase_policy)

    def _build_control_cycle_state(
        self,
        wallbox: WallboxState,
    ) -> ControlCycleState:
        sensors = self._read_control_inputs(wallbox)
        solar_surplus_w = self.controller.resolve_surplus_power(sensors, wallbox)
        phase_observability = build_phase_observability(wallbox)
        solar_strategy = self._resolve_cycle_solar_strategy()
        decision = self.controller.evaluate(self.effective_mode, wallbox, sensors, solar_strategy)
        phase_policy = self._build_phase_policy(
            wallbox=wallbox,
            sensors=sensors,
            solar_strategy=solar_strategy,
            decision=decision,
        )
        return ControlCycleState(
            wallbox=wallbox,
            sensors=sensors,
            solar_surplus_w=solar_surplus_w,
            phase_observability=phase_observability,
            solar_strategy=solar_strategy,
            decision=decision,
            phase_policy=phase_policy,
        )

    async def _apply_phase_actions(
        self,
        *,
        cycle: ControlCycleState,
        session_transition: SessionTransition,
        phase_session_settling: bool,
    ) -> bool:
        return await self._maybe_schedule_phase_action(
            wallbox=cycle.wallbox,
            phase_observability=cycle.phase_observability,
            phase_policy=cycle.phase_policy,
            vehicle_disconnected=session_transition.vehicle_disconnected,
            phase_session_settling=phase_session_settling,
        )

    async def _handle_disconnect_disable_write(
        self,
        *,
        session_transition: SessionTransition,
        wallbox: WallboxState,
    ) -> None:
        """Do not write charger registers while no vehicle is connected.

        EVCC also treats vehicle disconnect primarily as a session/state cleanup
        event. Writing current or phase registers during the charger shutdown
        window can race the Webasto/Vestel session handling, so unplug/no-vehicle
        handling intentionally stays read-only here.
        """
        return

    def _apply_runtime_guards(
        self,
        *,
        wallbox: WallboxState,
        sensors: HaSensorSnapshot,
        decision: ControlDecision,
        phase_action_executed: bool,
    ) -> None:
        # Apply transient/startup guards after the controller decision is
        # built, but before anything is enqueued for writing.
        if phase_action_executed:
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

    async def _enqueue_control_decision(
        self,
        decision: ControlDecision,
        *,
        wallbox: WallboxState,
    ) -> ControlWriteAccess:
        control_write_access = self._control_write_access()
        await self.write_runtime.enqueue_decision(
            decision,
            effective_mode=self.effective_mode,
            current_snapshot=SimpleNamespace(wallbox=wallbox),
            allows_control_writes=control_write_access.automatic_control_writes,
            blocked_reason=control_write_access.blocked_reason,
            enqueue_keepalive=self.write_runtime.enqueue_keepalive_if_needed,
        )
        await self.write_runtime.flush_write_queue()
        return control_write_access

    async def _build_snapshot(
        self,
        *,
        cycle: ControlCycleState,
        control_write_access: ControlWriteAccess,
    ) -> RuntimeSnapshot:
        keepalive_age_s = self.write_runtime.keepalive_age_seconds()
        return build_runtime_snapshot(
            SnapshotBuildInput(
                wallbox=cycle.wallbox,
                mode=self._mode,
                effective_mode=self.effective_mode,
                control_config=self.control_config,
                decision=cycle.decision,
                sensors=cycle.sensors,
                solar_strategy=cycle.solar_strategy,
                charging_paused=self._charging_paused,
                solar_until_unplug_active=self._solar_until_unplug_active,
                fixed_current_until_unplug_active=self._fixed_current_until_unplug_active,
                keepalive_age_s=keepalive_age_s,
                keepalive_overdue=self.write_runtime.is_keepalive_overdue(keepalive_age_s),
                keepalive_sent_count=self.write_runtime.keepalive_sent_count,
                keepalive_write_failures=self.write_runtime.keepalive_write_failures,
                queue_depth=await self.write_queue.size(),
                pending_write_kind=await self.write_queue.peek_next_kind(),
                control_writes_enabled=control_write_access.current_writes,
                write_runtime=self.write_runtime,
                solar_surplus_w=cycle.solar_surplus_w,
                solar_state=self.controller.solar_state,
                phase_observability=cycle.phase_observability,
                phase_recovery_warning=self._phase_recovery_warning,
                phase_switching_mode=self._phase_switching_mode,
                phase_switch_default_mode=self._configured_installed_phases(),
                phase_session_override_active=self._phase_session_override_active,
                phase_session_target=self._phase_session_target,
                phase_restore_pending=self._phase_restore_pending,
                phase_policy=cycle.phase_policy,
                phase_switch_last_result=self._phase_switch_last_result,
                phase_switch_last_block_reason=self._phase_switch_last_block_reason,
                phase_switch_last_target=self._phase_switch_last_target,
                phase_switch_state=self._phase_switch_state,
                last_client_error=self.client.stats.last_error,
                entry_title=self.entry.title,
            )
        )

    async def _async_update_data(self) -> RuntimeSnapshot:
        self._ensure_runtime_defaults()
        try:
            wallbox = await self.wallbox_reader.read_wallbox_state(self._configured_installed_phases())
            self.runtime_guards.record_startup_refresh()

            session_transition, phase_session_settling = self._handle_session_transition(wallbox)
            cycle = self._build_control_cycle_state(wallbox)
            await self._handle_disconnect_disable_write(
                session_transition=session_transition,
                wallbox=wallbox,
            )
            phase_action_executed = await self._apply_phase_actions(
                cycle=cycle,
                session_transition=session_transition,
                phase_session_settling=phase_session_settling,
            )
            self._apply_runtime_guards(
                wallbox=wallbox,
                sensors=cycle.sensors,
                decision=cycle.decision,
                phase_action_executed=phase_action_executed,
            )
            control_write_access = await self._enqueue_control_decision(cycle.decision, wallbox=wallbox)
            return await self._build_snapshot(cycle=cycle, control_write_access=control_write_access)
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(str(err)) from err
