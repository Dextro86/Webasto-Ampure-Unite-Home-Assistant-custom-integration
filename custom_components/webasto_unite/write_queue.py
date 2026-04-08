from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import IntEnum
from time import monotonic
from typing import Any

from .registers import RegisterDef


class WritePriority(IntEnum):
    SAFETY = 0
    CONTROL = 10
    KEEPALIVE = 20
    CURRENT = 30


@dataclass(slots=True)
class QueuedWrite:
    key: str
    register: RegisterDef
    value: Any
    priority: WritePriority
    created_monotonic: float = field(default_factory=monotonic)


@dataclass(slots=True)
class WriteQueueManager:
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _queue: dict[str, QueuedWrite] = field(default_factory=dict, init=False)
    _last_dequeued: QueuedWrite | None = field(default=None, init=False)

    async def enqueue(self, item: QueuedWrite) -> None:
        async with self._lock:
            existing = self._queue.get(item.key)
            if existing is None or item.priority <= existing.priority:
                self._queue[item.key] = item

    async def dequeue_next(self) -> QueuedWrite | None:
        async with self._lock:
            if not self._queue:
                return None
            item = sorted(self._queue.values(), key=lambda q: (q.priority, q.created_monotonic))[0]
            self._queue.pop(item.key, None)
            self._last_dequeued = item
            return item

    async def clear(self) -> None:
        async with self._lock:
            self._queue.clear()
            self._last_dequeued = None

    async def size(self) -> int:
        async with self._lock:
            return len(self._queue)

    async def peek_next_kind(self) -> str | None:
        async with self._lock:
            if not self._queue:
                return None
            item = sorted(self._queue.values(), key=lambda q: (q.priority, q.created_monotonic))[0]
            return item.key


    @property
    def last_dequeued(self) -> QueuedWrite | None:
        return self._last_dequeued

    async def peek_next(self) -> QueuedWrite | None:
        async with self._lock:
            if not self._queue:
                return None
            return sorted(self._queue.values(), key=lambda q: (q.priority, q.created_monotonic))[0]
