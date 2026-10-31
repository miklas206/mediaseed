import asyncio,json,sqlite3
from pathlib import Path
from contextlib import contextmanager
from types import SimpleNamespace
import httpx,pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from mediaseed.app import create_app
from mediaseed.storage import Storage
from mediaseed.uploader_bridge import UploaderBridge
from mediaseed.upload_seeding import UploadSeeding,torrent_payload
from mediaseed.upload_engine.upload_helper import make_torrent,decode_torrent,bencode
from test_uploader import login

IDENTIFIER='a'*24
ACCOUNT={'id':'test','provider':'nordicbytes','name':'Test','api_key':'synthetic-key'}


def setup(tmp_path):
    root=tmp_path/'downloads';folder=root/'Movie';folder.mkdir(parents=True)
    video=folder/'Movie.mkv';video.write_bytes(b'video'*30000);nfo=folder/'Movie.nfo';nfo.write_text('Synthetic NFO')
    content=make_torrent([(video,video.name),(nfo,nfo.name)],folder.name,'https://t.nordicbytes.org/announce/synthetic-passkey')
    tree=decode_torrent(content);tree[b'info'][b'source']=b'NordicBytes';canonical=bencode(tree);hash_=torrent_payload(canonical)[1]
    @contextmanager
    def database():
        with sqlite3.connect(tmp_path/'main.sqlite') as db:yield db
    bridge=UploaderBridge(database,None,Storage(root))
    job={'state':'done','hasTorrent':True,'videos':[{'file':video.name}],'source':{'singleFile':False,'clientSavePath':'/downloads','clientContentPath':'/downloads/Movie'}}
    async def job_read(identifier):return job
    async def upstream(path):return httpx.Response(200,content=content)
    bridge.job=job_read;bridge.upstream=upstream
    state={'row':None,'calls':[],'vpn':False,'filesProgress':1}
    async def qbit(path,method='GET',payload=None,files=None):
        state['calls'].append((path,method,payload))
        if path.endswith('/info'):return httpx.Response(200,json=[state['row']] if state['row'] else [])
        if path.endswith('/add'):
            state['row']={'hash':hash_,'save_path':'/downloads','state':'stoppedDL','progress':0}
            return httpx.Response(200,text='Ok.')
        if path.endswith('/files'):return httpx.Response(200,json=[{'progress':state['filesProgress']},{'progress':state['filesProgress']}])
        if path.endswith('/recheck'):state['row'].update(state='checkingUP',progress=0)
        if path.endswith('/stop') and state['row']:state['row']['state']='stoppedUP' if state['row']['progress']==1 else 'stoppedDL'
        if path.endswith('/start'):state['row']['state']='uploading'
        return httpx.Response(200,text='Ok.')
    async def vpn():return {'downloadsEnabled':state['vpn']}
    manager=UploadSeeding(bridge,qbit,vpn,tmp_path)
    return SimpleNamespace(manager=manager,bridge=bridge,canonical=canonical,state=state,qbit=qbit,job=job,hash=hash_,video=video)


async def checked(fixture):
    f=fixture;await f.manager.enqueue(IDENTIFIER,ACCOUNT,'',f.canonical)
    for _ in range(5):await f.manager.tick()
    assert f.manager.read(IDENTIFIER)['status']=='checking'
    assert f.manager.read(IDENTIFIER)['checkObserved']
    f.state['row'].update(state='stoppedUP',progress=1)
    await f.manager.tick()


def test_manual_seed_checks_every_file_and_waits_for_vpn_before_starting(tmp_path):
    async def run():
        f=setup(tmp_path);original=f.video.read_bytes()
        await checked(f)
        assert f.manager.read(IDENTIFIER)['status']=='waiting_vpn'
        await f.manager.tick()
        assert not any(path.endswith('/start') for path,_,_ in f.state['calls'])
        f.state['vpn']=True;await f.manager.tick();await f.manager.tick()
        assert f.manager.read(IDENTIFIER)['status']=='seeding'
        assert f.video.read_bytes()==original
        assert sum(path.endswith('/add') for path,_,_ in f.state['calls'])==1
        assert (await f.manager.enqueue(IDENTIFIER,ACCOUNT,'',f.canonical))['status']=='seeding'
        assert sum(path.endswith('/add') for path,_,_ in f.state['calls'])==1
        assert (f.manager.root/(IDENTIFIER+'.torrent')).stat().st_mode&0o777==0o600
        await f.bridge.close()
    asyncio.run(run())


