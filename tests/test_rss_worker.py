from mediaseed.rss_worker import date_matches
from mediaseed.rss_worker import RssWorker
import asyncio
import sqlite3
from contextlib import contextmanager
import httpx


def test_feed_date_gate_rejects_missing_old_and_future_dates():
    threshold = "2026-01-01T00:00:00+00:00"
    assert date_matches({"date": "2026-01-02T00:00:00Z"}, threshold)
    assert date_matches({"date": "Fri, 02 Jan 2026 00:00:00 GMT"}, threshold)
    assert not date_matches({"date": "2025-12-31T00:00:00Z"}, threshold)
    assert not date_matches({"date": "2099-01-01T00:00:00Z"}, threshold)
    assert not date_matches({}, threshold)
    assert not date_matches({"date": "invalid"}, threshold)


def test_rss_history_prevents_duplicate_import_and_vpn_loss_blocks_start(tmp_path):
    @contextmanager
    def database():
        with sqlite3.connect(tmp_path / "history.sqlite") as connection:
            connection.row_factory = sqlite3.Row
            yield connection
    feed = {"id": "a" * 24, "enabled": True, "url": "https://nordicbytes.org/rss/private", "folder": "releases", "ratio": 2, "start_at": "2026-01-01T00:00:00Z"}
    item_path = "MediaSeed-RSS-" + feed["id"]
    items = {item_path: {"url": feed["url"], "articles": [{"id": "first", "date": "2026-01-02T00:00:00Z", "torrentURL": "https://nordicbytes.org/torrents/download/12/private"}]}}
    calls = []
    ready = True
    async def vpn_status():
        return {"downloadsEnabled": ready}
    async def qbit(path, method="GET", payload=None, files=None):
        nonlocal ready
        calls.append((path, payload))
        if path.endswith("/items"):
            return httpx.Response(200, json=items)
        if path.endswith("/torrent"):
            return httpx.Response(200, content=b"dee")
        if path.endswith("/add"):
            ready = False
            assert payload["stopped"] == "true" and payload["savepath"] == "/downloads/releases"
            return httpx.Response(200, json={"success_count": 1, "failure_count": 0, "added_torrent_ids": ["b" * 40]})
        return httpx.Response(204)
    async def check():
        nonlocal ready
        worker = RssWorker(database, lambda kind: [feed], qbit, vpn_status)
        await worker.tick()
        assert not any(path.endswith("/start") for path, payload in calls)
        ready = True
        restarted = RssWorker(database, lambda kind: [feed], qbit, vpn_status)
        await restarted.tick()
        assert sum(path.endswith("/add") for path, payload in calls) == 1
    asyncio.run(check())