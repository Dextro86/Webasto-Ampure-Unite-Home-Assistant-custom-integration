from custom_components.webasto_unite.features.solar import (
    resolve_installed_phase_count,
    resolve_solar_phase_context,
)
from custom_components.webasto_unite.models import ChargeMode, SolarControlStrategy, WallboxState


def test_resolve_installed_phase_count_defaults_to_3p():
    assert resolve_installed_phase_count(WallboxState(installed_phases=1)) == 1
    assert resolve_installed_phase_count(WallboxState(installed_phases=3)) == 3
    assert resolve_installed_phase_count(WallboxState(installed_phases=None)) == 3


def test_solar_phase_context_uses_active_wallbox_phases_first():
    phase_count, source = resolve_solar_phase_context(
        mode=ChargeMode.SOLAR,
        wallbox=WallboxState(charging_active=True, phases_in_use=1, installed_phases=3),
        strategy=SolarControlStrategy.SMART_SOLAR,
        installed_phases=3,
        observed_session_phase_count=3,
    )

    assert phase_count == 1
    assert source == "wallbox_active_phases"


def test_solar_phase_context_uses_observed_session_phases_when_connected_but_not_charging():
    phase_count, source = resolve_solar_phase_context(
        mode=ChargeMode.SOLAR,
        wallbox=WallboxState(vehicle_connected=True, charging_active=False, installed_phases=3),
        strategy=SolarControlStrategy.SMART_SOLAR,
        installed_phases=3,
        observed_session_phase_count=3,
    )

    assert phase_count == 3
    assert source == "observed_session_phases"


def test_eco_solar_pre_start_uses_requested_phase_register_when_available():
    one_phase, one_phase_source = resolve_solar_phase_context(
        mode=ChargeMode.SOLAR,
        wallbox=WallboxState(charging_active=False, phases_in_use=None, phase_switch_mode_raw=0),
        strategy=SolarControlStrategy.ECO_SOLAR,
        installed_phases=3,
        observed_session_phase_count=None,
    )
    three_phase, three_phase_source = resolve_solar_phase_context(
        mode=ChargeMode.SOLAR,
        wallbox=WallboxState(charging_active=False, phases_in_use=None, phase_switch_mode_raw=1),
        strategy=SolarControlStrategy.ECO_SOLAR,
        installed_phases=3,
        observed_session_phase_count=None,
    )

    assert (one_phase, one_phase_source) == (1, "phase_switch_mode_1p")
    assert (three_phase, three_phase_source) == (3, "phase_switch_mode_3p")


def test_minimum_based_solar_pre_start_uses_conservative_1p_assumption():
    phase_count, source = resolve_solar_phase_context(
        mode=ChargeMode.SOLAR,
        wallbox=WallboxState(charging_active=False, phases_in_use=None, installed_phases=3),
        strategy=SolarControlStrategy.SMART_SOLAR,
        installed_phases=3,
        observed_session_phase_count=None,
    )

    assert phase_count == 1
    assert source == "pre_start_1p_assumption"


def test_non_solar_phase_context_uses_installed_phases():
    phase_count, source = resolve_solar_phase_context(
        mode=ChargeMode.NORMAL,
        wallbox=WallboxState(charging_active=False, phases_in_use=None, installed_phases=3),
        strategy=SolarControlStrategy.SMART_SOLAR,
        installed_phases=3,
        observed_session_phase_count=None,
    )

    assert phase_count == 3
    assert source == "installed_phases"
