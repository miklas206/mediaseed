import asyncio
from contextlib import contextmanager
import json
import sqlite3

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from mediaseed.app import create_app
from mediaseed.swarm import SwarmMonitor, peer_count, scope, REQUEST_BUDGET
from test_app import sign_in

HASH = 'a' * 40
ACCOUNT = {'id': 'site', 'name': 'Synthetic tracker', 'api_key': 'synthetic-private-key', 'base_url': 'https://nordicbytes.org'}


def setup(tmp_path, monkeypatch):
    clock = [1000.]
    monkeypatch.setattr('mediaseed.swarm.time.time', lambda: clock[0])
    @contextmanager
    def database():
        with sqlite3.connect(tmp_path / 'test.sqlite') as db:
            yield db
    accounts = [dict(ACCOUNT)]
    rows = [{'hash': HASH, 'tracker': 'https://t.polarswarm.org/private-passkey/announce'}]
    reads = []
    response = {'data': {'id': '123', 'attributes': {'info_hash': HASH, 'seeders': 25, 'leechers': 4}}}
    async def qbit(path, payload):
        assert path.endswith('/info') and payload == {'limit': 500}
        return httpx.Response(200, json=rows)
    async def read(account, path, params):
        reads.append((account['id'], path))
        return response
    monitor = SwarmMonitor(database, lambda kind: accounts, qbit, read, lambda h, a: '123')
    return monitor, accounts, rows, reads, response, clock


@pytest.mark.parametrize('value,expected', [(0, 0), (12, 12), ('12', 12), (-1, None), (True, None), (1.5, None), ('2x', None), (None, None), (2**53, None)])
def test_peer_counts_keep_unknown_distinct_from_zero(value, expected):
    assert peer_count(value) == expected


def test_snapshot_is_local_scoped_fresh_and_safe_after_restart(tmp_path, monkeypatch):
    monitor, accounts, rows, reads, response, clock = setup(tmp_path, monkeypatch)
    assert monitor.snapshot(rows)[HASH]['status'] == 'pending'
    assert monitor.observe(HASH, accounts[0], response['data']['attributes'])
    public = monitor.snapshot(rows)[HASH]
    assert public == {'tracker': 'Synthetic tracker', 'seeders': 25, 'leechers': 4, 'checkedAt': 1000., 'status': 'ok'}
    assert not reads and 'private' not in json.dumps(public) and 'scope' not in public
    restarted = SwarmMonitor(monitor.database, monitor.accounts, monitor.qbit, monitor.tracker_read, monitor.record_id)
    assert restarted.snapshot(rows)[HASH] == public
    clock[0] += 181
    assert restarted.snapshot(rows)[HASH]['status'] == 'stale'
    clock[0] = 900
    assert restarted.snapshot(rows)[HASH]['status'] == 'stale'
    accounts[0]['api_key'] = 'changed-key'
    assert restarted.snapshot(rows)[HASH]['status'] == 'pending'
    rows[0]['tracker'] = 'https://unrelated.example.org/announce'
    assert restarted.snapshot(rows) == {}
    rows[0]['tracker'] = ''
    assert restarted.snapshot(rows) == {}


def test_exact_hash_and_sparse_observations_do_not_create_false_counts(tmp_path, monkeypatch):
    monitor, accounts, rows, reads, response, clock = setup(tmp_path, monkeypatch)
    assert not monitor.observe(HASH, accounts[0], {'info_hash': 'b' * 40, 'seeders': 0})
    assert not monitor.observe(HASH, accounts[0], {'info_hash': HASH, 'seeders': -1})
    assert monitor.observe(HASH, accounts[0], {'info_hash': HASH, 'seeders': 0, 'leechers': '0'})
    assert monitor.snapshot(rows)[HASH]['seeders'] == 0
    assert monitor.observe(HASH, accounts[0], {'info_hash': HASH, 'seeders': 2})
    assert monitor.snapshot(rows)[HASH]['leechers'] is None


def test_polling_reuses_record_identity_and_honours_interval_and_restarts(tmp_path, monkeypatch):
    monitor, accounts, rows, reads, response, clock = setup(tmp_path, monkeypatch)
    asyncio.run(monitor.tick())
    assert reads == [('site', '/api/torrents/123')]
    assert monitor.snapshot(rows)[HASH]['seeders'] == 25
    restarted = SwarmMonitor(monitor.database, monitor.accounts, monitor.qbit, monitor.tracker_read, monitor.record_id)
    asyncio.run(restarted.tick())
    assert len(reads) == 1
    clock[0] += 61
    response['data']['attributes'].update(seeders=31, leechers=0)
    asyncio.run(restarted.tick())
    assert len(reads) == 2 and restarted.snapshot(rows)[HASH]['leechers'] == 0
    restarted.record_id = lambda h, a: None
    clock[0] += 61
    asyncio.run(restarted.tick())
    assert len(reads) == 2  # Missing metadata never causes blind cross-tracker searches.


