from custom_components.webasto_unite.control.orchestrator import (
    BLOCK_REASON_EXTERNAL_CONTROLLER,
    BLOCK_REASON_MONITORING_ONLY,
    BLOCK_REASON_PHASE_SWITCH_IN_PROGRESS,
    resolve_control_write_access,
)
from custom_components.webasto_unite.models import ControlMode


def test_managed_control_allows_current_writes():
    access = resolve_control_write_access(
        control_mode=ControlMode.MANAGED_CONTROL,
        phase_switch_in_progress=False,
    )

    assert access.automatic_control_writes is True
    assert access.current_writes is True
    assert access.blocked_reason is None


def test_external_controller_blocks_automatic_but_allows_direct_current_only():
    access = resolve_control_write_access(
        control_mode=ControlMode.EXTERNAL_CONTROLLER,
        phase_switch_in_progress=False,
    )

    assert access.automatic_control_writes is False
    assert access.current_writes is True
    assert access.blocked_reason == BLOCK_REASON_EXTERNAL_CONTROLLER


def test_monitoring_only_blocks_all_writes():
    access = resolve_control_write_access(
        control_mode=ControlMode.KEEPALIVE_ONLY,
        phase_switch_in_progress=False,
    )

    assert access.automatic_control_writes is False
    assert access.current_writes is False
    assert access.blocked_reason == BLOCK_REASON_MONITORING_ONLY


def test_phase_switch_blocks_current_writes():
    access = resolve_control_write_access(
        control_mode=ControlMode.EXTERNAL_CONTROLLER,
        phase_switch_in_progress=True,
    )

    assert access.automatic_control_writes is False
    assert access.current_writes is False
    assert access.blocked_reason == BLOCK_REASON_PHASE_SWITCH_IN_PROGRESS
