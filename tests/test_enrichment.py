import asyncio
import json
import sqlite3
from contextlib import contextmanager

import httpx
import pytest

from mediaseed.covers import Covers
from mediaseed.enrichment import DELAYS, MAX_ATTEMPTS

ACCOUNT = {"id": "synthetic", "name": "Tracker", "api_key": "private-synthetic-key"}
HASH = "a" * 40
POSTER = "https://image.tmdb.org/t/p/w500/synthetic.jpg"


def attributes(**updates):
    return {"info_hash": HASH, "description": "Description", "media_info": "MediaInfo", "meta": {"poster": POSTER}, **updates}


def setup_cache(tmp_path, read, torrents=None):
    async def qbit(*args, **kwargs):
        return httpx.Response(200, json=torrents or [{"hash": HASH, "name": "Synthetic.Release.mkv"}])
    return Covers(tmp_path, lambda kind: [ACCOUNT], read, qbit)


def test_missing_matches_stop_at_five_even_after_restart_or_repeated_enqueue(tmp_path, monkeypatch):
    clock = [100000.0]
    monkeypatch.setattr("mediaseed.enrichment.time.time", lambda: clock[0])
    calls = []

    async def read(*args):
        calls.append(True)
        return {"data": []}

    async def scenario():
        for attempt in range(MAX_ATTEMPTS):
            cache = setup_cache(tmp_path, read)
            cache.enqueue(HASH, "Synthetic.Release.mkv")
            await cache.tick()
            await cache.tick()
            assert len(calls) == attempt + 1
            clock[0] += DELAYS[attempt] + 1
        cache = setup_cache(tmp_path, read)
        cache.enqueue(HASH, "Synthetic.Release.mkv")
        await cache.tick()
        assert len(calls) == 5
        with sqlite3.connect(cache.enrichment.path) as db:
            content = db.execute("SELECT state FROM jobs").fetchone()[0]
            assert json.loads(content)["search"] == 5
            assert "private-synthetic-key" not in content

    asyncio.run(scenario())


@pytest.mark.parametrize("wrapped", [True, False])
def test_full_details_are_recovered_and_sparse_search_does_not_need_more_queries(tmp_path, wrapped):
    calls, saved = [], []

    async def read(account, path, params):
        calls.append(path)
        if path.endswith("filter"):
            return {"data": [{"id": "42", "attributes": attributes(description=None, media_info=None)}]}
        record = {"id": "42", "attributes": attributes(files=[{"name": "Synthetic.mkv", "size": 3}])}
        return {"data": record} if wrapped else record

    cache = setup_cache(tmp_path, read)
    cache.metadata_sink = lambda ident, account, attrs: saved.append(attrs)
    async def image(url, path):
        path.write_bytes(b"\xff\xd8\xffsynthetic")
    cache.download = image
    asyncio.run(cache.tick())
    asyncio.run(cache.tick())
    assert calls == ["/api/torrents/filter", "/api/torrents/42"]
    assert saved[-1]["description"] == "Description"
    assert saved[-1]["files"][0]["name"] == "Synthetic.mkv"
    assert cache.path(HASH).is_file()
    # An upgrade/restart uses the same successful job and cached image.
    restored = setup_cache(tmp_path, read)
    asyncio.run(restored.tick())
    assert len(calls) == 2


def test_image_recovery_has_own_persistent_limit_without_tracker_searches(tmp_path, monkeypatch):
    clock = [100000.0]
    monkeypatch.setattr("mediaseed.enrichment.time.time", lambda: clock[0])
    reads, images = [], []
    async def read(*args):
        reads.append(True)
        return {"data": [{"attributes": attributes()}]}
    async def image(*args):
        images.append(True)
        raise httpx.ConnectError("synthetic")
    for attempt in range(7):
        cache = setup_cache(tmp_path, read)
        cache.download = image
        asyncio.run(cache.tick())
        clock[0] += 86401
    assert len(reads) == 1
    assert len(images) == 5
    assert not cache.path(HASH).exists()


