from __future__ import annotations

import logging


_LOGGER = logging.getLogger(__name__)


class HaSensorAdapter:
    def __init__(self, hass) -> None:
        self.hass = hass
        self._unsupported_sensor_units: set[tuple[str, str, str | None]] = set()

    def state_as_current_a(
        self,
        entity_id: str | None,
        *,
        require_supported_unit: bool = False,
    ) -> float | None:
        return self._state_as_float(
            entity_id,
            expected_kind="current",
            require_supported_unit=require_supported_unit,
        )

    def state_as_power_w(
        self,
        entity_id: str | None,
        *,
        require_supported_unit: bool = False,
    ) -> float | None:
        return self._state_as_float(
            entity_id,
            expected_kind="power",
            require_supported_unit=require_supported_unit,
        )

    def _state_as_float(
        self,
        entity_id: str | None,
        expected_kind: str,
        *,
        require_supported_unit: bool = False,
    ) -> float | None:
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable", None):
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
