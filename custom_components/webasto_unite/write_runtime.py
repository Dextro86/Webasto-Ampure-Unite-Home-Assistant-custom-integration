from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic
from typing import Awaitable, Callable

from .models import ChargeMode, ControlConfig, ControlReason
from .registers import COMM_TIMEOUT_S, LIFE_BIT, SAFE_CURRENT_A, SET_CHARGE_CURRENT_A
from .write_queue import QueuedWrite, WritePriority, WriteQueueManager


@dataclass(slots=True)
class WriteRuntimeState:
    keepalive_started_monotonic: float
    last_keepalive_sent_monotonic: float = 0.0
    keepalive_sent_count: int = 0
    keepalive_write_failures: int = 0


class WriteRuntime:
    """Owns queued writes, keepalive timing, and write-result bookkeeping."""

    def __init__(
        self,
        config: ControlConfig,
        *,
        write_queue: WriteQueueManager,
        client,
        controller,
        state: WriteRuntimeState | None = None,
        monotonic_fn: Callable[[], float] = monotonic,
    ) -> None:
        self.config = config
        self.write_queue = write_queue
        self.client = client
        self.controller = controller
        self._monotonic = monotonic_fn
        self.state = state or WriteRuntimeState(keepalive_started_monotonic=self._monotonic())
        self.flush_lock = asyncio.Lock()

    @property
    def keepalive_sent_count(self) -> int:
        return self.state.keepalive_sent_count

    @property
    def keepalive_write_failures(self) -> int:
        return self.state.keepalive_write_failures

    async def enqueue_keepalive_if_needed(self) -> None:
        now = self._monotonic()
        elapsed = (
            now - self.state.last_keepalive_sent_monotonic
            if self.state.last_keepalive_sent_monotonic
            else now - self.state.keepalive_started_monotonic
        )
        if elapsed < self.config.keepalive_interval_s:
            return
        await self.write_queue.enqueue(QueuedWrite("keepalive", LIFE_BIT, 1, WritePriority.KEEPALIVE))

    async def enqueue_decision(
        self,
        decision,
        *,
        effective_mode: ChargeMode,
        current_snapshot,
        allows_control_writes: bool,
        enqueue_keepalive: Callable[[], Awaitable[None]],
    ) -> None:
        if not decision.charging_enabled and decision.reason == ControlReason.OFF_MODE:
            await self.write_queue.clear()
            await enqueue_keepalive()

        if not allows_control_writes:
            return

        if (
            not decision.charging_enabled
            and decision.reason == ControlReason.BELOW_MIN_CURRENT
            and getattr(decision, "dominant_limit_reason", None)
            in {ControlReason.DLB_LIMITED, ControlReason.CABLE_LIMITED, ControlReason.EV_LIMITED}
        ):
            await self.write_queue.clear()
            await enqueue_keepalive()
            await self.write_queue.enqueue(
                QueuedWrite("current_limit", SET_CHARGE_CURRENT_A, 0, WritePriority.CONTROL)
            )
            return

        if (
            not decision.charging_enabled
            and effective_mode == ChargeMode.SOLAR
            and decision.reason in (ControlReason.BELOW_MIN_CURRENT, ControlReason.SENSOR_UNAVAILABLE)
        ) and (
            current_snapshot is None
            or current_snapshot.wallbox.charging_active
            or current_snapshot.wallbox.vehicle_connected
            or (current_snapshot.wallbox.current_limit_a is not None and current_snapshot.wallbox.current_limit_a > 0)
        ):
            await self.write_queue.clear()
            await enqueue_keepalive()
            await self.write_queue.enqueue(
                QueuedWrite("current_limit", SET_CHARGE_CURRENT_A, 0, WritePriority.CONTROL)
            )
            return

        if decision.should_write and decision.target_current_a is not None:
            await self.write_queue.enqueue(
                QueuedWrite(
                    "current_limit",
                    SET_CHARGE_CURRENT_A,
                    int(round(decision.target_current_a)),
                    WritePriority.CURRENT,
                )
            )

    async def sync_static_registers(self, *, allows_static_sync: bool) -> None:
        if not allows_static_sync:
            return
        await self.write_queue.enqueue(
            QueuedWrite("safe_current", SAFE_CURRENT_A, int(round(self.config.safe_current_a)), WritePriority.SAFETY)
        )
        await self.write_queue.enqueue(
            QueuedWrite(
                "communication_timeout",
                COMM_TIMEOUT_S,
                int(round(self.config.communication_timeout_s)),
                WritePriority.SAFETY,
            )
        )
        await self.flush_write_queue()

    async def flush_write_queue(self) -> None:
        async with self.flush_lock:
            while True:
                item = await self.write_queue.dequeue_next()
                if item is None:
                    break
                try:
                    await self.client.write(item.register, item.value)
                except Exception:
                    if item.key == "keepalive":
                        self.state.keepalive_write_failures += 1
                    raise
                if item.key == "keepalive":
                    self.state.last_keepalive_sent_monotonic = self._monotonic()
                    self.state.keepalive_sent_count += 1
                if item.key == "current_limit" and self.controller is not None:
                    self.controller.mark_current_written(float(item.value))

    def keepalive_age_seconds(self) -> float | None:
        reference = self.state.last_keepalive_sent_monotonic or self.state.keepalive_started_monotonic
        return round(max(0.0, self._monotonic() - reference), 1)

    def is_keepalive_overdue(self, age_s: float | None) -> bool:
        if age_s is None:
            return False
        return age_s > (self.config.keepalive_interval_s * 1.5)
