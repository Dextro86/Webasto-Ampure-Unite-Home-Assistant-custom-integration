from custom_components.webasto_unite.core.config import (
    build_control_config,
    normalize_dlb_input_model,
    resolve_configured_max_current,
    resolve_dlb_input_model_from_options,
)
from custom_components.webasto_unite.models import (
    ControlMode,
    DlbInputModel,
    SolarControlStrategy,
    SolarOverrideStrategy,
)


def test_build_control_config_uses_defaults():
    config = build_control_config({})

    assert config.control_mode == ControlMode.KEEPALIVE_ONLY
    assert config.min_current_a == 6.0
    assert config.max_current_a == 16.0
    assert config.dlb_input_model == DlbInputModel.DISABLED
    assert config.solar_control_strategy == SolarControlStrategy.DISABLED


def test_resolve_configured_max_current_honors_legacy_user_limit_cap():
    assert resolve_configured_max_current({"max_current": 32, "user_limit": 20}) == 20.0
    assert resolve_configured_max_current({"max_current": 16, "user_limit": 32}) == 16.0
    assert resolve_configured_max_current({"max_current": 25, "user_limit": "invalid"}) == 25.0


def test_legacy_dlb_enabled_overrides_dlb_input_model():
    assert resolve_dlb_input_model_from_options({"dlb_enabled": True}) == DlbInputModel.PHASE_CURRENTS
    assert resolve_dlb_input_model_from_options({"dlb_enabled": False}) == DlbInputModel.DISABLED
    assert normalize_dlb_input_model("grid_power") == DlbInputModel.DISABLED


def test_solar_strategy_aliases_are_normalized():
    config = build_control_config(
        {
            "solar_control_strategy": "min_plus_surplus",
            "solar_until_unplug_strategy": "surplus",
        }
    )

    assert config.solar_control_strategy == SolarControlStrategy.SOLAR_BOOST
    assert config.solar_until_unplug_strategy == SolarOverrideStrategy.ECO_SOLAR