@pytest.mark.parametrize('problem',['missing-piece','file-changed','wrong-map','unverified-check'])
def test_no_start_or_download_when_checks_are_incomplete(tmp_path,problem):
    async def run():
        f=setup(tmp_path);f.state['vpn']=True
        await checked(f)
        if problem=='missing-piece':
            f.manager.save(IDENTIFIER,{**f.manager.read(IDENTIFIER),'status':'checking'})
            f.state['filesProgress']=.5
        elif problem=='file-changed':f.video.write_bytes(b'changed')
        elif problem=='wrong-map':f.state['row']['save_path']='/downloads/other'
        else:
            value=f.manager.read(IDENTIFIER);value.update(status='checking',checkObserved=False,newTorrent=False,checkAt=1);f.manager.save(IDENTIFIER,value)
        await f.manager.tick()
        assert f.manager.read(IDENTIFIER)['status']=='failed'
        assert not any(path.endswith('/start') for path,_,_ in f.state['calls'])
        await f.bridge.close()
    asyncio.run(run())


@pytest.mark.parametrize('change',['pieces','announce','traversal'])
def test_foreign_or_mismatched_tracker_torrent_is_rejected_before_add(tmp_path,change):
    async def run():
        f=setup(tmp_path);tree=decode_torrent(f.canonical)
        if change=='pieces':tree[b'info'][b'pieces']=b'x'*len(tree[b'info'][b'pieces'])
        elif change=='announce':tree[b'announce']=b'https://foreign.invalid/announce/secret'
        else:tree[b'info'][b'files'][0][b'path']=[b'..',b'Movie.mkv']
        with pytest.raises(HTTPException):await f.manager.enqueue(IDENTIFIER,ACCOUNT,'',bencode(tree))
        assert not f.state['calls']
        await f.bridge.close()
    asyncio.run(run())


def test_tracker_link_uses_only_read_requests_and_cannot_redirect(tmp_path):
    async def run():
        f=setup(tmp_path);calls=[]
        async def tracker(account,path,method='GET',**kwargs):
            calls.append((path,method))
            if path.startswith('/api/torrents/'):
                return httpx.Response(200,json={'data':{'attributes':{'download_link':'https://nordicbytes.org/torrents/download/123.synthetic'}}})
            return httpx.Response(200,content=f.canonical)
        f.bridge.tracker_request=tracker
        result=await f.manager.enqueue(IDENTIFIER,ACCOUNT,'https://nordicbytes.org/torrents/123',None)
        assert result['status']=='queued' and all(method=='GET' for _,method in calls)
        with pytest.raises(HTTPException):await f.manager.enqueue(IDENTIFIER,ACCOUNT,'https://evil.invalid/torrents/123',None)
        assert len(calls)==2
        await f.bridge.close()
    asyncio.run(run())


def test_restart_preserves_pending_check_and_files(tmp_path):
    async def run():
        f=setup(tmp_path);await checked(f)
        restarted=UploadSeeding(f.bridge,f.qbit,f.manager.vpn_status,tmp_path)
        assert restarted.read(IDENTIFIER)['status']=='waiting_vpn'
        f.state['vpn']=True;await restarted.tick();await restarted.tick()
        assert restarted.read(IDENTIFIER)['status']=='seeding'
        await f.bridge.close()
    asyncio.run(run())


def test_manual_seed_route_needs_auth_csrf_and_explicit_publication_confirmation(tmp_path):
    f=setup(tmp_path);app=create_app(tmp_path/'app','http://testserver',downloads_dir=tmp_path/'downloads')
    app.state.upload_seeding.bridge.job=f.bridge.job;app.state.upload_seeding.bridge.upstream=f.bridge.upstream
    app.state.upload_seeding.qbit=f.qbit
    with app.state.uploader_bridge.database() as db:db.execute('INSERT INTO settings VALUES(?,?,?)',('trackers','test',json.dumps(ACCOUNT)))
    with TestClient(app) as client:
        assert client.get('/api/uploader/jobs/'+IDENTIFIER+'/seeding').status_code==401
        csrf=login(client);form={'tracker':'test','confirmed':'true'}
        assert client.post('/api/uploader/jobs/'+IDENTIFIER+'/seed-file',params=form,content=f.canonical,headers={'Content-Type':'application/x-bittorrent'}).status_code==403
        client.headers['X-MediaSeed-CSRF']=csrf
        assert client.post('/api/uploader/jobs/'+IDENTIFIER+'/seed-file',params={'tracker':'test'},content=f.canonical,headers={'Content-Type':'application/x-bittorrent'}).status_code==422
        response=client.post('/api/uploader/jobs/'+IDENTIFIER+'/seed-file',params=form,content=f.canonical,headers={'Content-Type':'application/x-bittorrent'})
        assert response.status_code==202,response.text
        assert response.json()['status']=='queued'
        assert client.get('/api/uploader/status').json()['manualUpload'] is True
    asyncio.run(f.bridge.close())


