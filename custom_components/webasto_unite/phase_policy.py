from __future__ import annotations

from dataclasses import dataclass

from .const import PHASE_SWITCHING_MODE_OFF
from .electrical import voltage_sum_for_phases
from .models import ChargeMode, ControlDecision, ControlReason, SolarControlStrategy, WallboxState

AUTO_PHASE_STABLE_TO_1P_S = 300.0
AUTO_PHASE_STABLE_TO_3P_S = 600.0
AUTO_PHASE_SWITCH_COOLDOWN_S = 600.0
AUTO_PHASE_MAX_SWITCHES_PER_SESSION = 5
AUTO_PHASE_TO_3P_SURPLUS_MARGIN_W = 300.0


@dataclass(frozen=True, slots=True)
class PhasePolicyDecision:
    decision: str
    target: str | None = None
    block_reason: str | None = None
    required_surplus_1p_w: float | None = None
    required_surplus_3p_w: float | None = None
    auto_ready: bool = False
    auto_block_reason: str | None = None
    stable_elapsed_s: float | None = None
    stable_required_s: float | None = None
    cooldown_remaining_s: float | None = None
    session_switch_count: int = 0
    session_switch_limit: int = AUTO_PHASE_MAX_SWITCHES_PER_SESSION


def evaluate_phase_policy(
    *,
    effective_mode: ChargeMode,
    solar_strategy: SolarControlStrategy,
    phase_switching_mode: str,
    configured_installed_phases: str,
    wallbox: WallboxState,
    control_decision: ControlDecision,
    solar_input_state: str | None,
    filtered_surplus_w: float | None,
    phase_restore_pending: bool,
) -> PhasePolicyDecision:
    """Dry-run Solar phase switching policy.

    This function intentionally never writes. It only reports what the future
    automatic phase switching layer would request if execution were enabled.
    """
    required_1p = _required_surplus_w(1, wallbox, control_decision)
    required_3p = _required_surplus_w(3, wallbox, control_decision) + AUTO_PHASE_TO_3P_SURPLUS_MARGIN_W

    block_reason = _block_reason(
        effective_mode=effective_mode,
        solar_strategy=solar_strategy,
        phase_switching_mode=phase_switching_mode,
        configured_installed_phases=configured_installed_phases,
        wallbox=wallbox,
        control_decision=control_decision,
        solar_input_state=solar_input_state,
        filtered_surplus_w=filtered_surplus_w,
        phase_restore_pending=phase_restore_pending,
    )
    if block_reason is not None:
        return PhasePolicyDecision(
            decision="blocked",
            block_reason=block_reason,
            required_surplus_1p_w=required_1p,
            required_surplus_3p_w=required_3p,
        )

    assert filtered_surplus_w is not None
    current_mode = "1P" if wallbox.phase_switch_mode_raw == 0 else "3P"

    if filtered_surplus_w >= required_3p and current_mode != "3P":
        return PhasePolicyDecision(
            decision="would_request_3p",
            target="3P",
            required_surplus_1p_w=required_1p,
            required_surplus_3p_w=required_3p,
        )

    if required_1p <= filtered_surplus_w < required_3p and current_mode != "1P":
        return PhasePolicyDecision(
            decision="would_request_1p",
            target="1P",
            required_surplus_1p_w=required_1p,
            required_surplus_3p_w=required_3p,
        )

    return PhasePolicyDecision(
        decision="no_action",
        required_surplus_1p_w=required_1p,
        required_surplus_3p_w=required_3p,
    )


def _block_reason(
    *,
    effective_mode: ChargeMode,
    solar_strategy: SolarControlStrategy,
    phase_switching_mode: str,
    configured_installed_phases: str,
    wallbox: WallboxState,
    control_decision: ControlDecision,
    solar_input_state: str | None,
    filtered_surplus_w: float | None,
    phase_restore_pending: bool,
) -> str | None:
    if phase_switching_mode == PHASE_SWITCHING_MODE_OFF:
        return "phase_switching_off"
    if configured_installed_phases != "3p":
        return "integration_configured_1p"
    if wallbox.charge_point_phase_count != 3:
        return "charger_not_preconfigured_3p"
    if wallbox.phase_switch_mode_raw not in (0, 1):
        return "phase_switch_register_unavailable"
    if phase_restore_pending:
        return "phase_restore_pending"
    if effective_mode != ChargeMode.SOLAR or solar_strategy == SolarControlStrategy.DISABLED:
        return "not_solar_mode"
    if solar_input_state != "ready" or filtered_surplus_w is None:
        return "solar_input_not_ready"
    if not wallbox.vehicle_connected:
        return "vehicle_not_connected"
    if control_decision.dominant_limit_reason == ControlReason.DLB_LIMITED:
        return "dlb_limited"
    return None


def _required_surplus_w(phase_count: int, wallbox: WallboxState, decision: ControlDecision) -> float:
    target_current = decision.final_target_a or decision.mode_target_a or 6.0
    voltage_sum = voltage_sum_for_phases(
        phase_count,
        wallbox.voltage_l1_v,
        wallbox.voltage_l2_v,
        wallbox.voltage_l3_v,
    )
    return float(target_current * voltage_sum)
