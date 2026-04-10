
"""Own Modbus TCP client wrapper for the Webasto Unite integration."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from time import monotonic
from typing import Any

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException
from pymodbus.pdu import ExceptionResponse

from .registers import RegisterDef, RegisterType, ValueType

_LOGGER = logging.getLogger(__name__)


class ModbusClientError(RuntimeError):
    pass


class ModbusClientConnectionError(ModbusClientError):
    pass


class ModbusClientProtocolError(ModbusClientError):
    pass


@dataclass(slots=True)
class ModbusClientConfig:
    host: str
    port: int = 502
    unit_id: int = 255
    timeout_s: float = 3.0
    retries: int = 3
    reconnect_delay_s: float = 2.0


@dataclass(slots=True)
class ConnectionStats:
    connected: bool = False
    connect_attempts: int = 0
    read_failures: int = 0
    write_failures: int = 0
    reconnects: int = 0
    last_ok_monotonic: float | None = None
    last_error: str | None = None


@dataclass(slots=True)
class WebastoModbusClient:
    config: ModbusClientConfig
    _client: AsyncModbusTcpClient | None = field(default=None, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _stats: ConnectionStats = field(default_factory=ConnectionStats, init=False)

    @property
    def stats(self) -> ConnectionStats:
        return self._stats

    @property
    def is_connected(self) -> bool:
        return bool(self._client and self._client.connected)

    async def connect(self) -> None:
        async with self._lock:
            if self.is_connected:
                return
            self._stats.connect_attempts += 1
            client = AsyncModbusTcpClient(
                self.config.host,
                port=self.config.port,
                timeout=self.config.timeout_s,
                retries=0,
            )
            try:
                connected = await client.connect()
            except Exception as err:  # noqa: BLE001
                self._stats.connected = False
                self._stats.last_error = str(err)
                raise ModbusClientConnectionError(str(err)) from err
            if not connected:
                self._stats.connected = False
                self._stats.last_error = "connect() returned False"
                raise ModbusClientConnectionError("connect() returned False")
            self._client = client
            self._stats.connected = True
            self._stats.last_ok_monotonic = monotonic()
            self._stats.last_error = None

    async def close(self) -> None:
        async with self._lock:
            if self._client is not None:
                try:
                    self._client.close()
                except Exception:
                    pass
            self._client = None
            self._stats.connected = False

    async def ensure_connected(self) -> None:
        if not self.is_connected:
            await self.connect()

    async def reconnect(self) -> None:
        async with self._lock:
            self._stats.reconnects += 1
            old = self._client
            self._client = None
            self._stats.connected = False
            if old is not None:
                try:
                    old.close()
                except Exception:
                    pass
        await asyncio.sleep(self.config.reconnect_delay_s)
        await self.connect()

    async def read(self, register: RegisterDef) -> Any:
        if not register.readable:
            raise ModbusClientProtocolError(f"Register {register.name} is write-only")
        for attempt in range(1, self.config.retries + 1):
            try:
                await self.ensure_connected()
                async with self._lock:
                    response = await self._read_raw(register)
                value = self._decode_response(register, response.registers)
                self._mark_ok()
                return value
            except (ModbusClientConnectionError, ModbusClientProtocolError, ModbusException, OSError) as err:
                self._stats.read_failures += 1
                self._stats.last_error = str(err)
                if attempt >= self.config.retries:
                    raise ModbusClientError(f"Failed to read register {register.name}: {err}") from err
                await self._handle_retry()
        raise ModbusClientError(f"Unexpected read failure for register {register.name}")

    async def write(self, register: RegisterDef, value: Any) -> None:
        if not register.writable:
            raise ModbusClientProtocolError(f"Register {register.name} is not writable")
        payload = self._encode_value(register, value)
        for attempt in range(1, self.config.retries + 1):
            try:
                await self.ensure_connected()
                async with self._lock:
                    response = await self._write_raw(register, payload)
                if isinstance(response, ExceptionResponse) or response.isError():
                    raise ModbusClientProtocolError(f"Write failed for {register.name}: {response}")
                self._mark_ok()
                return
            except (ModbusClientConnectionError, ModbusClientProtocolError, ModbusException, OSError) as err:
                self._stats.write_failures += 1
                self._stats.last_error = str(err)
                if attempt >= self.config.retries:
                    raise ModbusClientError(f"Failed to write register {register.name}: {err}") from err
                await self._handle_retry()

    async def _read_raw(self, register: RegisterDef):
        assert self._client is not None
        if register.register_type == RegisterType.INPUT:
            response = await self._call_with_unit_fallback(
                self._client.read_input_registers,
                address=register.address,
                count=register.count,
            )
        else:
            response = await self._call_with_unit_fallback(
                self._client.read_holding_registers,
                address=register.address,
                count=register.count,
            )
        if isinstance(response, ExceptionResponse) or response.isError():
            raise ModbusClientProtocolError(f"Read failed for {register.name}: {response}")
        return response

    async def _write_raw(self, register: RegisterDef, payload: list[int]):
        assert self._client is not None
        if len(payload) == 1:
            return await self._call_with_unit_fallback(
                self._client.write_register,
                address=register.address,
                value=payload[0],
            )
        return await self._call_with_unit_fallback(
            self._client.write_registers,
            address=register.address,
            values=payload,
        )

    async def _call_with_unit_fallback(self, method, **kwargs):
        unit_keys = ("slave", "device_id", "unit")
        last_type_error: TypeError | None = None
        for unit_key in unit_keys:
            try:
                return await method(**kwargs, **{unit_key: self.config.unit_id})
            except TypeError as err:
                message = str(err)
                if "unexpected keyword argument" not in message or unit_key not in message:
                    raise
                last_type_error = err
        raise ModbusClientProtocolError(
            f"No supported Modbus unit parameter name found for client method {getattr(method, '__name__', method)}"
        ) from last_type_error

    def _decode_response(self, register: RegisterDef, registers: list[int]) -> Any:
        if register.value_type == ValueType.BOOL:
            return bool(registers[0])
        if register.value_type == ValueType.UINT16:
            return registers[0] * register.scale
        if register.value_type == ValueType.INT16:
            value = registers[0] if registers[0] < 0x8000 else registers[0] - 0x10000
            return value * register.scale
        if register.value_type == ValueType.UINT32:
            value = (registers[0] << 16) | registers[1]
            return value * register.scale
        if register.value_type == ValueType.INT32:
            value = (registers[0] << 16) | registers[1]
            if value >= 0x80000000:
                value -= 0x100000000
            return value * register.scale
        if register.value_type == ValueType.STRING:
            data = bytearray()
            for reg in registers:
                data.extend(reg.to_bytes(2, "big"))
            return data.decode("ascii", errors="ignore").strip("\x00 ")
        return registers[0]

    def _encode_value(self, register: RegisterDef, value: Any) -> list[int]:
        if register.value_type == ValueType.BOOL:
            return [1 if value else 0]
        if register.value_type in (ValueType.UINT16, ValueType.INT16):
            scaled = int(round(float(value) / register.scale))
            return [scaled & 0xFFFF]
        if register.value_type in (ValueType.UINT32, ValueType.INT32):
            scaled = int(round(float(value) / register.scale))
            return [(scaled >> 16) & 0xFFFF, scaled & 0xFFFF]
        raise ModbusClientProtocolError(f"Unsupported write type for {register.name}: {register.value_type}")

    async def _handle_retry(self) -> None:
        try:
            await self.reconnect()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Reconnect failed during retry: %s", err)

    def _mark_ok(self) -> None:
        self._stats.connected = True
        self._stats.last_ok_monotonic = monotonic()
        self._stats.last_error = None
