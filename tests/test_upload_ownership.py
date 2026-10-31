"""Upload ownership is independent from observed download history/bonuses."""
from contextlib import contextmanager
import json
import sqlite3

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mediaseed.intelligence import Intelligence
from mediaseed.tracker_credit import TrackerCredit
from mediaseed.features import install_features

HASH = 'a' * 40
OTHER = 'b' * 40
ACCOUNT = {'id': 'tracker', 'name': 'Tracker', 'provider': 'nordicbytes', 'api_key': 'synthetic-key', 'username': 'MyUploader'}
ROW = {'hash': HASH, 'tracker': 'https://t.nordicbytes.org/private', 'uploaded': 0, 'downloaded': 0, 'added_on': 1, 'state': 'stalledUP', 'progress': 1, 'name': 'Synthetic-Ghost'}


def database_at(tmp_path):
    @contextmanager
    def database():
        with sqlite3.connect(tmp_path / 'state.sqlite') as db:
            db.row_factory = sqlite3.Row
            yield db
    with database() as db:
        db.execute('CREATE TABLE IF NOT EXISTS settings (kind TEXT, id TEXT, payload TEXT, PRIMARY KEY(kind,id))')
    return database


@pytest.mark.parametrize('uploader,expected', [('MyUploader', True), ('AnotherUploader', False), (None, None), ('', None), ('Anonymous', None), ('Anonym', None)])
def test_exact_metadata_uploader_is_compared_to_current_account(tmp_path, uploader, expected):
    database = database_at(tmp_path)
    intelligence = Intelligence(database)
    intelligence.remember(HASH, ACCOUNT, {'info_hash': HASH, 'uploader': uploader})
    result = intelligence.upload_owners([ROW], [ACCOUNT])
    assert result.get(HASH) is expected
    assert intelligence.upload_owners([ROW], [{**ACCOUNT, 'username': None}]).get(HASH) is None
    assert intelligence.upload_owners([{**ROW, 'hash': OTHER}], [ACCOUNT]) == {}
    assert Intelligence(database).upload_owners([ROW], [ACCOUNT]).get(HASH) is expected


def test_wrong_hash_account_or_origin_cannot_claim_ownership(tmp_path):
    intelligence = Intelligence(database_at(tmp_path))
    intelligence.remember(HASH, ACCOUNT, {'info_hash': OTHER, 'uploader': ACCOUNT['username']})
    assert intelligence.upload_owners([ROW], [ACCOUNT]) == {}
    intelligence.remember(HASH, ACCOUNT, {'info_hash': HASH, 'uploader': ACCOUNT['username']})
    assert intelligence.upload_owners([ROW], [{**ACCOUNT, 'id': 'another-account'}]) == {}
    changed = {**ACCOUNT, 'provider': 'unit3d', 'base_url': 'https://anothertracker.org'}
    assert intelligence.upload_owners([ROW], [changed]) == {}
    assert intelligence.upload_owners([{**ROW, 'tracker': 'https://anothertracker.org/announce'}], [ACCOUNT]) == {}


def test_legacy_metadata_without_origin_works_for_same_nordicbytes_account(tmp_path):
    database = database_at(tmp_path)
    intelligence = Intelligence(database)
    with database() as db:
        db.execute('INSERT INTO insight_metadata VALUES (?,?,?)', (HASH, 1, json.dumps({'account': ACCOUNT['id'], 'uploader': ACCOUNT['username']})))
    assert intelligence.upload_owners([ROW], [ACCOUNT])[HASH] is True
    assert intelligence.upload_owners([ROW], [{**ACCOUNT, 'username': 'ReplacementUser'}])[HASH] is False


