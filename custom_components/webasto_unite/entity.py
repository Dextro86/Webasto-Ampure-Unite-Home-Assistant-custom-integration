from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_NAME, DOMAIN


class WebastoUniteCoordinatorEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    @property
    def device_info(self) -> DeviceInfo:
        entry = self.coordinator.entry
        data = self.coordinator.data
        wallbox = data.wallbox if data is not None else None
        return DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=DEFAULT_NAME,
            manufacturer=(wallbox.brand if wallbox is not None and wallbox.brand else "Webasto / Ampure"),
            model=(wallbox.model_name if wallbox is not None and wallbox.model_name else "Unite"),
            sw_version=(wallbox.firmware_version if wallbox is not None else None),
            serial_number=(wallbox.serial_number if wallbox is not None else None),
            configuration_url=f"http://{entry.data['host']}",
        )
