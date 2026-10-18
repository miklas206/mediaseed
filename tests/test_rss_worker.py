from mediaseed.rss_worker import date_matches
from mediaseed.rss_worker import RssWorker
import asyncio
import sqlite3
from contextlib import contextmanager
import httpx
import pytest
from mediaseed.cleanup import CleanupManager, source_hashes


@pytest.mark.parametrize("pending", [False, True])
def test_rss_cleanup_is_armed_before_start_and_preserved_on_restart(tmp_path, pending):
    @contextmanager
    def database():
        with sqlite3.connect(tmp_path / "cleanup-feed.sqlite") as db:
            yield db
    content = b"d4:infod6:lengthi1e4:name4:test12:piece lengthi16384e6:pieces20:aaaaaaaaaaaaaaaaaaaaee"
    identifier = source_hashes(content=content)[0]
    feed = {"id": "a"*24, "enabled": True, "url": "https://nordicbytes.org/rss/synthetic", "ratio": 2,
            "cleanup": {"mode": "ratio", "ratio": 3, "delete_files": True}}
    items = {"MediaSeed-RSS-"+feed['id']: {"url": feed['url'], "articles": [
        {"id": "one", "title": "Synthetic release", "torrentURL": "https://nordicbytes.org/torrents/download/1"}]}}
    calls, torrents = [], []
    async def vpn():
        return {"downloadsEnabled": True}
    async def qbit(path, method="GET", payload=None, files=None):
        calls.append(path)
        if path.endswith('/items'):
            return httpx.Response(200, json=items)
        if path.endswith('/torrent'):
            return httpx.Response(200, content=content)
        if path.endswith('/info'):
            return httpx.Response(200, json=torrents)
        if path.endswith('/add'):
            assert payload['stopped'] == 'true' and payload['tags'].startswith('MediaSeed-Cleanup-')
            if not pending:
                torrents.append({'hash': identifier, 'tags': payload['tags'], 'progress': 0, 'state': 'stoppedDL'})
            return httpx.Response(200, json={'success_count': 1, 'failure_count': 0, 'added_torrent_ids': [identifier]})
        return httpx.Response(204)
    async def check():
        worker = RssWorker(database, lambda kind: [feed], qbit, vpn)
        worker.cleanup = CleanupManager(database, qbit, vpn)
        await worker.tick()
        rule = CleanupManager(database, qbit, vpn).public(identifier)
        assert rule['mode'] == 'ratio' and rule['ratio'] == 3 and rule['delete_files']
        assert rule['status'] == ('waiting' if pending else 'active')
        if pending:
            assert '/api/v2/torrents/start' not in calls
        else:
            assert calls.index('/api/v2/torrents/setShareLimits') < calls.index('/api/v2/torrents/start')
        await worker.tick()
        assert calls.count('/api/v2/torrents/add') == 1
        assert '/api/v2/torrents/delete' not in calls
    asyncio.run(check())


def test_feed_date_gate_rejects_missing_old_and_future_dates():
    threshold = "2026-01-01T00:00:00+00:00"
    assert date_matches({"date": "2026-01-02T00:00:00Z"}, threshold)
    assert date_matches({"date": "Fri, 02 Jan 2026 00:00:00 GMT"}, threshold)
    assert not date_matches({"date": "2025-12-31T00:00:00Z"}, threshold)
    assert not date_matches({"date": "2099-01-01T00:00:00Z"}, threshold)
    assert not date_matches({}, threshold)
    assert not date_matches({"date": "invalid"}, threshold)


@pytest.mark.parametrize("prefix", ["torrent", "torrents"])
def test_rss_history_prevents_duplicate_import_and_vpn_loss_blocks_start(tmp_path, prefix):
    @contextmanager
    def database():
        with sqlite3.connect(tmp_path / "history.sqlite") as connection:
            connection.row_factory = sqlite3.Row
            yield connection
    feed = {"id": "a" * 24, "enabled": True, "url": "https://nordicbytes.org/rss/private", "folder": "releases", "ratio": 2, "start_at": "2026-01-01T00:00:00Z"}
    item_path = "MediaSeed-RSS-" + feed["id"]
    items = {item_path: {"url": feed["url"], "articles": [{"id": "first", "date": "2026-01-02T00:00:00Z", "torrentURL": f"https://nordicbytes.org/{prefix}/download/12/private"}]}}
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
        assert sum(path.endswith("/start") for path, payload in calls) == 1
    asyncio.run(check())


