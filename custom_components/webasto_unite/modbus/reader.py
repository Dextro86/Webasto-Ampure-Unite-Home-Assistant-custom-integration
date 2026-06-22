from __future__ import annotations

import logging

from ..models import ChargingState, ConnectionState, PhaseCurrents, WallboxState
from .client import WebastoModbusClient
from .registers import (
    ACTIVE_POWER_L1_W,
    ACTIVE_POWER_L2_W,
    ACTIVE_POWER_L3_W,
    BRAND,
    CABLE_STATE,
    CHARGE_POINT_ID,
    CHARGE_POINT_POWER_W,
    CHARGE_POINT_STATE,
    CHARGE_STATE,
    COMM_TIMEOUT_S,
    CURRENT_L1_A,
    CURRENT_L2_A,
    CURRENT_L3_A,
    ENERGY_METER_KWH,
    ERROR_CODE,
    EVSE_STATE,
    FIRMWARE_VERSION,
    LIFE_BIT,
    MAX_CURRENT_CABLE_A,
    MAX_CURRENT_EV_A,
    MODEL,
    MIN_CURRENT_HW_A,
    NUMBER_OF_PHASES,
    PHASE_SWITCH_MODE,
    SAFE_CURRENT_A,
    SESSION_MAX_CURRENT_A,
    SERIAL_NUMBER,
    SESSION_DURATION_S,
    SESSION_ENERGY_KWH,
    SESSION_END_TIME,
    SESSION_START_TIME,
    SET_CHARGE_CURRENT_A,
    TOTAL_CHARGE_ACTIVE_POWER_W,
    VOLTAGE_L1_V,
    VOLTAGE_L2_V,
    VOLTAGE_L3_V,
)

_LOGGER = logging.getLogger(__name__)

MAX_PLAUSIBLE_ACTIVE_POWER_W = 250_000


