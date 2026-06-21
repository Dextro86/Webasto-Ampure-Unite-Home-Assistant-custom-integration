from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models import (
    ChargeMode,
    ControlConfig,
    ControlDecision,
    ControlMode,
    HaSensorSnapshot,
    RuntimeSnapshot,
    SolarControlStrategy,
    WallboxState,
)
from ..features.phase_observer import build_phase_consistency
from .capabilities import build_capabilities, build_capability_summary
from .status import build_operating_state


@dataclass(slots=True)
class SnapshotBuildInput:
    wallbox: WallboxState
    mode: ChargeMode
    effective_mode: ChargeMode
    control_config: ControlConfig
    decision: ControlDecision
    sensors: HaSensorSnapshot
    solar_strategy: SolarControlStrategy
    charging_paused: bool
    solar_until_unplug_active: bool
    fixed_current_until_unplug_active: bool
    keepalive_age_s: float | None
    keepalive_overdue: bool
    keepalive_sent_count: int
    keepalive_write_failures: int
    queue_depth: int
    pending_write_kind: str | None
    control_writes_enabled: bool
    write_runtime: Any
    solar_surplus_w: float | None
    solar_state: Any
    phase_observability: Any
    phase_recovery_warning: str | None
    phase_switching_mode: str | None
    phase_switch_default_mode: str | None
    phase_session_override_active: bool
    phase_session_target: str | None
    phase_restore_pending: bool
    phase_policy: Any
    phase_switch_last_result: str | None
    phase_switch_last_block_reason: str | None
    phase_switch_last_target: str | None
    phase_switch_state: str | None
    last_client_error: str | None
    entry_title: str | None


def build_runtime_snapshot(input_data: SnapshotBuildInput) -> RuntimeSnapshot:
    decision = input_data.decision
    phase_policy = input_data.phase_policy
    solar_state = input_data.solar_state
    write_runtime = input_data.write_runtime

    return RuntimeSnapshot(
        wallbox=input_data.wallbox,
        mode=input_data.mode,
        effective_mode=input_data.effective_mode,
        operating_state=build_operating_state(
            effective_mode=input_data.effective_mode,
            charging_paused=input_data.charging_paused,
            fixed_current_until_unplug_active=input_data.fixed_current_until_unplug_active,
            solar_until_unplug_active=input_data.solar_until_unplug_active,
            control_config=input_data.control_config,
            decision=decision,
            solar_strategy=input_data.solar_strategy,
        ),
        control_mode=input_data.control_config.control_mode,
        control_reason=decision.reason.value,
        active_solar_strategy=input_data.solar_strategy,
        charging_paused=input_data.charging_paused,
        solar_until_unplug_active=input_data.solar_until_unplug_active,
        fixed_current_until_unplug_active=input_data.fixed_current_until_unplug_active,
        keepalive_age_s=input_data.keepalive_age_s,
        keepalive_interval_s=input_data.control_config.keepalive_interval_s,
        keepalive_overdue=input_data.keepalive_overdue,
        keepalive_sent_count=input_data.keepalive_sent_count,
        keepalive_write_failures=input_data.keepalive_write_failures,
        sensor_snapshot_valid=input_data.sensors.valid,
        sensor_invalid_reason=input_data.sensors.reason_invalid,
        queue_depth=input_data.queue_depth,
        pending_write_kind=input_data.pending_write_kind,
        control_writes_enabled=input_data.control_writes_enabled,
        last_control_write_value_a=write_runtime.last_control_write_value_a,
        last_control_write_reason=write_runtime.last_control_write_reason,
        last_control_write_register=write_runtime.last_control_write_register,
        last_control_write_age_s=write_runtime.last_control_write_age_seconds(),
        last_control_write_blocked_reason=write_runtime.last_control_write_blocked_reason,
        last_control_write_verification_status=write_runtime.last_control_write_verification_status,
        last_control_write_verification_reported_a=write_runtime.last_control_write_verification_reported_a,
        last_control_write_verification_delta_a=write_runtime.last_control_write_verification_delta_a,
        dlb_limit_a=decision.dlb_limit_a,
        final_target_a=decision.final_target_a,
        mode_target_a=decision.mode_target_a,
        solar_surplus_w=input_data.solar_surplus_w,
        solar_raw_surplus_w=solar_state.raw_surplus_w,
        solar_filtered_surplus_w=solar_state.filtered_surplus_w,
        solar_target_current_a=solar_state.target_current_a,
        solar_phase_count=solar_state.phase_count,
        solar_phase_source=solar_state.phase_source,
        solar_voltage_sum_v=solar_state.voltage_sum_v,
        solar_input_state=input_data.sensors.solar_input_state,
        phase_switch_mode_raw=input_data.phase_observability.phase_switch_mode_raw,
        phase_switch_mode=input_data.phase_observability.phase_switch_mode,
        phase_switch_register_available=input_data.phase_observability.phase_switch_register_available,
        phase_switch_available=input_data.phase_observability.phase_switch_available,
        phase_switch_block_reason=input_data.phase_observability.phase_switch_block_reason,
        observed_session_phase_usage=input_data.phase_observability.observed_session_phase_usage,
        phase_offer_state=input_data.phase_observability.phase_offer_state,
        phase_recovery_warning=input_data.phase_recovery_warning,
        phase_switching_mode=input_data.phase_switching_mode,
        phase_switch_default_mode=input_data.phase_switch_default_mode,
        phase_session_override_active=input_data.phase_session_override_active,
        phase_session_target=input_data.phase_session_target,
        phase_restore_pending=input_data.phase_restore_pending,
        phase_policy_decision=phase_policy.decision,
        phase_policy_block_reason=phase_policy.block_reason,
        phase_policy_target=phase_policy.target,
        phase_policy_required_surplus_1p_w=phase_policy.required_surplus_1p_w,
        phase_policy_required_surplus_3p_w=phase_policy.required_surplus_3p_w,
        phase_policy_auto_ready=phase_policy.auto_ready,
        phase_policy_auto_block_reason=phase_policy.auto_block_reason,
        phase_policy_stable_elapsed_s=phase_policy.stable_elapsed_s,
        phase_policy_stable_required_s=phase_policy.stable_required_s,
        phase_policy_cooldown_remaining_s=phase_policy.cooldown_remaining_s,
        phase_policy_session_switch_count=phase_policy.session_switch_count,
        phase_policy_session_switch_limit=phase_policy.session_switch_limit,
        phase_switch_last_result=input_data.phase_switch_last_result,
        phase_switch_last_block_reason=input_data.phase_switch_last_block_reason,
        phase_switch_last_target=input_data.phase_switch_last_target,
        phase_switch_state=input_data.phase_switch_state,
        phase_consistency=build_phase_consistency(input_data.wallbox),
        dominant_limit_reason=decision.dominant_limit_reason.value if decision.dominant_limit_reason is not None else None,
        fallback_active=decision.fallback_active,
        last_client_error=input_data.last_client_error,
        entry_title=input_data.entry_title,
        capability_summary=build_capability_summary(input_data.wallbox),
        capabilities=build_capabilities(input_data.wallbox),
    )
