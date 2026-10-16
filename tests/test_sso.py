import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from mediaseed.app import create_app


@pytest.mark.parametrize("app_origin,expected_hub", [("https://seed.example", "https://public-hub.example"), ("https://192.168.1.20:18800", "https://hub.example")])
def test_managed_login_and_live_permissions(tmp_path, monkeypatch, app_origin, expected_hub):
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

    app = create_app(tmp_path, app_origin, hub_transport=httpx.MockTransport(handler))
    with TestClient(app, base_url=app_origin) as client:
        assert client.get("/api/auth/status").json()["managed"] is True
        credentials = {"username": "local", "password": "synthetic-password-only"}
        assert client.post("/api/auth/setup", json=credentials).status_code == 403
        assert client.post("/api/auth/login", json=credentials).status_code == 403
        start = client.get("/auth/hub/start", follow_redirects=False)
        assert start.status_code == 303
        assert urlsplit(start.headers["location"]).netloc == urlsplit(expected_hub).netloc
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        assert client.get("/auth/hub/callback", params={"state": "wrong" * 12, "code": "c" * 64}, follow_redirects=False).status_code == 403
        callback = client.get("/auth/hub/callback", params={"state": state, "code": "c" * 64}, follow_redirects=False)
        assert callback.status_code == 303
        me = client.get("/api/auth/me").json()
        assert me["username"] == "member"
        assert me["hubHomeUrl"] == expected_hub
        client.headers["X-MediaSeed-CSRF"] = me["csrf"]
        assert client.put("/api/auth/navigation", json={"navigationOverride": "dock-bottom"}).status_code == 200
        assert client.get("/api/auth/me").json()["navigationOverride"] == "dock-bottom"
        client.headers["X-MediaSeed-CSRF"] = "bad"
        assert client.put("/api/auth/navigation", json={"navigationOverride": None}).status_code == 403
        client.headers["X-MediaSeed-CSRF"] = me["csrf"]
        assert client.put("/api/vpn", json={"enabled": False}).status_code == 403
        assert client.get("/api/feeds/missing").status_code == 403
        assert client.put("/api/download-settings", json={"maxActiveDownloads": 3}).status_code == 403
        assert client.put("/api/rss-settings", json={"rssRefreshMinutes": 5}).status_code == 403
        assert client.put("/api/ratio-scout/settings", json={}).status_code == 403
        assert client.post("/api/ratio-scout/" + "a" * 32 + "/add", json={}).status_code == 403
        role[0] = "user"
        assert client.get("/api/feeds/missing").status_code == 403
        assert client.put("/api/vpn", json={"enabled": False}).status_code == 403
        assert client.put("/api/download-settings", json={"maxActiveDownloads": 3}).status_code == 403
        assert client.put("/api/rss-settings", json={"rssRefreshMinutes": 5}).status_code == 403
        assert client.put("/api/ratio-scout/settings", json={}).status_code == 403
        assert client.post("/api/ratio-scout/" + "a" * 32 + "/add", json={}).status_code == 403
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
