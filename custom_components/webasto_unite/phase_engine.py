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


PHASE_SWITCH_WAIT_BEFORE_WRITE_S = 20.0
PHASE_SWITCH_WAIT_BEFORE_REGISTER_VERIFY_S = 20.0
PHASE_SWITCH_WAIT_BEFORE_RESUME_S = 20.0
PHASE_SWITCH_PHYSICAL_OBSERVATION_INTERVAL_S = 5.0
PHASE_SWITCH_PHYSICAL_VERIFY_POLLS = 2


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
    ) -> None:
        await write_queue.clear()
        verify_error: str | None = None
        register_verified = False
        try:
            async with flush_lock:
                if plan.was_charging:
                    self.state = "pausing"
                    await client.write(SET_CHARGE_CURRENT_A, 0)
                    self.state = "waiting_before_write"
                    await sleep(PHASE_SWITCH_WAIT_BEFORE_WRITE_S)

                self.state = "writing_register"
                await client.write(PHASE_SWITCH_MODE, plan.write_value)
                self.state = "waiting_before_register_verify"
                await sleep(PHASE_SWITCH_WAIT_BEFORE_REGISTER_VERIFY_S)

                self.state = "verifying_register"
                try:
                    readback = int(await client.read(PHASE_SWITCH_MODE))
                except Exception as err:  # noqa: BLE001
                    readback = None
                    verify_error = f"phase_switch_verify_unavailable:{err}"

                if readback is not None and readback != plan.write_value:
                    verify_error = f"phase_switch_verify_mismatch:{readback}"
                register_verified = verify_error is None

                if plan.was_charging and plan.resume_current_a is not None:
                    self.state = "waiting_before_resume"
                    await sleep(PHASE_SWITCH_WAIT_BEFORE_RESUME_S)
                    self.state = "resuming"
                    await client.write(SET_CHARGE_CURRENT_A, int(round(plan.resume_current_a)))
        except Exception as err:  # noqa: BLE001
            self.last_result = "failed"
            self.last_block_reason = str(err)
            self.state = "failed"
            raise

        if verify_error is not None:
            self.last_result = "register_unverified"
            self.last_block_reason = verify_error
            self.state = "register_unverified"
            raise ValueError(f"Phase switch could not be verified: {verify_error}")

        if register_verified and plan.was_charging and read_wallbox is not None:
            physical_result = await self._observe_physical_phase_result(plan, read_wallbox=read_wallbox, sleep=sleep)
            if physical_result is not None:
                self.last_result = physical_result
                self.last_block_reason = None if physical_result == "physical_verified" else "physical_phase_mismatch"
                self.state = physical_result
                return

        self.last_result = "register_verified"
        self.last_block_reason = None
        self.state = "register_verified"

    async def _observe_physical_phase_result(self, plan: PhaseSwitchPlan, *, read_wallbox, sleep) -> str | None:
        observed: list[int | None] = []
        self.state = "observing_physical"
        for _ in range(PHASE_SWITCH_PHYSICAL_VERIFY_POLLS):
            await sleep(PHASE_SWITCH_PHYSICAL_OBSERVATION_INTERVAL_S)
            wallbox = await read_wallbox()
            if wallbox is None or not wallbox.charging_active:
                observed.append(None)
                continue
            observed.append(wallbox.phases_in_use)

        if observed and all(value == plan.target_phases for value in observed):
            return "physical_verified"
        if any(value is not None for value in observed):
            self.last_block_reason = f"physical_phase_mismatch:{observed[-1]}"
            return "register_verified_physical_mismatch"
        return "register_verified"

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