def test_wrong_record_and_errors_mark_unavailable_with_backoff(tmp_path, monkeypatch):
    monitor, accounts, rows, reads, response, clock = setup(tmp_path, monkeypatch)
    response['data']['attributes']['info_hash'] = 'b' * 40
    asyncio.run(monitor.tick())
    assert monitor.snapshot(rows)[HASH]['status'] == 'unavailable'
    assert monitor.snapshot(rows)[HASH]['seeders'] is None
    async def rate_limited(*args):
        reads.append(True)
        raise HTTPException(429, 'private-passkey-must-not-be-logged')
    monitor.tracker_read = rate_limited
    clock[0] += 61
    asyncio.run(monitor.tick())
    after = len(reads)
    clock[0] += 120
    asyncio.run(monitor.tick())
    assert len(reads) == after
    clock[0] += 181
    asyncio.run(monitor.tick())
    assert len(reads) == after + 1


def test_request_budget_is_fair_and_unrelated_torrents_do_not_query_accounts(tmp_path, monkeypatch):
    monitor, accounts, rows, reads, response, clock = setup(tmp_path, monkeypatch)
    rows[:] = [{'hash': f'{i:040x}', 'tracker': rows[0]['tracker']} for i in range(12)]
    monitor.record_id = lambda h, a: str(int(h, 16) + 1)
    async def read(account, path, params):
        identifier = int(path.rsplit('/', 1)[-1])
        reads.append(identifier)
        return {'data': {'id': str(identifier), 'attributes': {'info_hash': f'{identifier-1:040x}', 'seeders': identifier, 'leechers': 0}}}
    monitor.tracker_read = read
    asyncio.run(monitor.tick())
    assert len(reads) == REQUEST_BUDGET
    asyncio.run(monitor.tick())
    assert len(reads) == 12 and len(set(reads)) == 12
    rows[:] = [{'hash': HASH, 'tracker': 'https://t.nordicbytes.org.attacker.org/announce'}, {'hash': 'b'*40, 'tracker': ''}]
    clock[0] += 120
    asyncio.run(monitor.tick())
    assert len(reads) == 12


def test_torrent_api_uses_local_swarm_cache_and_exposes_external_client_totals(tmp_path):
    row = {'hash': HASH, 'name': 'Synthetic', 'tracker': 'https://t.polarswarm.org/private-passkey/announce',
           'num_seeds': 2, 'num_leechs': 1, 'num_complete': 14, 'num_incomplete': 3}
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith('/info'):
            return httpx.Response(200, json=[row])
        if request.url.path.endswith('/app/preferences'):
            return httpx.Response(200, json={})
        return httpx.Response(204)
    app = create_app(tmp_path, 'http://testserver', qbit_password='synthetic', transport=httpx.MockTransport(handler))
    with TestClient(app) as client:
        assert client.get('/api/torrents').status_code == 401
        sign_in(client)
        result = client.post('/api/trackers', json={'name': ACCOUNT['name'], 'api_key': ACCOUNT['api_key']})
        assert result.status_code == 200
        account = {**ACCOUNT, 'id': result.json()['id']}
        assert app.state.swarm.observe(HASH, account, {'info_hash': HASH, 'seeders': 30, 'leechers': 8})
        for path in ['/api/torrents', '/api/dashboard']:
            response = client.get(path)
            public = response.json()[0] if path.endswith('torrents') else response.json()['torrents'][0]
            assert public['swarm']['seeders'] == 30 and public['swarm']['leechers'] == 8
            assert public['num_complete'] == 14 and public['num_incomplete'] == 3
            assert public['num_seeds'] == 2 and public['num_leechs'] == 1
            assert 'private' not in response.text and ACCOUNT['api_key'] not in response.text
        row['tracker'] = 'https://unconfigured.example.org/announce'
        external = client.get('/api/torrents').json()[0]
        assert 'swarm' not in external and external['num_complete'] == 14
        assert not any('filter' in path or path.endswith(('/start', '/stop', '/delete')) for path in calls)
