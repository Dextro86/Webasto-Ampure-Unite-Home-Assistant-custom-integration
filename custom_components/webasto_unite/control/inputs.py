from __future__ import annotations

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
from ..models import (
    ControlConfig,
    DlbInputModel,
    HaSensorSnapshot,
    PhaseCurrents,
    SolarControlStrategy,
    SolarInputModel,
    WallboxState,
)
from ..sensor_adapter import HaSensorAdapter


class ControlInputReader:
    """Read and validate Home Assistant sensor inputs used for control."""

    def __init__(
        self,
        *,
        options: dict,
        config: ControlConfig,
        sensor_adapter: HaSensorAdapter,
        surplus_resolver,
        configured_phase_count,
    ) -> None:
        self.options = options
        self.config = config
        self.sensor_adapter = sensor_adapter
        self._surplus_resolver = surplus_resolver
        self._configured_phase_count = configured_phase_count

    def read(self, wallbox: WallboxState | None = None) -> HaSensorSnapshot:
        snapshot = HaSensorSnapshot(valid=True)
        snapshot.solar_input_state = "disabled"

        if self.config.dlb_input_model == DlbInputModel.PHASE_CURRENTS:
            snapshot.phase_currents = PhaseCurrents(
                l1=self.sensor_adapter.state_as_current_a(
                    self.options.get(CONF_DLB_L1_SENSOR),
                    require_supported_unit=self.config.dlb_require_units,
                    max_age_s=self.config.control_sensor_timeout_s,
                ),
                l2=self.sensor_adapter.state_as_current_a(
                    self.options.get(CONF_DLB_L2_SENSOR),
                    require_supported_unit=self.config.dlb_require_units,
                    max_age_s=self.config.control_sensor_timeout_s,
                ),
                l3=self.sensor_adapter.state_as_current_a(
                    self.options.get(CONF_DLB_L3_SENSOR),
                    require_supported_unit=self.config.dlb_require_units,
                    max_age_s=self.config.control_sensor_timeout_s,
                ),
            )

        if self.config.solar_input_model == SolarInputModel.SURPLUS_SENSOR:
            snapshot.surplus_power_w = self.sensor_adapter.stale_zero_state_as_power_w(
                self.options.get(CONF_SOLAR_SURPLUS_SENSOR),
                require_supported_unit=self.config.solar_require_units,
                max_age_s=self.config.control_sensor_timeout_s,
            )
        elif self.config.solar_input_model == SolarInputModel.DSMR_IMPORT_EXPORT:
            import_power_w = self.sensor_adapter.stale_zero_state_as_power_w(
                self.options.get(CONF_SOLAR_IMPORT_POWER_SENSOR),
                require_supported_unit=self.config.solar_require_units,
                max_age_s=self.config.control_sensor_timeout_s,
            )
            export_power_w = self.sensor_adapter.stale_zero_state_as_power_w(
                self.options.get(CONF_SOLAR_EXPORT_POWER_SENSOR),
                require_supported_unit=self.config.solar_require_units,
                max_age_s=self.config.control_sensor_timeout_s,
            )
            if import_power_w is not None and export_power_w is not None:
                snapshot.grid_power_w = import_power_w - export_power_w
        elif snapshot.grid_power_w is None:
            snapshot.grid_power_w = self.sensor_adapter.state_as_power_w(
                self.options.get(CONF_SOLAR_GRID_POWER_SENSOR) or self.options.get(CONF_DLB_GRID_POWER_SENSOR),
                require_supported_unit=self.config.solar_require_units,
                max_age_s=self.config.control_sensor_timeout_s,
            )

        self._validate_dlb_inputs(snapshot, wallbox)
        self._validate_solar_inputs(snapshot, wallbox)
        return snapshot

    def _validate_dlb_inputs(self, snapshot: HaSensorSnapshot, wallbox: WallboxState | None) -> None:
        if self.config.dlb_input_model != DlbInputModel.PHASE_CURRENTS:
            return
        required_indices = self._required_dlb_sensor_indices(wallbox)
        phase_values = (
            snapshot.phase_currents.l1,
            snapshot.phase_currents.l2,
            snapshot.phase_currents.l3,
        )
        phase_entities = (
            self.options.get(CONF_DLB_L1_SENSOR),
            self.options.get(CONF_DLB_L2_SENSOR),
            self.options.get(CONF_DLB_L3_SENSOR),
        )
        required_values = tuple(phase_values[idx] for idx in required_indices)
        if any(value is None for value in required_values):
            stale_entities = self._stale_sensor_entities(tuple(phase_entities[idx] for idx in required_indices))
            snapshot.valid = False
            snapshot.reason_invalid = self._control_sensor_invalid_reason(
                "Required DLB phase sensors",
                stale=bool(stale_entities),
                require_units=self.config.dlb_require_units,
            )

    def _validate_solar_inputs(self, snapshot: HaSensorSnapshot, wallbox: WallboxState | None) -> None:
        if self.config.solar_control_strategy == SolarControlStrategy.DISABLED:
            return
        if snapshot.reason_invalid is None and self._surplus_resolver(snapshot, wallbox) is None:
            snapshot.solar_input_state = "unavailable"
            snapshot.reason_invalid = self._control_sensor_invalid_reason(
                "Required Solar sensor",
                stale=any(
                    self.sensor_adapter.state_is_stale(
                        entity_id,
                        max_age_s=self.config.control_sensor_timeout_s,
                    )
                    for entity_id in self.solar_input_entities()
                ),
                require_units=self.config.solar_require_units,
            )
        elif snapshot.reason_invalid is None:
            snapshot.solar_input_state = "ready"

    def solar_input_entities(self) -> tuple[str | None, ...]:
        if self.config.solar_input_model == SolarInputModel.SURPLUS_SENSOR:
            return (self.options.get(CONF_SOLAR_SURPLUS_SENSOR),)
        if self.config.solar_input_model == SolarInputModel.DSMR_IMPORT_EXPORT:
            return (
                self.options.get(CONF_SOLAR_IMPORT_POWER_SENSOR),
                self.options.get(CONF_SOLAR_EXPORT_POWER_SENSOR),
            )
        return (
            self.options.get(CONF_SOLAR_GRID_POWER_SENSOR) or self.options.get(CONF_DLB_GRID_POWER_SENSOR),
        )

    def _required_dlb_sensor_indices(self, wallbox: WallboxState | None) -> tuple[int, ...]:
        if self._configured_phase_count() == 1:
            return (0,)
        if wallbox is None or not wallbox.charging_active:
            return (0, 1, 2)
        active_indices = tuple(
            idx
            for idx, value in enumerate(
                (
                    wallbox.phase_currents.l1,
                    wallbox.phase_currents.l2,
                    wallbox.phase_currents.l3,
                )
            )
            if value is not None and value >= 0.5
        )
        return active_indices or (0, 1, 2)

    @staticmethod
    def _control_sensor_invalid_reason(prefix: str, *, stale: bool, require_units: bool) -> str:
        if stale:
            return f"{prefix} stale"
        if require_units:
            return f"{prefix} unavailable or invalid unit"
        return f"{prefix} unavailable"

    def _stale_sensor_entities(self, entity_ids: tuple[str | None, ...]) -> list[str]:
        return [
            entity_id
            for entity_id in entity_ids
            if self.sensor_adapter.state_is_stale(
                entity_id,
                max_age_s=self.config.control_sensor_timeout_s,
            )
        ]


def read_control_inputs(
    *,
    options: dict,
    config: ControlConfig,
    sensor_adapter: HaSensorAdapter,
    surplus_resolver,
    configured_phase_count,
    wallbox: WallboxState | None = None,
) -> HaSensorSnapshot:
    return ControlInputReader(
        options=options,
        config=config,
        sensor_adapter=sensor_adapter,
        surplus_resolver=surplus_resolver,
        configured_phase_count=configured_phase_count,
    ).read(wallbox)
