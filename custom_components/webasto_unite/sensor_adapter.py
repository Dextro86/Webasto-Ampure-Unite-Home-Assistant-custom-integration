from __future__ import annotations

import logging
from datetime import datetime, timezone


_LOGGER = logging.getLogger(__name__)


class HaSensorAdapter:
    def __init__(self, hass) -> None:
        self.hass = hass
        self._unsupported_sensor_units: set[tuple[str, str, str | None]] = set()
        self._stale_sensors: set[tuple[str, float]] = set()

    def state_as_current_a(
        self,
        entity_id: str | None,
        *,
        require_supported_unit: bool = False,
        max_age_s: float | None = None,
    ) -> float | None:
        return self._state_as_float(
            entity_id,
            expected_kind="current",
            require_supported_unit=require_supported_unit,
            max_age_s=max_age_s,
        )

    def state_as_power_w(
        self,
        entity_id: str | None,
        *,
        require_supported_unit: bool = False,
        max_age_s: float | None = None,
    ) -> float | None:
        return self._state_as_float(
            entity_id,
            expected_kind="power",
            require_supported_unit=require_supported_unit,
            max_age_s=max_age_s,
        )

    def _state_as_float(
        self,
        entity_id: str | None,
        expected_kind: str,
        *,
        require_supported_unit: bool = False,
        max_age_s: float | None = None,
    ) -> float | None:
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable", None):
            return None
        if self.state_is_stale(entity_id, max_age_s=max_age_s, state=state):
            return None
        try:
            value = float(state.state)
        except (TypeError, ValueError):
            return None
        unit = state.attributes.get("unit_of_measurement")
        return self._normalize_sensor_value(
            entity_id,
            expected_kind,
            value,
            unit,
            require_supported_unit=require_supported_unit,
        )

    def state_is_stale(
        self,
        entity_id: str | None,
        *,
        max_age_s: float | None,
        state=None,
    ) -> bool:
        if not entity_id or max_age_s is None:
            return False
        state = state if state is not None else self.hass.states.get(entity_id)
        if state is None:
            return False
        timestamp = self._state_timestamp(state)
        if timestamp is None:
            return False
        now = datetime.now(timezone.utc)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        age_s = (now - timestamp).total_seconds()
        if age_s <= max_age_s:
            return False
        key = (entity_id, float(max_age_s))
        if key not in self._stale_sensors:
            _LOGGER.warning(
                "Ignoring sensor %s for control: last update is %.1f seconds old "
                "(Control Sensor Timeout: %.1f seconds)",
                entity_id,
                age_s,
                max_age_s,
            )
            self._stale_sensors.add(key)
        return True

    @staticmethod
    def _state_timestamp(state) -> datetime | None:
        for attr in ("last_reported", "last_updated", "last_changed"):
            value = getattr(state, attr, None)
            if isinstance(value, datetime):
                return value
        return None

    def _normalize_sensor_value(
        self,
        entity_id: str,
        expected_kind: str,
        value: float,
        unit: str | None,
        *,
        require_supported_unit: bool = False,
    ) -> float | None:
        normalized_unit = unit.strip() if isinstance(unit, str) else unit

        if expected_kind == "current":
            factors = {"A": 1.0, "mA": 0.001, "kA": 1000.0}
        else:
            factors = {"W": 1.0, "mW": 0.001, "kW": 1000.0, "MW": 1_000_000.0}

        if normalized_unit in (None, ""):
            if require_supported_unit:
                key = (entity_id, expected_kind, normalized_unit)
                if key not in self._unsupported_sensor_units:
                    _LOGGER.warning(
                        "Ignoring sensor %s for %s control: missing unit_of_measurement",
                        entity_id,
                        expected_kind,
                    )
                    self._unsupported_sensor_units.add(key)
                return None
            return value

        factor = factors.get(normalized_unit)
        if factor is None:
            key = (entity_id, expected_kind, normalized_unit)
            if key not in self._unsupported_sensor_units:
                _LOGGER.warning(
                    "Ignoring sensor %s for %s control: unsupported unit %s",
                    entity_id,
                    expected_kind,
                    normalized_unit,
                )
                self._unsupported_sensor_units.add(key)
            return None

        return value * factor
