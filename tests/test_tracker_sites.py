import asyncio
import json
import socket
import sqlite3
import time

import httpcore
import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from mediaseed.app import create_app
from mediaseed.tracker_sites import normalize_origin, download_address, PublicNetworkBackend, matching_accounts
from mediaseed.tracker_reads import TrackerReads
from test_app import sign_in, PASSWORD
from mediaseed.cleanup import source_hashes


@pytest.mark.parametrize('value',['http://sagmire.org','https://localhost','https://127.0.0.1','https://[::1]','https://site.local','https://site.org/api','https://user:secret@site.org','https://site.org:8080','https://site.org?key=secret','https://site.org/#fragment'])
def test_invalid_tracker_origins(value):
    with pytest.raises(ValueError): normalize_origin(value)


def test_origin_download_and_account_matching():
    account={'provider':'unit3d','base_url':'https://sagmire.org'}
    assert normalize_origin(' https://Sagmire.org:443/ ')=='https://sagmire.org'
    assert download_address(account,'https://sagmire.org/torrent/download/12/synthetic').hostname=='sagmire.org'
    for url in ['https://nordicbytes.org/torrents/download/12/key','https://sagmire.org:99/torrent/download/12/key','https://sagmire.org/torrent/download/../private','https://sagmire.org/torrent/download/%2e%2e/private','https://sagmire.org/api/user','https://evil.org/torrent/download/12/key']:
        with pytest.raises(HTTPException): download_address(account,url)
    nordic={'provider':'nordicbytes'}
    assert matching_accounts([account,nordic],'https://t.sagmire.org/announce')==[account]
    assert matching_accounts([account,nordic],'https://t.polarswarm.org/announce')==[nordic]
    assert matching_accounts([account,nordic],'https://sagmire.org.evil.org/announce')==[]


def test_public_transport_pins_dns_and_refuses_private_answers(monkeypatch):
    async def scenario():
        loop=asyncio.get_running_loop();dialed=[]
        answers=['8.8.8.8']
        async def resolve(*args,**kwargs):return [(socket.AF_INET,socket.SOCK_STREAM,6,'',(a,443)) for a in answers]
        async def connect(self,host,port,*args,**kwargs):dialed.append((host,port));return 'synthetic-stream'
        monkeypatch.setattr(loop,'getaddrinfo',resolve)
        monkeypatch.setattr(httpcore.AnyIOBackend,'connect_tcp',connect)
        assert await PublicNetworkBackend().connect_tcp('synthetic.org',443)=='synthetic-stream'
        assert dialed==[('8.8.8.8',443)]
        for value in ['127.0.0.1','192.168.1.1','169.254.169.254','::1','::ffff:192.168.1.1']:
            answers[:]=['8.8.8.8',value]
            with pytest.raises(httpcore.ConnectError):await PublicNetworkBackend().connect_tcp('synthetic.org',443)
        assert len(dialed)==1
    asyncio.run(scenario())


def test_tracker_caches_are_isolated_by_site_even_with_same_key():
    async def scenario():
        cache=TrackerReads()
        async def nordic():return {'site':'nordic'}
        async def other():return {'site':'other'}
        assert await cache.read({'api_key':'synthetic'},'/api/user',{},nordic,30)=={'site':'nordic'}
        assert await cache.read({'api_key':'synthetic','base_url':'https://sagmire.org'},'/api/user',{},other,30)=={'site':'other'}
        await cache.close()
    asyncio.run(scenario())


def test_other_tracker_account_search_test_and_import_survive_upgrade(tmp_path,monkeypatch):
    from mediaseed import features
    original=httpx.AsyncClient;calls=[];redirect=False
    def remote(request):
        calls.append(request)
        assert request.url.host in {'nordicbytes.org','sagmire.org'}
        if request.url.path.startswith('/api/'):
            assert request.headers['Authorization'].startswith('Bearer synthetic')
            if redirect:return httpx.Response(302,headers={'Location':'https://nordicbytes.org/api/user'})
            if request.url.path=='/api/user':return httpx.Response(200,json={'username':'synthetic-user','stats':{'ratio':2,'warnings':{}},'autogroup':{'title':'Member'}})
            row={'id':'12','attributes':{'name':'Synthetic release','size':42,'info_hash':next(iter(source_hashes(content=b'd4:infod4:name4:testee'))),'created_at':'2026-10-19T00:00:00Z','download_link':'https://sagmire.org/torrent/download/12/synthetic-passkey'}}
            return httpx.Response(200,json={'data':[row] if request.url.path.endswith('filter') else row})
        assert 'Authorization' not in request.headers
        return httpx.Response(200,content=b'd4:infod4:name4:testee')
    def factory(**kwargs):
        kwargs.setdefault('transport',httpx.MockTransport(remote));return original(**kwargs)
    monkeypatch.setattr(features.httpx,'AsyncClient',factory)
    def qbit(request):
        return httpx.Response(204)
    with TestClient(create_app(tmp_path,'http://testserver',qbit_password='synthetic',transport=httpx.MockTransport(qbit))) as client:
        sign_in(client)
        old=client.post('/api/trackers',json={'name':'Existing','api_key':'synthetic'}).json()['id']
        with sqlite3.connect(tmp_path/'mediaseed.sqlite') as db:
            payload=json.loads(db.execute("SELECT payload FROM settings WHERE kind='trackers' AND id=?",(old,)).fetchone()[0]);payload.pop('base_url',None)
            db.execute("UPDATE settings SET payload=? WHERE kind='trackers' AND id=?",(json.dumps(payload),old))
        body={'name':'Sagmire','provider':'unit3d','base_url':'https://sagmire.org/','api_key':'synthetic'}
        tested=client.post('/api/trackers/test',json=body)
        assert tested.status_code==200 and tested.json()['connected']
        assert len(client.get('/api/trackers').json())==1 # test does not save
        identifier=client.post('/api/trackers',json=body).json()['id']
        accounts=client.get('/api/trackers').json()
        other=next(a for a in accounts if a['id']==identifier)
        assert other['base_url']=='https://sagmire.org' and other['rankProgress'] is None
        assert next(a for a in accounts if a['id']==old)['rankProgress'] is not None
        assert all('api_key' not in a for a in accounts)
        assert client.get(f'/api/trackers/{identifier}/search').json()['results'][0]['name']=='Synthetic release'
        assert client.post(f'/api/trackers/{identifier}/torrents/12',json={'ratio':1}).status_code==200
        assert client.put(f'/api/trackers/{identifier}',json={**body,'base_url':'https://another.org','api_key':''}).status_code==422
        redirect=True
        assert client.post('/api/trackers/test',json={**body,'api_key':'synthetic-new'}).status_code==502
        assert not any(r.url.host=='another.org' for r in calls)
    # Opening an existing installation preserves both sites and credentials.
    with TestClient(create_app(tmp_path,'http://testserver')) as client:
        assert client.post('/api/auth/login',json={'username':'tester','password':PASSWORD}).status_code==200
        listed=client.get('/api/trackers').json()
        assert {a['id'] for a in listed}=={old,identifier}
        assert next(a for a in listed if a['id']==identifier)['base_url']=='https://sagmire.org'
