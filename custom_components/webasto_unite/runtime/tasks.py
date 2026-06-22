from __future__ import annotations

import asyncio


class TaskRuntime:
    """Small helper for cancelling coordinator-owned background tasks."""

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator

    async def cancel_task_attr(self, attr_name: str) -> None:
        task = getattr(self.coordinator, attr_name, None)
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        setattr(self.coordinator, attr_name, None)
