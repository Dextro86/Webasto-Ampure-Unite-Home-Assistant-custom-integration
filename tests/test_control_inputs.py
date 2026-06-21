from custom_components.webasto_unite.control.inputs import ControlInputReader, read_control_inputs
from custom_components.webasto_unite.control_inputs import ControlInputReader as LegacyControlInputReader
from custom_components.webasto_unite.models import (
    ControlConfig,
    DlbInputModel,
    SolarControlStrategy,
    SolarInputModel,
    WallboxState,
)


class _Adapter:
    def state_as_current_a(self, *args, **kwargs):
        return None

    def state_as_power_w(self, *args, **kwargs):
        return None

    def stale_zero_state_as_power_w(self, *args, **kwargs):
        return 123.0

    def state_is_stale(self, *args, **kwargs):
        return False


def test_legacy_control_input_reader_import_remains_available():
    assert LegacyControlInputReader is ControlInputReader


def test_read_control_inputs_helper_returns_snapshot():
    snapshot = read_control_inputs(
        options={"solar_surplus_sensor": "sensor.surplus"},
        config=ControlConfig(
            solar_input_model=SolarInputModel.SURPLUS_SENSOR,
            solar_control_strategy=SolarControlStrategy.SMART_SOLAR,
            dlb_input_model=DlbInputModel.DISABLED,
        ),
        sensor_adapter=_Adapter(),
        surplus_resolver=lambda sensors, wallbox: sensors.surplus_power_w,
        configured_phase_count=lambda: 3,
        wallbox=WallboxState(),
    )

    assert snapshot.surplus_power_w == 123.0
    assert snapshot.solar_input_state == "ready"