def test_new_torrent_can_finish_its_automatic_check_between_poll_intervals(tmp_path):
    async def run():
        f=setup(tmp_path);f.state['vpn']=True
        await f.manager.enqueue(IDENTIFIER,ACCOUNT,'',f.canonical)
        for _ in range(4):await f.manager.tick()
        assert f.manager.read(IDENTIFIER)['status']=='checking'
        # Fresh addition had no resume data; client verification can finish quickly.
        f.state['row'].update(state='stoppedUP',progress=1)
        await f.manager.tick();await f.manager.tick();await f.manager.tick()
        assert f.manager.read(IDENTIFIER)['status']=='seeding'
        await f.bridge.close()
    asyncio.run(run())


def test_manual_mode_blocks_legacy_api_publication_even_from_cached_old_ui(tmp_path):
    app=create_app(tmp_path,'http://testserver')
    with TestClient(app) as client:
        csrf=login(client);client.headers['X-MediaSeed-CSRF']=csrf
        response=client.post('/api/uploader/jobs/'+IDENTIFIER+'/publish',json={'tracker':'test','category_id':1,'type_id':14,'resolution_id':2,'confirmed':True})
        assert response.status_code==409 and 'manuelt' in response.json()['detail']
        with app.state.uploader_bridge.database() as db:assert db.execute('SELECT COUNT(*) FROM uploader_publications').fetchone()[0]==0


@pytest.mark.parametrize('host',['t.nordicswarm.org','t.polarswarm.org'])
def test_nordicbytes_canonical_announce_aliases_keep_account_and_payload_checks(tmp_path,host):
    async def run():
        f=setup(tmp_path);tree=decode_torrent(f.canonical)
        tree[b'announce']=('https://'+host+'/announce/synthetic-passkey').encode()
        tree[b'announce-list']=[[('https://'+h+'/announce/synthetic-passkey').encode()] for h in ['t.nordicswarm.org','t.nordicbytes.org','t.polarswarm.org']]
        result=await f.manager.enqueue(IDENTIFIER,ACCOUNT,'',bencode(tree))
        assert result['status']=='queued'
        assert not any(method=='POST' for _,method,_ in f.state['calls'])
        await f.bridge.close()
    asyncio.run(run())


@pytest.mark.parametrize('address',[
    'https://t.nordicswarm.org/announce/other-account',
    'http://t.nordicswarm.org/announce/synthetic-passkey',
    'https://t.nordicswarm.org:444/announce/synthetic-passkey',
    'https://t.nordicswarm.org/announce/synthetic-passkey?other=1',
    'https://t.nordicswarm.org.evil.invalid/announce/synthetic-passkey',
    'https://user@t.nordicswarm.org/announce/synthetic-passkey',
    'https://t.nordicswarm.org/announce/synthetic-passkey#ignored',
])
def test_alias_validation_rejects_foreign_keys_ports_and_addresses(tmp_path,address):
    async def run():
        f=setup(tmp_path);tree=decode_torrent(f.canonical)
        tree[b'announce-list']=[[address.encode()]]
        with pytest.raises(HTTPException):await f.manager.enqueue(IDENTIFIER,ACCOUNT,'',bencode(tree))
        assert not f.state['calls']
        await f.bridge.close()
    asyncio.run(run())



def test_manual_seed_add_uses_supported_mediavpn_upload_parameters(tmp_path):
    async def run():
        f=setup(tmp_path);original=f.manager.qbit
        async def validated(path,method='GET',payload=None,files=None):
            if path.endswith('/add'):
                # MediaVPN's upload endpoint permits cleanup IDs or an empty tag.
                import re
                assert re.fullmatch(r'(?:MediaSeed-Cleanup-[a-f0-9]{24})?',payload.get('tags',''))
                assert payload['paused']=='true' and payload['stopped']=='true'
                assert payload['savepath']=='/downloads'
            return await original(path,method,payload,files)
        f.manager.qbit=validated
        await f.manager.enqueue(IDENTIFIER,ACCOUNT,'',f.canonical)
        await f.manager.tick()
        assert f.manager.read(IDENTIFIER)['status']=='awaiting_client'
        assert not any(path.endswith('/start') for path,_,_ in f.state['calls'])
        await f.bridge.close()
    asyncio.run(run())
