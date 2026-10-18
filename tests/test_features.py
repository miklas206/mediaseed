from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from mediaseed.app import create_app
from test_app import sign_in


@pytest.mark.parametrize("add_format", ["no-content", "legacy-text", "current-json"])
def test_tracker_search_and_stopped_import_keep_secrets_private(tmp_path, monkeypatch, add_format):
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
        assert request.headers["Referer"] == "http://vpn:8080"
        if request.url.path.endswith("login"):
            return httpx.Response(204)
        calls.append(request)
        assert b'name="stopped"\r\n\r\ntrue' in request.content
        assert b'name="paused"\r\n\r\ntrue' in request.content
        assert b'name="torrents"; filename="upload.torrent"' in request.content
        if add_format == "current-json":
            return httpx.Response(200, json={"added_torrent_ids": ["a" * 40], "success_count": 1, "failure_count": 0, "pending_count": 0})
        return httpx.Response(200, text="Ok.") if add_format == "legacy-text" else httpx.Response(204)

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
        calls.clear()
        overview = client.get(f"/api/torrents/{identifier}/details?section=overview")
        assert overview.status_code == 200 and 'files' not in overview.json()
        assert [r.url.path for r in calls if not r.url.path.endswith('/login')] == ['/api/v2/torrents/properties']
        calls.clear()
        assert client.get(f"/api/torrents/{identifier}/details?section=files").json()['files'][0]['name'] == 'file.iso'
        assert [r.url.path for r in calls if not r.url.path.endswith('/login')] == ['/api/v2/torrents/files']
        calls.clear()
        connections = client.get(f"/api/torrents/{identifier}/details?section=connections")
        assert connections.status_code == 200 and 'passkey' not in connections.text
        assert {r.url.path for r in calls if not r.url.path.endswith('/login')} == {'/api/v2/torrents/trackers','/api/v2/sync/torrentPeers'}

        assert client.post("/api/torrents/stop", json={"torrent_hash": identifier}).status_code == 409
        assert not any(request.url.path.endswith("/stop") for request in calls)
        assert client.post("/api/torrents/remove", json={"torrent_hash": identifier, "confirm_early": True}).status_code == 200
        assert parse_qs(calls[-1].content.decode())["deleteFiles"] == ["false"]
        assert client.post("/api/torrents/remove", json={"torrent_hash": identifier, "delete_files": True}).status_code == 409
        assert client.post("/api/torrents/remove", json={"torrent_hash": identifier, "delete_files": True, "confirm_early": True}).status_code == 200
        assert parse_qs(calls[-1].content.decode())["deleteFiles"] == ["true"]
        assert client.post("/api/torrents/stop", json={"torrent_hash": identifier, "delete_files": True, "confirm_early": True}).status_code == 200
        assert parse_qs(calls[-1].content.decode())["deleteFiles"] == ["false"]
        assert client.post(f"/api/torrents/{identifier}/policy", json={"ratio": 0.5}).status_code == 422
        assert client.post(f"/api/torrents/{identifier}/policy", json={"ratio": 2}).status_code == 200
        assert parse_qs(calls[-1].content.decode())["seedingTimeLimit"] == ["-1"]


def test_saved_sources_mask_secrets_and_preserve_credentials(tmp_path):
    app = create_app(tmp_path, "http://testserver")
    with TestClient(app) as client:
        sign_in(client)
        source = {"name": "Legal releases", "url": "https://tracker.test/rss?passkey=private-secret", "folder": "releases", "ratio": 2, "start_at": "2026-10-11T12:00:00Z"}
        identifier = client.post("/api/feeds", json=source).json()["id"]
        assert "private-secret" not in client.get("/api/feeds").text
        assert client.get("/api/feeds").json()[0]["paused"] is True
        source["url"] = ""
        source["start_at"] = "2026-10-15T10:15:00+02:00"
        assert client.put(f"/api/feeds/{identifier}", json=source).status_code == 200
        assert client.get("/api/feeds").json()[0]["start_at"] == source["start_at"]
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


