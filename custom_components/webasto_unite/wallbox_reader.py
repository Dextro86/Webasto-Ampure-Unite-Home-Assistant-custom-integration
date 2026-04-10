from __future__ import annotations

import logging

from .modbus_client import WebastoModbusClient
from .models import ChargingState, ConnectionState, PhaseCurrents, WallboxState
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
    MAX_CURRENT_HW_A,
    MODEL,
    MIN_CURRENT_HW_A,
    NUMBER_OF_PHASES,
    SAFE_CURRENT_A,
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

            charge_point_state = await self.client.read(CHARGE_POINT_STATE)
            charge_state = await self.client.read(CHARGE_STATE)
            evse_state = await self.client.read(EVSE_STATE)
            cable_state = await self.client.read(CABLE_STATE)

            current_l1 = await self.client.read(CURRENT_L1_A)
            current_l2 = await self.client.read(CURRENT_L2_A)
            current_l3 = await self.client.read(CURRENT_L3_A)
            voltage_l1 = await self.client.read(VOLTAGE_L1_V)
            voltage_l2 = await self.client.read(VOLTAGE_L2_V)
            voltage_l3 = await self.client.read(VOLTAGE_L3_V)
            active_power_l1 = await self.client.read(ACTIVE_POWER_L1_W)
            active_power_l2 = await self.client.read(ACTIVE_POWER_L2_W)
            active_power_l3 = await self.client.read(ACTIVE_POWER_L3_W)

            wallbox.charge_point_state_raw = int(charge_point_state)
            wallbox.charge_state_raw = int(charge_state)
            wallbox.evse_state_raw = int(evse_state)
            wallbox.cable_state_raw = int(cable_state)
            wallbox.error_code = int(await self.client.read(ERROR_CODE))
            wallbox.active_power_w = await self.client.read(TOTAL_CHARGE_ACTIVE_POWER_W)
            wallbox.active_power_l1_w = active_power_l1
            wallbox.active_power_l2_w = active_power_l2
            wallbox.active_power_l3_w = active_power_l3
            wallbox.phase_currents = PhaseCurrents(l1=current_l1, l2=current_l2, l3=current_l3)
            wallbox.voltage_l1_v = voltage_l1
            wallbox.voltage_l2_v = voltage_l2
            wallbox.voltage_l3_v = voltage_l3
            wallbox.actual_current_a = wallbox.phase_currents.max_present()
            wallbox.phases_in_use = wallbox.phase_currents.active_phase_count()
            wallbox.charge_point_phase_count = 1 if number_of_phases == 0 else 3
            wallbox.hardware_max_current_a = self._normalize_optional_current_limit_a(
                await self.client.read(MAX_CURRENT_HW_A)
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
            wallbox.session_energy_kwh = await self.client.read(SESSION_ENERGY_KWH)
            wallbox.energy_meter_kwh = await self.client.read(ENERGY_METER_KWH)
            wallbox.session_start_time = self.format_clock_hhmmss(await self.client.read(SESSION_START_TIME))
            wallbox.session_duration_s = int(await self.client.read(SESSION_DURATION_S))
            wallbox.session_end_time = self.format_clock_hhmmss(await self.client.read(SESSION_END_TIME))
            wallbox.current_limit_a = await self.client.read(SET_CHARGE_CURRENT_A)
            wallbox.life_bit_seen = int(await self.client.read(LIFE_BIT))

            wallbox.charging_state = self.map_charging_state(charge_point_state)
            wallbox.vehicle_connected = int(cable_state) >= 2
            wallbox.charging_enabled = wallbox.charging_state in (
                ChargingState.PREPARING,
                ChargingState.CHARGING,
                ChargingState.SUSPENDED,
            )
            wallbox.available = True
            wallbox.connection_state = ConnectionState.CONNECTED
            wallbox.installed_phases = 1 if configured_installed_phases == "1p" else 3
            wallbox.last_update_success = True
            return wallbox
        except Exception:
            wallbox.available = False
            wallbox.connection_state = ConnectionState.ERROR
            raise

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
            3: ChargingState.CHARGING,
            4: ChargingState.SUSPENDED,
            7: ChargingState.ERROR,
            8: ChargingState.RESERVED,
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
