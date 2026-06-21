import asyncio

from custom_components.webasto_unite.models import RestDiagnosticsData
from custom_components.webasto_unite.rest.client import RestDiagnosticsClient, WebUiActionClient


class _FakeResponse:
    def __init__(self, status, payload, *, headers=None, cookies=None):
        self.status = status
        self.payload = payload
        self.headers = headers or {}
        self.cookies = cookies or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return self.payload

    async def text(self):
        return str(self.payload)


class _FakeSession:
    def __init__(self):
        self.post_calls = []
        self.get_calls = []

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return _FakeResponse(201, {"access_token": "token"})

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        if url.endswith("/system-information"):
            return _FakeResponse(
                200,
                {
                    "apiVersion": "1.12.0",
                    "hmiVersion": "v3.187.0",
                    "identifier": "Wallbox API",
                    "model": "Webasto Unite AC22",
                },
            )
        if url.endswith("/configuration-fields"):
            return _FakeResponse(
                200,
                [
                    {
                        "fieldKey": "installationSettings.currentLimiterValue",
                        "value": 16,
                    },
                    {
                        "fieldKey": "installationSettings.currentLimiterPhase",
                        "value": 1,
                    },
                    {
                        "fieldKey": "ocppConfigurations.connectorSwitch3To1PhaseSupported",
                        "value": "FALSE",
                    },
                    {
                        "fieldKey": "ocppConfigurations.freeModeActive",
                        "value": "TRUE",
                    },
                ],
            )
        return _FakeResponse(404, {})


class _FakeWebUiSession:
    def __init__(self):
        self.post_calls = []
        self.get_calls = []

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        if url.endswith("/index_main.php") and kwargs.get("data", {}).get("button_soft_reset") == "":
            return _FakeResponse(302, "")
        return _FakeResponse(
            200,
            '<html><input type="hidden" name="token" value="csrf-token"></html>',
            headers={"Set-Cookie": "PHPSESSID=session-id; path=/"},
        )

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return _FakeResponse(
            200,
            '<html><meta name="csrf-token" content="csrf-token"></html>',
        )


def test_rest_diagnostics_client_parses_unite_api_fields():
    async def _run():
        session = _FakeSession()
        client = RestDiagnosticsClient(
            host="192.0.2.10",
            username="admin",
            password="secret",
            session=session,
        )

        data = await client.fetch_system_information(RestDiagnosticsData(enabled=True))
        return await client.fetch_configuration_fields(data)

    data = asyncio.run(_run())

    assert data.status == "connected"
    assert data.api_version == "1.12.0"
    assert data.hmi_version == "v3.187.0"
    assert data.model == "Webasto Unite AC22"
    assert data.installation_current_limiter_value_a == 16.0
    assert data.installation_current_limiter_phase == "3P"
    assert data.ocpp_phase_switching_supported is False
    assert data.ocpp_free_mode_active is True
    assert data.field_count == 4
    assert "installationSettings.currentLimiterPhase" in data.discovered_field_keys


def test_webui_action_client_soft_reset_posts_classic_form():
    async def _run():
        session = _FakeWebUiSession()
        client = WebUiActionClient(
            host="192.0.2.10",
            username="admin",
            password="secret",
            session=session,
        )

        await client.soft_reset()
        return session

    session = asyncio.run(_run())

    assert len(session.post_calls) == 2
    login_url, login_kwargs = session.post_calls[0]
    reset_url, reset_kwargs = session.post_calls[1]
    assert login_url == "http://192.0.2.10/index_main.php"
    assert login_kwargs["data"] == {"username": "admin", "password": "secret"}
    assert reset_url == "http://192.0.2.10/index_main.php"
    assert reset_kwargs["data"] == {"token": "csrf-token", "button_soft_reset": ""}
    assert reset_kwargs["headers"]["Cookie"] == "PHPSESSID=session-id"
    assert reset_kwargs["allow_redirects"] is False
