from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .const import PHASE_SWITCHING_MODE_MANUAL_ONLY
from .models import ControlConfig, ControlMode, WallboxState
from .phase_observer import (
    PHASE_SWITCH_VALUE_1P,
    PHASE_SWITCH_VALUE_3P,
    build_phase_observability,
)
from .registers import PHASE_SWITCH_MODE, SET_CHARGE_CURRENT_A


PHASE_SWITCH_MIN_PAUSE_CONFIRM_S = 30.0
PHASE_SWITCH_PAUSE_CONFIRM_TIMEOUT_S = 90.0
PHASE_SWITCH_REGISTER_VERIFY_INTERVAL_S = 5.0
PHASE_SWITCH_REGISTER_VERIFY_TIMEOUT_S = 60.0
PHASE_SWITCH_WAIT_BEFORE_RESUME_S = 30.0
PHASE_SWITCH_PHYSICAL_OBSERVATION_INTERVAL_S = 5.0
PHASE_SWITCH_PHYSICAL_VERIFY_TIMEOUT_S = 120.0
PHASE_SWITCH_REQUIRED_STABLE_POLLS = 2
PHASE_SWITCH_MAX_SEQUENCE_ATTEMPTS = 2
PHASE_SWITCH_PAUSE_CURRENT_THRESHOLD_A = 1.0
PHASE_SWITCH_PAUSE_POWER_THRESHOLD_W = 150.0

_TERMINAL_STATES = {
    "idle",
    "blocked",
    "failed",
    "pause_not_confirmed",
    "register_unverified",
    "register_reverted",
    "register_verified",
    "physical_verified",
    "vehicle_did_not_resume",
    "physical_timeout",
    "already_in_target_phase",
}

REGISTER_ACCEPTED_RESULTS = {
    "register_verified",
    "physical_verified",
    "physical_timeout",
    "vehicle_did_not_resume",
    "already_in_target_phase",
}


@dataclass(frozen=True, slots=True)
class PhaseSwitchPlan:
    target_phases: int
    write_value: int
    was_charging: bool
    resume_current_a: float | None


@dataclass(frozen=True, slots=True)
class PhaseSwitchDecision:
    allowed: bool
    plan: PhaseSwitchPlan | None = None
    block_reason: str | None = None


