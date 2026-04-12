
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


class ChargeMode(str, Enum):
    OFF = "off"
    NORMAL = "normal"
    PV = "pv"
    FIXED_CURRENT = "fixed_current"


class DlbInputModel(str, Enum):
    DISABLED = "disabled"
    PHASE_CURRENTS = "phase_currents"
    GRID_POWER = "grid_power"


class DlbSensorScope(str, Enum):
    TOTAL_INCLUDING_CHARGER = "total_including_charger"
    LOAD_EXCLUDING_CHARGER = "load_excluding_charger"


class PvInputModel(str, Enum):
    SURPLUS_SENSOR = "surplus_sensor"
    GRID_POWER_DERIVED = "grid_power_derived"


class PvControlStrategy(str, Enum):
    DISABLED = "disabled"
    SURPLUS = "surplus"
    MIN_PLUS_SURPLUS = "min_plus_surplus"


class PvOverrideStrategy(str, Enum):
    INHERIT = "inherit"
    SURPLUS = "surplus"
    MIN_PLUS_SURPLUS = "min_plus_surplus"


class KeepaliveMode(str, Enum):
    AUTO = "auto"
    FORCED = "forced"
    DISABLED = "disabled"


class ControlMode(str, Enum):
    KEEPALIVE_ONLY = "keepalive_only"
    MANAGED_CONTROL = "managed_control"


class ConnectionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DEGRADED = "degraded"
    ERROR = "error"


class ChargingState(str, Enum):
    IDLE = "idle"
    PREPARING = "preparing"
    CHARGING = "charging"
    SUSPENDED = "suspended"
    ERROR = "error"
    RESERVED = "reserved"
    UNKNOWN = "unknown"


class ControlReason(str, Enum):
    OFF_MODE = "off_mode"
    NORMAL_MODE = "normal_mode"
    FIXED_CURRENT_MODE = "fixed_current_mode"
    PV_MODE = "pv_mode"
    DLB_LIMITED = "dlb_limited"
    HARDWARE_LIMITED = "hardware_limited"
    CABLE_LIMITED = "cable_limited"
    EV_LIMITED = "ev_limited"
    SAFE_CURRENT_FALLBACK = "safe_current_fallback"
    SENSOR_UNAVAILABLE = "sensor_unavailable"
    COMMUNICATION_LOSS = "communication_loss"
    BELOW_MIN_CURRENT = "below_min_current"
    NO_CHANGE = "no_change"


class CapabilityState(str, Enum):
    CONFIRMED = "confirmed"
    OPTIONAL_ABSENT = "optional_absent"
    UNCONFIRMED = "unconfirmed"


@dataclass(slots=True)
class PhaseCurrents:
    l1: Optional[float] = None
    l2: Optional[float] = None
    l3: Optional[float] = None

    def max_present(self) -> Optional[float]:
        vals = [v for v in (self.l1, self.l2, self.l3) if v is not None]
        return max(vals) if vals else None

    def active_phase_count(self, threshold: float = 0.5) -> int:
        return sum(1 for v in (self.l1, self.l2, self.l3) if v is not None and v >= threshold)


@dataclass(slots=True)
class WallboxState:
    available: bool = False
    connection_state: ConnectionState = ConnectionState.DISCONNECTED
    charging_state: ChargingState = ChargingState.UNKNOWN

    charge_point_state_raw: Optional[int] = None
    charge_state_raw: Optional[int] = None
    evse_state_raw: Optional[int] = None
    cable_state_raw: Optional[int] = None

    vehicle_connected: bool = False
    charging_enabled: bool = False

    actual_current_a: Optional[float] = None
    current_limit_a: Optional[float] = None
    active_power_w: Optional[float] = None
    phase_currents: PhaseCurrents = field(default_factory=PhaseCurrents)

    phases_in_use: Optional[int] = None
    installed_phases: Optional[int] = None
    charge_point_phase_count: Optional[int] = None

    error_code: Optional[int] = None
    serial_number: Optional[str] = None
    charge_point_id: Optional[str] = None
    brand: Optional[str] = None
    model_name: Optional[str] = None
    firmware_version: Optional[str] = None

    safe_current_a: Optional[float] = None
    communication_timeout_s: Optional[int] = None
    hardware_max_current_a: Optional[float] = None
    hardware_min_current_a: Optional[float] = None
    cable_max_current_a: Optional[float] = None
    ev_max_current_a: Optional[float] = None
    session_energy_kwh: Optional[float] = None
    charge_point_power_w: Optional[float] = None
    energy_meter_kwh: Optional[float] = None
    voltage_l1_v: Optional[float] = None
    voltage_l2_v: Optional[float] = None
    voltage_l3_v: Optional[float] = None
    active_power_l1_w: Optional[float] = None
    active_power_l2_w: Optional[float] = None
    active_power_l3_w: Optional[float] = None
    session_start_time: Optional[str] = None
    session_duration_s: Optional[int] = None
    session_end_time: Optional[str] = None

    life_bit_seen: Optional[int] = None
    last_update_success: bool = False

    @property
    def current_l1_a(self) -> Optional[float]:
        return self.phase_currents.l1

    @property
    def current_l2_a(self) -> Optional[float]:
        return self.phase_currents.l2

    @property
    def current_l3_a(self) -> Optional[float]:
        return self.phase_currents.l3


