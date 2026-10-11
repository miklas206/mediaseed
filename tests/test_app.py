import httpx
import pytest
from fastapi.testclient import TestClient
from mediaseed.app import create_app

PASSWORD = "synthetic-test-password-only"


@pytest.fixture
def client(tmp_path):
    def handler(request):
        if request.url.path == "/api/v2/auth/login":
            return httpx.Response(200, text="Ok.")
        return httpx.Response(200, json=[{"hash": "a" * 40, "name": "Test torrent", "progress": 0.5, "save_path": "private"}])

    with TestClient(create_app(tmp_path, "http://testserver", qbit_password="synthetic", transport=httpx.MockTransport(handler))) as client:
        yield client


def sign_in(client):
    assert client.post("/api/auth/setup", json={"username": "tester", "password": PASSWORD}).status_code == 200
    response = client.post("/api/auth/login", json={"username": "tester", "password": PASSWORD})
    assert response.status_code == 200
    client.headers["X-MediaSeed-CSRF"] = response.json()["csrf"]


def test_independent_account_and_sessions(client):
    assert client.get("/api/torrents").status_code == 401
    sign_in(client)
    assert client.get("/api/auth/status").json() == {"needsSetup": False}
    assert client.post("/api/auth/setup", json={"username": "other", "password": PASSWORD}).status_code == 409
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/torrents").status_code == 401


def test_torrents_are_filtered_and_controls_fail_closed(client):
    sign_in(client)
    assert client.get("/api/torrents").json() == [{"hash": "a" * 40, "name": "Test torrent", "progress": 0.5}]
    assert client.post("/api/torrents", json={"url": "magnet:?xt=urn:btih:" + "a" * 40}).status_code == 409
    assert client.post("/api/torrents/start", json={"torrent_hash": "a" * 40}).status_code == 409
    assert client.post("/api/torrents/remove", json={"torrent_hash": "all"}).status_code == 422
    client.headers["X-MediaSeed-CSRF"] = "invalid"
    assert client.post("/api/torrents/remove", json={"torrent_hash": "a" * 40}).status_code == 403


def test_host_origin_and_cookie_guards(client):
    assert client.get("/healthz", headers={"host": "untrusted.test"}).status_code == 400
    assert client.post("/api/auth/setup", json={"username": "tester", "password": PASSWORD}, headers={"origin": "https://untrusted.test"}).status_code == 403
    sign_in(client)
    assert client.get("/api/auth/me").headers["Cache-Control"] == "no-store"