import asyncio
import json
import sqlite3

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from mediaseed.app import create_app
from mediaseed.cleanup import CleanupManager, CleanupPolicy, CleanupUpdate
from mediaseed.features import add_stopped
from test_app import sign_in
from test_cleanup import HASH, MAGNET, manager


def existing(state):
    state['rows'] = [{'hash': HASH, 'added_on': 100, 'tags': '', 'state': 'stalledUP',
                      'progress': 1, 'ratio': 1, 'seeding_time': 3600, 'num_leechs': 0, 'upspeed': 0}]


def test_existing_torrent_rule_is_editable_persistent_and_only_worker_removes(tmp_path):
    async def scenario():
        m, state, calls = manager(tmp_path)
        existing(state)
        assert (await m.inspect(HASH))['policy']['mode'] == 'never'
        await m.update(HASH, CleanupUpdate(mode='both', hours=2, ratio=3, added_on=100))
        assert all(call[1] == 'GET' for call in calls)
        restarted = CleanupManager(m.database, m.qbit, m.vpn_status)
        assert restarted.public(HASH)['hours'] == 2
        await restarted.update(HASH, CleanupUpdate(mode='ratio', ratio=2, added_on=100))
        assert len(restarted.records()) == 1
        await restarted.tick()
        assert not any(call[0].endswith('/delete') for call in calls)
        state['rows'][0]['ratio'] = 2
        await restarted.tick()
        assert next(call for call in calls if call[0].endswith('/delete'))[2]['deleteFiles'] == 'false'
        assert restarted.public(HASH)['mode'] == 'never'
    asyncio.run(scenario())


def test_readded_hash_and_stale_editor_never_inherit_rule(tmp_path):
    async def scenario():
        m, state, calls = manager(tmp_path)
        existing(state)
        await m.update(HASH, CleanupUpdate(mode='ratio', ratio=1, added_on=100))
        state['rows'][0]['added_on'] = 200
        with pytest.raises(HTTPException, match='409'):
            await m.update(HASH, CleanupUpdate(mode='time', added_on=100))
        await m.tick()
        assert m.public(HASH)['status'] == 'identity_mismatch'
        assert not any(call[0].endswith('/delete') for call in calls)
        with pytest.raises(HTTPException, match='409'):
            await m.update(HASH, CleanupUpdate(mode='time', added_on=200))
        # Disabling a mismatched rule remains possible and does not mutate the replacement.
        calls.clear()
        await m.update(HASH, CleanupUpdate(mode='never', added_on=200))
        assert m.public(HASH)['mode'] == 'never'
        assert all(call[1] == 'GET' for call in calls)
    asyncio.run(scenario())


def test_editing_legacy_tagged_import_preserves_its_identity(tmp_path):
    async def scenario():
        m, state, calls = manager(tmp_path)
        await m.add(add_stopped, CleanupPolicy(mode='time'), 1, magnet=MAGNET)
        state['rows'][0]['added_on'] = 100
        original = m.records()[0]
        await m.update(HASH, CleanupUpdate(mode='both', hours=60, ratio=4, added_on=100))
        updated = m.records()[0]
        assert updated['tag'] == original['tag'] and updated['id'] == original['id']
        assert updated['added_on'] == 100 and updated['start'] is False
        state['rows'][0].update(tags='', ratio=10, seeding_time=999999)
        await m.tick()
        assert m.public(HASH)['status'] == 'identity_mismatch'
        assert not any(call[0].endswith('/delete') for call in calls)
    asyncio.run(scenario())


def test_file_deletion_requires_explicit_confirmation_and_disable_is_durable(tmp_path):
    async def scenario():
        m, state, calls = manager(tmp_path)
        existing(state)
        with pytest.raises(HTTPException, match='422'):
            await m.update(HASH, CleanupUpdate(mode='ratio', added_on=100, delete_files=True))
        assert m.public(HASH)['mode'] == 'never'
        await m.update(HASH, CleanupUpdate(mode='ratio', added_on=100, delete_files=True, confirm_delete_files=True))
        assert m.public(HASH)['delete_files'] is True
        await m.update(HASH, CleanupUpdate(mode='never', added_on=100))
        await m.tick()
        assert not any(call[0].endswith('/delete') for call in calls)
        assert CleanupManager(m.database, m.qbit, m.vpn_status).public(HASH)['mode'] == 'never'
    asyncio.run(scenario())


