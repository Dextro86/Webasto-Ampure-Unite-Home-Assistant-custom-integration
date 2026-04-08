from __future__ import annotations

from dataclasses import dataclass

from .models import ControlConfig, ControlReason, DlbInputModel, DlbResult, HaSensorSnapshot


@dataclass(slots=True)
class DlbEngine:
    config: ControlConfig

    def calculate_available_current(self, sensors: HaSensorSnapshot, installed_phases: int) -> DlbResult:
        if not sensors.valid:
            return DlbResult(self.config.safe_current_a, False, ControlReason.SENSOR_UNAVAILABLE)
        if self.config.dlb_input_model == DlbInputModel.PHASE_CURRENTS:
            return self._from_phase_currents(sensors, installed_phases)
        return self._from_grid_power(sensors, installed_phases)

    def _from_phase_currents(self, sensors: HaSensorSnapshot, installed_phases: int) -> DlbResult:
        fuse = self.config.main_fuse_a
        margin = self.config.safety_margin_a
        c = sensors.phase_currents
        if installed_phases == 1:
            if c.l1 is None:
                return DlbResult(self.config.safe_current_a, False, ControlReason.SENSOR_UNAVAILABLE)
            return DlbResult(max(0.0, fuse - c.l1 - margin), True, ControlReason.DLB_LIMITED)
        vals = [c.l1, c.l2, c.l3]
        if any(v is None for v in vals):
            return DlbResult(self.config.safe_current_a, False, ControlReason.SENSOR_UNAVAILABLE)
        available = min(fuse - float(v) - margin for v in vals)
        return DlbResult(max(0.0, available), True, ControlReason.DLB_LIMITED)

    def _from_grid_power(self, sensors: HaSensorSnapshot, installed_phases: int) -> DlbResult:
        if sensors.grid_power_w is None:
            return DlbResult(self.config.safe_current_a, False, ControlReason.SENSOR_UNAVAILABLE)
        nominal_voltage = 230.0
        total_capacity_w = self.config.main_fuse_a * installed_phases * nominal_voltage
        safety_margin_w = self.config.safety_margin_a * installed_phases * nominal_voltage
        used_w = max(0.0, sensors.grid_power_w)
        available_w = total_capacity_w - used_w - safety_margin_w
        available_a = max(0.0, available_w / (installed_phases * nominal_voltage))
        return DlbResult(available_a, True, ControlReason.DLB_LIMITED)
