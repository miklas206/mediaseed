from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient

from mediaseed.app import create_app
from test_app import sign_in


def test_tracker_search_and_stopped_import_keep_secrets_private(tmp_path, monkeypatch):
    from mediaseed import features
    original_client = httpx.AsyncClient
    calls = []
    secret_link = "https://nordicbytes.org/torrents/download/12/private-passkey"

    def tracker_handler(request):
        calls.append(request)
        if request.url.path.startswith("/api/"):
            assert request.headers["Authorization"] == "Bearer synthetic-key"
            row = {"id": "12", "attributes": {"name": "Legal test", "size": 42, "seeders": 3, "leechers": 0, "download_link": secret_link}}
            return httpx.Response(200, json={"data": [row] if request.url.path.endswith("filter") else row})
        assert "Authorization" not in request.headers
        return httpx.Response(200, content=b"d4:infod4:name4:testee")

    def qbit_handler(request):
        if request.url.path.endswith("login"):
            return httpx.Response(204)
        calls.append(request)
        assert b'name="stopped"\r\n\r\ntrue' in request.content
        assert b'name="paused"\r\n\r\ntrue' in request.content
        assert b'name="torrents"; filename="tracker.torrent"' in request.content
        return httpx.Response(204)

    def client_factory(**kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(tracker_handler))
        return original_client(**kwargs)

    monkeypatch.setattr(features.httpx, "AsyncClient", client_factory)
    with TestClient(create_app(tmp_path, "http://testserver", qbit_password="synthetic", transport=httpx.MockTransport(qbit_handler))) as client:
        assert client.get("/api/trackers/unknown/search").status_code == 401
        sign_in(client)
        identifier = client.post("/api/trackers", json={"name": "Test", "api_key": "synthetic-key"}).json()["id"]
        result = client.get(f"/api/trackers/{identifier}/search", params={"query": "Legal"})
        assert result.status_code == 200
        assert result.json()["results"][0]["name"] == "Legal test"
        assert "passkey" not in result.text and "synthetic-key" not in result.text
        assert client.post(f"/api/trackers/{identifier}/torrents/12", json={"ratio": 1}).json() == {"added": True, "stopped": True, "downloadsEnabled": False}
        secret_link = "https://localhost/torrents/download/12/private-passkey"
        assert client.post(f"/api/trackers/{identifier}/torrents/12", json={"ratio": 1}).status_code == 502
        client.headers["X-MediaSeed-CSRF"] = "invalid"
        assert client.post(f"/api/trackers/{identifier}/torrents/12", json={"ratio": 1}).status_code == 403


@pytest.mark.parametrize("remote_status,content,expected", [(401, b"private-key", 502), (429, b"private-key", 503), (302, b"private-key", 502), (200, b"x" * (1024 * 1024 + 1), 502), (200, b'{"data":null}', 502)], ids=["unauthorized", "rate-limit", "redirect", "oversized", "invalid-shape"])
def test_tracker_search_rejects_remote_errors_and_oversized_responses(tmp_path, monkeypatch, remote_status, content, expected):
    from mediaseed import features
    original_client = httpx.AsyncClient

    def handler(request):
        assert request.url.host == "nordicbytes.org"
        return httpx.Response(remote_status, content=content, headers={"Location": "https://localhost/private-key"})

    monkeypatch.setattr(features.httpx, "AsyncClient", lambda **kwargs: original_client(**kwargs, transport=httpx.MockTransport(handler)))
    with TestClient(create_app(tmp_path, "http://testserver")) as client:
        sign_in(client)
        identifier = client.post("/api/trackers", json={"name": "Test", "api_key": "private-key"}).json()["id"]
        response = client.get(f"/api/trackers/{identifier}/search")
        assert response.status_code == expected
        assert "private-key" not in response.text


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