def test_cleanup_edit_api_guards_validation_and_upgrade(tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path.endswith('/info'):
            return httpx.Response(200, json=[{'hash': HASH, 'added_on': 100, 'state': 'stoppedDL', 'tags': ''}])
        return httpx.Response(204)
    settings = dict(qbit_password='synthetic', transport=httpx.MockTransport(handler))
    url = f'/api/torrents/{HASH}/cleanup'
    with TestClient(create_app(tmp_path, 'http://testserver', **settings)) as client:
        assert client.get(url).status_code == 401
        assert client.put(url, json={'mode': 'never', 'added_on': 100}).status_code == 401
        sign_in(client)
        assert client.get(url).json() == {'policy': {'mode': 'never', 'status': 'disabled'}, 'added_on': 100}
        assert client.put(url, json={'mode': 'time', 'added_on': 100, 'hours': 72}).status_code == 200
        assert client.put(url, json={'mode': 'time', 'added_on': 99}).status_code == 409
        assert client.put(url, json={'mode': 'time', 'added_on': 100, 'hours': 0}).status_code == 422
        assert client.put(url, json={'mode': 'time', 'added_on': 100, 'delete_files': True}).status_code == 422
        assert client.put('/api/torrents/all/cleanup', json={'added_on': 100}).status_code == 422
        cookies, csrf = client.cookies, client.headers['X-MediaSeed-CSRF']
        client.headers['X-MediaSeed-CSRF'] = 'wrong'
        assert client.put(url, json={'mode': 'never', 'added_on': 100}).status_code == 403
    with TestClient(create_app(tmp_path, 'http://testserver', **settings)) as client:
        client.cookies.update(cookies)
        client.headers['X-MediaSeed-CSRF'] = csrf
        assert client.get(url).json()['policy']['hours'] == 72
        assert client.put(url, json={'mode': 'never', 'added_on': 100}).status_code == 200
        assert client.get(url).json()['policy']['mode'] == 'never'
    assert not any(request.url.path.endswith(('/start', '/stop', '/delete', '/add')) for request in calls)


@pytest.mark.parametrize('existing_count', [0, 50])
def test_cleanup_fresh_and_upgrade_accept_more_than_fifty_rules(tmp_path, existing_count):
    async def scenario():
        m, state, calls = manager(tmp_path)
        for i in range(existing_count):
            m.save({'id': str(i), 'hashes': [format(i, '040x')], 'mode': 'ratio',
                    'ratio': 3, 'hours': 72, 'delete_files': False, 'status': 'active',
                    'added_on': 100, 'tag': None})
        before = m.records()
        restored = CleanupManager(m.database, m.qbit, m.vpn_status)
        existing(state)
        await restored.update(HASH, CleanupUpdate(mode='ratio', ratio=3, added_on=100))
        assert len(restored.records()) == existing_count + 1
        assert all(r in restored.records() for r in before)
        assert not any(call[1] == 'POST' for call in calls)
    asyncio.run(scenario())


def test_cleanup_batches_large_observations_before_any_action(tmp_path):
    async def scenario():
        m, _, _ = manager(tmp_path)
        for i in range(500):
            m.save({'id': str(i), 'hashes': [format(i, '064x')], 'mode': 'ratio',
                    'status': 'active', 'ratio': 3, 'hours': 72, 'delete_files': False})
        sizes = []
        async def read(path, method='GET', payload=None):
            assert method == 'GET'
            assert len(json.dumps(payload)) < 12000
            sizes.append(len(payload['hashes'].split('|')))
            return httpx.Response(200, json=[])
        m.qbit = read
        await m.tick()
        assert sizes == [100] * 5
        with pytest.raises(HTTPException) as error:
            m.require_capacity()
        assert '500 active imports' in error.value.detail
    asyncio.run(scenario())


@pytest.mark.parametrize('restart', [False, True])
def test_torrent_list_exposes_identity_bound_cleanup_goal_on_fresh_and_existing_install(tmp_path, restart):
    row = {'hash': HASH, 'added_on': 100, 'state': 'stoppedUP', 'tags': '',
           'progress': 1, 'ratio': 1.3, 'ratio_limit': 1, 'seeding_time': 72 * 3600}
    def handler(request):
        if request.url.path.endswith('/info'):
            return httpx.Response(200, json=[row])
        if request.url.path.endswith('/app/preferences'):
            return httpx.Response(200, json={})
        return httpx.Response(204)
    settings = dict(qbit_password='synthetic', transport=httpx.MockTransport(handler))
    with TestClient(create_app(tmp_path, 'http://testserver', **settings)) as client:
        sign_in(client)
        assert client.get('/api/torrents').json()[0]['cleanup']['mode'] == 'never'
        response = client.put(f'/api/torrents/{HASH}/cleanup', json={'mode': 'ratio', 'ratio': 3, 'added_on': 100})
        assert response.status_code == 200
        cookies = client.cookies
        if not restart:
            assert client.get('/api/torrents').json()[0]['cleanup']['ratio'] == 3
    with TestClient(create_app(tmp_path, 'http://testserver', **settings)) as client:
        client.cookies.update(cookies)
        assert client.get('/api/torrents').json()[0]['cleanup']['ratio'] == 3
        assert client.get('/api/dashboard').json()['torrents'][0]['cleanup']['ratio'] == 3
        row['added_on'] = 200
        assert client.get('/api/torrents').json()[0]['cleanup']['mode'] == 'never'