@pytest.mark.parametrize("old_kind", ["stale", "configured", "external"])
def test_recreated_feed_cleans_only_owned_unconfigured_duplicate(tmp_path, old_kind):
    @contextmanager
    def database():
        with sqlite3.connect(tmp_path / "rss.sqlite") as c:
            yield c
    feed = {"id": "a" * 24, "url": "https://nordicbytes.org/rss/synthetic", "folder": "", "enabled": True}
    old = "MediaSeed-RSS-" + "b" * 24 if old_kind != "external" else "My manual feed"
    current = [feed] + ([{"id": "b" * 24}] if old_kind == "configured" else [])
    calls = []
    async def qbit(path, method="GET", payload=None, files=None):
        calls.append((path, payload))
        return httpx.Response(200, json={})
    async def status():
        return {"downloadsEnabled": True}
    worker = RssWorker(database, lambda kind: current, qbit, status)
    asyncio.run(worker.process(feed, {old: {"url": feed["url"]}}))
    removed = [payload for path, payload in calls if path.endswith("removeItem")]
    assert removed == ([{"path": old}] if old_kind == "stale" else [])
    assert not any(path.endswith("torrents/add") for path, _ in calls)


def test_received_batch_progresses_with_six_request_bound_and_no_repeated_cross_feed_duplicates(tmp_path):
    @contextmanager
    def database():
        with sqlite3.connect(tmp_path / "batch.sqlite") as connection:
            yield connection
    feed = {"id": "a"*24, "enabled": True, "url": "https://nordicbytes.org/rss/synthetic", "folder": "", "ratio": 3}
    articles = [{"id": str(i), "title": "Synthetic release "+str(i), "torrentURL": "https://nordicbytes.org/torrents/download/"+str(i)} for i in range(12)]
    feeds = [feed]
    items = {"MediaSeed-RSS-"+feed["id"]: {"url": feed["url"], "articles": articles}}
    reads, additions, starts = [], [], []
    async def vpn_status():
        return {"downloadsEnabled": True}
    async def qbit(path, method="GET", payload=None, files=None):
        if path.endswith("/items"):
            return httpx.Response(200, json=items)
        if path.endswith("/torrent"):
            i = int(payload["url"].rsplit("/",1)[1])
            reads.append(i)
            return httpx.Response(200, content=("synthetic-metadata-"+str(i)).encode())
        if path.endswith("/add"):
            additions.append(files["torrents"][1])
            assert payload["stopped"] == "true"
            return httpx.Response(200,json={"success_count":1,"failure_count":0,"added_torrent_ids":[format(len(additions),"040x")]})
        if path.endswith("/start"):
            starts.append(payload["hashes"])
        return httpx.Response(204)
    async def check():
        worker = RssWorker(database, lambda kind: feeds, qbit, vpn_status)
        await worker.tick()
        assert len(reads) == len(additions) == len(starts) == 6
        await worker.tick()
        assert len(reads) == len(additions) == len(starts) == 12
        other = {**feed, "id": "b"*24}
        feeds.append(other)
        items["MediaSeed-RSS-"+other["id"]] = {"url": other["url"], "articles": articles}
        await worker.tick()
        assert len(reads) == 18 and len(additions) == 12
        await worker.tick()
        assert len(reads) == 24 and len(additions) == 12
        restarted = RssWorker(database, lambda kind: feeds, qbit, vpn_status)
        await restarted.tick()
        assert len(reads) == 24 and len(additions) == 12
    asyncio.run(check())


@pytest.mark.parametrize("change", ["none", "disabled", "edited", "removed"])
def test_pending_rss_start_survives_restart_and_respects_feed_changes(tmp_path, change):
    import json
    @contextmanager
    def database():
        with sqlite3.connect(tmp_path / "pending.sqlite") as db:
            yield db
    feed = {"id": "a" * 24, "enabled": True, "url": "https://nordicbytes.org/rss/synthetic"}
    feeds = [feed]
    calls = []
    async def vpn():
        return {"downloadsEnabled": True}
    async def qbit(path, method="GET", payload=None, **kwargs):
        calls.append(path)
        return httpx.Response(200, json={})
    async def check():
        worker = RssWorker(database, lambda kind: feeds, qbit, vpn)
        with database() as db:
            db.execute("INSERT INTO rss_pending_starts VALUES (?,?,?)", ("b" * 40, feed["id"], worker.revision(feed)))
        if change == "disabled":
            feeds[0] = {**feed, "enabled": False}
        elif change == "edited":
            feeds[0] = {**feed, "folder": "other"}
        elif change == "removed":
            feeds.clear()
        restarted = RssWorker(database, lambda kind: feeds, qbit, vpn)
        ready = False
        class Cleanup:
            def public(self, identifier):
                return {"status": "active" if ready else "waiting"}
        restarted.cleanup = Cleanup()
        await restarted.start_pending()
        assert not calls
        ready = True
        await restarted.start_pending()
        assert calls == (["/api/v2/torrents/topPrio", "/api/v2/torrents/start"] if change == "none" else [])
        calls.clear()
        await restarted.start_pending()
        assert not calls  # A later manual pause is never undone.
    asyncio.run(check())
