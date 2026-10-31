import json
import httpx
import pytest
from fastapi.testclient import TestClient
from mediaseed.app import create_app
from test_app import sign_in
HASH='a'*40


@pytest.mark.parametrize('state',['queuedDL','downloading','forcedDL','stalledDL','metaDL','forcedMetaDL','stoppedDL','pausedDL'])
def test_download_can_pause_before_seeding_goal_without_erasing_cleanup(tmp_path,state):
    calls=[]
    def handler(request):
        if request.url.path.endswith('/login'):return httpx.Response(204)
        calls.append(request)
        if request.url.path.endswith('/info'):return httpx.Response(200,json=[{'hash':HASH,'state':state,'progress':.2}])
        if request.url.path.endswith('/properties'):pytest.fail('Incomplete download must not require seeding properties')
        return httpx.Response(204)
    app=create_app(tmp_path,'http://testserver',qbit_password='synthetic',transport=httpx.MockTransport(handler))
    with TestClient(app) as client:
        sign_in(client)
        # Retain the policy, but cancel only its unfulfilled automatic-start intent.
        import sqlite3
        record={'id':'rule','hashes':[HASH],'mode':'time','hours':50,'ratio':1,'delete_files':True,'start':True,'status':'waiting','tag':'unchanged'}
        with sqlite3.connect(tmp_path/'mediaseed.sqlite') as db:db.execute('INSERT INTO torrent_cleanup VALUES (?,?)',('rule',json.dumps(record)))
        r=client.post('/api/torrents/stop',json={'torrent_hash':HASH});assert r.status_code==200,r.text
        writes=[r for r in calls if r.method=='POST'];assert len(writes)==1
        assert writes[0].url.path.endswith('/torrents/stop')
        assert dict(httpx.QueryParams(writes[0].content))=={'hashes':HASH}
        with sqlite3.connect(tmp_path/'mediaseed.sqlite') as db:actual=json.loads(db.execute('SELECT payload FROM torrent_cleanup').fetchone()[0])
        assert actual=={**record,'start':False}
        client.headers['X-MediaSeed-CSRF']='invalid'
        assert client.post('/api/torrents/stop',json={'torrent_hash':HASH}).status_code==403
        assert len([r for r in calls if r.method=='POST'])==1


@pytest.mark.parametrize('rows',[[],[{'hash':HASH,'state':'queuedDL'}],[{'hash':'b'*40,'state':'downloading','progress':.2}],{},[{'hash':HASH,'state':'queuedDL','progress':True}]])
def test_download_pause_does_not_write_on_missing_or_invalid_observation(tmp_path,rows):
    writes=[]
    def handler(request):
        if request.url.path.endswith('/login'):return httpx.Response(204)
        if request.method=='POST':writes.append(request)
        return httpx.Response(200,json=rows)
    with TestClient(create_app(tmp_path,'http://testserver',qbit_password='synthetic',transport=httpx.MockTransport(handler))) as client:
        sign_in(client);r=client.post('/api/torrents/stop',json={'torrent_hash':HASH})
        assert r.status_code in (404,503);assert writes==[]


def test_mediavpn_pause_uses_only_supported_rpc_fields(tmp_path,monkeypatch):
    import base64
    import mediaseed.app as module
    token=tmp_path/'token';token.write_text('synthetic')
    monkeypatch.setenv('MEDIASEED_VPN_URL','http://mediavpn:18802')
    monkeypatch.setenv('MEDIASEED_VPN_TOKEN_FILE',str(token))
    native=[]
    def handler(request):
        if request.url.path.endswith('/status'):
            return httpx.Response(200,json={'enabled':True,'profileId':'a'*24,'ready':True,'downloadsEnabled':True})
        if request.url.path.endswith('/rpc'):
            body=json.loads(request.content)
            if body['path']=='torrents/info':value=[{'hash':HASH,'state':'queuedDL','progress':0}]
            elif body['path']=='torrents/stop':
                assert body['method']=='POST';assert body['payload']=={'hashes':HASH}
                native.append(body);value=None
            else:pytest.fail('Unexpected RPC '+body['path'])
            raw=json.dumps(value).encode() if value is not None else b''
            return httpx.Response(200,json={'status':200,'body':base64.b64encode(raw).decode()})
        return httpx.Response(200,json={})
    original=httpx.AsyncClient
    monkeypatch.setattr(module.httpx,'AsyncClient',lambda **kw:original(**{**kw,'transport':httpx.MockTransport(handler)}))
    with TestClient(create_app(tmp_path/'state','http://testserver')) as client:
        sign_in(client)
        import sqlite3
        with sqlite3.connect(tmp_path/'state/mediaseed.sqlite') as db:
            db.execute("UPDATE vpn_selection SET provider='mediavpn',profile_id=? WHERE id=1",('a'*24,))
        r=client.post('/api/torrents/stop',json={'torrent_hash':HASH})
        assert r.status_code==200,r.text;assert len(native)==1
