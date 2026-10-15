import json
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient
from test_app import sign_in

from mediaseed.app import create_app
from mediaseed.storage import Storage

HASH = "a" * 40


@pytest.mark.parametrize(
    "state", ["downloading", "stalledUP", "stoppedDL", "missingFiles", "error"]
)
def test_check_stops_exact_torrent_then_rechecks_without_download_or_deletion(
    tmp_path, state
):
    writes = []
    current = state

    def handler(request):
        nonlocal current
        path = request.url.path
        if path.endswith("/login"):
            return httpx.Response(200, text="Ok.")
        if path.endswith("/info"):
            assert request.url.params["hashes"] == HASH
            return httpx.Response(200, json=[{"hash": HASH, "state": current}])
        fields = parse_qs(request.content.decode())
        writes.append((path, fields))
        if path.endswith("/stop") and current not in {"error", "missingFiles"}:
            current = "stoppedDL"
        return httpx.Response(200)

    with TestClient(
        create_app(
            tmp_path,
            "http://testserver",
            qbit_password="synthetic",
            transport=httpx.MockTransport(handler),
        )
    ) as client:
        assert (
            client.post(
                "/api/torrents/recheck", json={"torrent_hash": HASH}
            ).status_code
            == 401
        )
        sign_in(client)
        csrf = client.headers["X-MediaSeed-CSRF"]
        client.headers["X-MediaSeed-CSRF"] = "invalid"
        assert (
            client.post(
                "/api/torrents/recheck", json={"torrent_hash": HASH}
            ).status_code
            == 403
        )
        client.headers["X-MediaSeed-CSRF"] = csrf
        response = client.post("/api/torrents/recheck", json={"torrent_hash": HASH})
        assert response.status_code == 200, response.text
        assert response.json() == {"accepted": True, "stopped": True}
        assert writes == [
            ("/api/v2/torrents/stop", {"hashes": [HASH]}),
            ("/api/v2/torrents/recheck", {"hashes": [HASH]}),
        ]


@pytest.mark.parametrize(
    "state,code",
    [("checkingDL", 409), ("moving", 409), ("metaDL", 409), ("absent", 404)],
)
def test_busy_or_missing_torrent_is_not_mutated(tmp_path, state, code):
    writes = []

    def handler(request):
        if request.url.path.endswith("/login"):
            return httpx.Response(200, text="Ok.")
        if request.url.path.endswith("/info"):
            return httpx.Response(
                200, json=[] if state == "absent" else [{"hash": HASH, "state": state}]
            )
        writes.append(request.url.path)
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
        assert (
            client.post(
                "/api/torrents/recheck", json={"torrent_hash": HASH}
            ).status_code
            == code
        )
        assert (
            client.post(
                "/api/torrents/recheck",
                json={"torrent_hash": HASH, "delete_files": True},
            ).status_code
            == 422
        )
        assert not writes


def test_approved_folder_is_exposed_only_in_authenticated_details(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(
        "MEDIASEED_STORAGE_ROOTS",
        json.dumps(
            [{"id": "movies", "label": "Movies", "path": "/downloads/media/movies"}]
        ),
    )

    def handler(request):
        path = request.url.path
        if path.endswith("/login"):
            return httpx.Response(200, text="Ok.")
        if path.endswith("/properties"):
            return httpx.Response(
                200,
                json={
                    "save_path": "/downloads/media/movies/Existing Film",
                    "comment": "private-passkey",
                },
            )
        if path.endswith("/torrentPeers"):
            return httpx.Response(200, json={"peers": {}})
        return httpx.Response(200, json=[])

    with TestClient(
        create_app(
            tmp_path,
            "http://testserver",
            qbit_password="synthetic",
            transport=httpx.MockTransport(handler),
        )
    ) as client:
        assert client.get(f"/api/torrents/{HASH}/details").status_code == 401
        sign_in(client)
        response = client.get(f"/api/torrents/{HASH}/details")
        assert response.status_code == 200
        assert response.json()["downloadFolder"] == {
            "path": "/downloads/media/movies/Existing Film",
            "label": "Movies",
            "location": "movies",
            "folder": "Existing Film",
        }
        assert "private-passkey" not in response.text


@pytest.mark.parametrize(
    "path",
    [
        "/secret",
        "/downloads-other",
        "/downloads/media/unknown",
        "/downloads/../secret",
        "/downloads//bad",
        "/downloads/x\\y",
    ],
)
def test_unapproved_paths_remain_private(tmp_path, path):
    assert Storage(tmp_path).describe(path) is None


def test_existing_configuration_survives_new_storage_description(tmp_path):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    media = downloads / "media/movies"
    media.mkdir(parents=True)
    existing = media / "existing.iso"
    existing.write_bytes(b"preserve")
    for _ in range(2):
        storage = Storage(
            downloads,
            json.dumps(
                [{"id": "movies", "label": "Movies", "path": "/downloads/media/movies"}]
            ),
        )
        assert storage.describe("/downloads/media/movies")["location"] == "movies"
        assert existing.read_bytes() == b"preserve"


def test_older_mediavpn_explains_upgrade_before_pausing_anything(tmp_path, monkeypatch):
    import mediaseed.app as module

    token = tmp_path / "token"
    token.write_text("synthetic-token")
    monkeypatch.setenv("MEDIASEED_VPN_URL", "http://mediavpn:18802")
    monkeypatch.setenv("MEDIASEED_VPN_TOKEN_FILE", str(token))
    original = httpx.AsyncClient
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/status"):
            return httpx.Response(
                200,
                json={
                    "enabled": True,
                    "profileId": "a" * 24,
                    "ready": True,
                    "downloadsEnabled": True,
                },
            )
        return httpx.Response(200, json={})

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
        response = client.post("/api/torrents/recheck", json={"torrent_hash": HASH})
        assert response.status_code == 409
        assert "MediaVPN 0.2.18" in response.json()["detail"]
        assert not any(path.endswith("/rpc") for path in calls)


@pytest.mark.parametrize("failure", ["stop", "recheck"])
def test_failed_client_action_never_starts_or_deletes_torrent(tmp_path, failure):
    calls = []

    def handler(request):
        path = request.url.path
        if path.endswith("/login"):
            return httpx.Response(200, text="Ok.")
        if path.endswith("/info"):
            return httpx.Response(200, json=[{"hash": HASH, "state": "stoppedDL"}])
        calls.append(path)
        return httpx.Response(500 if path.endswith("/" + failure) else 200)

    with TestClient(
        create_app(
            tmp_path,
            "http://testserver",
            qbit_password="synthetic",
            transport=httpx.MockTransport(handler),
        )
    ) as client:
        sign_in(client)
        assert (
            client.post(
                "/api/torrents/recheck", json={"torrent_hash": HASH}
            ).status_code
            == 503
        )
        assert calls == (
            ["/api/v2/torrents/stop"]
            if failure == "stop"
            else ["/api/v2/torrents/stop", "/api/v2/torrents/recheck"]
        )
