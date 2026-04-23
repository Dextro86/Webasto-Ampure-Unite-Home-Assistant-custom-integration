import asyncio
from time import monotonic

from custom_components.webasto_unite.models import ControlConfig
from custom_components.webasto_unite.write_queue import WriteQueueManager, QueuedWrite, WritePriority
from custom_components.webasto_unite.registers import LIFE_BIT


class DummyCoordinator:
    def __init__(self):
        self.control_config = ControlConfig(keepalive_interval_s=999)
        self.write_queue = WriteQueueManager()
        self._last_keepalive_sent_monotonic = 0.0
        self._keepalive_started_monotonic = monotonic() - 1000

    async def _enqueue_keepalive_if_needed(self):
        from time import monotonic

        now = monotonic()
        elapsed = now - self._last_keepalive_sent_monotonic if self._last_keepalive_sent_monotonic else now - self._keepalive_started_monotonic
        if elapsed < self.control_config.keepalive_interval_s:
            return
        await self.write_queue.enqueue(
            QueuedWrite("keepalive", LIFE_BIT, 1, WritePriority.KEEPALIVE)
        )

    def _is_keepalive_overdue(self, age_s: float | None) -> bool:
        if age_s is None:
            return False
        return age_s > (self.control_config.keepalive_interval_s * 1.5)


def test_forced_keepalive_enqueues_write_of_one():
    async def _run():
        coordinator = DummyCoordinator()
        await coordinator._enqueue_keepalive_if_needed()
        item = await coordinator.write_queue.dequeue_next()
        assert item is not None
        assert item.register == LIFE_BIT
        assert item.value == 1

    asyncio.run(_run())


def test_keepalive_overdue_uses_interval_budget():
    coordinator = DummyCoordinator()
    coordinator.control_config = ControlConfig(keepalive_interval_s=10)

    assert coordinator._is_keepalive_overdue(12.0) is False
    assert coordinator._is_keepalive_overdue(16.0) is True
