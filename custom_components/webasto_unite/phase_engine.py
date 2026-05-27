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


PHASE_SWITCH_PAUSE_BEFORE_S = 10.0
PHASE_SWITCH_PAUSE_AFTER_S = 20.0


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

    The manager verifies register 405 only. Measured active phases are observed
    for diagnostics and must not drive automatic correction.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.last_result: str | None = None
        self.last_block_reason: str | None = None
        self.last_target: str | None = None

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
        require_vehicle: bool = True,
    ) -> None:
        if self._lock.locked():
            self.last_target = f"{target_phases}P"
            self.last_result = "blocked"
            self.last_block_reason = "phase_switch_in_progress"
            raise ValueError("Phase switch blocked: phase_switch_in_progress")

        async with self._lock:
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
                raise ValueError(f"Phase switch blocked: {reason}")

            await self._execute_plan(
                decision.plan,
                client=client,
                write_queue=write_queue,
                flush_lock=flush_lock,
                sleep=sleep,
            )

    async def _execute_plan(self, plan: PhaseSwitchPlan, *, client, write_queue, flush_lock: asyncio.Lock, sleep) -> None:
        await write_queue.clear()
        verify_error: str | None = None
        try:
            async with flush_lock:
                if plan.was_charging:
                    await client.write(SET_CHARGE_CURRENT_A, 0)
                    await sleep(PHASE_SWITCH_PAUSE_BEFORE_S)

                await client.write(PHASE_SWITCH_MODE, plan.write_value)

                try:
                    readback = int(await client.read(PHASE_SWITCH_MODE))
                except Exception as err:  # noqa: BLE001
                    readback = None
                    verify_error = f"phase_switch_verify_unavailable:{err}"

                if readback is not None and readback != plan.write_value:
                    verify_error = f"phase_switch_verify_mismatch:{readback}"

                if plan.was_charging and plan.resume_current_a is not None:
                    await sleep(PHASE_SWITCH_PAUSE_AFTER_S)
                    await client.write(SET_CHARGE_CURRENT_A, int(round(plan.resume_current_a)))
        except Exception as err:  # noqa: BLE001
            self.last_result = "failed"
            self.last_block_reason = str(err)
            raise

        if verify_error is not None:
            self.last_result = "unverified"
            self.last_block_reason = verify_error
            raise ValueError(f"Phase switch could not be verified: {verify_error}")

        self.last_result = "verified"
        self.last_block_reason = None

    def reset(self) -> None:
        self.last_result = None
        self.last_block_reason = None
        self.last_target = None


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
    if observability.phase_switch_mode == f"{target_phases}P":
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