def test_partial_details_and_wrong_hashes_cannot_be_retried_forever(tmp_path, monkeypatch):
    clock = [100000.0]
    monkeypatch.setattr("mediaseed.enrichment.time.time", lambda: clock[0])
    calls, saved = [], []
    async def read(account, path, params):
        calls.append(path)
        if path.endswith("filter"):
            return {"data": [{"id": "42", "attributes": attributes(description=None, meta={})}]}
        return {"data": {"id": "42", "attributes": attributes(info_hash="b" * 40)}}
    cache = setup_cache(tmp_path, read)
    cache.metadata_sink = lambda ident, account, attrs: saved.append(attrs)
    for attempt in range(7):
        asyncio.run(cache.tick())
        clock[0] += 86401
    assert calls.count("/api/torrents/filter") == 1
    assert calls.count("/api/torrents/42") == 5
    assert len(saved) == 1
    assert saved[0]["info_hash"] == HASH


def test_global_budget_does_not_consume_unvisited_jobs_and_later_torrents_progress(tmp_path):
    hashes = [format(i, "040x") for i in range(12)]
    torrents = [{"hash": h, "name": h} for h in hashes]
    calls = []
    async def read(account, path, params):
        calls.append(params["name"])
        return {"data": []}
    cache = setup_cache(tmp_path, read, torrents)
    asyncio.run(cache.tick())
    assert len(calls) == 6
    asyncio.run(cache.tick())
    assert calls == hashes


def test_pagination_resumes_when_tick_budget_ends(tmp_path):
    calls = []
    async def read(account, path, params):
        cursor = params.get("cursor")
        calls.append(cursor)
        if cursor != "page-three":
            return {"data": [], "meta": {"next_cursor": "page-two" if cursor is None else "page-three"}}
        return {"data": [{"attributes": attributes()}]}
    cache = setup_cache(tmp_path, read)
    async def image(url, path):
        path.write_bytes(b"\xff\xd8\xffsynthetic")
    cache.download = image
    asyncio.run(cache.tick())
    assert calls == [None, "page-two"]
    restored = setup_cache(tmp_path, read)
    restored.download = image
    asyncio.run(restored.tick())
    assert calls == [None, "page-two", "page-three"]
    assert restored.path(HASH).is_file()


def test_new_credential_gets_new_retry_scope_without_saving_credential(tmp_path, monkeypatch):
    clock = [100000.0]
    monkeypatch.setattr("mediaseed.enrichment.time.time", lambda: clock[0])
    calls = []
    async def read(*args):
        calls.append(True)
        return {"data": []}
    cache = setup_cache(tmp_path, read)
    for _ in range(5):
        asyncio.run(cache.tick())
        clock[0] += 86401
    replacement = {**ACCOUNT, "api_key": "new-private-key"}
    cache.accounts = lambda kind: [replacement]
    asyncio.run(cache.tick())
    assert len(calls) == 6
    assert b"new-private-key" not in cache.enrichment.path.read_bytes()


def test_fresh_install_and_upgrade_preserve_old_images_metadata_and_settings(tmp_path):
    from mediaseed.intelligence import Intelligence

    @contextmanager
    def database():
        connection = sqlite3.connect(tmp_path / "mediaseed.sqlite")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    with database() as db:
        db.execute("CREATE TABLE original_settings (value TEXT)")
        db.execute("INSERT INTO original_settings VALUES ('preserved')")
    intelligence = Intelligence(database)
    intelligence.remember(HASH, ACCOUNT, attributes(seeders=4, freeleech=100, files=[{"name": "Synthetic.mkv", "size": 3}]))
    # Later filter observations are often sparse; static detail fields survive.
    intelligence.remember(HASH, ACCOUNT, {"info_hash": HASH, "name": "Synthetic"})
    record = intelligence.media(HASH)
    assert record["description"] == "Description"
    assert record["media_info"] == "MediaInfo"
    assert record["files"] == [{"name": "Synthetic.mkv", "size": 3}]
    assert record["seeders"] is None and record["freeleech"] is None
    async def read(*args):
        return {"data": []}
    old = setup_cache(tmp_path, read)
    old.path(HASH).write_bytes(b"\xff\xd8\xffexisting")
    old.path(HASH).with_suffix(".json").write_text('{"legacy":true}')
    restored = setup_cache(tmp_path, read)
    assert restored.path(HASH).read_bytes() == b"\xff\xd8\xffexisting"
    assert json.loads(restored.path(HASH).with_suffix(".json").read_text()) == {"legacy": True}
    with database() as db:
        assert db.execute("SELECT value FROM original_settings").fetchone()[0] == "preserved"
    intelligence.remember(HASH, {**ACCOUNT, "id": "other"}, {"info_hash": HASH})
    assert intelligence.media(HASH)["description"] is None


