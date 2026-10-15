import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import sqlite3
import time

import httpx
import pytest

from mediaseed.cleanup import CleanupPolicy, reached
from mediaseed.covers import Covers
from mediaseed.tracker_credit import TrackerCredit

HASH = 'a' * 40
CREATED = 1700000000
END = CREATED + 24 * 3600 + 1800


def setup(tmp_path, known=True, created=CREATED, own=False):
    @contextmanager
    def database():
        with sqlite3.connect(tmp_path / 'state.sqlite') as db:
            yield db
    with database() as db:
        db.execute('CREATE TABLE settings (kind TEXT)')
        db.execute("INSERT INTO settings VALUES ('trackers')")
    calls, rows = [], []
    async def qbit(path, method='GET', payload=None, files=None):
        calls.append((path, method, payload))
        return httpx.Response(200, json=rows) if path.endswith('/info') else httpx.Response(204)
    ledger = TrackerCredit(database, tmp_path, qbit)
    cache = Covers(tmp_path, None, None, None, username=lambda identifier: 'me')
    if known:
        cache.remember(HASH, {'id':'test', 'name':'Test'}, {'info_hash':HASH, 'created_at':datetime.fromtimestamp(created, timezone.utc).isoformat(), 'uploader':'me' if own else 'other', 'freeleech':'100%', 'double_upload':True})
    return ledger, calls, rows, cache


def row(uploaded=1000, **values):
    return {'hash':HASH, 'uploaded':uploaded, 'downloaded':1000, 'ratio':uploaded/1000,
            'ratio_limit':1, 'added_on':CREATED+60, 'seeding_time':60,
            'state':'stalledUP', 'progress':1, 'num_leechs':0, 'upspeed':0, **values}


def test_half_credit_is_used_for_cleanup_and_persists_after_expiry_and_restart(tmp_path):
    ledger, _, _, _ = setup(tmp_path)
    first = ledger.observe(row(), CREATED+120)
    assert first['green_active'] and first['credited_ratio'] == .5
    assert first['credited_uploaded'] == 500 and first['native_ratio_limit'] == 2
    assert not reached(CleanupPolicy(mode='ratio', ratio=1).model_dump(), {**row(), **first})
    ledger = TrackerCredit(ledger.database, tmp_path, ledger.raw_qbit)
    expiry = ledger.observe(row(1100), END+1)
    assert not expiry['green_active'] and expiry['credited_uploaded'] == 550
    assert expiry['native_ratio_limit'] == 1.55
    later = ledger.observe(row(1550), END+16)
    assert later['credited_uploaded'] == 1000 and later['credited_ratio'] == 1
    assert reached(CleanupPolicy(mode='ratio', ratio=1).model_dump(), {**row(1550), **later})


def test_unknown_and_historical_upload_are_conservative_until_exact_match(tmp_path):
    ledger, _, _, cache = setup(tmp_path, known=False)
    assert ledger.observe(row(), CREATED+120)['credited_ratio'] == .5
    assert ledger.observe(row(), END+30)['green_pending']
    cache.remember(HASH, {'id':'test','name':'Test'}, {'info_hash':HASH, 'created_at':datetime.fromtimestamp(CREATED, timezone.utc).isoformat()})
    result = ledger.observe(row(1100), END+60)
    assert result['credited_uploaded'] == 550 and result['green_history_estimated']
    cache.path(HASH).with_suffix('.json').unlink()
    assert not ledger.observe(row(1200), END+90)['green_pending'], 'Persisted publication evidence survives cover-cache pruning'


def test_old_publication_does_not_discount_newly_added_torrent(tmp_path):
    ledger, _, _, _ = setup(tmp_path)
    result = ledger.observe(row(1000, added_on=END+10), END+30)
    assert result['credited_uploaded'] == 1000 and result['credited_ratio'] == 1


