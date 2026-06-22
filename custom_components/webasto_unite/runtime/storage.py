from __future__ import annotations

from homeassistant.helpers.storage import Store

from ..const import DOMAIN, STORAGE_KEY_CHARGING_STATE


class ChargingStateStorageRuntime:
    """Persists the user-facing charging on/off state across HA restarts."""

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator

    def initialize(self) -> None:
        entry_id = getattr(self.coordinator.entry, "entry_id", "default")
        self.coordinator._charging_state_store = Store(
            self.coordinator.hass,
            1,
            f"{DOMAIN}.{entry_id}.{STORAGE_KEY_CHARGING_STATE}",
        )

    async def restore_charging_enabled(self) -> bool:
        if not hasattr(self.coordinator, "_charging_state_store"):
            return True
        stored = await self.coordinator._charging_state_store.async_load()
        if isinstance(stored, dict):
            return bool(stored.get("charging_enabled", True))
        return True

    async def save_charging_enabled(self, enabled: bool) -> None:
        if not hasattr(self.coordinator, "_charging_state_store"):
            return
        await self.coordinator._charging_state_store.async_save({"charging_enabled": enabled})
