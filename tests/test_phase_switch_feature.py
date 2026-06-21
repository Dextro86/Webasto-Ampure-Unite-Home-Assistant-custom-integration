from types import SimpleNamespace

from custom_components.webasto_unite.features.phase_switch import (
    PhaseSwitchRuntimeFacade,
    default_phase_switch_raw_value,
    default_phase_target,
    observed_phases_match_target,
    phase_label,
    phase_register_control_available,
    wallbox_matches_default_phase,
)
from custom_components.webasto_unite.const import PHASE_SWITCHING_MODE_MANUAL_ONLY, PHASE_SWITCHING_MODE_OFF
from custom_components.webasto_unite.models import WallboxState
from custom_components.webasto_unite.features.phase_runtime import PhaseRuntimeState


def test_default_phase_helpers():
    assert default_phase_target("1p") == 1
    assert default_phase_target("3p") == 3
    assert default_phase_switch_raw_value("1p") == 0
    assert default_phase_switch_raw_value("3p") == 1
    assert phase_label(3) == "3P"


def test_observed_phase_matching_is_strict_only_while_charging():
    assert observed_phases_match_target(WallboxState(charging_active=False, phases_in_use=1), 3) is True
    assert observed_phases_match_target(WallboxState(charging_active=True, phases_in_use=3), 3) is True
    assert observed_phases_match_target(WallboxState(charging_active=True, phases_in_use=1), 3) is False


def test_wallbox_matches_default_phase_requires_register_and_physical_match():
    assert (
        wallbox_matches_default_phase(
            WallboxState(vehicle_connected=True, charging_active=True, phases_in_use=3, phase_switch_mode_raw=1),
            "3p",
        )
        is True
    )
    assert (
        wallbox_matches_default_phase(
            WallboxState(vehicle_connected=True, charging_active=True, phases_in_use=1, phase_switch_mode_raw=1),
            "3p",
        )
        is False
    )
    assert (
        wallbox_matches_default_phase(
            WallboxState(vehicle_connected=False, charging_active=False, phase_switch_mode_raw=1),
            "3p",
        )
        is True
    )


def test_phase_register_control_availability_is_shared_for_buttons_and_select():
    assert (
        phase_register_control_available(
            phase_switching_mode=PHASE_SWITCHING_MODE_MANUAL_ONLY,
            data=SimpleNamespace(phase_switch_register_available=True),
        )
        is True
    )
    assert (
        phase_register_control_available(
            phase_switching_mode=PHASE_SWITCHING_MODE_OFF,
            data=SimpleNamespace(phase_switch_register_available=True),
        )
        is False
    )
    assert (
        phase_register_control_available(
            phase_switching_mode=PHASE_SWITCHING_MODE_MANUAL_ONLY,
            data=SimpleNamespace(phase_switch_register_available=False),
        )
        is False
    )


def test_phase_facade_syncs_diagnostics():
    runtime = PhaseRuntimeState()
    manager = SimpleNamespace(
        last_result="register_written",
        last_block_reason=None,
        last_target="3P",
        state="phase_switch_settling",
    )

    PhaseSwitchRuntimeFacade(runtime, manager).sync_diagnostics()

    assert runtime.switch_last_result == "register_written"
    assert runtime.switch_last_block_reason is None
    assert runtime.switch_last_target == "3P"
    assert runtime.switch_state == "phase_switch_settling"


def test_phase_facade_records_observed_register_override_without_writes():
    runtime = PhaseRuntimeState()
    facade = PhaseSwitchRuntimeFacade(runtime, SimpleNamespace())

    assert facade.handle_observed_register_state(WallboxState(vehicle_connected=True, phase_switch_mode_raw=0), "3p") is False
    assert runtime.session_override_active is True
    assert runtime.session_target == "1P"
    assert runtime.restore_pending is False

    assert facade.handle_observed_register_state(WallboxState(vehicle_connected=False, phase_switch_mode_raw=0), "3p") is False
    assert runtime.session_override_active is False
    assert runtime.session_target is None
