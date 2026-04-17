from __future__ import annotations

from dataclasses import dataclass

from .electrical import voltage_sum_for_phases
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
        if self.config.dlb_input_model == DlbInputModel.PHASE_CURRENTS:
            return self._from_phase_currents(sensors, installed_phases, charger_phase_currents)
        return self._from_grid_power(
            sensors,
            installed_phases,
            charger_power_w,
            voltage_l1_v,
            voltage_l2_v,
            voltage_l3_v,
        )

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
        vals = [c.l1, c.l2, c.l3]
        if any(v is None for v in vals):
            return DlbResult(self.config.safe_current_a, False, ControlReason.SENSOR_UNAVAILABLE)
        charger_vals = [
            self._charger_current(charger_phase_currents.l1 if charger_phase_currents else None),
            self._charger_current(charger_phase_currents.l2 if charger_phase_currents else None),
            self._charger_current(charger_phase_currents.l3 if charger_phase_currents else None),
        ]
        available = min(fuse - float(v) + charger - margin for v, charger in zip(vals, charger_vals, strict=True))
        return DlbResult(max(0.0, available), True, ControlReason.DLB_LIMITED)

    def _from_grid_power(
        self,
        sensors: HaSensorSnapshot,
        installed_phases: int,
        charger_power_w: float | None,
        voltage_l1_v: float | None,
        voltage_l2_v: float | None,
        voltage_l3_v: float | None,
    ) -> DlbResult:
        if sensors.grid_power_w is None:
            return DlbResult(self.config.safe_current_a, False, ControlReason.SENSOR_UNAVAILABLE)
        voltage_sum_v = voltage_sum_for_phases(installed_phases, voltage_l1_v, voltage_l2_v, voltage_l3_v)
        total_capacity_w = self.config.main_fuse_a * voltage_sum_v
        safety_margin_w = self.config.safety_margin_a * voltage_sum_v
        used_w = max(0.0, sensors.grid_power_w)
        if self.config.dlb_sensor_scope == DlbSensorScope.TOTAL_INCLUDING_CHARGER and charger_power_w is not None:
            used_w = max(0.0, used_w - max(0.0, charger_power_w))
        available_w = total_capacity_w - used_w - safety_margin_w
        available_a = max(0.0, available_w / voltage_sum_v)
        return DlbResult(available_a, True, ControlReason.DLB_LIMITED)

    def _charger_current(self, value: float | None) -> float:
        if self.config.dlb_sensor_scope != DlbSensorScope.TOTAL_INCLUDING_CHARGER:
            return 0.0
        return max(0.0, value or 0.0)
