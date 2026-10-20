import asyncio
import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient
from test_app import PASSWORD, sign_in

from mediaseed import features
from mediaseed.app import create_app
from mediaseed.covers import Covers
from mediaseed.intelligence import metadata

HASH = 'a' * 40
OLD_HASH = 'b' * 40
POSTER = 'https://image.tmdb.org/t/p/w500/synthetic.jpg'


def test_metadata_sanitization_excludes_private_links_and_active_html():
    record = metadata({
        'description': '<script>unsafe()</script><p>Description</p>',
        'download_link': 'https://nordicbytes.org/torrents/download/private-passkey',
        'screenshots': [{'url': 'https://127.0.0.1/private'}, {'url': 'https://example.org/image.jpg'},
                        {'url': 'https://example.org/image.jpg?private-token=secret'}],
        'labels': [{'name': '<img src=x onerror=bad()>Label'}],
        'media_info': 'x' * 50000,
    })
    assert record['description'] == 'Description'
    assert record['screenshots'] == [{'url': 'https://example.org/image.jpg', 'thumbnail': None}]
    assert record['labels'][0]['name'] == 'Label'
    assert len(record['media_info']) == 24000
    assert 'private' not in json.dumps(record)


@pytest.mark.parametrize('upgrade', [False, True])
def test_recovered_metadata_and_cover_are_authenticated_persistent_and_upgrade_safe(tmp_path, monkeypatch, upgrade):
    observed = []

    def capture(*args, **kwargs):
        cache = Covers(*args, **kwargs)
        observed.append(cache)
        return cache

    monkeypatch.setattr(features, 'Covers', capture)
    tracker_id = None
    if upgrade:
        with TestClient(create_app(tmp_path, 'http://testserver')) as client:
            sign_in(client)
            tracker_id = client.post('/api/trackers', json={'name': 'Synthetic', 'api_key': 'synthetic-key'}).json()['id']
        # The older installation had accounts/settings/images but neither of the
        # new metadata tables nor a retry ledger. No real data/credentials used.
        with sqlite3.connect(tmp_path / 'mediaseed.sqlite') as db:
            db.execute('DROP TABLE insight_metadata')
            db.execute('DROP TABLE insight_swarm')
        (tmp_path / 'cover-cache/metadata-retries.sqlite').unlink()
        observed[-1].path(OLD_HASH).write_bytes(b'\xff\xd8\xffold-cover')

    with TestClient(create_app(tmp_path, 'http://testserver')) as client:
        assert client.get(f'/api/torrents/{HASH}/media').status_code == 401
        if upgrade:
            login = client.post('/api/auth/login', json={'username': 'tester', 'password': PASSWORD})
            assert login.status_code == 200
            client.headers['X-MediaSeed-CSRF'] = login.json()['csrf']
            with sqlite3.connect(tmp_path / 'mediaseed.sqlite') as db:
                assert json.loads(db.execute("SELECT payload FROM settings WHERE id=?", (tracker_id,)).fetchone()[0])['api_key'] == 'synthetic-key'
            assert observed[-1].path(OLD_HASH).read_bytes() == b'\xff\xd8\xffold-cover'
        else:
            sign_in(client)
            tracker_id = client.post('/api/trackers', json={'name': 'Synthetic', 'api_key': 'synthetic-key'}).json()['id']
        assert client.get(f'/api/torrents/{HASH}/media').json() == {'metadata': None}
        assert client.get('/api/torrents/invalid/media').status_code == 422
        cache = observed[-1]
        calls = []

        async def qbit(*args, **kwargs):
            return httpx.Response(200, json=[{'hash': HASH, 'name': 'Synthetic.Release.mkv'}])

        async def read(account, path, params):
            calls.append(path)
            assert account['id'] == tracker_id
            attrs = {'info_hash': HASH, 'meta': {'poster': POSTER}}
            if path.endswith('filter'):
                return {'data': [{'id': '42', 'attributes': attrs}]}
            return {'id': '42', 'attributes': {
                **attrs, 'description': '<p>Description</p>', 'media_info': 'MediaInfo',
                'files': [{'name': 'Synthetic.mkv', 'size': 3}],
                'download_link': 'https://nordicbytes.org/torrents/download/private-passkey',
            }}

        async def download(url, path):
            assert url == POSTER
            path.write_bytes(b'\xff\xd8\xffsynthetic')

        cache.qbit, cache.tracker_read, cache.download = qbit, read, download
        asyncio.run(cache.tick())
        record = client.get(f'/api/torrents/{HASH}/media').json()['metadata']
        assert record['description'] == 'Description' and record['media_info'] == 'MediaInfo'
        assert record['files'] == [{'name': 'Synthetic.mkv', 'size': 3}]
        assert 'private-passkey' not in json.dumps(record)
        assert client.get(f'/api/covers/{HASH}').content == b'\xff\xd8\xffsynthetic'
        assert calls == ['/api/torrents/filter', '/api/torrents/42']
        asyncio.run(cache.tick())
        assert len(calls) == 2

    with TestClient(create_app(tmp_path, 'http://testserver')) as client:
        assert client.post('/api/auth/login', json={'username': 'tester', 'password': PASSWORD}).status_code == 200
        assert client.get(f'/api/torrents/{HASH}/media').json()['metadata']['description'] == 'Description'
        assert client.get(f'/api/covers/{HASH}').content == b'\xff\xd8\xffsynthetic'
