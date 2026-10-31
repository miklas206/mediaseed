import asyncio
from contextlib import suppress
import json

import httpx
import pytest

from mediaseed.background import run_background
from mediaseed.covers import Covers
from mediaseed.download_history import DownloadHistory

HASH = 'a' * 40
ACCOUNT = {'id': 'test', 'name': 'Tracker', 'api_key': 'synthetic'}
TRACKER = 'https://t.nordicbytes.org/announce'


@pytest.mark.parametrize('unknown,image,tracker,expected', [
    (True, True, TRACKER, True),
    (False, False, TRACKER, True),
    (False, True, TRACKER, False),
    (True, False, '', False),
    (True, False, 'https://anothertracker.org/announce', False),
    (True, False, 'https://t.nordicbytes.org.evil.org/announce', False),
])
def test_only_unknown_or_missing_cover_on_matching_tracker(tmp_path, unknown, image, tracker, expected):
    torrent = {'hash': HASH, 'name': 'Test', 'tracker': tracker}
    async def qbit(*args, **kwargs): return httpx.Response(200, json=[torrent])
    async def read(*args, **kwargs): raise AssertionError('No real API calls in gate test')
    cache = Covers(tmp_path, lambda kind: [ACCOUNT], read, qbit)
    if image: cache.path(HASH).write_bytes(b'\xff\xd8\xffexisting')
    cache.recovery_needed = lambda torrents: {HASH: unknown}
    selected = []
    async def run(torrents, accounts): selected.extend(torrents)
    cache.enrichment.run = run
    asyncio.run(cache.tick())
    assert bool(selected) is expected


def test_manual_refresh_bypasses_automatic_gate_and_survives_restart(tmp_path):
    torrent = {'hash': HASH, 'name': 'Test', 'tracker': TRACKER}
    async def qbit(*args, **kwargs): return httpx.Response(200, json=[torrent])
    def make():
        cache = Covers(tmp_path, lambda kind: [ACCOUNT], None, qbit)
        cache.recovery_needed = lambda torrents: {HASH: False}
        return cache
    cache = make();cache.path(HASH).write_bytes(b'\xff\xd8\xffexisting')
    assert asyncio.run(cache.refresh(torrent)) == 'queued'
    cache = make()  # No transient pending queue; durable manual intent remains.
    selected = []
    async def run(torrents, accounts): selected.extend(torrents)
    cache.enrichment.run = run
    asyncio.run(cache.tick());assert len(selected) == 1
    state = cache.enrichment.load(HASH, ACCOUNT)
    state.update(force_image=False, detail_read=True)
    cache.enrichment.save(HASH, ACCOUNT, state)
    selected.clear();asyncio.run(cache.tick());assert selected == []


@pytest.mark.parametrize('history,own,unknown', [
    (None, False, True),
    (None, True, False),
    ({'downloaded': 0, 'observations': []}, False, True),
    ({'downloaded': 100, 'unknownBytes': 100, 'observations': []}, False, True),
    ({'downloaded': 100, 'unknownBytes': 50, 'observations': [{'bytes': 50, 'freeleech': 100}]}, False, False),
    ({'downloaded': 100, 'unknownBytes': 0, 'observations': [{'bytes': 100, 'globalFreeleech': False, 'freeleech': 0, 'doubleUpload': False, 'green': False}]}, False, False),
    ({'downloaded': 100, 'unknownBytes': 0, 'observations': [{'bytes': 100, 'globalFreeleech': None, 'freeleech': 0, 'doubleUpload': False, 'green': None}]}, False, True),
])
def test_history_filter_matches_visible_unknown_badge(history, own, unknown):
    assert DownloadHistory.history_unknown(history, own) is unknown


def test_only_metadata_worker_uses_sixty_seconds(monkeypatch):
    async def scenario():
        waits = {};ready = asyncio.Event()
        async def sleep(delay):
            waits[asyncio.current_task().get_name()] = delay
            if len(waits) == 2: ready.set()
            await asyncio.Event().wait()
        monkeypatch.setattr('mediaseed.background.asyncio.sleep', sleep)
        async def job(): pass
        task = asyncio.create_task(run_background([('covers', job), ('rss', job)], intervals={'covers': 60}))
        await asyncio.wait_for(ready.wait(), 1)
        assert waits == {'mediaseed-covers': 60, 'mediaseed-rss': 15}
        task.cancel()
        with suppress(asyncio.CancelledError): await task
    asyncio.run(scenario())


def test_saved_history_matches_current_added_identity_without_rewriting_it(tmp_path):
    from test_upload_ownership import database_at
    database = database_at(tmp_path)
    history = DownloadHistory(database)
    payload = json.dumps({'downloaded': 100, 'unknownBytes': 0, 'observations': [{'bytes': 100, 'globalFreeleech': False, 'freeleech': 0, 'doubleUpload': False, 'green': False}]})
    with database() as db:
        db.execute('INSERT INTO download_history VALUES (?,?,?)', (HASH, 1, payload))
    assert history.recovery_needed([{'hash': HASH, 'added_on': 1}], {}) == {HASH: False}
    assert history.recovery_needed([{'hash': HASH, 'added_on': 2}], {}) == {HASH: True}
    assert history.recovery_needed([{'hash': HASH, 'added_on': 2}], {HASH: True}) == {HASH: False}
    with database() as db:
        assert db.execute('SELECT payload FROM download_history').fetchone()[0] == payload
