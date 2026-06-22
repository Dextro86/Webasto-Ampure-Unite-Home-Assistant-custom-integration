from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from ..models import RestDiagnosticsData


class RestDiagnosticsError(Exception):
    """Raised when the optional REST diagnostics API cannot be read."""


class RestDiagnosticsClient:
    """Small client for the Unite WebUI REST API.

    Diagnostics are read-only. The only explicit action exposed here is the
    user-triggered system restart endpoint; REST must never own charging logic.
    """

    def __init__(
        self,
        *,
        host: str,
        username: str,
        password: str,
        session,
        timeout_s: float = 15.0,
    ) -> None:
        self.host = host
        self.username = username
        self.password = password
        self.session = session
        self.timeout_s = timeout_s
        self.base_url = f"https://{host}/api"
        self._token: str | None = None
        self._token_expires_at: datetime | None = None

    async def fetch_system_information(self, current: RestDiagnosticsData) -> RestDiagnosticsData:
        data = await self._get_json("/system-information")
        if not isinstance(data, dict):
            raise RestDiagnosticsError("system-information returned unexpected data")
        return replace(
            current,
            enabled=True,
            status="connected",
            last_error=None,
            api_version=self._string_or_none(data.get("apiVersion")),
            hmi_version=self._string_or_none(data.get("hmiVersion")),
            identifier=self._string_or_none(data.get("identifier")),
            model=self._string_or_none(data.get("model")),
        )

    async def fetch_configuration_fields(self, current: RestDiagnosticsData) -> RestDiagnosticsData:
        data = await self._get_json("/configuration-fields")
        if not isinstance(data, list):
            raise RestDiagnosticsError("configuration-fields returned unexpected data")

        values_by_key = {
            str(item.get("fieldKey")): item.get("value")
            for item in data
            if isinstance(item, dict) and item.get("fieldKey") is not None
        }
        return replace(
            current,
            enabled=True,
            status="connected",
            last_error=None,
            installation_current_limiter_value_a=self._float_or_none(
                values_by_key.get("installationSettings.currentLimiterValue")
            ),
            installation_current_limiter_phase=self._phase_value(
                values_by_key.get("installationSettings.currentLimiterPhase")
            ),
            ocpp_phase_switching_supported=self._bool_or_none(
                values_by_key.get("ocppConfigurations.connectorSwitch3To1PhaseSupported")
            ),
            ocpp_free_mode_active=self._bool_or_none(
                values_by_key.get("ocppConfigurations.freeModeActive")
            ),
            field_count=len(values_by_key),
            discovered_field_keys=tuple(sorted(values_by_key)),
        )

    async def _get_json(self, path: str) -> Any:
        await self._ensure_token()
        timeout_s = max(self.timeout_s, 20.0) if path == "/configuration-fields" else self.timeout_s
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._token}",
        }
        async with self.session.get(
            f"{self.base_url}{path}",
            headers=headers,
            timeout=timeout_s,
        ) as response:
            if response.status == 401:
                self._token = None
                await self._ensure_token()
                headers["Authorization"] = f"Bearer {self._token}"
                async with self.session.get(
                    f"{self.base_url}{path}",
                    headers=headers,
                    timeout=timeout_s,
                ) as retry_response:
                    return await self._parse_response(retry_response, path)
            return await self._parse_response(response, path)

    async def restart_system(self) -> None:
        """Request a charger reboot through the modern REST action endpoint."""
        await self._post_action("/custom-actions/restart-system")

    async def _post_action(self, path: str) -> None:
        await self._ensure_token()
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._token}",
        }
        async with self.session.post(
            f"{self.base_url}{path}",
            headers=headers,
            timeout=self.timeout_s,
        ) as response:
            if response.status == 401:
                self._token = None
                await self._ensure_token()
                headers["Authorization"] = f"Bearer {self._token}"
                async with self.session.post(
                    f"{self.base_url}{path}",
                    headers=headers,
                    timeout=self.timeout_s,
                ) as retry_response:
                    if retry_response.status >= 400:
                        raise RestDiagnosticsError(f"{path} returned HTTP {retry_response.status}")
                    return
            if response.status >= 400:
                raise RestDiagnosticsError(f"{path} returned HTTP {response.status}")

    async def _ensure_token(self) -> None:
        if (
            self._token
            and self._token_expires_at
            and datetime.now(UTC) < self._token_expires_at - timedelta(minutes=5)
        ):
            return
        payload = {"username": self.username, "password": self.password}
        async with self.session.post(
            f"{self.base_url}/login",
            json=payload,
            headers={"Accept": "application/json"},
            timeout=self.timeout_s,
        ) as response:
            data = await self._parse_response(response, "/login")
        if not isinstance(data, dict) or not data.get("access_token"):
            raise RestDiagnosticsError("REST login did not return an access token")
        self._token = str(data["access_token"])
        self._token_expires_at = datetime.now(UTC) + timedelta(hours=1)

    @staticmethod
    async def _parse_response(response, path: str) -> Any:
        if response.status >= 400:
            raise RestDiagnosticsError(f"{path} returned HTTP {response.status}")
        try:
            return await response.json()
        except Exception as err:  # noqa: BLE001
            text = await response.text()
            raise RestDiagnosticsError(f"{path} returned invalid JSON: {text[:80]}") from err

    @staticmethod
    def _string_or_none(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _float_or_none(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _bool_or_none(cls, value: Any) -> bool | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "enabled", "yes", "on"}:
            return True
        if text in {"0", "false", "disabled", "no", "off"}:
            return False
        return None

    @staticmethod
    def _phase_value(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip().lower()
        if text in {"0", "1p", "one phase", "one_phase"}:
            return "1P"
        if text in {"1", "3p", "three phase", "three_phase"}:
            return "3P"
        return str(value)
