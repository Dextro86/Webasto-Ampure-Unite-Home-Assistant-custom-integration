from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import re
from typing import Any

from ..models import RestDiagnosticsData


class RestDiagnosticsError(Exception):
    """Raised when the optional REST diagnostics API cannot be read."""


class WebUiActionError(Exception):
    """Raised when an explicit WebUI action cannot be executed."""


class RestDiagnosticsClient:
    """Small read-only client for the Unite WebUI REST API.

    This intentionally does not expose writes, restart, free-charging or
    firmware update endpoints. REST diagnostics must never own charging logic.
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


class WebUiActionClient:
    """Explicit WebUI actions using the classic PHP WebUI flow.

    The Unite WebUI soft-reset button is not part of the bearer-token REST API.
    It posts a CSRF-protected form to /index_main.php with button_soft_reset.
    This helper intentionally implements only soft reset.
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
        self.base_url = f"http://{host}"
        self._cookie_header: str | None = None

    async def soft_reset(self) -> None:
        token = await self._login_and_get_csrf_token()
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        if self._cookie_header:
            headers["Cookie"] = self._cookie_header
        data = {
            "token": token,
            "button_soft_reset": "",
        }
        async with self.session.post(
            f"{self.base_url}/index_main.php",
            data=data,
            headers=headers,
            timeout=self.timeout_s,
            allow_redirects=False,
        ) as response:
            if response.status not in {200, 302, 303}:
                raise WebUiActionError(f"soft reset returned HTTP {response.status}")

    async def _login_and_get_csrf_token(self) -> str:
        login_data = {
            "username": self.username,
            "password": self.password,
        }
        async with self.session.post(
            f"{self.base_url}/index_main.php",
            data=login_data,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=self.timeout_s,
            allow_redirects=True,
        ) as response:
            if response.status >= 400:
                raise WebUiActionError(f"WebUI login returned HTTP {response.status}")
            self._capture_cookie_header(response)
            text = await response.text()
        token = self._extract_csrf_token(text)
        if token is None:
            headers = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
            if self._cookie_header:
                headers["Cookie"] = self._cookie_header
            async with self.session.get(
                f"{self.base_url}/index_main.php",
                headers=headers,
                timeout=self.timeout_s,
            ) as response:
                if response.status >= 400:
                    raise WebUiActionError(f"WebUI page returned HTTP {response.status}")
                self._capture_cookie_header(response)
                token = self._extract_csrf_token(await response.text())
        if token is None:
            raise WebUiActionError("WebUI CSRF token not found")
        return token

    def _capture_cookie_header(self, response) -> None:
        cookies = getattr(response, "cookies", None)
        if cookies:
            pairs = []
            for name, morsel in cookies.items():
                value = getattr(morsel, "value", None)
                if value is not None:
                    pairs.append(f"{name}={value}")
            if pairs:
                self._cookie_header = "; ".join(pairs)
                return
        headers = getattr(response, "headers", None) or {}
        set_cookie = None
        try:
            set_cookie = headers.get("Set-Cookie")
        except AttributeError:
            set_cookie = None
        if set_cookie:
            self._cookie_header = set_cookie.split(";", 1)[0]

    @staticmethod
    def _extract_csrf_token(html: str) -> str | None:
        patterns = (
            r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']',
            r'<input[^>]+name=["\']token["\'][^>]+value=["\']([^"\']+)["\']',
        )
        for pattern in patterns:
            match = re.search(pattern, html, flags=re.IGNORECASE)
            if match:
                token = match.group(1).strip()
                if token:
                    return token
        return None