def test_own_upload_is_exempt_only_with_confirmed_account_identity(tmp_path):
    ledger, _, _, _ = setup(tmp_path, own=True)
    result = ledger.observe(row(), CREATED+120)
    assert not result['green_active'] and not result['green_pending']
    assert result['credited_uploaded'] == 1000 and result['native_ratio_limit'] == 1


@pytest.mark.parametrize('created', [None, 'not-a-date', '2026-01-01T00:00:00'])
def test_missing_invalid_or_naive_dates_never_release_pending_credit(tmp_path, created):
    ledger, _, _, cache = setup(tmp_path, known=False)
    cache.remember(HASH, {'id':'test','name':'Test'}, {'info_hash':HASH,'created_at':created})
    result = ledger.observe(row(), CREATED+120)
    assert result['green_pending'] and result['credited_ratio'] == .5


def test_counter_reset_and_missing_observations_never_invent_credit(tmp_path):
    ledger, _, _, _ = setup(tmp_path)
    ledger.observe(row(), CREATED+120)
    assert ledger.observe({**row(), 'uploaded':None}, CREATED+130)['green_pending']
    result = ledger.observe(row(1100), CREATED+140)
    assert result['credited_uploaded'] == 550
    reset = ledger.observe(row(100), CREATED+150)
    assert reset['credit_observation_stale'] and reset['credited_ratio'] == 0
    assert ledger.load(HASH)['uploaded'] == 1100
    assert ledger.observe(row(1200), CREATED+160)['credited_uploaded'] == 600


def test_changed_goal_and_native_limits_keep_selected_credit_target(tmp_path):
    ledger, calls, rows, _ = setup(tmp_path)
    async def scenario():
        await ledger.request('/api/v2/torrents/add','POST', {'urls':'magnet:?xt=urn:btih:'+HASH, 'ratioLimit':'3','stopped':'true','paused':'true'})
        assert calls[-1][2]['ratioLimit']=='6.0'
        assert ledger.load(HASH)['target']==3
        await ledger.request('/api/v2/torrents/setShareLimits','POST', {'hashes':HASH,'ratioLimit':'5','seedingTimeLimit':'2940'})
        assert calls[-1][2]['ratioLimit']=='10.0'
        ledger.observe(row(), CREATED+120)
        rows.append(row(1100))
        # Use expired publication evidence in real clock time.
        await ledger.tick()
        assert calls[-1][0].endswith('/setShareLimits')
        assert float(calls[-1][2]['ratioLimit']) > 5
        assert ledger.load(HASH)['target']==5
    asyncio.run(scenario())


def test_cleanup_native_unlimited_limits_are_not_overridden(tmp_path):
    ledger, calls, rows, _ = setup(tmp_path)
    ledger.cleanup=lambda identifier: {'mode':'both'}
    rows.append(row())
    asyncio.run(ledger.tick())
    assert all(method=='GET' for _,method,_ in calls)


def test_moving_time_does_not_advance_accounted_seeding_clock(tmp_path):
    ledger, _, _, _ = setup(tmp_path)
    ledger.observe(row(seeding_time=100), CREATED+100)
    ledger.observe(row(seeding_time=150,state='moving'), CREATED+150)
    result=ledger.observe(row(seeding_time=200), CREATED+200)
    assert result['seeding_time']==100
    assert ledger.observe(row(seeding_time=210), CREATED+210)['seeding_time']==110


def test_different_hash_never_supplies_green_policy_or_bonuses(tmp_path):
    ledger, _, _, cache = setup(tmp_path, known=False)
    cache.remember(HASH, {'id':'test','name':'Test'}, {'info_hash':'b'*40,'created_at':'2020-01-01T00:00:00Z','featured':True})
    assert not cache.path(HASH).with_suffix('.json').exists()
    assert ledger.observe(row(), CREATED+120)['green_pending']