class PhaseSwitchManager:
    """Owns explicit manual phase-switch execution and diagnostics.

    The manager separates register verification from physical phase
    verification. Register 405 can be accepted while the active charging
    session still keeps using the old phase layout.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.last_result: str | None = None
        self.last_block_reason: str | None = None
        self.last_target: str | None = None
        self.state: str = "idle"

    @property
    def active(self) -> bool:
        return self._lock.locked() and self.state not in _TERMINAL_STATES

    async def request(
        self,
        *,
        phase_switching_mode: str,
        wallbox: WallboxState | None,
        target_phases: int,
        config: ControlConfig,
        client,
        write_queue,
        flush_lock: asyncio.Lock,
        sleep,
        read_wallbox=None,
        pause_charging=None,
        resume_charging=None,
        require_vehicle: bool = True,
    ) -> None:
        if self._lock.locked():
            self.last_target = f"{target_phases}P"
            self.last_result = "blocked"
            self.last_block_reason = "phase_switch_in_progress"
            self.state = "blocked"
            raise ValueError("Phase switch blocked: phase_switch_in_progress")

        async with self._lock:
            self.state = "requested"
            self.last_target = f"{target_phases}P"
            decision = build_manual_phase_switch_decision(
                phase_switching_mode=phase_switching_mode,
                wallbox=wallbox,
                target_phases=target_phases,
                config=config,
                require_vehicle=require_vehicle,
            )
            if not decision.allowed or decision.plan is None:
                reason = decision.block_reason or "phase_switch_blocked"
                self.last_result = "blocked"
                self.last_block_reason = reason
                self.state = "blocked"
                raise ValueError(f"Phase switch blocked: {reason}")

            await self._execute_plan(
                decision.plan,
                client=client,
                write_queue=write_queue,
                flush_lock=flush_lock,
                sleep=sleep,
                read_wallbox=read_wallbox,
                pause_charging=pause_charging,
                resume_charging=resume_charging,
            )

    async def _execute_plan(
        self,
        plan: PhaseSwitchPlan,
        *,
        client,
        write_queue,
        flush_lock: asyncio.Lock,
        sleep,
        read_wallbox=None,
        pause_charging=None,
        resume_charging=None,
    ) -> None:
        await write_queue.clear()
        attempts = PHASE_SWITCH_MAX_SEQUENCE_ATTEMPTS if plan.was_charging and read_wallbox is not None else 1
        for attempt in range(attempts):
            try:
                await self._run_sequence_attempt(
                    plan,
                    client=client,
                    write_queue=write_queue,
                    flush_lock=flush_lock,
                    sleep=sleep,
                    read_wallbox=read_wallbox,
                    pause_charging=pause_charging,
                    resume_charging=resume_charging,
                    retry=attempt > 0,
                )
            except Exception as err:  # noqa: BLE001
                if self.last_result in {"pause_not_confirmed", "register_unverified", "register_reverted"}:
                    raise
                self.last_result = "failed"
                self.last_block_reason = str(err)
                self.state = "failed"
                raise

            if plan.was_charging and read_wallbox is not None:
                physical_result = await self._observe_physical_phase_result(plan, read_wallbox=read_wallbox, sleep=sleep)
                if physical_result == "physical_verified":
                    self.last_result = physical_result
                    self.last_block_reason = None
                    self.state = physical_result
                    return
                if physical_result in {"physical_timeout", "vehicle_did_not_resume"} and attempt < attempts - 1:
                    self.state = "retrying_sequence"
                    continue
                self.last_result = physical_result
                self.last_block_reason = physical_result
                self.state = physical_result
                return

        self.last_result = "register_verified"
        self.last_block_reason = None
        self.state = "register_verified"

    async def _run_sequence_attempt(
        self,
        plan: PhaseSwitchPlan,
        *,
        client,
        write_queue,
        flush_lock: asyncio.Lock,
        sleep,
        read_wallbox=None,
        pause_charging=None,
        resume_charging=None,
        retry: bool = False,
    ) -> None:
        if retry:
            self.state = "retrying_sequence"
        if plan.was_charging:
            self.state = "retry_pausing" if retry else "pausing"
            if pause_charging is not None:
                await pause_charging()
            else:
                await self._write_direct(
                    client=client,
                    write_queue=write_queue,
                    flush_lock=flush_lock,
                    register=SET_CHARGE_CURRENT_A,
                    value=0,
                )
            if not await self._wait_for_pause_confirmed(read_wallbox=read_wallbox, sleep=sleep):
                self.last_result = "pause_not_confirmed"
                self.last_block_reason = "pause_not_confirmed"
                self.state = "pause_not_confirmed"
                raise ValueError("Phase switch blocked: pause_not_confirmed")

        self.state = "retry_writing_register" if retry else "writing_register"
        await self._write_direct(
            client=client,
            write_queue=write_queue,
            flush_lock=flush_lock,
            register=PHASE_SWITCH_MODE,
            value=plan.write_value,
        )

        register_result = await self._wait_for_register_target(
            client=client,
            plan=plan,
            sleep=sleep,
        )
        if register_result != "register_verified":
            self.last_result = register_result
            self.last_block_reason = register_result
            self.state = register_result
            raise ValueError(f"Phase switch could not be verified: {register_result}")

        if plan.was_charging and plan.resume_current_a is not None:
            self.state = "retry_waiting_before_resume" if retry else "waiting_before_resume"
            await sleep(PHASE_SWITCH_WAIT_BEFORE_RESUME_S)
            self.state = "retry_resuming" if retry else "resuming"
            if resume_charging is not None:
                await resume_charging(plan.resume_current_a)
            else:
                await self._write_direct(
                    client=client,
                    write_queue=write_queue,
                    flush_lock=flush_lock,
                    register=SET_CHARGE_CURRENT_A,
                    value=int(round(plan.resume_current_a)),
                )

    async def _write_direct(self, *, client, write_queue, flush_lock: asyncio.Lock, register, value: int) -> None:
        async with flush_lock:
            await write_queue.clear()
            await client.write(register, value)

    async def _wait_for_pause_confirmed(self, *, read_wallbox, sleep) -> bool:
        self.state = "waiting_for_pause"
        await sleep(PHASE_SWITCH_MIN_PAUSE_CONFIRM_S)
        if read_wallbox is None:
            return False

        stable_pause_reads = 0
        polls = int(PHASE_SWITCH_PAUSE_CONFIRM_TIMEOUT_S / PHASE_SWITCH_REGISTER_VERIFY_INTERVAL_S)
        for index in range(max(1, polls)):
            wallbox = await read_wallbox()
            if _pause_is_confirmed(wallbox):
                stable_pause_reads += 1
                if stable_pause_reads >= PHASE_SWITCH_REQUIRED_STABLE_POLLS:
                    return True
            else:
                stable_pause_reads = 0
            if index < polls - 1:
                await sleep(PHASE_SWITCH_REGISTER_VERIFY_INTERVAL_S)
        return False

    async def _wait_for_register_target(self, *, client, plan: PhaseSwitchPlan, sleep) -> str:
        self.state = "verifying_register"
        stable_target_reads = 0
        saw_target = False
        saw_read_error = False
        polls = int(PHASE_SWITCH_REGISTER_VERIFY_TIMEOUT_S / PHASE_SWITCH_REGISTER_VERIFY_INTERVAL_S)
        for _ in range(max(1, polls)):
            await sleep(PHASE_SWITCH_REGISTER_VERIFY_INTERVAL_S)
            try:
                readback = int(await client.read(PHASE_SWITCH_MODE))
            except Exception:  # noqa: BLE001
                saw_read_error = True
                stable_target_reads = 0
                continue

            if readback == plan.write_value:
                saw_target = True
                stable_target_reads += 1
                if stable_target_reads >= PHASE_SWITCH_REQUIRED_STABLE_POLLS:
                    return "register_verified"
                continue

            stable_target_reads = 0
            if saw_target:
                return "register_reverted"

        if saw_read_error and not saw_target:
            return "register_unverified"
        return "register_reverted" if saw_target else "register_unverified"

    async def _observe_physical_phase_result(self, plan: PhaseSwitchPlan, *, read_wallbox, sleep) -> str | None:
        self.state = "observing_physical"
        stable_physical_reads = 0
        saw_charging = False
        polls = int(PHASE_SWITCH_PHYSICAL_VERIFY_TIMEOUT_S / PHASE_SWITCH_PHYSICAL_OBSERVATION_INTERVAL_S)
        for _ in range(max(1, polls)):
            await sleep(PHASE_SWITCH_PHYSICAL_OBSERVATION_INTERVAL_S)
            wallbox = await read_wallbox()
            if wallbox is None:
                continue
            if wallbox.phase_switch_mode_raw is not None and wallbox.phase_switch_mode_raw != plan.write_value:
                return "register_reverted"
            if wallbox.charging_active:
                saw_charging = True
            if wallbox.charging_active and wallbox.phases_in_use == plan.target_phases:
                stable_physical_reads += 1
                if stable_physical_reads >= PHASE_SWITCH_REQUIRED_STABLE_POLLS:
                    return "physical_verified"
            else:
                stable_physical_reads = 0
        return "physical_timeout" if saw_charging else "vehicle_did_not_resume"

    def reset(self) -> None:
        self.last_result = None
        self.last_block_reason = None
        self.last_target = None
        self.state = "idle"


def build_manual_phase_switch_decision(
    *,
    phase_switching_mode: str,
    wallbox: WallboxState | None,
    target_phases: int,
    config: ControlConfig,
    require_vehicle: bool = True,
) -> PhaseSwitchDecision:
    """Validate an explicit manual phase-switch request.

    This module only decides whether a manual request is allowed and what value
    should be written. The coordinator owns the actual Modbus writes.
    """
    if phase_switching_mode != PHASE_SWITCHING_MODE_MANUAL_ONLY:
        return _blocked("manual_phase_switching_disabled")
    if config.control_mode != ControlMode.MANAGED_CONTROL:
        return _blocked("integration_control_disabled")
    if target_phases not in (1, 3):
        return _blocked("invalid_target_phase")
    if wallbox is None:
        return _blocked("charger_state_unavailable")
    if not wallbox.available:
        return _blocked("charger_unavailable")

    observability = build_phase_observability(wallbox)
    if observability.phase_switch_block_reason is not None and (
        require_vehicle or observability.phase_switch_block_reason != "vehicle_not_connected"
    ):
        return _blocked(observability.phase_switch_block_reason)
    if observability.phase_switch_mode == f"{target_phases}P" and _physical_phase_matches(wallbox, target_phases):
        return _blocked("already_in_target_phase")

    resume_current_a = None
    was_charging = bool(wallbox.charging_active)
    if was_charging:
        reported_limit = wallbox.current_limit_a
        if reported_limit is not None and config.min_current_a <= reported_limit <= config.max_current_a:
            resume_current_a = float(round(reported_limit))
        else:
            resume_current_a = float(round(config.min_current_a))

    return PhaseSwitchDecision(
        allowed=True,
        plan=PhaseSwitchPlan(
            target_phases=target_phases,
            write_value=PHASE_SWITCH_VALUE_1P if target_phases == 1 else PHASE_SWITCH_VALUE_3P,
            was_charging=was_charging,
            resume_current_a=resume_current_a,
        ),
    )

def _blocked(reason: str) -> PhaseSwitchDecision:
    return PhaseSwitchDecision(allowed=False, block_reason=reason)


def _physical_phase_matches(wallbox: WallboxState, target_phases: int) -> bool:
    if not wallbox.charging_active:
        return True
    return wallbox.phases_in_use == target_phases


def _pause_is_confirmed(wallbox: WallboxState | None) -> bool:
    if wallbox is None:
        return False
    if not wallbox.charging_active:
        return True
    max_current = wallbox.phase_currents.max_present()
    if max_current is None:
        max_current = wallbox.actual_current_a
    active_power = wallbox.active_power_w
    if max_current is None or active_power is None:
        return False
    return max_current <= PHASE_SWITCH_PAUSE_CURRENT_THRESHOLD_A and active_power <= PHASE_SWITCH_PAUSE_POWER_THRESHOLD_W