def test_feature_wiring_current_account_key_change_and_unchanged_credit(tmp_path, monkeypatch):
    from mediaseed import features
    database = database_at(tmp_path)
    with database() as db:
        db.execute('INSERT INTO settings VALUES (?,?,?)', ('trackers', ACCOUNT['id'], json.dumps({k:v for k,v in ACCOUNT.items() if k not in {'id', 'username'}})))
    async def qbit(*args, **kwargs): return httpx.Response(200, json=[ROW])
    original_client = httpx.AsyncClient
    requests = []
    def handler(request):
        requests.append(request.url.path)
        assert request.url.path == '/api/user'
        return httpx.Response(200, json={'username': ACCOUNT['username'], 'stats': {'uploaded': 0, 'downloaded': 0}})
    monkeypatch.setattr(features.httpx, 'AsyncClient', lambda **kwargs: original_client(**kwargs, transport=httpx.MockTransport(handler)))
    credit = TrackerCredit(database, tmp_path, qbit)
    app = FastAPI()
    install_features(app, lambda: {'role': 'administrator'}, database, qbit, data_dir=tmp_path, credit=credit)
    Intelligence(database).remember(HASH, ACCOUNT, {'info_hash': HASH, 'uploader': ACCOUNT['username']})
    before = credit.observe_many([ROW])[0]
    assert before['own_upload'] is None
    with TestClient(app) as client:
        assert client.get('/api/trackers').status_code == 200
        after = credit.observe_many([ROW])[0]
        assert after['own_upload'] is True
        for field in ['green_pending', 'credited_ratio', 'credited_uploaded', 'ratio_limit']:
            assert after[field] == before[field], 'Display ownership does not change credit/cleanup rules'
        assert after['download_history'] == before['download_history'], 'No download history is invented'
        assert 'synthetic-key' not in json.dumps(after)
        with database() as db:
            db.execute('UPDATE settings SET payload=? WHERE id=?', (json.dumps({**ACCOUNT, 'api_key': 'replacement-key'}), ACCOUNT['id']))
        assert credit.observe_many([ROW])[0]['own_upload'] is None, 'Old account identity is not reused for a changed key'
    assert requests == ['/api/user'], 'Dashboard ownership creates no extra tracker requests'


def test_real_authenticated_dashboard_preserves_ownership_field(tmp_path, monkeypatch):
    from mediaseed import features
    from mediaseed.app import create_app
    from test_app import sign_in
    original_client = httpx.AsyncClient
    def tracker(request):
        if request.url.path == '/api/user':
            return httpx.Response(200, json={'username': ACCOUNT['username'], 'stats': {'uploaded': 0, 'downloaded': 0}})
        assert request.url.path == '/api/torrents/filter'
        return httpx.Response(200, json={'data': [{'id': '42', 'attributes': {'info_hash': HASH, 'name': ROW['name'], 'uploader': ACCOUNT['username']}}]})
    def factory(**kwargs):
        kwargs.setdefault('transport', httpx.MockTransport(tracker))
        return original_client(**kwargs)
    monkeypatch.setattr(features.httpx, 'AsyncClient', factory)
    def qbit(request):
        if request.url.path.endswith('/login'): return httpx.Response(204)
        if request.url.path.endswith('/torrents/info'): return httpx.Response(200, json=[ROW])
        if request.url.path.endswith('/app/preferences'): return httpx.Response(200, json={})
        return httpx.Response(200, json={})
    with TestClient(create_app(tmp_path, 'http://testserver', qbit_password='synthetic', transport=httpx.MockTransport(qbit))) as client:
        assert client.get('/api/dashboard').status_code == 401
        sign_in(client)
        identifier = client.post('/api/trackers', json={'name': 'Test', 'api_key': 'synthetic-key'}).json()['id']
        assert client.get('/api/trackers').status_code == 200
        assert client.get(f'/api/trackers/{identifier}/search', params={'query': 'Synthetic'}).status_code == 200
        response = client.get('/api/dashboard')
        assert response.status_code == 200
        torrent = response.json()['torrents'][0]
        assert torrent['hash'] == HASH and torrent['own_upload'] is True
        assert 'synthetic-key' not in response.text and 'MyUploader' not in response.text
