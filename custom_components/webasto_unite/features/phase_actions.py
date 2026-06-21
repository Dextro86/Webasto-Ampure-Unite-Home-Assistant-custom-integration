from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from time import monotonic

from ..const import (
    PHASE_MODE_3P,
    PHASE_SWITCHING_MODE_AUTOMATIC_SOLAR,
    PHASE_SWITCHING_MODE_OFF,
)
from ..models import ChargingState, ControlMode, WallboxState
from .phase_engine import REGISTER_ACCEPTED_RESULTS
from .phase_policy import (
    AUTO_PHASE_MAX_SWITCHES_PER_SESSION,
    AUTO_PHASE_STABLE_TO_1P_S,
    AUTO_PHASE_STABLE_TO_3P_S,
    AUTO_PHASE_SWITCH_COOLDOWN_S,
    PhasePolicyDecision,
)
from .phase_switch import (
    default_phase_switch_raw_value,
    default_phase_target,
    observed_phases_match_target,
    phase_label,
    wallbox_matches_default_phase,
)

_LOGGER = logging.getLogger(__name__)

NEW_SESSION_PHASE_SETTLE_S = 45.0
AUTOMATIC_PHASE_SWITCH_EXECUTION_ENABLED = True


class PhaseActionMixin:
    """Phase switch scheduling and restore behavior for the coordinator.

    The mixin intentionally depends on coordinator attributes. Keeping it in a
    separate module makes the phase action flow reviewable without changing the
    public coordinator API.
    """

    async def async_schedule_phase_switch(self, target_phases: int, *, request_refresh: bool = True) -> None:
        self._ensure_runtime_defaults()
        if self._phase_switch_in_progress():
            raise ValueError("Phase switch blocked: phase_switch_in_progress")
        self._schedule_phase_switch_task(
            target_phases,
            source="manual",
            force_edge_trigger=False,
        )
        if request_refresh:
            await self.async_request_refresh()

    def _schedule_phase_switch_task(
        self,
        target_phases: int,
        *,
        source: str,
        force_edge_trigger: bool = False,
        wallbox: WallboxState | None = None,
    ) -> None:
        self.phase_switch_manager.last_target = phase_label(target_phases)
        self.phase_switch_manager.last_block_reason = None
        self.phase_switch_manager.state = "queued"
        self._sync_phase_switch_diagnostics()
        self._phase_switch_task = self._create_background_task(
            self._run_scheduled_phase_switch(
                target_phases,
                source=source,
                force_edge_trigger=force_edge_trigger,
                wallbox=wallbox,
            )
        )

    async def _run_scheduled_phase_switch(
        self,
        target_phases: int,
        *,
        source: str,
        force_edge_trigger: bool = False,
        wallbox: WallboxState | None = None,
    ) -> None:
        failed_before_accept = True
        try:
            await self.async_request_phase_switch(
                target_phases,
                request_refresh=False,
                force_edge_trigger=force_edge_trigger,
                wallbox=wallbox,
            )
            failed_before_accept = self._phase_switch_last_result not in REGISTER_ACCEPTED_RESULTS
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("%s phase switch to %sP failed: %s", source.capitalize(), target_phases, err)
        finally:
            if source == "automatic" and failed_before_accept:
                self._record_phase_policy_failed_attempt()
                self._phase_runtime().record_policy_failed_target(phase_label(target_phases))
            elif source == "automatic" and self._phase_switch_last_result in REGISTER_ACCEPTED_RESULTS:
                self._record_phase_policy_switch_attempt()
            await self._flush_pending_external_current_limit()
            self._clear_control_write_blocked("phase_switch_in_progress")
            self._sync_phase_switch_diagnostics()
            await self.async_request_refresh()

    async def async_request_phase_switch(
        self,
        target_phases: int,
        *,
        request_refresh: bool = True,
        force_edge_trigger: bool = False,
        wallbox: WallboxState | None = None,
    ) -> None:
        self._ensure_runtime_defaults()
        current_snapshot = getattr(self, "data", None)
        wallbox = wallbox or getattr(current_snapshot, "wallbox", None)
        try:
            await self.phase_switch_manager.request(
                phase_switching_mode=self._phase_switching_mode,
                wallbox=wallbox,
                target_phases=target_phases,
                config=self.control_config,
                client=self.client,
                write_queue=self.write_queue,
                flush_lock=self.write_runtime.flush_lock,
                force_edge_trigger=force_edge_trigger,
            )
        finally:
            self._sync_phase_switch_diagnostics()
        if self._phase_switch_last_result in REGISTER_ACCEPTED_RESULTS:
            self.controller.reset_session_phase_observation()
            self._update_phase_session_override(target_phases)
        await self._flush_pending_external_current_limit()
        if request_refresh:
            await self.async_request_refresh()

    async def async_schedule_restore_default_phase_mode(
        self,
        wallbox: WallboxState | None = None,
        *,
        request_refresh: bool = True,
        force_edge_trigger: bool = False,
    ) -> None:
        self._ensure_runtime_defaults()
        if self._phase_switch_in_progress():
            raise ValueError("Phase restore blocked: phase_switch_in_progress")
        self._schedule_phase_restore_task(wallbox, force_edge_trigger=force_edge_trigger)
        if request_refresh:
            await self.async_request_refresh()

    def _schedule_phase_restore_task(
        self,
        wallbox: WallboxState | None = None,
        *,
        force_edge_trigger: bool = False,
    ) -> None:
        target_phases = default_phase_target(self._configured_installed_phases())
        self._phase_restore_pending = True
        self.phase_switch_manager.last_target = phase_label(target_phases)
        self.phase_switch_manager.last_block_reason = None
        self.phase_switch_manager.state = "restore_queued"
        self._sync_phase_switch_diagnostics()
        self._phase_restore_task = self._create_background_task(
            self._run_scheduled_phase_restore(wallbox, force_edge_trigger=force_edge_trigger)
        )

    async def _run_scheduled_phase_restore(
        self,
        wallbox: WallboxState | None = None,
        *,
        force_edge_trigger: bool = False,
    ) -> None:
        try:
            await self.async_restore_default_phase_mode(
                wallbox,
                request_refresh=False,
                force_edge_trigger=force_edge_trigger,
            )
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Default phase restore failed: %s", err)
            self._phase_restore_pending = True
        finally:
            await self._flush_pending_external_current_limit()
            self._clear_control_write_blocked("phase_switch_in_progress")
            self._sync_phase_switch_diagnostics()
            await self.async_request_refresh()

    async def async_restore_default_phase_mode(
        self,
        wallbox: WallboxState | None = None,
        *,
        request_refresh: bool = True,
        force_edge_trigger: bool = False,
    ) -> None:
        self._ensure_runtime_defaults()
        target_phases = default_phase_target(self._configured_installed_phases())
        current_snapshot = getattr(self, "data", None)
        wallbox = await self._fresh_wallbox_for_phase_action(wallbox or getattr(current_snapshot, "wallbox", None))
        if wallbox is not None and not wallbox.vehicle_connected:
            self.phase_switch_manager.last_target = phase_label(target_phases)
            self.phase_switch_manager.last_result = "vehicle_not_connected"
            self.phase_switch_manager.last_block_reason = "vehicle_not_connected"
            self.phase_switch_manager.state = "blocked"
            self._sync_phase_switch_diagnostics()
            self._clear_phase_session_override()
            if request_refresh:
                await self.async_request_refresh()
            return
        if (
            wallbox is not None
            and not force_edge_trigger
            and wallbox.phase_switch_mode_raw == default_phase_switch_raw_value(self._configured_installed_phases())
            and self._observed_phases_match_target(wallbox, target_phases)
        ):
            self.phase_switch_manager.last_target = phase_label(target_phases)
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
                require_vehicle=False,
                force_edge_trigger=force_edge_trigger,
            )
        finally:
            self._sync_phase_switch_diagnostics()
        self._clear_phase_session_override()
        await self._flush_pending_external_current_limit()
        if request_refresh:
            await self.async_request_refresh()

    def reset_phase_switch_state(self) -> None:
        self._ensure_runtime_defaults()
        self.phase_switch_manager.reset()
        self._sync_phase_switch_diagnostics()

    def _reset_phase_policy_runtime_state(self) -> None:
        self._phase_runtime().reset_policy_state()

    def _record_phase_policy_switch_attempt(self) -> None:
        self._phase_runtime().record_policy_switch_attempt()

    def _record_phase_policy_failed_attempt(self) -> None:
        self._phase_runtime().record_policy_failed_attempt()

    def _phase_session_start_settling(self) -> bool:
        started = self._phase_session_started_monotonic
        if started is None:
            return False
        return (monotonic() - started) < NEW_SESSION_PHASE_SETTLE_S

    def _apply_phase_policy_runtime_state(self, phase_policy: PhasePolicyDecision) -> PhasePolicyDecision:
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
        if (
            getattr(self, "_phase_switching_mode", PHASE_SWITCHING_MODE_OFF)
            != PHASE_SWITCHING_MODE_AUTOMATIC_SOLAR
            or not AUTOMATIC_PHASE_SWITCH_EXECUTION_ENABLED
        ):
            auto_block_reason = "automatic_phase_switching_disabled"
        elif self.control_config.control_mode != ControlMode.MANAGED_CONTROL:
            auto_block_reason = "external_controller_mode"
        elif self._phase_session_start_settling():
            auto_block_reason = "phase_startup_settling"
        elif target in self._phase_runtime().policy_failed_targets:
            auto_block_reason = "automatic_phase_switch_failed_this_session"
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

    async def _maybe_execute_automatic_phase_policy(
        self,
        phase_policy: PhasePolicyDecision,
        *,
        wallbox: WallboxState | None = None,
    ) -> bool:
        if not phase_policy.auto_ready or phase_policy.target not in {"1P", "3P"}:
            return False
        if self._phase_switching_mode != PHASE_SWITCHING_MODE_AUTOMATIC_SOLAR:
            return False
        if self.control_config.control_mode != ControlMode.MANAGED_CONTROL:
            return False
        if self._phase_switch_in_progress():
            return False
        self._schedule_phase_switch_task(
            1 if phase_policy.target == "1P" else 3,
            source="automatic",
            force_edge_trigger=False,
            wallbox=wallbox,
        )
        return True

    async def _maybe_schedule_phase_action(
        self,
        *,
        wallbox: WallboxState,
        phase_observability,
        phase_policy: PhasePolicyDecision,
        vehicle_disconnected: bool,
        phase_session_settling: bool,
    ) -> bool:
        """Pick at most one phase action for this update cycle.

        Phase actions are intentionally centralized here so startup restore,
        default restore, automatic solar switching and mismatch recovery cannot
        all make independent scheduling decisions in the same poll.
        """
        if vehicle_disconnected:
            return False
        if phase_session_settling:
            if self._configured_installed_phases() == PHASE_MODE_3P:
                self._phase_recovery_warning = "waiting_for_phase_startup_settle"
            return False
        if self._phase_recovery_warning == "waiting_for_phase_startup_settle":
            self._phase_recovery_warning = None

        await self._async_handle_phase_restore_state(wallbox)
        return await self._maybe_execute_automatic_phase_policy(phase_policy, wallbox=wallbox)

    def _wallbox_matches_default_phase(self, wallbox: WallboxState) -> bool:
        return wallbox_matches_default_phase(wallbox, self._configured_installed_phases())

    def _sync_phase_switch_diagnostics(self) -> None:
        self._phase_switch_runtime().sync_diagnostics()

    async def _read_wallbox_for_phase_switch(self) -> WallboxState | None:
        if not hasattr(self, "wallbox_reader"):
            current_snapshot = getattr(self, "data", None)
            return getattr(current_snapshot, "wallbox", None)
        return await self.wallbox_reader.read_wallbox_state(self._configured_installed_phases())

    async def _fresh_wallbox_for_phase_action(self, fallback: WallboxState | None = None) -> WallboxState | None:
        try:
            wallbox = await self._read_wallbox_for_phase_switch()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Could not refresh charger state before phase action: %s", err)
            return fallback
        if wallbox is None or fallback is None:
            return wallbox or fallback

        has_session_context = (
            wallbox.charge_point_state_raw is not None
            or wallbox.charge_state_raw is not None
            or wallbox.cable_state_raw is not None
            or wallbox.charging_state is not None and wallbox.charging_state != ChargingState.UNKNOWN
            or wallbox.phase_currents.max_present() is not None
        )
        return replace(
            wallbox,
            installed_phases=wallbox.installed_phases or fallback.installed_phases,
            charge_point_phase_count=wallbox.charge_point_phase_count or fallback.charge_point_phase_count,
            phase_switch_mode_raw=(
                wallbox.phase_switch_mode_raw
                if wallbox.phase_switch_mode_raw is not None
                else fallback.phase_switch_mode_raw
            ),
            current_limit_a=wallbox.current_limit_a if wallbox.current_limit_a is not None else fallback.current_limit_a,
            vehicle_connected=wallbox.vehicle_connected if has_session_context else fallback.vehicle_connected,
            charging_active=wallbox.charging_active if has_session_context else fallback.charging_active,
        )

    @staticmethod
    def _observed_phases_match_target(wallbox: WallboxState, target_phases: int) -> bool:
        return observed_phases_match_target(wallbox, target_phases)

    def _update_phase_session_override(self, target_phases: int) -> None:
        self._phase_switch_runtime().update_session_override(
            target_phases=target_phases,
            installed_phases=self._configured_installed_phases(),
        )

    def _clear_phase_session_override(self) -> None:
        self._phase_switch_runtime().clear_session_override()

    def _default_phase_switch_raw_value(self) -> int:
        return default_phase_switch_raw_value(self._configured_installed_phases())

    async def _async_handle_phase_restore_state(self, wallbox: WallboxState) -> bool:
        return self._phase_switch_runtime().handle_observed_register_state(
            wallbox,
            self._configured_installed_phases(),
        )
