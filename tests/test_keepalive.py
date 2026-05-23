import asyncio

from custom_components.webasto_unite.models import ControlConfig
from custom_components.webasto_unite.registers import LIFE_BIT
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
