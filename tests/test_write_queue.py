import asyncio

from custom_components.webasto_unite.write_queue import QueuedWrite, WritePriority, WriteQueueManager
from custom_components.webasto_unite.registers import LIFE_BIT, SAFE_CURRENT_A, SET_CHARGE_CURRENT_A


def test_queue_priority_and_coalescing():
    async def _run():
        queue = WriteQueueManager()
        await queue.enqueue(QueuedWrite('current_limit', SET_CHARGE_CURRENT_A, 10, WritePriority.CURRENT))
        await queue.enqueue(QueuedWrite('keepalive', LIFE_BIT, 1, WritePriority.KEEPALIVE))
        await queue.enqueue(QueuedWrite('current_limit', SET_CHARGE_CURRENT_A, 12, WritePriority.CURRENT))
        await queue.enqueue(QueuedWrite('safe_current', SAFE_CURRENT_A, 6, WritePriority.SAFETY))

        first = await queue.dequeue_next()
        second = await queue.dequeue_next()
        third = await queue.dequeue_next()
        fourth = await queue.dequeue_next()

        assert first.key == 'safe_current'
        assert second.key == 'keepalive'
        assert third.key == 'current_limit'
        assert third.value == 12
        assert fourth is None

    asyncio.run(_run())
