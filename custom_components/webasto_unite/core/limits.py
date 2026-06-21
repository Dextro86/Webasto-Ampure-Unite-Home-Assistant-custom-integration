from __future__ import annotations

from dataclasses import dataclass

from ..models import ControlConfig, ControlReason, WallboxState


@dataclass(slots=True)
class CurrentLimitResult:
    target_current_a: float | None
    dominant_limit_reason: ControlReason | None


def combine_current_limits(
    *,
    config: ControlConfig,
    wallbox: WallboxState,
    mode_target_a: float | None,
    dlb_limit_a: float | None,
) -> CurrentLimitResult:
    if mode_target_a is None:
        return CurrentLimitResult(target_current_a=None, dominant_limit_reason=None)

    limits: list[tuple[float, ControlReason | None]] = [
        (mode_target_a, None),
        (config.max_current_a, None),
    ]

    if dlb_limit_a is not None:
        limits.append((dlb_limit_a, ControlReason.DLB_LIMITED))
    if wallbox.cable_max_current_a is not None:
        limits.append((wallbox.cable_max_current_a, ControlReason.CABLE_LIMITED))
    if wallbox.ev_max_current_a is not None:
        limits.append((wallbox.ev_max_current_a, ControlReason.EV_LIMITED))

    final_target, dominant_limit_reason = min(limits, key=lambda item: item[0])

    minimum_current = config.min_current_a
    if wallbox.hardware_min_current_a is not None:
        minimum_current = max(minimum_current, wallbox.hardware_min_current_a)

    if final_target < minimum_current:
        return CurrentLimitResult(target_current_a=None, dominant_limit_reason=dominant_limit_reason)

    return CurrentLimitResult(target_current_a=round(final_target, 1), dominant_limit_reason=dominant_limit_reason)