def test_cancellation_reserves_an_attempt_and_prevents_immediate_restart_requests(tmp_path):
    calls = []
    async def scenario():
        entered = asyncio.Event()
        async def read(*args):
            calls.append(True)
            entered.set()
            await asyncio.Event().wait()
        cache = setup_cache(tmp_path, read)
        task = asyncio.create_task(cache.tick())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        restored = setup_cache(tmp_path, read)
        await restored.tick()
        assert calls == [True]
    asyncio.run(scenario())


def test_existing_approved_metadata_can_repair_cover_when_search_is_unavailable(tmp_path):
    from fastapi import HTTPException

    async def read(*args):
        raise HTTPException(503, "synthetic outage")
    cache = setup_cache(tmp_path, read)
    cache.metadata_source = lambda identifier: {"account": ACCOUNT["id"], "poster": POSTER, "description": "Already saved"}
    downloaded = []
    async def image(url, path):
        downloaded.append(url)
        path.write_bytes(b"\xff\xd8\xffsynthetic")
    cache.download = image
    asyncio.run(cache.tick())
    assert downloaded == [POSTER]
    assert cache.path(HASH).is_file()
    (tmp_path / "other").mkdir()
    cache = setup_cache(tmp_path / "other", read)
    cache.metadata_source = lambda identifier: {"account": "different", "poster": POSTER}
    cache.download = image
    asyncio.run(cache.tick())
    assert not cache.path(HASH).is_file()


def test_image_using_last_budget_slot_cannot_query_another_account(tmp_path):
    from fastapi import HTTPException

    hashes = ["a" * 40, "b" * 40, "c" * 40]
    remote = []
    async def read(account, path, params):
        remote.append("api")
        if params["name"] == hashes[-1]:
            raise HTTPException(503, "synthetic")
        return {"data": [{"attributes": attributes(info_hash=params["name"])}]}
    cache = setup_cache(tmp_path, read, [{"hash": h, "name": h} for h in hashes])
    cache.accounts = lambda kind: [ACCOUNT, {**ACCOUNT, "id": "other", "api_key": "another-key"}]
    cache.metadata_source = lambda identifier: {"account": ACCOUNT["id"], "poster": POSTER}
    async def image(url, path):
        remote.append("image")
        path.write_bytes(b"\xff\xd8\xffsynthetic")
    cache.download = image
    asyncio.run(cache.tick())
    assert remote == ["api", "image", "api", "image", "api", "image"]


def test_missing_cover_is_prioritized_ahead_of_existing_cover_metadata_work(tmp_path):
    from fastapi import HTTPException

    hashes = [format(i, "040x") for i in range(10)]
    async def read(*args):
        raise HTTPException(503, "synthetic")
    cache = setup_cache(tmp_path, read, [{"hash": h, "name": h} for h in hashes])
    for identifier in hashes[:-1]:
        cache.path(identifier).write_bytes(b"\xff\xd8\xffexisting")
    cache.metadata_source = lambda identifier: {"account": ACCOUNT["id"], "poster": POSTER} if identifier == hashes[-1] else None
    async def image(url, path):
        path.write_bytes(b"\xff\xd8\xffrecovered")
    cache.download = image
    asyncio.run(cache.tick())
    assert cache.path(hashes[-1]).is_file()
