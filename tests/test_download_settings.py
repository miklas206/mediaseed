import json

import httpx
import pytest
from fastapi.testclient import TestClient
from test_app import sign_in

from mediaseed.app import create_app


def test_download_limit_reads_real_preferences_writes_narrow_fields_and_verifies(
    tmp_path,
):
    preferences = {"max_active_downloads": 8, "queueing_enabled": True}
    writes = []

    def handler(request):
        if request.url.path.endswith("/login"):
            return httpx.Response(200, text="Ok.")
        if request.url.path.endswith("/setPreferences"):
            from urllib.parse import parse_qs

            fields = parse_qs(request.content.decode())
            value = json.loads(fields["json"][0])
            writes.append(value)
            preferences.update(value)
            return httpx.Response(200)
        if request.url.path.endswith("/preferences"):
            return httpx.Response(200, json=preferences)
        return httpx.Response(200, json=[])

    with TestClient(
        create_app(
            tmp_path,
            "http://testserver",
            qbit_password="synthetic",
            transport=httpx.MockTransport(handler),
        )
    ) as client:
        assert client.get("/api/download-settings").status_code == 401
        assert (
            client.put(
                "/api/download-settings", json={"maxActiveDownloads": 3}
            ).status_code
            == 401
        )
        sign_in(client)
        assert client.get("/api/download-settings").json()["maxActiveDownloads"] == 8
        assert (
            client.put("/api/download-settings", json={"maxActiveDownloads": 3}).json()[
                "maxActiveDownloads"
            ]
            == 3
        )
        assert writes == [{"max_active_downloads": 3, "queueing_enabled": True}]
        assert client.get("/api/dashboard").json()["queue"]["limit"] == 3
        assert client.get("/api/queue").json()["limit"] == 3
        client.headers["X-MediaSeed-CSRF"] = "invalid"
        assert (
            client.put(
                "/api/download-settings", json={"maxActiveDownloads": 4}
            ).status_code
            == 403
        )
        assert len(writes) == 1


@pytest.mark.parametrize("value", [0, -1, 101, True, 2.5, "8"])
def test_invalid_download_limit_cannot_change_client(tmp_path, value):
    with TestClient(create_app(tmp_path, "http://testserver")) as client:
        sign_in(client)
        assert (
            client.put(
                "/api/download-settings", json={"maxActiveDownloads": value}
            ).status_code
            == 422
        )


def test_unapplied_download_limit_is_not_reported_saved(tmp_path):
    def handler(request):
        if request.url.path.endswith("/login"):
            return httpx.Response(200, text="Ok.")
        if request.url.path.endswith("/preferences"):
            return httpx.Response(
                200, json={"max_active_downloads": 8, "queueing_enabled": True}
            )
        return httpx.Response(200)

    with TestClient(
        create_app(
            tmp_path,
            "http://testserver",
            qbit_password="synthetic",
            transport=httpx.MockTransport(handler),
        )
    ) as client:
        sign_in(client)
        response = client.put("/api/download-settings", json={"maxActiveDownloads": 3})
        assert response.status_code == 503
        assert "could not be verified" in response.json()["detail"]


def test_mediavpn_queue_update_requires_capability_and_verifies_native_value(
    tmp_path, monkeypatch
):
    import base64

    import mediaseed.app as module

    token = tmp_path / "token"
    token.write_text("synthetic-token")
    monkeypatch.setenv("MEDIASEED_VPN_URL", "http://mediavpn:18802")
    monkeypatch.setenv("MEDIASEED_VPN_TOKEN_FILE", str(token))
    original = httpx.AsyncClient
    limit = 8
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
                    "torrentCapabilities": ["queue-settings"] if capable else [],
                },
            )
        if request.url.path.endswith("/settings"):
            return httpx.Response(200, json={})
        if request.url.path.endswith("/queue"):
            writes.append(request.read())
            limit = json.loads(request.content)["maxActiveDownloads"]
            return httpx.Response(
                200, json={"maxActiveDownloads": limit, "queueingEnabled": True}
            )
        body = json.loads(request.content)
        assert body["path"] == "app/preferences" and body["method"] == "GET"
        return httpx.Response(
            200,
            json={
                "status": 200,
                "body": base64.b64encode(
                    json.dumps(
                        {"max_active_downloads": limit, "queueing_enabled": True}
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
                "/api/download-settings", json={"maxActiveDownloads": 3}
            ).status_code
            == 409
        )
        assert writes == []
        capable = True
        response = client.put("/api/download-settings", json={"maxActiveDownloads": 3})
        assert response.status_code == 200, response.text
        assert response.json()["maxActiveDownloads"] == 3
        assert [json.loads(value) for value in writes] == [{"maxActiveDownloads": 3}]
