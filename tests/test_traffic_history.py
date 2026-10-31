from contextlib import contextmanager
import json
import sqlite3
import pytest
from mediaseed.traffic_history import TrafficHistory


def database_at(tmp_path):
    @contextmanager
    def database():
        with sqlite3.connect(tmp_path/'history.sqlite') as db:yield db
    return database


def test_physical_totals_are_separate_durable_and_never_fabricated_from_sessions(tmp_path):
    database=database_at(tmp_path)
    with database() as db:db.execute('CREATE TABLE existing(value TEXT)');db.execute("INSERT INTO existing VALUES ('preserved')")
    history=TrafficHistory(database)
    assert not history.observe({'up_info_data':999,'dl_info_data':888},now=6000)
    assert history.public(now=6000)['stats']=={}
    assert history.observe({'alltime_ul':0,'alltime_dl':123,'password':'secret'},now=6000)
    assert history.observe({'alltime_ul':1024,'alltime_dl':2048},now=6060)
    restored=TrafficHistory(database);result=restored.public(now=6060)
    assert result['stats']=={'real_uploaded':1024,'real_downloaded':2048}
    assert result['status']=='ok' and result['scope']=='seedbox'
    assert len(result['samples'])==2 and 'secret' not in json.dumps(result)
    assert restored.public(now=6301)['status']=='stale'
    with database() as db:assert db.execute('SELECT value FROM existing').fetchone()[0]=='preserved'


@pytest.mark.parametrize('bad',[True,1.5,-1,2**63,None,'123'])
def test_invalid_or_missing_counters_do_not_become_zero(tmp_path,bad):
    history=TrafficHistory(database_at(tmp_path))
    assert not history.observe({'alltime_ul':bad,'alltime_dl':bad},now=6000)
    assert history.public(now=6000)['stats']=={}


def test_downsampling_keeps_physical_resets_and_missing_observations_visible(tmp_path):
    history=TrafficHistory(database_at(tmp_path))
    for i in range(600):
        if i==6:continue
        values={'alltime_ul':1000+i,'alltime_dl':100+i}
        if i==10:values['alltime_ul']=1
        if i==13:values['alltime_dl']=None
        history.observe(values,now=6000+i*60)
    result=history.public(now=6000+599*60)
    assert len(result['samples'])<=288 and result['sampleInterval']==180
    assert any(row['gapBefore'] for row in result['samples'])
    assert any('real_uploaded' in row['gapMetrics'] for row in result['samples'])
    assert any('real_downloaded' in row['gapMetrics'] for row in result['samples'])
    assert not history.observe({'alltime_ul':5,'alltime_dl':5},now=6000)
    assert history.public(now=6000+599*60)['stats']['real_uploaded']==1599

@pytest.mark.parametrize('source',['scoped','standalone'])
def test_background_reads_lifetime_counters_through_existing_client_transport(tmp_path,monkeypatch,source):
    import asyncio
    import importlib
    import httpx
    from mediaseed import features
    module=importlib.import_module('mediaseed.app')
    original=module.install_features;callbacks=[];requests=[]
    def capture(*args,**kwargs):
        result=original(*args,**kwargs);callbacks.append(result);return result
    async def run(jobs,**kwargs):
        await next(callback for name,callback in jobs if name=='traffic')()
    def handler(request):
        requests.append((request.method,request.url.path))
        if request.url.path.endswith('auth/login'):return httpx.Response(200,text='Ok.')
        if request.url.path.endswith('transfer/info'):return httpx.Response(200,json={'alltime_ul':12,'alltime_dl':34} if source=='scoped' else {'up_info_data':900,'dl_info_data':800})
        if request.url.path.endswith('sync/maindata'):return httpx.Response(200,json={'server_state':{'alltime_ul':12,'alltime_dl':34,'private':'secret'}})
        return httpx.Response(200,json=[])
    monkeypatch.setattr(module,'install_features',capture);monkeypatch.setattr(features,'run_background',run)
    app=module.create_app(tmp_path,'http://testserver',qbit_password='synthetic',transport=httpx.MockTransport(handler))
    asyncio.run(callbacks[0]())
    result=next(route.endpoint for route in app.routes if getattr(route,'path',None)=='/api/insights')(days=7,session={})
    assert result['traffic']['stats']=={'real_uploaded':12,'real_downloaded':34}
    assert 'secret' not in json.dumps(result)
    assert all(method=='GET' or path.endswith('auth/login') for method,path in requests)
    assert any(path.endswith('sync/maindata') for _,path in requests)==(source=='standalone')
