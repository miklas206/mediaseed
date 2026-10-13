import json
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient

from mediaseed.app import create_app
from mediaseed.storage import Storage
from test_app import sign_in


def test_media_capacity_folder_browsing_and_both_add_modes(tmp_path, monkeypatch):
    downloads = tmp_path / "downloads"
    folder = downloads / "media/movies/Film med danske tegn æ"
    folder.mkdir(parents=True)
    (folder / "nested").mkdir()
    monkeypatch.setenv(
        "MEDIASEED_STORAGE_ROOTS",
        json.dumps(
            [{"id": "movies", "label": "Movies", "path": "/downloads/media/movies"}]
        ),
    )
    calls = []

    def handler(request):
        if request.url.path.endswith("auth/login"):
            return httpx.Response(200, text="Ok.")
        if request.url.path.endswith("torrents/add"):
            calls.append(request)
            return httpx.Response(200, text="Ok.")
        return httpx.Response(200, json=[])

    with TestClient(
        create_app(
            tmp_path / "state",
            "http://testserver",
            downloads_dir=downloads,
            qbit_password="synthetic",
            transport=httpx.MockTransport(handler),
        )
    ) as client:
        assert client.get("/api/storage/folders").status_code == 401
        sign_in(client)
        locations = client.get("/api/storage").json()["locations"]
        assert [r["id"] for r in locations] == ["local", "movies"]
        assert locations[1]["freeBytes"] > 0 and locations[1]["writable"]
        response = client.get(
            "/api/storage/folders", params={"location": "movies"}
        ).json()
        assert response["folders"][0]["name"] == folder.name
        selection = {"location": "movies", "folder": folder.name}
        response = client.post(
            "/api/torrents",
            json={"url": "magnet:?xt=urn:btih:" + "a" * 40, **selection},
        )
        assert response.status_code == 200
        assert parse_qs(calls[-1].content.decode())["savepath"] == [
            "/downloads/media/movies/" + folder.name
        ]
        response = client.post(
            "/api/torrents/upload",
            params={"filename": "synthetic.torrent", **selection},
            content=b"dee",
            headers={"Content-Type": "application/x-bittorrent"},
        )
        assert response.status_code == 200
        assert ("/downloads/media/movies/" + folder.name).encode() in calls[-1].content
        count = len(calls)
        for location, relative in [
            ("movies", "../outside"),
            ("local", "media/movies"),
            ("unknown", ""),
            ("movies", "missing"),
            ("movies", "/etc"),
        ]:
            response = client.post(
                "/api/torrents",
                json={
                    "url": "magnet:?xt=urn:btih:" + "b" * 40,
                    "location": location,
                    "folder": relative,
                },
            )
            assert response.status_code in {409, 422}
        assert len(calls) == count


def test_unavailable_and_readonly_media_remain_visible(tmp_path, monkeypatch):
    (tmp_path / "media/movies").mkdir(parents=True)
    catalog = Storage(
        tmp_path,
        json.dumps(
            [
                {"id": "movies", "label": "Movies", "path": "/downloads/media/movies"},
                {"id": "tv", "label": "TV", "path": "/downloads/media/tv"},
            ]
        ),
    )
    monkeypatch.setattr("mediaseed.storage.os.access", lambda *args: False)
    rows = catalog.snapshot()["locations"]
    assert rows[1]["available"] and not rows[1]["writable"]
    assert not rows[2]["available"]
    with pytest.raises(Exception, match="read-only"):
        catalog.destination("movies", "")
