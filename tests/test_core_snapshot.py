from types import SimpleNamespace

from custom_components.webasto_unite.core.snapshot import SnapshotBuildInput, build_runtime_snapshot
from custom_components.webasto_unite.models import (
    ChargeMode,
    ControlConfig,
    ControlDecision,
    ControlMode,
    ControlReason,
    HaSensorSnapshot,
    SolarControlStrategy,
    RestDiagnosticsData,
    WallboxState,
)


def test_build_runtime_snapshot_maps_control_solar_phase_and_write_diagnostics():
    write_runtime = SimpleNamespace(
        last_control_write_value_a=6.0,
        last_control_write_reason="solar_mode",
        last_control_write_register="set_charge_current_a",
        last_control_write_age_seconds=lambda: 12.5,
        last_control_write_blocked_reason=None,
        last_control_write_verification_status="accepted",
        last_control_write_verification_reported_a=6.0,
        last_control_write_verification_delta_a=0.0,
    )
    solar_state = SimpleNamespace(
        raw_surplus_w=1000.0,
        filtered_surplus_w=900.0,
        target_current_a=6.0,
        phase_count=1,
        phase_source="wallbox_active_phases",
        voltage_sum_v=230.0,
    )
    phase_observability = SimpleNamespace(
        phase_switch_mode_raw=0,
        phase_switch_mode="1P",
        phase_switch_register_available=True,
        phase_switch_available=True,
        phase_switch_block_reason=None,
        observed_session_phase_usage="observed_1p",
        phase_offer_state="offering_1p",
    )
    wallbox = WallboxState(
        available=True,
        vehicle_connected=True,
        charging_active=True,
        phases_in_use=1,
        phase_switch_mode_raw=0,
    )
    decision = ControlDecision(
        charging_enabled=True,
        target_current_a=6.0,
        reason=ControlReason.SOLAR_MODE,
        dlb_limit_a=20.0,
        mode_target_a=6.0,
        final_target_a=6.0,
        fallback_active=False,
    )

    snapshot = build_runtime_snapshot(
        SnapshotBuildInput(
            wallbox=wallbox,
            mode=ChargeMode.SOLAR,
            effective_mode=ChargeMode.SOLAR,
            control_config=ControlConfig(control_mode=ControlMode.MANAGED_CONTROL),
            decision=decision,
            sensors=HaSensorSnapshot(valid=True, solar_input_state="ready"),
            solar_strategy=SolarControlStrategy.SMART_SOLAR,
            charging_paused=False,
            solar_until_unplug_active=False,
            fixed_current_until_unplug_active=False,
            keepalive_age_s=1.0,
            keepalive_overdue=False,
            keepalive_sent_count=2,
            keepalive_write_failures=0,
            queue_depth=0,
            pending_write_kind=None,
            control_writes_enabled=True,
            write_runtime=write_runtime,
            solar_surplus_w=950.0,
            solar_state=solar_state,
            phase_observability=phase_observability,
            phase_recovery_warning=None,
            phase_switching_mode="manual_only",
            phase_switch_default_mode="3p",
            phase_session_override_active=True,
            phase_session_target="1P",
            phase_restore_pending=False,
            phase_policy=SimpleNamespace(
                decision="no_action",
                block_reason=None,
                target=None,
                required_surplus_1p_w=1380.0,
                required_surplus_3p_w=4140.0,
                auto_ready=False,
                auto_block_reason="automatic_phase_switching_disabled",
                stable_elapsed_s=None,
                stable_required_s=None,
                cooldown_remaining_s=0.0,
                session_switch_count=0,
                session_switch_limit=5,
            ),
            phase_switch_last_result="register_written",
            phase_switch_last_block_reason=None,
            phase_switch_last_target="1P",
            phase_switch_state="phase_switch_settling",
            rest_diagnostics=RestDiagnosticsData(
                enabled=True,
                status="connected",
                hmi_version="v3.187.0",
            ),
            last_client_error=None,
            entry_title="Webasto Unite",
        )
    )

    assert snapshot.operating_state == "smart_solar"
    assert snapshot.control_reason == "solar_mode"
    assert snapshot.solar_surplus_w == 950.0
    assert snapshot.solar_filtered_surplus_w == 900.0
    assert snapshot.phase_switch_mode == "1P"
    assert snapshot.phase_session_override_active is True
    assert snapshot.phase_consistency == "register_and_physical_match"
    assert snapshot.last_control_write_reason == "solar_mode"
    assert snapshot.capability_summary is not None
    assert snapshot.rest_diagnostics.hmi_version == "v3.187.0"
