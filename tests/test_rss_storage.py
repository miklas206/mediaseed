import asyncio
import json
import sqlite3
from contextlib import contextmanager

import httpx
from fastapi.testclient import TestClient

from mediaseed.app import create_app
from mediaseed.rss_worker import RssWorker
from mediaseed.storage import Storage
from test_app import sign_in


def storage(tmp_path, monkeypatch):
    downloads = tmp_path / "downloads"
    folder = downloads / "media/tv/Dansk TV/Serie med \u00e6\u00f8\u00e5"
    folder.mkdir(parents=True)
    configuration = json.dumps(
        [{"id": "tv", "label": "Serier", "path": "/downloads/media/tv"}]
    )
    monkeypatch.setenv("MEDIASEED_STORAGE_ROOTS", configuration)
    return downloads, folder, Storage(downloads, configuration)


def test_feed_location_is_saved_editable_and_validated_without_exposing_credentials(
    tmp_path, monkeypatch
):
    downloads, folder, _ = storage(tmp_path, monkeypatch)
    with TestClient(
        create_app(tmp_path / "state", "http://testserver", downloads_dir=downloads)
    ) as client:
        sign_in(client)
        source = {
            "name": "Dansk TV",
            "url": "https://nordicbytes.org/rss/synthetic-private",
            "location": "tv",
            "folder": "Dansk TV/" + folder.name,
            "ratio": 2,
        }
        response = client.post("/api/feeds", json=source)
        assert response.status_code == 200
        identifier = response.json()["id"]
        feed = client.get("/api/feeds").json()[0]
        assert (
            feed["location"] == "tv"
            and feed["folder"] == source["folder"]
            and feed["paused"]
        )
        assert "synthetic-private" not in json.dumps(feed)
        assert (
            client.put(
                f"/api/feeds/{identifier}",
                json=source | {"url": "", "folder": "Dansk TV"},
            ).status_code
            == 200
        )
        assert client.get("/api/feeds").json()[0]["folder"] == "Dansk TV"
        for selection in (
            {"location": "unknown"},
            {"folder": "../outside"},
            {"folder": "missing"},
            {"location": "local", "folder": "media/tv"},
        ):
            assert client.post("/api/feeds", json=source | selection).status_code in {
                409,
                422,
            }
        folder.rmdir()
        (folder.parent).rmdir()
        response = client.put(f"/api/feeds/{identifier}/state", json={"enabled": True})
        assert response.status_code == 409 and "Download folder" in response.text


def test_readonly_feed_target_is_rejected(tmp_path, monkeypatch):
    downloads, _, _ = storage(tmp_path, monkeypatch)
    with TestClient(
        create_app(tmp_path / "state", "http://testserver", downloads_dir=downloads)
    ) as client:
        sign_in(client)
        monkeypatch.setattr("mediaseed.storage.os.access", lambda *args: False)
        response = client.post(
            "/api/feeds",
            json={
                "name": "Test",
                "url": "https://nordicbytes.org/rss/synthetic",
                "location": "tv",
                "folder": "",
            },
        )
        assert response.status_code == 409


def test_rss_category_and_new_torrents_use_chosen_media_directory_and_block_missing_storage(
    tmp_path, monkeypatch
):
    _, folder, catalog = storage(tmp_path, monkeypatch)

    @contextmanager
    def database():
        with sqlite3.connect(tmp_path / "rss.sqlite") as connection:
            yield connection

    feed = {
        "id": "a" * 24,
        "enabled": True,
        "url": "https://nordicbytes.org/rss/synthetic",
        "location": "tv",
        "folder": "Dansk TV/" + folder.name,
        "ratio": 2,
    }
    category = "MediaSeed-RSS-" + feed["id"]
    calls = []

    async def qbit(path, method="GET", payload=None, files=None):
        calls.append((path, payload))
        if path.endswith("/categories"):
            return httpx.Response(200, json={})
        if path.endswith("/torrent"):
            return httpx.Response(200, content=b"dee")
        if path.endswith("/add"):
            return httpx.Response(
                200,
                json={
                    "success_count": 1,
                    "failure_count": 0,
                    "added_torrent_ids": ["b" * 40],
                },
            )
        return httpx.Response(200, json={})

    async def vpn():
        return {"downloadsEnabled": True}

    worker = RssWorker(database, lambda kind: [feed], qbit, vpn, storage=catalog)
    asyncio.run(worker.process(feed, {}))
    expected = "/downloads/media/tv/" + feed["folder"]
    assert (
        next(payload for path, payload in calls if path.endswith("createCategory"))[
            "savePath"
        ]
        == expected
    )
    items = {
        category: {
            "url": feed["url"],
            "articles": [
                {
                    "id": "synthetic",
                    "torrentURL": "https://nordicbytes.org/torrents/download/12/synthetic",
                }
            ],
        }
    }
    asyncio.run(worker.process(feed, items))
    assert (
        next(payload for path, payload in calls if path.endswith("/add"))["savepath"]
        == expected
    )
    assert any(path.endswith("/start") for path, payload in calls)
    folder.rmdir()
    calls.clear()
    import pytest

    with pytest.raises(Exception, match="unavailable"):
        asyncio.run(worker.process(feed, items))
    assert not calls  # no fallback to local downloads or client-side folder creation
