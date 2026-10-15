import asyncio
import sqlite3
import time
from contextlib import contextmanager

import httpx
import pytest
from fastapi.testclient import TestClient
from test_app import sign_in

from mediaseed.app import create_app
from mediaseed.cleanup import source_hashes
from mediaseed.features import FeedSettings
from mediaseed.rss_worker import RssWorker, title_matches

TORRENT = b'd4:infod6:lengthi1024e4:name4:testee'


def test_preview_is_authenticated_read_only_and_does_not_fetch_magnet_urls(tmp_path):
    with TestClient(create_app(tmp_path, 'http://testserver')) as client:
        assert client.post('/api/torrent-preview', content=TORRENT).status_code == 401
        sign_in(client)
        response = client.post('/api/torrent-preview', content=TORRENT, headers={'Content-Type': 'application/x-bittorrent'})
        assert response.status_code == 200 and response.json() == {'size': 1024, 'name': 'test', 'estimates': []}
        assert client.post('/api/torrent-preview', json={'magnet': 'https://example.invalid/private'}).status_code == 422
        response = client.post('/api/torrent-preview', json={'magnet': 'magnet:?xt=urn:btih:' + 'a'*40 + '&xs=https://example.invalid/private'})
        assert response.status_code == 200 and response.json()['size'] is None
        assert client.post('/api/torrent-preview', content=b'bad', headers={'Content-Type': 'application/x-bittorrent'}).status_code == 422


def test_metadata_size_and_filters_are_bounded_and_normalized():
    assert source_hashes(content=TORRENT, include_metadata=True)['size'] == 1024
    assert title_matches('Release_2160P.REMUX', {'include':['2160p remux']})
    assert not title_matches('Release 2160p CAM', {'include':['2160p'], 'exclude':['CAM']})
    with pytest.raises(ValueError):
        FeedSettings(name='Test', include=['...'])
    with pytest.raises(ValueError):
        FeedSettings(name='Test', badges=['unrecognized'])


def test_feed_rules_persist_and_blank_url_keeps_private_address(tmp_path):
    app = create_app(tmp_path, 'http://testserver')
    with TestClient(app) as client:
        sign_in(client)
        source = {'name':'Movies', 'url':'https://nordicbytes.org/rss/private-passkey', 'include':['2160p'], 'exclude':['CAM'], 'max_bytes':1024, 'badges':['freeleech']}
        response = client.post('/api/feeds', json=source)
        assert response.status_code == 200
        identifier = response.json()['id']
        assert client.put('/api/feeds/'+identifier, json={**source,'url':''}).status_code == 200
        cookies = client.cookies
        public = client.get('/api/feeds').json()[0]
        assert 'private-passkey' not in str(public) and public['include']==['2160p'] and public['paused']
    with TestClient(create_app(tmp_path, 'http://testserver')) as client:
        client.cookies.update(cookies)
        public = client.get('/api/feeds').json()[0]
        assert public['max_bytes']==1024 and public['badges']==['freeleech']
        assert client.get('/api/feeds/'+identifier+'/history').json()['entries']==[]
    with sqlite3.connect(tmp_path/'mediaseed.sqlite') as connection:
        assert 'private-passkey' in connection.execute("SELECT payload FROM settings WHERE id=?",(identifier,)).fetchone()[0]


@pytest.mark.parametrize('scenario', ['legacy', 'title', 'size', 'badges_unknown', 'badges_confirmed'])
def test_feed_filters_history_and_existing_upgrade_preserve_intent(tmp_path, scenario):
    @contextmanager
    def database():
        with sqlite3.connect(tmp_path/'test.sqlite') as connection:
            yield connection
    with database() as connection:
        connection.execute('CREATE TABLE rss_history (token TEXT PRIMARY KEY, feed TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL, torrent_hash TEXT)')
    feed={'id':'a'*24,'url':'https://nordicbytes.org/rss/private-passkey','ratio':1,'enabled':True}
    if scenario=='title':feed['exclude']=['test']
    if scenario=='size':feed['max_bytes']=1000
    if scenario.startswith('badges'):feed['badges']=['freeleech','double_upload']
    calls=[]
    async def qbit(path, method='GET', payload=None, files=None):
        calls.append(path)
        if path.endswith('/torrent'):return httpx.Response(200,content=TORRENT)
        if path.endswith('/add'):return httpx.Response(200,json={'success_count':1,'failure_count':0,'added_torrent_ids':['b'*40]})
        return httpx.Response(204)
    async def vpn():return {'downloadsEnabled':True}
    async def lookup(identifier,title):
        return {'status':'matched','infoHash':identifier,'checkedAt':time.time(),'freeleech':100,'doubleUpload':True} if scenario=='badges_confirmed' else {'status':'unknown'}
    worker=RssWorker(database,lambda kind:[feed],qbit,vpn);worker.benefits_lookup=lookup
    item={'MediaSeed-RSS-'+feed['id']:{'url':feed['url'],'articles':[{'id':'one','title':'Test release','torrentURL':'https://nordicbytes.org/torrent/download/1/private'}]}}
    asyncio.run(worker.process(feed,item))
    should_add=scenario in {'legacy','badges_confirmed'}
    assert any(path.endswith('/add') for path in calls)==should_add
    restarted=RssWorker(database,lambda kind:[feed],qbit,vpn)
    history=restarted.history(feed['id'])
    assert len(history)==1
    assert 'private' not in str(history)
    if should_add:
        assert history[0]['status']=='added'
        asyncio.run(restarted.process(feed,item))
        assert sum(path.endswith('/add') for path in calls)==1


def test_downloaded_feed_history_is_paginated_deduplicated_and_keeps_credit(tmp_path):
    from mediaseed import feed_uploads
    app=create_app(tmp_path,'http://testserver')
    with TestClient(app) as client:
        sign_in(client)
        identifier=client.post('/api/feeds',json={'name':'Feed','url':'https://nordicbytes.org/rss/private'}).json()['id']
        with sqlite3.connect(tmp_path/'mediaseed.sqlite') as db:
            for index in range(28):
                feed_uploads.record(db,identifier,f'{index:040x}',f'Release {index}',index)
                db.execute('UPDATE rss_downloads SET credited=100,last_value=100 WHERE feed=? AND hash=?',(identifier,f'{index:040x}'))
            db.execute('INSERT INTO rss_observations VALUES (?,?,?,?,?)',(identifier,'skipped','Filtered release','title_filtered',100))
        page=client.get(f'/api/feeds/{identifier}/history').json()
        assert len(page['entries'])==25 and page['total']==28
        assert page['upload']['creditedUploadedBytes']==2800
        assert all(row['status']=='added' for row in page['entries'])
        assert 'Filtered release' not in str(page)
        assert len(client.get(f'/api/feeds/{identifier}/history?page=2').json()['entries'])==3
        assert client.get('/api/feeds').json()[0]['upload']['creditedUploadedBytes']==2800
        assert client.get(f'/api/feeds/{identifier}/history?page=0').status_code==422
