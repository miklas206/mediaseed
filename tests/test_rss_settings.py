import json
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient
from test_app import sign_in

from mediaseed.app import create_app


def test_rss_interval_is_authenticated_narrow_and_verified(tmp_path):
    preferences = {"rss_refresh_interval": 30}
    writes = []
    apply = True

    def handler(request):
        if request.url.path.endswith("/login"):
            return httpx.Response(200, text="Ok.")
        if request.url.path.endswith("/setPreferences"):
            value = json.loads(parse_qs(request.content.decode())["json"][0])
            writes.append(value)
            if apply:
                preferences.update(value)
        return httpx.Response(200, json=preferences)

    with TestClient(create_app(tmp_path, "http://testserver", qbit_password="synthetic", transport=httpx.MockTransport(handler))) as client:
        assert client.get("/api/rss-settings").status_code == 401
        assert client.put("/api/rss-settings", json={"rssRefreshMinutes": 5}).status_code == 401
        sign_in(client)
        assert client.get("/api/rss-settings").json() == {"rssRefreshMinutes": 30}
        assert client.put("/api/rss-settings", json={"rssRefreshMinutes": 5}).json() == {"rssRefreshMinutes": 5}
        assert writes == [{"rss_refresh_interval": 5}]
        apply = False
        assert client.put("/api/rss-settings", json={"rssRefreshMinutes": 10}).status_code == 503
        client.headers["X-MediaSeed-CSRF"] = "invalid"
        assert client.put("/api/rss-settings", json={"rssRefreshMinutes": 10}).status_code == 403
        assert len(writes) == 2


@pytest.mark.parametrize("value", [0, -1, 1441, True, 2.5, "5"])
def test_invalid_interval_is_rejected(tmp_path, value):
    with TestClient(create_app(tmp_path, "http://testserver")) as client:
        sign_in(client)
        assert client.put("/api/rss-settings", json={"rssRefreshMinutes": value}).status_code == 422


def test_mediavpn_rss_update_requires_capability_and_verifies_native_value(
    tmp_path, monkeypatch
):
    import base64

    import mediaseed.app as module

    token = tmp_path / "token"
    token.write_text("synthetic-token")
    monkeypatch.setenv("MEDIASEED_VPN_URL", "http://mediavpn:18802")
    monkeypatch.setenv("MEDIASEED_VPN_TOKEN_FILE", str(token))
    original = httpx.AsyncClient
    limit = 5
    capable = False
    writes = []

    def handler(request):
        nonlocal limit
        if request.url.path.endswith("/status"):
            return httpx.Response(
                200,
                json={
                    "enabled": True,
                    "profileId": "a" * 24,
                    "ready": True,
                    "downloadsEnabled": True,
                    "torrentCapabilities": ["rss-settings"] if capable else [],
                },
            )
        if request.url.path.endswith("/settings"):
            return httpx.Response(200, json={})
        if request.url.path.endswith("/rss"):
            writes.append(request.read())
            limit = json.loads(request.content)["rssRefreshMinutes"]
            return httpx.Response(
                200, json={"rssRefreshMinutes": limit, "verified": True}
            )
        body = json.loads(request.content)
        assert body["path"] == "app/preferences" and body["method"] == "GET"
        return httpx.Response(
            200,
            json={
                "status": 200,
                "body": base64.b64encode(
                    json.dumps(
                        {"rss_refresh_interval": limit}
                    ).encode()
                ).decode(),
            },
        )

    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handler)),
    )
    with TestClient(create_app(tmp_path / "state", "http://testserver")) as client:
        sign_in(client)
        assert (
            client.put(
                "/api/vpn",
                json={"provider": "mediavpn", "enabled": True, "profileId": "a" * 24},
            ).status_code
            == 200
        )
        assert (
            client.put(
                "/api/rss-settings", json={"rssRefreshMinutes": 3}
            ).status_code
            == 409
        )
        assert writes == []
        capable = True
        response = client.put("/api/rss-settings", json={"rssRefreshMinutes": 3})
        assert response.status_code == 200, response.text
        assert response.json()["rssRefreshMinutes"] == 3
        assert [json.loads(value) for value in writes] == [{"rssRefreshMinutes": 3}]
