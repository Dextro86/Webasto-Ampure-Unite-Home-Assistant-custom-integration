from __future__ import annotations

from dataclasses import dataclass

from .models import ControlConfig, ControlReason, DlbInputModel, DlbResult, DlbSensorScope, HaSensorSnapshot, PhaseCurrents


@dataclass(slots=True)
class DlbEngine:
    config: ControlConfig

    def calculate_available_current(
        self,
        sensors: HaSensorSnapshot,
        installed_phases: int,
        charger_phase_currents: PhaseCurrents | None = None,
        charger_power_w: float | None = None,
        voltage_l1_v: float | None = None,
        voltage_l2_v: float | None = None,
        voltage_l3_v: float | None = None,
    ) -> DlbResult:
        if self.config.dlb_input_model == DlbInputModel.DISABLED:
            return DlbResult(None, True, ControlReason.NO_CHANGE)
        if not sensors.valid:
            return DlbResult(self.config.safe_current_a, False, ControlReason.SENSOR_UNAVAILABLE)
        if self.config.dlb_input_model != DlbInputModel.PHASE_CURRENTS:
            return DlbResult(None, True, ControlReason.NO_CHANGE)
        return self._from_phase_currents(sensors, installed_phases, charger_phase_currents)

    def _from_phase_currents(
        self,
        sensors: HaSensorSnapshot,
        installed_phases: int,
        charger_phase_currents: PhaseCurrents | None,
    ) -> DlbResult:
        fuse = self.config.main_fuse_a
        margin = self.config.safety_margin_a
        c = sensors.phase_currents
        if installed_phases == 1:
            if c.l1 is None:
                return DlbResult(self.config.safe_current_a, False, ControlReason.SENSOR_UNAVAILABLE)
            charger_l1 = self._charger_current(charger_phase_currents.l1 if charger_phase_currents else None)
            return DlbResult(max(0.0, fuse - c.l1 + charger_l1 - margin), True, ControlReason.DLB_LIMITED)
        grid_currents = [c.l1, c.l2, c.l3]
        if any(v is None for v in grid_currents):
            return DlbResult(self.config.safe_current_a, False, ControlReason.SENSOR_UNAVAILABLE)
        charger_currents = [
            self._charger_current(charger_phase_currents.l1 if charger_phase_currents else None),
            self._charger_current(charger_phase_currents.l2 if charger_phase_currents else None),
            self._charger_current(charger_phase_currents.l3 if charger_phase_currents else None),
        ]
        active_indices = self._active_phase_indices(charger_phase_currents)
        if not active_indices:
            active_indices = [0, 1, 2]
        available = min(
            fuse - float(grid_currents[idx]) + charger_currents[idx] - margin
            for idx in active_indices
        )
        return DlbResult(max(0.0, available), True, ControlReason.DLB_LIMITED)

    def _charger_current(self, value: float | None) -> float:
        if self.config.dlb_sensor_scope != DlbSensorScope.TOTAL_INCLUDING_CHARGER:
            return 0.0
        return max(0.0, value or 0.0)

    def _active_phase_indices(self, charger_phase_currents: PhaseCurrents | None) -> list[int]:
        if charger_phase_currents is None:
            return []
        indices: list[int] = []
        for idx, value in enumerate(
            (charger_phase_currents.l1, charger_phase_currents.l2, charger_phase_currents.l3)
        ):
            if value is not None and value >= 0.5:
                indices.append(idx)
        return indices
