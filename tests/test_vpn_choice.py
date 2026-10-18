import base64
import json
import time

import httpx
from fastapi.testclient import TestClient

import mediaseed.app as module
from mediaseed.app import create_app
from test_app import sign_in


def integration(tmp_path, monkeypatch):
    token = tmp_path / "token"
    token.write_text("synthetic-token")
    monkeypatch.setenv("MEDIASEED_VPN_URL", "http://mediavpn")
    monkeypatch.setenv("MEDIASEED_VPN_TOKEN_FILE", str(token))
    state = {"enabled": True, "profileId": "a" * 24, "ready": True, "downloadsEnabled": True, "country": "Sweden", "checkedAt": time.time(), "profiles": [{"id": "a" * 24, "name": "Test VPN"}]}
    calls = []
    torrents = []
    original = httpx.AsyncClient

    def handler(request):
        assert request.headers["X-MediaSeed-Key"] == "synthetic-token"
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/settings"):
            state.update(json.loads(request.content))
            return httpx.Response(200, json=state)
        if request.url.path.endswith("/status"):
            return httpx.Response(200, json=state)
        if request.url.path.endswith("/rpc"):
            body = json.loads(request.content)
            payload = body.get("payload", {})
            if body["path"] == "torrents/info":
                rows = [r for r in torrents if not payload.get("hashes") or r["hash"] in payload["hashes"].split("|")]
                return httpx.Response(200, json={"status": 200, "body": base64.b64encode(json.dumps(rows).encode()).decode()})
            if body["path"] == "torrents/add":
                from mediaseed.cleanup import source_hashes
                torrents.append({"hash":source_hashes(magnet=payload["urls"])[0],"tags":payload.get("tags", ""),"progress":0,"state":"stoppedDL"})
        if request.url.path.endswith("/upload"):
            from mediaseed.cleanup import source_hashes
            torrents.append({"hash":source_hashes(content=request.content)[0],"tags":request.url.params.get("tags", ""),"progress":0,"state":"stoppedDL"})
        return httpx.Response(200, json={"status": 204, "body": base64.b64encode(b"").decode()})

    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handler)))
    return state, calls


def test_installation_and_discovery_never_select_or_enable_vpn(tmp_path, monkeypatch):
    _, calls = integration(tmp_path, monkeypatch)
    with TestClient(create_app(tmp_path / "data", "http://testserver")) as client:
        sign_in(client)
        assert not any(method != "GET" for method, _ in calls)
        status = client.get("/api/vpn").json()
        assert status["provider"] is None and status["enabled"] is False
        assert not status["downloadsEnabled"] and not status["ready"]
        assert status["profiles"] and status["integrationAvailable"]
        assert status["country"] is None and status["checkedAt"]
        assert client.post("/api/torrents/start", json={"torrent_hash": "a" * 40}).status_code == 409
        assert client.get("/api/torrents").status_code == 409
        assert not any(method != "GET" for method, _ in calls)
        assert client.put("/api/vpn", json={"enabled": True, "profileId": "a" * 24}).status_code == 422


def test_explicit_selection_persists_and_disconnect_releases_it(tmp_path, monkeypatch):
    state, calls = integration(tmp_path, monkeypatch)
    data = tmp_path / "data"
    with TestClient(create_app(data, "http://testserver")) as client:
        sign_in(client)
        connected = client.put("/api/vpn", json={"provider": "mediavpn", "enabled": True, "profileId": "a" * 24}).json()
        assert connected["ready"] and connected["country"] == "Sweden" and connected["checkedAt"]
        assert client.post("/api/torrents/start", json={"torrent_hash": "a" * 40}).status_code == 200
        cookies, headers = dict(client.cookies), dict(client.headers)
    with TestClient(create_app(data, "http://testserver"), cookies=cookies, headers=headers) as client:
        assert client.get("/api/vpn").json()["provider"] == "mediavpn"
        state["profileId"] = "b" * 24
        assert not client.get("/api/vpn").json()["downloadsEnabled"]
        assert client.post("/api/torrents/start", json={"torrent_hash": "a" * 40}).status_code == 409
        result = client.put("/api/vpn", json={"enabled": False}).json()
        assert result["provider"] is None and not result["downloadsEnabled"] and not result["disconnectPending"]
        assert state["enabled"] is False
        assert client.get("/api/torrents").status_code == 409
    count = calls.count(("PUT", "/api/integration/settings"))
    with TestClient(create_app(data, "http://testserver"), cookies=cookies, headers=headers) as client:
        assert client.get("/api/vpn").json()["provider"] is None
        assert calls.count(("PUT", "/api/integration/settings")) == count


def test_vpn_choice_requires_authentication_and_csrf(tmp_path, monkeypatch):
    _, calls = integration(tmp_path, monkeypatch)
    with TestClient(create_app(tmp_path / "data", "http://testserver")) as client:
        body = {"provider": "mediavpn", "enabled": True, "profileId": "a" * 24}
        assert client.put("/api/vpn", json=body).status_code == 401
        sign_in(client)
        client.headers.pop("X-MediaSeed-CSRF")
        assert client.put("/api/vpn", json=body).status_code == 403
        assert not any(method != "GET" for method, _ in calls)


def test_failed_disconnect_blocks_locally_and_retries_after_restart(tmp_path, monkeypatch):
    integration(tmp_path, monkeypatch)
    data = tmp_path / "data"
    with TestClient(create_app(data, "http://testserver")) as client:
        sign_in(client)
        assert client.put("/api/vpn", json={"provider": "mediavpn", "enabled": True, "profileId": "a" * 24}).status_code == 200
        from mediaseed.http_pool import HttpPool
        original = HttpPool.request
        offline = True

        async def offline_client(self, *args, **kwargs):
            if offline:
                raise httpx.ConnectError("Synthetic offline controller")
            return await original(self, *args, **kwargs)

        monkeypatch.setattr(HttpPool, "request", offline_client)
        assert client.put("/api/vpn", json={"enabled": False}).status_code == 503
        assert client.post("/api/torrents/start", json={"torrent_hash": "a" * 40}).status_code == 409
        assert client.get("/api/vpn").json()["disconnectPending"]
        cookies, headers = dict(client.cookies), dict(client.headers)
    offline = False
    with TestClient(create_app(data, "http://testserver"), cookies=cookies, headers=headers) as client:
        status = client.get("/api/vpn").json()
        assert not status["disconnectPending"] and status["provider"] is None
        assert not status["downloadsEnabled"]
