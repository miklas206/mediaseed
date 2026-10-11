from urllib.parse import parse_qs

import httpx
from fastapi.testclient import TestClient

from mediaseed.app import create_app
from test_app import sign_in


def test_torrent_details_policy_and_early_stop(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/login"):
            return httpx.Response(200, text="Ok.")
        if request.url.path.endswith("/properties"):
            return httpx.Response(200, json={"share_ratio": 0.2, "seeding_time": 100, "save_path": "/secret"})
        if request.url.path.endswith("/files"):
            return httpx.Response(200, json=[{"index": 0, "name": "file.iso", "size": 100}])
        if request.url.path.endswith("/trackers"):
            return httpx.Response(200, json=[{"url": "https://tracker.test/private/passkey/announce", "status": 2}])
        if request.url.path.endswith("/torrentPeers"):
            return httpx.Response(200, json={"peers": {"one": {"ip": "192.0.2.1", "client": "test"}}})
        return httpx.Response(200, text="")

    with TestClient(create_app(tmp_path, "http://testserver", qbit_password="synthetic", transport=httpx.MockTransport(handler))) as client:
        sign_in(client)
        identifier = "a" * 40
        result = client.get(f"/api/torrents/{identifier}/details")
        assert result.status_code == 200
        assert "passkey" not in result.text and "/secret" not in result.text
        assert result.json()["files"][0]["name"] == "file.iso"
        assert client.post("/api/torrents/stop", json={"torrent_hash": identifier}).status_code == 409
        assert not any(request.url.path.endswith("/stop") for request in calls)
        assert client.post("/api/torrents/remove", json={"torrent_hash": identifier, "confirm_early": True}).status_code == 200
        assert parse_qs(calls[-1].content.decode())["deleteFiles"] == ["false"]
        assert client.post(f"/api/torrents/{identifier}/policy", json={"ratio": 0.5}).status_code == 422
        assert client.post(f"/api/torrents/{identifier}/policy", json={"ratio": 2}).status_code == 200
        assert parse_qs(calls[-1].content.decode())["seedingTimeLimit"] == ["2940"]


def test_saved_sources_mask_secrets_and_preserve_credentials(tmp_path):
    app = create_app(tmp_path, "http://testserver")
    with TestClient(app) as client:
        sign_in(client)
        source = {"name": "Legal releases", "url": "https://tracker.test/rss?passkey=private-secret", "folder": "releases", "ratio": 2, "start_at": "2026-10-11T12:00:00Z"}
        identifier = client.post("/api/feeds", json=source).json()["id"]
        assert "private-secret" not in client.get("/api/feeds").text
        assert client.get("/api/feeds").json()[0]["paused"] is True
        source["url"] = ""
        assert client.put(f"/api/feeds/{identifier}", json=source).status_code == 200
        source["folder"] = "../outside"
        assert client.put(f"/api/feeds/{identifier}", json=source).status_code == 422
        account = client.post("/api/trackers", json={"name": "My account", "api_key": "synthetic-key"})
        assert account.status_code == 200
        assert "synthetic-key" not in client.get("/api/trackers").text
        assert client.post("/api/trackers", json={"name": "Empty"}).status_code == 422
        assert client.post("/api/feeds", json={"name": "Unsafe", "url": "http://localhost/rss"}).status_code == 422
        assert client.post("/api/feeds", json={"name": "Unsafe", "url": "https://user:password@example.test/rss"}).status_code == 422
        assert client.post("/api/feeds", json={"name": "Bad date", "url": "https://example.test/rss", "start_at": "2026-10-11T12:00"}).status_code == 422
        assert client.post("/api/trackers", json={"name": "Unknown", "provider": "arbitrary", "api_key": "test"}).status_code == 422
        assert client.get("/api/runtime").json()["downloadsEnabled"] is False
        assert client.delete(f"/api/settings/feeds/{identifier}").status_code == 200
        assert client.get("/api/feeds").json() == []
    with TestClient(create_app(tmp_path, "http://testserver")) as restarted:
        assert restarted.post("/api/auth/login", json={"username": "tester", "password": "synthetic-test-password-only"}).status_code == 200
        assert len(restarted.get("/api/trackers").json()) == 1


def test_configuration_mutations_require_session_and_csrf(tmp_path):
    with TestClient(create_app(tmp_path, "http://testserver")) as client:
        assert client.get("/api/feeds").status_code == 401
        assert client.post("/api/trackers", json={"name": "Test", "api_key": "synthetic"}).status_code == 401
        sign_in(client)
        client.headers["X-MediaSeed-CSRF"] = "invalid"
        assert client.post("/api/feeds", json={"name": "Test", "url": "https://example.test/rss"}).status_code == 403
        assert client.delete("/api/settings/feeds/unknown").status_code == 403
        assert client.post("/api/torrents/" + "a" * 40 + "/policy", json={"ratio": 2}).status_code == 403


def test_local_storage_is_ready_without_external_mount(tmp_path):
    downloads = tmp_path / "local-downloads"
    with TestClient(create_app(tmp_path / "state", "http://testserver", downloads_dir=downloads)) as client:
        assert downloads.is_dir()
        assert client.get("/api/storage").status_code == 401
        sign_in(client)
        observed = client.get("/api/storage").json()
        assert observed["externalMountRequired"] is False
        assert observed["writable"] is True
        assert observed["totalBytes"] >= observed["freeBytes"] > 0
        assert not (downloads / ".mediahub-storage-id").exists()


def test_local_storage_rejects_symlink(tmp_path):
    import os
    import pytest
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        os.symlink(real, link, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation unavailable")
    with pytest.raises(ValueError):
        create_app(tmp_path / "data", "http://testserver", downloads_dir=link)