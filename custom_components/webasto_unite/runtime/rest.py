from __future__ import annotations

from dataclasses import replace
from time import monotonic

from homeassistant.const import CONF_HOST

from ..const import (
    CONF_REST_DIAGNOSTICS_ENABLED,
    CONF_REST_PASSWORD,
    CONF_REST_USERNAME,
    CONF_TIMEOUT,
    DEFAULT_REST_CONFIGURATION_INITIAL_DELAY_S,
    DEFAULT_REST_CONFIGURATION_REFRESH_S,
    DEFAULT_REST_SYSTEM_REFRESH_S,
    DEFAULT_REST_USERNAME,
    DEFAULT_TIMEOUT_S,
)
from ..models import RestDiagnosticsData
from ..rest.client import RestDiagnosticsClient


class RestDiagnosticsRuntime:
    """Owns optional REST diagnostics and explicit REST actions.

    The coordinator still exposes ``rest_diagnostics`` and ``rest_client`` for
    entity/test compatibility; this helper owns the actual setup/refresh flow.
    """

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator

    def initialize(self) -> None:
        self.coordinator.rest_diagnostics = RestDiagnosticsData()
        self.coordinator.rest_client = None
        self.coordinator._rest_system_last_fetch_monotonic = None
        self.coordinator._rest_configuration_last_fetch_monotonic = None
        self.coordinator._rest_configuration_not_before_monotonic = (
            monotonic() + DEFAULT_REST_CONFIGURATION_INITIAL_DELAY_S
        )

    @property
    def enabled(self) -> bool:
        merged = self._merged_options()
        return bool(merged.get(CONF_REST_DIAGNOSTICS_ENABLED, False))

    async def setup(self) -> None:
        if not self.enabled:
            self.coordinator.rest_diagnostics = RestDiagnosticsData(enabled=False, status="disabled")
            self.coordinator.rest_client = None
            return

        merged = self._merged_options()
        username = str(merged.get(CONF_REST_USERNAME, DEFAULT_REST_USERNAME)).strip()
        password = str(merged.get(CONF_REST_PASSWORD, "") or "")
        if not username or not password:
            self.coordinator.rest_diagnostics = RestDiagnosticsData(
                enabled=True,
                status="missing_credentials",
                last_error="REST diagnostics enabled but username or password is missing",
            )
            self.coordinator.rest_client = None
            return

        try:
            from homeassistant.helpers.aiohttp_client import async_get_clientsession

            session = async_get_clientsession(self.coordinator.hass, verify_ssl=False)
        except Exception as err:  # noqa: BLE001
            self.coordinator.rest_diagnostics = RestDiagnosticsData(
                enabled=True,
                status="unavailable",
                last_error=f"Unable to create REST session: {err}",
            )
            self.coordinator.rest_client = None
            return

        self.coordinator.rest_diagnostics = RestDiagnosticsData(enabled=True, status="pending")
        self.coordinator.rest_client = RestDiagnosticsClient(
            host=merged.get(CONF_HOST),
            username=username,
            password=password,
            session=session,
            timeout_s=self._timeout_s(merged),
        )

    async def restart_charger(self) -> None:
        """Request an explicit REST restart/reboot of the charger."""
        if not self.enabled:
            raise RuntimeError("Charger restart requires REST Diagnostics to be enabled")

        merged = self._merged_options()
        username = str(merged.get(CONF_REST_USERNAME, DEFAULT_REST_USERNAME)).strip()
        password = str(merged.get(CONF_REST_PASSWORD, "") or "")
        host = str(merged.get(CONF_HOST, "") or "")
        if not host or not username or not password:
            raise RuntimeError("Charger restart requires REST host, username and password")

        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        session = async_get_clientsession(self.coordinator.hass, verify_ssl=False)
        client = self.coordinator.rest_client or RestDiagnosticsClient(
            host=host,
            username=username,
            password=password,
            session=session,
            timeout_s=self._timeout_s(merged),
        )
        await client.restart_system()

    async def refresh_if_needed(self) -> None:
        if not self.enabled:
            self.coordinator.rest_diagnostics = RestDiagnosticsData(enabled=False, status="disabled")
            return
        if self.coordinator.rest_client is None:
            return

        now = monotonic()
        try:
            if (
                self.coordinator._rest_system_last_fetch_monotonic is None
                or now - self.coordinator._rest_system_last_fetch_monotonic
                >= DEFAULT_REST_SYSTEM_REFRESH_S
            ):
                self.coordinator.rest_diagnostics = (
                    await self.coordinator.rest_client.fetch_system_information(
                        self.coordinator.rest_diagnostics
                    )
                )
                self.coordinator._rest_system_last_fetch_monotonic = now

            if (
                now >= self.coordinator._rest_configuration_not_before_monotonic
                and (
                    self.coordinator._rest_configuration_last_fetch_monotonic is None
                    or now - self.coordinator._rest_configuration_last_fetch_monotonic
                    >= DEFAULT_REST_CONFIGURATION_REFRESH_S
                )
            ):
                self.coordinator.rest_diagnostics = (
                    await self.coordinator.rest_client.fetch_configuration_fields(
                        self.coordinator.rest_diagnostics
                    )
                )
                self.coordinator._rest_configuration_last_fetch_monotonic = now
        except Exception as err:  # noqa: BLE001
            self.coordinator.rest_diagnostics = replace(
                self.coordinator.rest_diagnostics,
                enabled=True,
                status="error",
                last_error=str(err),
            )

    def snapshot(self) -> RestDiagnosticsData:
        data = self.coordinator.rest_diagnostics
        now = monotonic()
        return RestDiagnosticsData(
            enabled=data.enabled,
            status=data.status,
            last_error=data.last_error,
            api_version=data.api_version,
            hmi_version=data.hmi_version,
            identifier=data.identifier,
            model=data.model,
            installation_current_limiter_value_a=data.installation_current_limiter_value_a,
            installation_current_limiter_phase=data.installation_current_limiter_phase,
            ocpp_phase_switching_supported=data.ocpp_phase_switching_supported,
            ocpp_free_mode_active=data.ocpp_free_mode_active,
            field_count=data.field_count,
            discovered_field_keys=data.discovered_field_keys,
            last_system_update_age_s=(
                None
                if self.coordinator._rest_system_last_fetch_monotonic is None
                else now - self.coordinator._rest_system_last_fetch_monotonic
            ),
            last_configuration_update_age_s=(
                None
                if self.coordinator._rest_configuration_last_fetch_monotonic is None
                else now - self.coordinator._rest_configuration_last_fetch_monotonic
            ),
        )

    def shutdown(self) -> None:
        self.coordinator.rest_client = None

    def _merged_options(self) -> dict:
        entry = self.coordinator.entry
        return {**getattr(entry, "data", {}), **getattr(entry, "options", {})}

    @staticmethod
    def _timeout_s(merged: dict) -> float:
        return max(5.0, float(merged.get(CONF_TIMEOUT, DEFAULT_TIMEOUT_S)))