def test_manual_magnet_and_file_additions_are_stopped(tmp_path):
    calls = []
    def handler(request):
        if request.url.path.endswith("/login"):
            return httpx.Response(204)
        calls.append(request)
        return httpx.Response(200, json={"success_count": 1, "failure_count": 0, "pending_count": 0})

    with TestClient(create_app(tmp_path, "http://testserver", qbit_password="synthetic", transport=httpx.MockTransport(handler))) as client:
        path = "/api/torrents/upload?filename=example.torrent&ratio=2"
        content = b'd4:infod4:name4:testee'
        headers = {"Content-Type": "application/x-bittorrent"}
        assert client.post(path, content=content, headers=headers).status_code == 401
        sign_in(client)
        magnet = "magnet:?dn=Example&xt=urn:btih:" + "a" * 40
        response = client.post("/api/torrents", json={"url": magnet, "ratio": 2})
        assert response.json() == {"added": True, "pending": False, "stopped": True, "downloadsEnabled": False}
        payload = parse_qs(calls[-1].content.decode())
        assert payload["urls"] == [magnet]
        assert payload["stopped"] == ["true"] and payload["paused"] == ["true"]
        assert payload["ratioLimit"] == ["2.0"] and payload["seedingTimeLimit"] == ["2940"]
        assert client.post(path, content=content, headers=headers).json()["stopped"] is True
        assert b'name="torrents"; filename="upload.torrent"' in calls[-1].content
        assert b'name="stopped"\r\n\r\ntrue' in calls[-1].content
        count = len(calls)
        for url in ("https://example.test/file.torrent", "magnet:?xt=urn:btih:bad", magnet + "\n"):
            assert client.post("/api/torrents", json={"url": url}).status_code == 422
        response = client.post("/api/torrents", json={"url": magnet + "&tr=udp%3A%2F%2Ftracker.example%3A1337&ws=https%3A%2F%2Fwebtorrent.io%2Ftorrents%2F&xs=http%3A%2F%2Flocalhost%2Fsecret"})
        assert response.status_code == 200 and response.json()["webSourcesIgnored"] is True
        submitted = parse_qs(calls[-1].content.decode())["urls"][0]
        assert "localhost" not in submitted and "webtorrent.io" not in submitted
        assert parse_qs(urlsplit(submitted).query)["tr"] == ["udp://tracker.example:1337"]
        count = len(calls)
        assert client.post("/api/torrents", json={"url": magnet, "ratio": 0.5}).status_code == 422
        assert client.post(path, content=b"not a torrent", headers=headers).status_code == 422
        assert client.post(path, content=b"d" + b"x" * (4 * 1024 * 1024) + b"e", headers=headers).status_code == 413
        assert client.post(path, content=content, headers={"Content-Type": "text/html"}).status_code == 415
        assert client.post("/api/torrents/upload?filename=file.exe", content=content, headers=headers).status_code == 422
        assert len(calls) == count
        client.headers["X-MediaSeed-CSRF"] = "invalid"
        assert client.post(path, content=content, headers=headers).status_code == 403
        assert client.post("/api/torrents", json={"url": magnet}).status_code == 403


def test_manual_add_reports_pending_metadata_and_client_rejection(tmp_path):
    def handler(request):
        if request.url.path.endswith("/login"):
            return httpx.Response(204)
        if "multipart/form-data" in request.headers.get("Content-Type", ""):
            return httpx.Response(415, text="invalid torrent with private details")
        return httpx.Response(200, json={"success_count": 0, "failure_count": 0, "pending_count": 1})

    with TestClient(create_app(tmp_path, "http://testserver", qbit_password="synthetic", transport=httpx.MockTransport(handler))) as client:
        sign_in(client)
        response = client.post("/api/torrents", json={"url": "magnet:?xt=urn:btmh:1220" + "a" * 64})
        assert response.json() == {"added": False, "pending": True, "stopped": True, "downloadsEnabled": False}
        response = client.post("/api/torrents/upload?filename=test.torrent", content=b"dee", headers={"Content-Type": "application/x-bittorrent"})
        assert response.status_code == 422
        assert "private details" not in response.text


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


def test_feed_activation_requires_vpn_and_pause_is_always_available(tmp_path):
    with TestClient(create_app(tmp_path, "http://testserver")) as client:
        sign_in(client)
        identifier = client.post("/api/feeds", json={"name": "Test", "url": "https://nordicbytes.org/rss/private"}).json()["id"]
        assert client.get("/api/feeds").json()[0]["paused"] is True
        assert client.put(f"/api/feeds/{identifier}/state", json={"enabled": True}).status_code == 409
        assert client.put(f"/api/feeds/{identifier}/state", json={"enabled": False}).status_code == 200
        client.headers["X-MediaSeed-CSRF"] = "wrong"
        assert client.put(f"/api/feeds/{identifier}/state", json={"enabled": False}).status_code == 403