@dataclass(slots=True)
class IntegrationConfig:
    host: str
    port: int = 502
    unit_id: int = 255
    installed_phases: str = "3p"


@dataclass(slots=True)
class ControlConfig:
    polling_interval_s: float = 2.0
    communication_timeout_s: float = 30.0
    timeout_s: float = 3.0
    retries: int = 3
    control_mode: ControlMode = ControlMode.KEEPALIVE_ONLY
    keepalive_mode: KeepaliveMode = KeepaliveMode.AUTO
    keepalive_interval_s: float = 10.0
    safe_current_a: float = 6.0
    min_current_a: float = 6.0
    max_current_a: float = 16.0
    user_limit_a: float = 16.0
    main_fuse_a: float = 25.0
    safety_margin_a: float = 2.0
    dlb_input_model: DlbInputModel = DlbInputModel.PHASE_CURRENTS
    dlb_sensor_scope: DlbSensorScope = DlbSensorScope.LOAD_EXCLUDING_CHARGER
    pv_input_model: PvInputModel = PvInputModel.GRID_POWER_DERIVED
    pv_control_strategy: PvControlStrategy = PvControlStrategy.SURPLUS
    pv_until_unplug_strategy: PvOverrideStrategy = PvOverrideStrategy.INHERIT
    pv_start_threshold_w: float = 1800.0
    pv_stop_threshold_w: float = 1200.0
    pv_start_delay_s: float = 0.0
    pv_stop_delay_s: float = 0.0
    pv_min_runtime_s: float = 0.0
    pv_min_pause_s: float = 0.0
    pv_min_current_a: float = 6.0
    fixed_current_a: float = 6.0
    min_seconds_between_writes: float = 5.0
    min_current_change_a: float = 1.0
    stable_cycles_before_write: int = 2


@dataclass(slots=True)
class SensorInputState:
    phase_currents: PhaseCurrents = field(default_factory=PhaseCurrents)
    grid_power_w: Optional[float] = None
    surplus_power_w: Optional[float] = None
    valid: bool = True
    reason_invalid: Optional[str] = None


HaSensorSnapshot = SensorInputState


@dataclass(slots=True)
class DlbResult:
    available_current_a: Optional[float]
    valid: bool
    reason: ControlReason


@dataclass(slots=True)
class PvResult:
    target_current_a: Optional[float]
    valid: bool
    reason: ControlReason


@dataclass(slots=True)
class ControlDecision:
    charging_enabled: bool
    target_current_a: Optional[float]
    reason: ControlReason
    dlb_limit_a: Optional[float] = None
    mode_target_a: Optional[float] = None
    final_target_a: Optional[float] = None
    dominant_limit_reason: Optional[ControlReason] = None
    fallback_active: bool = False
    sensor_invalid_reason: Optional[str] = None
    should_write: bool = False
    issue_start_command: bool = False
    issue_cancel_command: bool = False


@dataclass(slots=True)
class RuntimeSnapshot:
    wallbox: WallboxState
    mode: ChargeMode
    effective_mode: ChargeMode
    operating_state: str
    control_mode: ControlMode
    control_reason: str
    charging_paused: bool
    pv_until_unplug_active: bool
    fixed_current_until_unplug_active: bool
    keepalive_age_s: Optional[float]
    keepalive_interval_s: Optional[float]
    keepalive_overdue: bool
    keepalive_sent_count: int
    keepalive_write_failures: int
    queue_depth: int
    pending_write_kind: str | None
    sensor_snapshot_valid: bool = True
    sensor_invalid_reason: Optional[str] = None
    dlb_limit_a: Optional[float] = None
    final_target_a: Optional[float] = None
    mode_target_a: Optional[float] = None
    dominant_limit_reason: Optional[str] = None
    fallback_active: bool = False
    last_client_error: str | None = None
    entry_title: str | None = None
    capability_summary: str | None = None
    capabilities: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "wallbox": asdict(self.wallbox),
            "mode": self.mode.value,
            "effective_mode": self.effective_mode.value,
            "operating_state": self.operating_state,
            "control_mode": self.control_mode.value,
            "control_reason": self.control_reason,
            "charging_paused": self.charging_paused,
            "pv_until_unplug_active": self.pv_until_unplug_active,
            "fixed_current_until_unplug_active": self.fixed_current_until_unplug_active,
            "keepalive_age_s": self.keepalive_age_s,
            "keepalive_interval_s": self.keepalive_interval_s,
            "keepalive_overdue": self.keepalive_overdue,
            "keepalive_sent_count": self.keepalive_sent_count,
            "keepalive_write_failures": self.keepalive_write_failures,
            "sensor_snapshot_valid": self.sensor_snapshot_valid,
            "sensor_invalid_reason": self.sensor_invalid_reason,
            "queue_depth": self.queue_depth,
            "pending_write_kind": self.pending_write_kind,
            "dlb_limit_a": self.dlb_limit_a,
            "final_target_a": self.final_target_a,
            "mode_target_a": self.mode_target_a,
            "dominant_limit_reason": self.dominant_limit_reason,
            "fallback_active": self.fallback_active,
            "last_client_error": self.last_client_error,
            "entry_title": self.entry_title,
            "capability_summary": self.capability_summary,
            "capabilities": dict(self.capabilities),
        }
