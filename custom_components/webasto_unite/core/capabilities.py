from __future__ import annotations

from ..models import CapabilityState, WallboxState


def build_capabilities(wallbox: WallboxState) -> dict[str, str]:
    ev_max_state = CapabilityState.CONFIRMED if wallbox.ev_max_current_a is not None else CapabilityState.OPTIONAL_ABSENT
    return {
        "core_measurements": CapabilityState.CONFIRMED.value,
        "phase_count_404": CapabilityState.CONFIRMED.value,
        "failsafe_2000_2002": CapabilityState.CONFIRMED.value,
        "current_control_5004": CapabilityState.CONFIRMED.value,
        "keepalive_6000": CapabilityState.CONFIRMED.value,
        "ev_max_current_1108": ev_max_state.value,
    }


def build_capability_summary(wallbox: WallboxState) -> str:
    capabilities = build_capabilities(wallbox)
    if CapabilityState.UNCONFIRMED.value in capabilities.values():
        return "partially_validated"
    if CapabilityState.OPTIONAL_ABSENT.value in capabilities.values():
        return "validated_with_optional_gaps"
    return "validated"