def test_tracker_statistics_are_bounded_private_and_cached(tmp_path, monkeypatch):
    from mediaseed import features
    original = httpx.AsyncClient
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"username": "tester", "passkey": "private", "autogroup": {"title": "Explorer", "icon": "private-icon"}, "uploadgroup": {"title": "Uploader", "secret": "private"}, "stats": {"uploaded": 123, "ratio": 2, "api_key": "private", "warnings": {"hnr": 2, "manual": 1, "unknown": 100}}})
    monkeypatch.setattr(features.httpx, "AsyncClient", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handler)))
    with TestClient(create_app(tmp_path, "http://testserver")) as client:
        sign_in(client)
        identifier = client.post("/api/trackers", json={"name": "Test", "api_key": "private-key"}).json()["id"]
        result = client.get("/api/trackers")
        assert result.status_code == 200
        assert result.json()[0]["stats"]["uploaded"] == 123
        assert result.json()[0]["stats"]["warnings"] == 3
        assert result.json()[0]["ranks"] == {"automatic": "Explorer", "upload": "Uploader"}
        assert result.json()[0]["rankProgress"]["target"]["title"] == "Contributor"
        overview = client.app.state.tracker_overview()[0]
        assert overview["ranks"] == result.json()[0]["ranks"]
        assert overview["rankProgress"] == result.json()[0]["rankProgress"]
        assert "private" not in result.text
        assert client.get("/api/trackers").status_code == 200 and len(requests) == 1
        client.put(f"/api/trackers/{identifier}", json={"name": "Test", "api_key": "new-key"})
        client.get("/api/trackers")
        assert len(requests) == 2


def test_missing_tracker_warnings_are_unknown_not_zero(tmp_path, monkeypatch):
    from mediaseed import features
    original = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"autogroup": {"title": 45}, "stats": {"ratio": 0, "downloaded": 0}}))
    monkeypatch.setattr(features.httpx, "AsyncClient", lambda **kwargs: original(**kwargs, transport=transport))
    with TestClient(create_app(tmp_path, "http://testserver")) as client:
        sign_in(client)
        client.post("/api/trackers", json={"name": "Test", "api_key": "synthetic-key"})
        account = client.get("/api/trackers").json()[0]
        assert account["ranks"] == {}
        assert account["rankProgress"]["target"] is None
        stats = account["stats"]
        assert stats["warnings"] is None
        assert stats["uploaded"] is None
        assert stats["ratio"] == 0


def test_mediavpn_proxy_requires_fresh_readiness_for_start(tmp_path, monkeypatch):
    import base64
    import json
    import mediaseed.app as module
    token = tmp_path / "machine-token"
    token.write_text("synthetic-machine-token")
    monkeypatch.setenv("MEDIASEED_VPN_URL", "http://mediavpn:18802")
    monkeypatch.setenv("MEDIASEED_VPN_TOKEN_FILE", str(token))
    original_client = httpx.AsyncClient
    ready = True
    mutations = []

    def handler(request):
        assert request.headers["X-MediaSeed-Key"] == "synthetic-machine-token"
        if request.url.path.endswith("/status"):
            return httpx.Response(200, json={"enabled": True, "profileId": "a" * 24, "ready": ready, "downloadsEnabled": ready})
        body = json.loads(request.content)
        if request.url.path.endswith("/settings"):
            return httpx.Response(200, json={})
        mutations.append(body)
        return httpx.Response(200, json={"status": 204, "body": base64.b64encode(b"").decode()})

    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: original_client(**kwargs, transport=httpx.MockTransport(handler)))
    with TestClient(create_app(tmp_path / "state", "http://testserver")) as client:
        sign_in(client)
        assert client.put("/api/vpn", json={"provider": "mediavpn", "enabled": True, "profileId": "a" * 24}).status_code == 200
        assert client.post("/api/torrents/start", json={"torrent_hash": "a" * 40}).status_code == 200
        assert mutations[-1]["path"] == "torrents/start"
        ready = False
        assert client.post("/api/torrents/start", json={"torrent_hash": "a" * 40}).status_code == 409
        assert [row["path"] for row in mutations] == ["torrents/setShareLimits", "torrents/start"]


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
