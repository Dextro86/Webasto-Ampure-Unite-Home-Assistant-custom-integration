from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SessionTransition:
    """Vehicle connection transition detected during a poll cycle."""

    vehicle_connected: bool = False
    vehicle_disconnected: bool = False


@dataclass(slots=True)
class SessionRuntimeState:
    """Tracks plug/unplug state without owning charger control actions."""

    last_vehicle_connected: bool = False
    vehicle_connection_initialized: bool = False

    def observe_vehicle_connection(self, connected: bool) -> SessionTransition:
        vehicle_disconnected = (
            self.vehicle_connection_initialized
            and self.last_vehicle_connected
            and not connected
        )
        vehicle_connected = (
            self.vehicle_connection_initialized
            and not self.last_vehicle_connected
            and connected
        )
        self.last_vehicle_connected = connected
        self.vehicle_connection_initialized = True
        return SessionTransition(
            vehicle_connected=vehicle_connected,
            vehicle_disconnected=vehicle_disconnected,
        )
