import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from custom_components.webasto_unite.models import ChargeMode, ControlConfig, ControlReason
from custom_components.webasto_unite.registers import LIFE_BIT, SET_CHARGE_CURRENT_A
from custom_components.webasto_unite.write_queue import WriteQueueManager
from custom_components.webasto_unite.write_runtime import WriteRuntime, WriteRuntimeState


def test_forced_keepalive_enqueues_write_of_one():
    async def _run():
        write_queue = WriteQueueManager()
        runtime = WriteRuntime(
            ControlConfig(keepalive_interval_s=999),
            write_queue=write_queue,
            client=None,
            controller=None,
            state=WriteRuntimeState(keepalive_started_monotonic=0.0),
            monotonic_fn=lambda: 1000.0,
        )

        await runtime.enqueue_keepalive_if_needed()
        item = await write_queue.dequeue_next()

        assert item is not None
        assert item.register == LIFE_BIT
        assert item.value == 1

    asyncio.run(_run())


def test_keepalive_overdue_uses_interval_budget():
    runtime = WriteRuntime(
        ControlConfig(keepalive_interval_s=10),
        write_queue=WriteQueueManager(),
        client=None,
        controller=None,
    )

    assert runtime.is_keepalive_overdue(12.0) is False
    assert runtime.is_keepalive_overdue(16.0) is True


def test_current_write_records_value_reason_register_and_age():
    async def _run():
        write_queue = WriteQueueManager()
        runtime = WriteRuntime(
            ControlConfig(),
            write_queue=write_queue,
            client=SimpleNamespace(write=AsyncMock()),
            controller=None,
            state=WriteRuntimeState(keepalive_started_monotonic=0.0),
            monotonic_fn=lambda: 42.0,
        )
        decision = SimpleNamespace(
            charging_enabled=True,
            reason=ControlReason.FIXED_CURRENT_MODE,
            should_write=True,
            target_current_a=15.0,
        )

        await runtime.enqueue_decision(
            decision,
            effective_mode=ChargeMode.FIXED_CURRENT,
            current_snapshot=None,
            allows_control_writes=True,
            enqueue_keepalive=AsyncMock(),
        )
        await runtime.flush_write_queue()

        runtime.client.write.assert_awaited_once_with(SET_CHARGE_CURRENT_A, 15)
        assert runtime.last_control_write_value_a == 15.0
        assert runtime.last_control_write_reason == "fixed_current_mode"
        assert runtime.last_control_write_register == "set_charge_current_a"
        assert runtime.last_control_write_age_seconds() == 0.0
        assert runtime.last_control_write_blocked_reason is None

    asyncio.run(_run())


def test_monitoring_only_records_blocked_control_write_reason():
    async def _run():
        write_queue = WriteQueueManager()
        runtime = WriteRuntime(
            ControlConfig(),
            write_queue=write_queue,
            client=None,
            controller=None,
            state=WriteRuntimeState(keepalive_started_monotonic=0.0),
        )
        decision = SimpleNamespace(
            charging_enabled=True,
            reason=ControlReason.NORMAL_MODE,
            should_write=True,
            target_current_a=16.0,
        )

        await runtime.enqueue_decision(
            decision,
            effective_mode=ChargeMode.NORMAL,
            current_snapshot=None,
            allows_control_writes=False,
            enqueue_keepalive=AsyncMock(),
        )

        assert await write_queue.size() == 0
        assert runtime.last_control_write_blocked_reason == "monitoring_only"
        assert runtime.last_control_write_value_a is None

    asyncio.run(_run())


def test_external_controller_records_distinct_blocked_control_write_reason():
    async def _run():
        write_queue = WriteQueueManager()
        runtime = WriteRuntime(
            ControlConfig(),
            write_queue=write_queue,
            client=None,
            controller=None,
            state=WriteRuntimeState(keepalive_started_monotonic=0.0),
        )
        decision = SimpleNamespace(
            charging_enabled=True,
            reason=ControlReason.NORMAL_MODE,
            should_write=True,
            target_current_a=16.0,
        )

        await runtime.enqueue_decision(
            decision,
            effective_mode=ChargeMode.NORMAL,
            current_snapshot=None,
            allows_control_writes=False,
            blocked_reason="external_controller_mode",
            enqueue_keepalive=AsyncMock(),
        )

        assert await write_queue.size() == 0
        assert runtime.last_control_write_blocked_reason == "external_controller_mode"

    asyncio.run(_run())
