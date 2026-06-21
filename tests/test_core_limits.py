from custom_components.webasto_unite.core.limits import combine_current_limits
from custom_components.webasto_unite.models import ControlConfig, ControlReason, WallboxState


def test_combine_current_limits_applies_dlb_and_charger_limits():
    result = combine_current_limits(
        config=ControlConfig(max_current_a=32.0, min_current_a=6.0),
        wallbox=WallboxState(cable_max_current_a=20.0, ev_max_current_a=25.0),
        mode_target_a=32.0,
        dlb_limit_a=18.5,
    )

    assert result.target_current_a == 18.5
    assert result.dominant_limit_reason == ControlReason.DLB_LIMITED


def test_combine_current_limits_returns_none_below_effective_minimum():
    result = combine_current_limits(
        config=ControlConfig(max_current_a=32.0, min_current_a=6.0),
        wallbox=WallboxState(hardware_min_current_a=8.0),
        mode_target_a=7.0,
        dlb_limit_a=None,
    )

    assert result.target_current_a is None
    assert result.dominant_limit_reason is None
