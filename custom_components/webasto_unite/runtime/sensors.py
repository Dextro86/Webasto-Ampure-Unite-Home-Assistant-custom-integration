from __future__ import annotations

import asyncio

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event

from ..const import (
    CONF_DLB_GRID_POWER_SENSOR,
    CONF_DLB_L1_SENSOR,
    CONF_DLB_L2_SENSOR,
    CONF_DLB_L3_SENSOR,
    CONF_SOLAR_EXPORT_POWER_SENSOR,
    CONF_SOLAR_GRID_POWER_SENSOR,
    CONF_SOLAR_IMPORT_POWER_SENSOR,
    CONF_SOLAR_SURPLUS_SENSOR,
)

SENSOR_REFRESH_DEBOUNCE_S = 0.4


class SensorListenerRuntime:
    """Owns Home Assistant control-input listener registration and debounce."""

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator

    def initialize(self) -> None:
        self.coordinator._sensor_unsubscribers = []
        self.coordinator._sensor_refresh_task = None

    def setup_listeners(self) -> None:
        entities = [
            self.coordinator.entry.options.get(CONF_DLB_L1_SENSOR),
            self.coordinator.entry.options.get(CONF_DLB_L2_SENSOR),
            self.coordinator.entry.options.get(CONF_DLB_L3_SENSOR),
            self.coordinator.entry.options.get(CONF_SOLAR_GRID_POWER_SENSOR),
            self.coordinator.entry.options.get(CONF_DLB_GRID_POWER_SENSOR),
            self.coordinator.entry.options.get(CONF_SOLAR_SURPLUS_SENSOR),
            self.coordinator.entry.options.get(CONF_SOLAR_IMPORT_POWER_SENSOR),
            self.coordinator.entry.options.get(CONF_SOLAR_EXPORT_POWER_SENSOR),
        ]
        entities = [entity_id for entity_id in entities if entity_id]
        if not entities:
            return

        @callback
        def _handle_state_change(_event):
            self.coordinator.async_set_updated_data(self.coordinator.data)
            self.schedule_refresh()

        self.coordinator._sensor_unsubscribers.append(
            async_track_state_change_event(self.coordinator.hass, entities, _handle_state_change)
        )

    async def debounced_refresh(self) -> None:
        await asyncio.sleep(SENSOR_REFRESH_DEBOUNCE_S)
        await self.coordinator.async_request_refresh()

    def schedule_refresh(self) -> None:
        if (
            self.coordinator._sensor_refresh_task is not None
            and not self.coordinator._sensor_refresh_task.done()
        ):
            self.coordinator._sensor_refresh_task.cancel()
        self.coordinator._sensor_refresh_task = self.coordinator.hass.async_create_task(
            self.debounced_refresh()
        )

    async def shutdown(self) -> None:
        if self.coordinator._sensor_refresh_task is not None:
            self.coordinator._sensor_refresh_task.cancel()
            try:
                await self.coordinator._sensor_refresh_task
            except asyncio.CancelledError:
                pass
            self.coordinator._sensor_refresh_task = None
        for unsub in self.coordinator._sensor_unsubscribers:
            unsub()
        self.coordinator._sensor_unsubscribers.clear()
