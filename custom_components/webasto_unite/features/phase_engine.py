from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic

from ..const import PHASE_SWITCHING_MODE_OFF
from ..models import ControlConfig, ControlMode, WallboxState
from .phase_observer import (
    PHASE_SWITCH_VALUE_1P,
    PHASE_SWITCH_VALUE_3P,
    build_phase_observability,
)
from ..modbus.registers import PHASE_SWITCH_MODE


PHASE_SWITCH_SETTLING_S = 60.0

_TERMINAL_STATES = {
    "idle",
    "blocked",
    "failed",
    "register_written",
    "already_in_target_phase",
    "phase_switch_settling",
}

REGISTER_ACCEPTED_RESULTS = {
    "register_written",
    "already_in_target_phase",
}


@dataclass(frozen=True, slots=True)
class PhaseSwitchPlan:
    target_phases: int
    write_value: int


@dataclass(frozen=True, slots=True)
class PhaseSwitchDecision:
    allowed: bool
    plan: PhaseSwitchPlan | None = None
    block_reason: str | None = None


class PhaseSwitchManager:
    """Owns explicit phase-switch register writes.

    The Webasto/Vestel phase request is register 405: 0 = 1P, 1 = 3P.
    Like EVCC's Vestel driver, this manager only writes the requested register
    value. It does not pause charging, resume charging, retry, or treat physical
    phase observations as success/failure criteria. Physical phase usage remains
    a diagnostic signal from the normal polling loop.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.last_result: str | None = None
        self.last_block_reason: str | None = None
        self.last_target: str | None = None
        self._state: str = "idle"
        self._settling_started_monotonic: float | None = None

    @property
    def state(self) -> str:
        if (
            self._state == "phase_switch_settling"
            and self._settling_started_monotonic is not None
            and monotonic() - self._settling_started_monotonic >= PHASE_SWITCH_SETTLING_S
        ):
            return "idle"
        return self._state

    @state.setter
    def state(self, value: str) -> None:
        self._state = value
        if value != "phase_switch_settling":
            self._settling_started_monotonic = None

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
        require_vehicle: bool = True,
        force_edge_trigger: bool = False,
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
                force_edge_trigger=force_edge_trigger,
            )
            if not decision.allowed or decision.plan is None:
                reason = decision.block_reason or "phase_switch_blocked"
                self.last_result = "blocked"
                self.last_block_reason = reason
                self.state = "blocked"
                raise ValueError(f"Phase switch blocked: {reason}")

            await self._write_phase_register(
                decision.plan,
                client=client,
                write_queue=write_queue,
                flush_lock=flush_lock,
            )

    async def _write_phase_register(self, plan: PhaseSwitchPlan, *, client, write_queue, flush_lock: asyncio.Lock) -> None:
        self.state = "writing_register"
        async with flush_lock:
            await write_queue.clear()
            await client.write(PHASE_SWITCH_MODE, plan.write_value)
        self.last_result = "register_written"
        self.last_block_reason = None
        self.state = "phase_switch_settling"
        self._settling_started_monotonic = monotonic()

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
    force_edge_trigger: bool = False,
) -> PhaseSwitchDecision:
    """Validate an explicit phase-switch request."""
    if phase_switching_mode == PHASE_SWITCHING_MODE_OFF:
        return _blocked("manual_phase_switching_disabled")
    if config.control_mode not in {ControlMode.MANAGED_CONTROL, ControlMode.EXTERNAL_CONTROLLER}:
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
    return PhaseSwitchDecision(
        allowed=True,
        plan=PhaseSwitchPlan(
            target_phases=target_phases,
            write_value=PHASE_SWITCH_VALUE_1P if target_phases == 1 else PHASE_SWITCH_VALUE_3P,
        ),
    )


def _blocked(reason: str) -> PhaseSwitchDecision:
    return PhaseSwitchDecision(allowed=False, block_reason=reason)
