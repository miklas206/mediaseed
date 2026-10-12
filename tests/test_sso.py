import time
from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi.testclient import TestClient
from mediaseed.app import create_app


def test_managed_login_and_live_permissions(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIASEED_HUB_URL", "https://hub.example")
    monkeypatch.setenv("MEDIASEED_HUB_BROWSER_URL", "https://public-hub.example")
    role = ["viewer"]
    allowed = [True]
    online = [True]

    def handler(request):
        if not online[0]:
            raise httpx.ConnectError("Synthetic hub outage")
        assert request.url.host == "hub.example"
        if request.url.path.endswith("origin"):
            return httpx.Response(200, json={"data": {"origin": "https://seed.example"}})
        if request.url.path.endswith("exchange"):
            return httpx.Response(200, json={"data": {"token": "t" * 64, "expires": int(time.time()) + 3600, "user": {"id": "member", "username": "member", "role": role[0]}}})
        assert request.headers["Authorization"] == "Bearer " + "t" * 64
        if not allowed[0]:
            return httpx.Response(403, json={"error": {"message": "Access denied"}})
        return httpx.Response(200, json={"data": {"id": "member", "username": "member", "role": role[0]}})

    app = create_app(tmp_path, "https://seed.example", hub_transport=httpx.MockTransport(handler))
    with TestClient(app, base_url="https://seed.example") as client:
        assert client.get("/api/auth/status").json()["managed"] is True
        credentials = {"username": "local", "password": "synthetic-password-only"}
        assert client.post("/api/auth/setup", json=credentials).status_code == 403
        assert client.post("/api/auth/login", json=credentials).status_code == 403
        start = client.get("/auth/hub/start", follow_redirects=False)
        assert start.status_code == 303
        assert urlsplit(start.headers["location"]).netloc == "public-hub.example"
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        assert client.get("/auth/hub/callback", params={"state": "wrong" * 12, "code": "c" * 64}, follow_redirects=False).status_code == 403
        callback = client.get("/auth/hub/callback", params={"state": state, "code": "c" * 64}, follow_redirects=False)
        assert callback.status_code == 303
        me = client.get("/api/auth/me").json()
        assert me["username"] == "member"
        client.headers["X-MediaSeed-CSRF"] = me["csrf"]
        assert client.put("/api/vpn", json={"enabled": False}).status_code == 403
        role[0] = "user"
        assert client.put("/api/vpn", json={"enabled": False}).status_code == 403
        assert client.get("/api/auth/me").json()["role"] == "user"
        allowed[0] = False
        assert client.get("/api/storage").status_code == 403
        online[0] = False
        response = client.get("/", headers={"Host": "custom.example"})
        assert response.status_code == 503
        assert response.json()["detail"] == "MediaHub authentication unavailable"


def test_sso_requires_https(tmp_path, monkeypatch):
    import pytest

    monkeypatch.setenv("MEDIASEED_HUB_URL", "http://hub.example")
    with pytest.raises(ValueError, match="HTTPS"):
        create_app(tmp_path, "https://seed.example")