def test_manual_early_stop_checks_credit_instead_of_raw_ratio(tmp_path):
    from fastapi.testclient import TestClient
    from mediaseed.app import create_app
    from test_app import sign_in
    mutations = []
    def handler(request):
        if request.url.path.endswith('/login'):
            return httpx.Response(204)
        if request.url.path.endswith('/properties'):
            return httpx.Response(200, json={'total_uploaded':1000,'total_downloaded':1000,'share_ratio':1,'addition_date':CREATED+60,'seeding_time':60})
        if request.url.path.endswith('/info'):
            return httpx.Response(200, json=[])
        mutations.append(request.url.path)
        return httpx.Response(204)
    with TestClient(create_app(tmp_path, 'http://testserver', qbit_password='synthetic', transport=httpx.MockTransport(handler))) as client:
        sign_in(client)
        client.post('/api/trackers', json={'name':'Test','api_key':'synthetic'})
        response=client.post('/api/torrents/stop',json={'torrent_hash':HASH})
        assert response.status_code == 409 and 'credited ratio' in response.json()['detail']
        assert not mutations
        assert client.post('/api/torrents/stop',json={'torrent_hash':HASH,'confirm_early':True}).status_code == 200
        assert mutations == ['/api/v2/torrents/stop']


def test_large_goals_are_armed_while_paused_and_worker_preserves_files(tmp_path):
    ledger, calls, rows, _ = setup(tmp_path)
    async def scenario():
        await ledger.request('/api/v2/torrents/add','POST', {'urls':'magnet:?xt=urn:btih:'+HASH,'ratioLimit':'750','stopped':'false','paused':'false'})
        assert calls[0][2]['stopped'] == 'true'
        assert calls[1][2]['ratioLimit'] == '-1'
        assert calls[2][0].endswith('/start')
        rows.append(row(1_500_000,ratio_limit=-1))
        await ledger.tick()
        assert any(path.endswith('/stop') for path,_,_ in calls)
        assert not any(path.endswith('/delete') for path,_,_ in calls)
    asyncio.run(scenario())


def test_reimport_does_not_inherit_an_absent_torrents_old_target(tmp_path):
    ledger, calls, _, _ = setup(tmp_path)
    ledger.register(HASH, 5)
    asyncio.run(ledger.request('/api/v2/torrents/add','POST',{'urls':'magnet:?xt=urn:btih:'+HASH,'ratioLimit':'2','stopped':'true','paused':'true'}))
    assert ledger.load(HASH)['target'] == 2
    assert calls[-1][2]['ratioLimit'] == '4.0'


def test_batched_snapshot_uses_one_database_transaction(tmp_path):
    ledger, _, _, _ = setup(tmp_path)
    original, count = ledger.database, []
    @contextmanager
    def measured():
        count.append(1)
        with original() as db:
            yield db
    ledger.database=measured
    result=ledger.observe_many([row(),{**row(),'hash':'b'*40}])
    assert len(result)==2 and len(count)==1


def test_rejected_high_goal_import_never_starts_a_torrent(tmp_path):
    ledger, calls, _, _ = setup(tmp_path)
    async def reject(path, method='GET', payload=None, files=None):
        calls.append((path,method,payload))
        return httpx.Response(200,json={'success_count':0,'failure_count':1,'pending_count':0})
    ledger.raw_qbit=reject
    asyncio.run(ledger.request('/api/v2/torrents/add','POST',{'urls':'magnet:?xt=urn:btih:'+HASH,'ratioLimit':'750','stopped':'false','paused':'false'}))
    assert len(calls)==1 and calls[0][0].endswith('/add')


def test_tracker_identity_survives_expired_benefits_without_extending_bonus_evidence(tmp_path):
    ledger,_,_,_=setup(tmp_path,created=1000000000)
    now=time.time()
    first=ledger.observe(row(0,added_on=now-10),now)
    assert first['tracker_name']=='Test' and first['tracker_benefits'] is not None
    later=ledger.observe(row(100,added_on=now-10),now+301)
    assert later['tracker_name']=='Test' and later['tracker_benefits'] is None
    assert later['account_credited_uploaded']==100
    stale=ledger.observe(row(90,added_on=now-10),now+302)
    assert stale['credit_observation_stale'] and stale['tracker_name']=='Test'