class WallboxReader:
    def __init__(self, client: WebastoModbusClient) -> None:
        self.client = client

    async def read_wallbox_state(self, configured_installed_phases: str) -> WallboxState:
        wallbox = WallboxState(connection_state=ConnectionState.CONNECTING)
        try:
            wallbox.serial_number = await self.client.read(SERIAL_NUMBER)
            wallbox.charge_point_id = await self.client.read(CHARGE_POINT_ID)
            wallbox.brand = await self.client.read(BRAND)
            wallbox.model_name = await self.client.read(MODEL)
            wallbox.firmware_version = await self.client.read(FIRMWARE_VERSION)
            wallbox.charge_point_power_w = await self.client.read(CHARGE_POINT_POWER_W)
            number_of_phases = int(await self.client.read(NUMBER_OF_PHASES))

            # 1000..1037 contains the high-frequency runtime telemetry we poll each cycle.
            telemetry_base = 1000
            telemetry = await self.client.read_input_registers_block(telemetry_base, 38)

            block_charge_point_state_raw = self._block_u16(telemetry, telemetry_base, CHARGE_POINT_STATE.address)
            block_charge_state_raw = self._block_u16(telemetry, telemetry_base, CHARGE_STATE.address)
            block_evse_state_raw = self._block_u16(telemetry, telemetry_base, EVSE_STATE.address)
            block_cable_state_raw = self._block_u16(telemetry, telemetry_base, CABLE_STATE.address)
            status_registers = await self._read_direct_runtime_status(
                block_charge_point_state_raw=block_charge_point_state_raw,
                block_charge_state_raw=block_charge_state_raw,
                block_evse_state_raw=block_evse_state_raw,
                block_cable_state_raw=block_cable_state_raw,
            )
            wallbox.charge_point_state_raw = status_registers["charge_point_state_raw"]
            wallbox.charge_state_raw = status_registers["charge_state_raw"]
            wallbox.evse_state_raw = status_registers["evse_state_raw"]
            wallbox.cable_state_raw = status_registers["cable_state_raw"]
            wallbox.vehicle_connected = int(wallbox.cable_state_raw or 0) >= 2
            wallbox.error_code = self._block_u16(telemetry, telemetry_base, ERROR_CODE.address)
            wallbox.active_power_w = self._normalize_active_power_w(
                self._block_u32(telemetry, telemetry_base, TOTAL_CHARGE_ACTIVE_POWER_W.address),
                vehicle_connected=wallbox.vehicle_connected,
                register_name=TOTAL_CHARGE_ACTIVE_POWER_W.name,
            )
            wallbox.active_power_l1_w = self._normalize_active_power_w(
                self._block_u32(telemetry, telemetry_base, ACTIVE_POWER_L1_W.address),
                vehicle_connected=wallbox.vehicle_connected,
                register_name=ACTIVE_POWER_L1_W.name,
            )
            wallbox.active_power_l2_w = self._normalize_active_power_w(
                self._block_u32(telemetry, telemetry_base, ACTIVE_POWER_L2_W.address),
                vehicle_connected=wallbox.vehicle_connected,
                register_name=ACTIVE_POWER_L2_W.name,
            )
            wallbox.active_power_l3_w = self._normalize_active_power_w(
                self._block_u32(telemetry, telemetry_base, ACTIVE_POWER_L3_W.address),
                vehicle_connected=wallbox.vehicle_connected,
                register_name=ACTIVE_POWER_L3_W.name,
            )
            wallbox.phase_currents = PhaseCurrents(
                l1=self._block_u16(telemetry, telemetry_base, CURRENT_L1_A.address) * CURRENT_L1_A.scale,
                l2=self._block_u16(telemetry, telemetry_base, CURRENT_L2_A.address) * CURRENT_L2_A.scale,
                l3=self._block_u16(telemetry, telemetry_base, CURRENT_L3_A.address) * CURRENT_L3_A.scale,
            )
            wallbox.voltage_l1_v = self._block_u16(telemetry, telemetry_base, VOLTAGE_L1_V.address)
            wallbox.voltage_l2_v = self._block_u16(telemetry, telemetry_base, VOLTAGE_L2_V.address)
            wallbox.voltage_l3_v = self._block_u16(telemetry, telemetry_base, VOLTAGE_L3_V.address)
            wallbox.energy_meter_kwh = self._block_u32(telemetry, telemetry_base, ENERGY_METER_KWH.address) * ENERGY_METER_KWH.scale
            wallbox.actual_current_a = wallbox.phase_currents.max_present()
            wallbox.phases_in_use = wallbox.phase_currents.active_phase_count()
            wallbox.charge_point_phase_count = 1 if number_of_phases == 0 else 3
            wallbox.installed_phases = 1 if configured_installed_phases == "1p" else 3
            try:
                wallbox.phase_switch_mode_raw = int(await self.client.read(PHASE_SWITCH_MODE))
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Optional phase switch register 405 unavailable: %s", err)
                wallbox.phase_switch_mode_raw = None
            wallbox.session_max_current_a = self._normalize_optional_current_limit_a(
                await self.client.read(SESSION_MAX_CURRENT_A)
            )
            wallbox.hardware_min_current_a = await self.client.read(MIN_CURRENT_HW_A)
            wallbox.cable_max_current_a = self._normalize_optional_current_limit_a(
                await self.client.read(MAX_CURRENT_CABLE_A)
            )
            try:
                wallbox.ev_max_current_a = self._normalize_optional_current_limit_a(
                    await self.client.read(MAX_CURRENT_EV_A)
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Optional EV max current register 1108 unavailable: %s", err)
                wallbox.ev_max_current_a = None
            wallbox.safe_current_a = await self.client.read(SAFE_CURRENT_A)
            wallbox.communication_timeout_s = int(await self.client.read(COMM_TIMEOUT_S))
            session_base = 1502
            session_block = await self.client.read_input_registers_block(session_base, 12)
            wallbox.session_energy_kwh = self._block_u32(session_block, session_base, SESSION_ENERGY_KWH.address) * SESSION_ENERGY_KWH.scale
            wallbox.session_start_time = self.format_clock_hhmmss(
                self._block_u32(session_block, session_base, SESSION_START_TIME.address)
            )
            wallbox.session_duration_s = self._block_u32(session_block, session_base, SESSION_DURATION_S.address)
            wallbox.session_end_time = self.format_clock_hhmmss(
                self._block_u32(session_block, session_base, SESSION_END_TIME.address)
            )
            wallbox.current_limit_a = await self.client.read(SET_CHARGE_CURRENT_A)
            wallbox.life_bit_seen = int(await self.client.read(LIFE_BIT))

            wallbox.charging_state = self.map_charging_state(wallbox.charge_point_state_raw)
            wallbox.update_charging_active()
            wallbox.available = True
            wallbox.connection_state = ConnectionState.CONNECTED
            wallbox.last_update_success = True
            return wallbox
        except Exception:
            wallbox.available = False
            wallbox.connection_state = ConnectionState.ERROR
            raise

    @staticmethod
    def _block_u16(block: list[int], base_address: int, address: int) -> int:
        return int(block[address - base_address])

    @staticmethod
    def _block_u32(block: list[int], base_address: int, address: int) -> int:
        offset = address - base_address
        return int((block[offset] << 16) | block[offset + 1])

    async def _read_direct_runtime_status(
        self,
        *,
        block_charge_point_state_raw: int,
        block_charge_state_raw: int,
        block_evse_state_raw: int,
        block_cable_state_raw: int,
    ) -> dict[str, int]:
        """Read state registers directly so plug/unplug state is not tied to one large telemetry block."""

        fallback = {
            "charge_point_state_raw": block_charge_point_state_raw,
            "charge_state_raw": block_charge_state_raw,
            "evse_state_raw": block_evse_state_raw,
            "cable_state_raw": block_cable_state_raw,
        }
        try:
            direct = {
                "charge_point_state_raw": int(await self.client.read(CHARGE_POINT_STATE)),
                "charge_state_raw": int(await self.client.read(CHARGE_STATE)),
                "evse_state_raw": int(await self.client.read(EVSE_STATE)),
                "cable_state_raw": int(await self.client.read(CABLE_STATE)),
            }
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Direct runtime status read failed; using telemetry block values: %s", err)
            return fallback

        for key, direct_value in direct.items():
            block_value = fallback[key]
            if direct_value != block_value:
                _LOGGER.debug(
                    "Runtime status %s differs between direct read and telemetry block: direct=%s block=%s",
                    key,
                    direct_value,
                    block_value,
                )
        return direct

    @staticmethod
    def _normalize_active_power_w(
        raw_value: int | float | None,
        *,
        vehicle_connected: bool,
        register_name: str,
    ) -> float | None:
        if raw_value is None:
            return None
        value = float(raw_value)
        if 0.0 <= value <= MAX_PLAUSIBLE_ACTIVE_POWER_W:
            return value
        _LOGGER.debug("Ignoring implausible %s value from charger: %s W", register_name, raw_value)
        return 0.0 if not vehicle_connected else None

    @staticmethod
    def format_clock_hhmmss(raw_value: float | int | None) -> str | None:
        if raw_value is None:
            return None
        value = int(raw_value)
        if value <= 0:
            return None
        hours = value // 10000
        minutes = (value // 100) % 100
        seconds = value % 100
        if hours > 23 or minutes > 59 or seconds > 59:
            return str(value).zfill(6)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    @staticmethod
    def map_charging_state(raw_state: int) -> ChargingState:
        mapping = {
            0: ChargingState.IDLE,
            1: ChargingState.PREPARING,
            2: ChargingState.CHARGING,
            3: ChargingState.SUSPENDED,
            4: ChargingState.SUSPENDED,
            5: ChargingState.IDLE,
            6: ChargingState.RESERVED,
            7: ChargingState.ERROR,
            8: ChargingState.ERROR,
        }
        return mapping.get(int(raw_state), ChargingState.UNKNOWN)

    @staticmethod
    def _normalize_optional_current_limit_a(value: float | int | None) -> float | None:
        if value is None:
            return None
        numeric = float(value)
        if numeric <= 0:
            return None
        return numeric
