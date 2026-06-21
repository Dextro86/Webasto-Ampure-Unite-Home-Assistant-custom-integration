from custom_components.webasto_unite.core.mode import ModeRuntimeState, resolve_startup_mode
from custom_components.webasto_unite.core.session import SessionRuntimeState
from custom_components.webasto_unite.models import ChargeMode, ControlConfig, SolarControlStrategy


def test_legacy_core_wrappers_reexport_current_implementations():
    from custom_components.webasto_unite.capabilities import build_capabilities as legacy_build_capabilities
    from custom_components.webasto_unite.control.runtime_guards import RuntimeGuards
    from custom_components.webasto_unite.control.write_queue import WriteQueueManager
    from custom_components.webasto_unite.control.write_runtime import WriteRuntime
    from custom_components.webasto_unite.core.capabilities import build_capabilities
    from custom_components.webasto_unite.core.status import build_operating_state
    from custom_components.webasto_unite.dlb import DlbEngine as LegacyDlbEngine
    from custom_components.webasto_unite.features.dlb import DlbEngine
    from custom_components.webasto_unite.features.phase_engine import PhaseSwitchManager
    from custom_components.webasto_unite.features.phase_observer import build_phase_observability
    from custom_components.webasto_unite.features.phase_policy import evaluate_phase_policy
    from custom_components.webasto_unite.features.phase_runtime import PhaseRuntimeState
    from custom_components.webasto_unite.features.solar import SolarEngine
    from custom_components.webasto_unite.modbus.client import WebastoModbusClient
    from custom_components.webasto_unite.modbus.reader import WallboxReader
    from custom_components.webasto_unite.modbus.registers import SET_CHARGE_CURRENT_A
    from custom_components.webasto_unite.modbus_client import WebastoModbusClient as LegacyWebastoModbusClient
    from custom_components.webasto_unite.operating_status import build_operating_state as legacy_build_operating_state
    from custom_components.webasto_unite.phase_engine import PhaseSwitchManager as LegacyPhaseSwitchManager
    from custom_components.webasto_unite.phase_observer import build_phase_observability as legacy_build_phase_observability
    from custom_components.webasto_unite.phase_policy import evaluate_phase_policy as legacy_evaluate_phase_policy
    from custom_components.webasto_unite.phase_runtime import PhaseRuntimeState as LegacyPhaseRuntimeState
    from custom_components.webasto_unite.registers import SET_CHARGE_CURRENT_A as LEGACY_SET_CHARGE_CURRENT_A
    from custom_components.webasto_unite.runtime_guards import RuntimeGuards as LegacyRuntimeGuards
    from custom_components.webasto_unite.solar import SolarEngine as LegacySolarEngine
    from custom_components.webasto_unite.wallbox_reader import WallboxReader as LegacyWallboxReader
    from custom_components.webasto_unite.write_queue import WriteQueueManager as LegacyWriteQueueManager
    from custom_components.webasto_unite.write_runtime import WriteRuntime as LegacyWriteRuntime

    assert legacy_build_capabilities is build_capabilities
    assert legacy_build_operating_state is build_operating_state
    assert LegacyRuntimeGuards is RuntimeGuards
    assert LegacyWriteQueueManager is WriteQueueManager
    assert LegacyWriteRuntime is WriteRuntime
    assert LegacyPhaseRuntimeState is PhaseRuntimeState
    assert LegacySolarEngine is SolarEngine
    assert LegacyDlbEngine is DlbEngine
    assert LegacyPhaseSwitchManager is PhaseSwitchManager
    assert legacy_build_phase_observability is build_phase_observability
    assert legacy_evaluate_phase_policy is evaluate_phase_policy
    assert LegacyWebastoModbusClient is WebastoModbusClient
    assert LegacyWallboxReader is WallboxReader
    assert LEGACY_SET_CHARGE_CURRENT_A is SET_CHARGE_CURRENT_A


def test_mode_runtime_effective_mode_priorities():
    runtime = ModeRuntimeState(
        mode=ChargeMode.NORMAL,
        solar_until_unplug_active=True,
        fixed_current_until_unplug_active=True,
    )

    assert runtime.effective_mode() == ChargeMode.FIXED_CURRENT

    runtime.charging_paused = True
    assert runtime.effective_mode() == ChargeMode.OFF


def test_mode_runtime_set_mode_clears_temporary_overrides():
    runtime = ModeRuntimeState(
        solar_until_unplug_active=True,
        fixed_current_until_unplug_active=True,
    )

    should_reset_solar = runtime.set_mode(
        ChargeMode.SOLAR,
        default_solar_strategy=SolarControlStrategy.ECO_SOLAR,
        solar_strategy=SolarControlStrategy.SMART_SOLAR,
    )

    assert should_reset_solar is False
    assert runtime.mode == ChargeMode.SOLAR
    assert runtime.active_solar_strategy == SolarControlStrategy.SMART_SOLAR
    assert runtime.solar_until_unplug_active is False
    assert runtime.fixed_current_until_unplug_active is False


def test_resolve_startup_mode_falls_back_when_solar_is_disabled():
    config = ControlConfig(solar_control_strategy=SolarControlStrategy.DISABLED)

    assert resolve_startup_mode({"startup_charge_mode": "solar"}, config) == ChargeMode.NORMAL


def test_session_runtime_detects_vehicle_transitions():
    runtime = SessionRuntimeState()

    first = runtime.observe_vehicle_connection(False)
    assert first.vehicle_connected is False
    assert first.vehicle_disconnected is False

    connected = runtime.observe_vehicle_connection(True)
    assert connected.vehicle_connected is True
    assert connected.vehicle_disconnected is False

    unchanged = runtime.observe_vehicle_connection(True)
    assert unchanged.vehicle_connected is False
    assert unchanged.vehicle_disconnected is False

    disconnected = runtime.observe_vehicle_connection(False)
    assert disconnected.vehicle_connected is False
    assert disconnected.vehicle_disconnected is True
