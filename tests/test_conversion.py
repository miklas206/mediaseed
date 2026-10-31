"""Only synthetic files/transports. No torrent writes are needed for conversion."""
import asyncio
from contextlib import contextmanager
import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from mediaseed.app import create_app
from mediaseed.conversion import Conversions, ConversionChoice, atomic, fingerprint
from mediaseed.conversion_worker import process, STOP
from mediaseed.storage import Storage
from mediaseed.conversion_naming import output_name

HASH = 'a'*40


@pytest.fixture
def setup(tmp_path):
    root=tmp_path/'downloads';root.mkdir()
    release=root/'Release';release.mkdir()
    (release/'Film.mkv').write_bytes(b'x'*1000)
    (release/'Film.da.srt').write_bytes(b'subtitle original')
    queue=tmp_path/'queue';queue.mkdir()
    atomic(queue/'worker.json',{'ready':True,'at':time.time()})
    @contextmanager
    def database():
        with sqlite3.connect(tmp_path/'jobs.db') as db:yield db
    row={'hash':HASH,'name':'Synthetic film','progress':1,'state':'uploading','save_path':'/downloads','added_on':123}
    calls=[]
    async def qbit(path,method='GET',payload=None,files=None):
        calls.append((path,method,payload))
        assert method=='GET','Conversion must never change qBittorrent files or policies'
        return httpx.Response(200,json=[row] if path.endswith('/info') else [{'name':'Release/Film.mkv','size':1000,'progress':1}])
    cleanup=Mock(lock=asyncio.Lock())
    return Conversions(database,qbit,Storage(root),cleanup,queue),root,queue,row,calls,database


def choice(**changes):
    return ConversionChoice(**({'torrent_hash':HASH,'indexes':[0],'bitrate':20,'location':'local'}|changes))


def test_create_upgrade_recovery_preserves_original_policy_and_only_queues_selected_video(setup):
    manager,root,queue,row,calls,database=setup
    job=asyncio.run(manager.create(choice()))
    assert job['originalsRetained'] and job['fileCount']==1
    assert (root/'Release/Film.mkv').read_bytes()==b'x'*1000
    record=manager.records()[0]
    assert record['companions'][0]['name']=='Release/Film.da.srt'
    # Existing DB/schema upgraded idempotently; queued intent survives restart.
    recreated=Conversions(database,manager.qbit,manager.storage,manager.cleanup,queue)
    assert recreated.records()[0]==record
    (queue/(job['id']+'.request.json')).unlink()
    asyncio.run(recreated.tick())
    assert (queue/(job['id']+'.request.json')).exists()
    for action in ('delete','setLocation','recheck','renameFile'):
        with pytest.raises(HTTPException):manager.guard('/api/v2/torrents/'+action,{'hashes':HASH})
    manager.guard('/api/v2/torrents/stop',{'hashes':HASH})
    assert all(method=='GET' for _,method,_ in calls)


def result(manager,queue,size=400):
    record=manager.records()[0];out=Path(record['work'])/'result';out.mkdir()
    video=out/output_name(record['files'][0]);video.parent.mkdir();video.write_bytes(b'y'*size)
    subtitle=out/record['companions'][0].get('output', 'Release/Film.da.srt');subtitle.write_bytes(b'subtitle original')
    atomic(out/'.mediaseed-converted.json',{'id':record['id'],'verified':True,'files':[video.relative_to(out).as_posix()],
        'identities':{video.relative_to(out).as_posix():fingerprint(video)},'companionIdentities':{subtitle.relative_to(out).as_posix():fingerprint(subtitle)}})
    atomic(queue/(record['id']+'.status.json'),{'id':record['id'],'state':'ready','progress':100})
    return record,out


def test_atomic_publication_restart_and_independent_subtitles(setup):
    manager,root,queue,*_=setup
    asyncio.run(manager.create(choice()));record,out=result(manager,queue)
    # Simulate crash just after atomic directory publication.
    out.rename(record['destination'])
    asyncio.run(manager.tick())
    public=manager.public(manager.records()[0])
    assert public['status']=='complete' and public['potentialSavingBytes']==600
    assert (root/'Release/Film.mkv').stat().st_size==1000
    target=Path(record['destination'])/record['companions'][0]['output']
    target.write_bytes(b'edited library subtitles')
    assert (root/'Release/Film.da.srt').read_bytes()==b'subtitle original'
    manager.guard('/api/v2/torrents/delete',{'hashes':HASH})


@pytest.mark.parametrize('failure',['source-changed','output-changed','no-saving','collision'])
def test_failed_verification_never_overwrites_or_deletes_originals(setup,failure):
    manager,root,queue,*_=setup
    asyncio.run(manager.create(choice()));record,out=result(manager,queue,size=1200 if failure=='no-saving' else 400)
    if failure=='source-changed':(root/'Release/Film.mkv').write_bytes(b'z'*1000)
    if failure=='output-changed':(out/output_name(record['files'][0])).write_bytes(b'z'*400)
    if failure=='collision':
        target=Path(record['destination']);target.mkdir();(target/'user-data').write_text('keep')
    asyncio.run(manager.tick())
    assert manager.records()[0]['status']=='error'
    assert (root/'Release/Film.mkv').exists() and out.exists()
    if failure=='collision':assert (Path(record['destination'])/'user-data').read_text()=='keep'
    with pytest.raises(HTTPException):manager.guard('/api/v2/torrents/delete',{'hashes':HASH})


def test_unready_space_incomplete_unsafe_and_duplicate_requests_fail_closed(setup,monkeypatch):
    manager,root,queue,row,*_=setup
    atomic(queue/'worker.json',{'ready':True,'at':0})
    with pytest.raises(HTTPException):asyncio.run(manager.create(choice()))
    assert not manager.records()
    atomic(queue/'worker.json',{'ready':True,'at':time.time()})
    with pytest.raises(HTTPException):asyncio.run(manager.create(choice(indexes=[0,0])))
    with pytest.raises(HTTPException):asyncio.run(manager.create(choice(folder='Release')))
    row['progress']=.5
    with pytest.raises(HTTPException):asyncio.run(manager.create(choice()))
    row['progress']=1
    monkeypatch.setattr('mediaseed.conversion.shutil.disk_usage',lambda _:Mock(free=1))
    with pytest.raises(HTTPException):asyncio.run(manager.create(choice()))
    assert not manager.records()


def test_worker_restart_verification_cancel_and_scope(setup,monkeypatch):
    manager,root,queue,*_=setup
    asyncio.run(manager.create(choice()));job=manager.records()[0]
    out=Path(job['work'])/'result';out.mkdir();video=out/output_name(job['files'][0]);video.parent.mkdir();video.write_bytes(b'y'*400)
    # Resume verifies existing output rather than launching an encoder again.
    checked=[]
    monkeypatch.setattr('mediaseed.conversion_worker.probe',lambda path:str(path))
    monkeypatch.setattr('mediaseed.conversion_worker.validate',lambda a,b,**kwargs:checked.append((a,b)))
    process(queue,job,[root])
    assert len(checked)==1
    assert json.loads((queue/(job['id']+'.status.json')).read_text())['state']=='ready'
    assert (out/job['companions'][0]['output']).read_bytes()==b'subtitle original'
    assert (out/job['companions'][0]['output']).stat().st_ino!=(root/'Release/Film.da.srt').stat().st_ino
    atomic(queue/(job['id']+'.cancel'),{'id':job['id']})
    process(queue,job,[root]);asyncio.run(manager.tick())
    assert manager.records()[0]['status']=='cancelled' and (root/'Release/Film.mkv').exists()
    assert not Path(job['work']).exists()
    with pytest.raises(ValueError):process(queue,{**job,'work':str(root/'Release')},[root])


def test_queued_job_can_be_cancelled_even_with_worker_offline(setup):
    manager,root,queue,*_=setup
    job=asyncio.run(manager.create(choice()))
    (queue/'worker.json').unlink()
    result=asyncio.run(manager.cancel(job['id']))
    assert result['status']=='cancelled' and (root/'Release/Film.mkv').exists()
    manager.guard('/api/v2/torrents/delete',{'hashes':HASH})


def test_nested_torrent_and_destination_use_root_staging_and_separate_library(setup,monkeypatch):
    manager,root,queue,row,*_=setup
    row['save_path']='/downloads/Release'
    async def nested(path,method='GET',payload=None,files=None):
        assert method=='GET'
        return httpx.Response(200,json=[row] if path.endswith('/info') else [{'name':'Film.mkv','size':1000,'progress':1}])
    manager.qbit=nested
    destination=root/'Library/Subfolder';destination.mkdir(parents=True)
    asyncio.run(manager.create(choice(folder='Library/Subfolder')))
    job=manager.records()[0]
    work=Path(job['work']);target=Path(job['destination'])
    assert work.parent==root/'.incomplete'
    assert target.parent==destination/'Konverteret'
    assert not target.is_relative_to(root/'Release')
    out=work/'result';out.mkdir();(out/output_name(job['files'][0])).write_bytes(b'y'*400)
    monkeypatch.setattr('mediaseed.conversion_worker.probe',lambda p:str(p))
    monkeypatch.setattr('mediaseed.conversion_worker.validate',lambda a,b,**kwargs:None)
    process(queue,job,[root])
    asyncio.run(manager.tick())
    assert manager.records()[0]['status']=='complete'
    assert (target/output_name(job['files'][0])).stat().st_size==400
    assert (root/'Release/Film.mkv').stat().st_size==1000


def test_rejects_destination_inside_single_file_torrents_own_folder(setup):
    manager,root,queue,row,*_=setup
    row['save_path']='/downloads/Release'
    async def nested(path,method='GET',payload=None,files=None):
        return httpx.Response(200,json=[row] if path.endswith('/info') else [{'name':'Film.mkv','size':1000,'progress':1}])
    manager.qbit=nested
    with pytest.raises(HTTPException,match='outside the original torrent folder'):
        asyncio.run(manager.create(choice(folder='Release')))
    assert not manager.records()


def test_cancelled_legacy_nested_job_is_acknowledged_without_retry_loop(setup,monkeypatch):
    import threading
    from mediaseed import conversion_worker as worker
    manager,root,queue,*_=setup
    job=asyncio.run(manager.create(choice()))
    record=manager.records()[0]
    record['work']=str(root/'Release/.incomplete'/('mediaseed-conversion-'+record['id']))
    atomic(queue/(record['id']+'.request.json'),record)
    atomic(queue/(record['id']+'.status.json'),{'id':record['id'],'state':'error','progress':0})
    atomic(queue/(record['id']+'.cancel'),{'id':record['id']})
    stop=threading.Event()
    monkeypatch.setattr(worker,'STOP',stop)
    monkeypatch.setattr(worker,'gpu_test',lambda:None)
    monkeypatch.setattr(worker.threading,'Thread',Mock())
    monkeypatch.setenv('MEDIASEED_CONVERSION_QUEUE',str(queue))
    monkeypatch.setenv('MEDIASEED_CONVERSION_ROOTS',json.dumps([str(root)]))
    monkeypatch.setattr(stop,'wait',lambda delay:stop.set())
    worker.main()
    assert json.loads((queue/(record['id']+'.status.json')).read_text())['state']=='cancelled'
    assert not Path(record['work']).exists()
    assert (root/'Release/Film.mkv').stat().st_size==1000


def test_endpoints_require_login_csrf_and_show_missing_requirement(tmp_path):
    with TestClient(create_app(tmp_path,'http://testserver')) as client:
        assert client.get('/api/conversions').status_code==401
        assert client.post('/api/auth/setup',json={'username':'tester','password':'synthetic-password-only'}).status_code==200
        login=client.post('/api/auth/login',json={'username':'tester','password':'synthetic-password-only'})
        response=client.get('/api/conversions').json()
        assert response['ready'] is False and 'NVIDIA' in response['reason'] and response['jobs']==[]
        assert client.post('/api/conversions',json=choice().model_dump()).status_code==403
        client.headers['X-MediaSeed-CSRF']=login.json()['csrf']
        assert client.post('/api/conversions',json=choice().model_dump()).status_code==409


def test_legacy_queued_job_retains_names_and_can_publish_after_upgrade(setup,monkeypatch):
    manager,root,queue,*_=setup
    asyncio.run(manager.create(choice()))
    record=manager.records()[0]
    record.pop('releaseTag')
    for entry in record['files']+record['companions']:entry.pop('output')
    manager.save(record)
    out=Path(record['work'])/'result';(out/'Release').mkdir(parents=True)
    (out/'Release/Film.mkv').write_bytes(b'y'*400)
    monkeypatch.setattr('mediaseed.conversion_worker.probe',lambda p:str(p))
    monkeypatch.setattr('mediaseed.conversion_worker.validate',lambda a,b,**kwargs:None)
    process(queue,record,[root]);asyncio.run(manager.tick())
    assert manager.records()[0]['status']=='complete'
    assert (Path(record['destination'])/'Release/Film.mkv').is_file()
    assert (root/'Release/Film.mkv').read_bytes()==b'x'*1000


def test_metadata_analysis_is_bounded_read_only_and_reuses_cache(setup,monkeypatch):
    from mediaseed.conversion_advice import analyze_inventory
    manager,root,queue,row,calls,*_=setup
    asyncio.run(manager.analyze())
    assert manager.advice(30)['pending']==1
    probes=[]
    monkeypatch.setattr('mediaseed.conversion_advice.metadata',lambda p:probes.append(p) or {'duration':100,'codec':'hevc','height':2160,'audioBps':640000,'audioEstimated':False})
    analyze_inventory(queue,[root],lambda:False)
    response=manager.advice(30)
    assert response['pending']==0 and len(response['rows'])==1
    analyze_inventory(queue,[root],lambda:False)
    assert len(probes)==1
    assert all(method=='GET' for _,method,_ in calls)
    assert (root/'Release/Film.mkv').read_bytes()==b'x'*1000
    assert not manager.records()
    (root/'Release/Film.mkv').write_bytes(b'z'*1000)
    assert manager.advice(30)['errors']==1


def test_advice_excludes_jobs_already_queued_or_completed(setup,monkeypatch):
    from mediaseed.conversion_advice import analyze_inventory
    manager,root,queue,*_=setup
    asyncio.run(manager.analyze())
    monkeypatch.setattr('mediaseed.conversion_advice.metadata',lambda p:{'duration':100,'codec':'hevc','height':2160,'audioBps':0,'audioEstimated':False})
    analyze_inventory(queue,[root],lambda:False)
    assert len(manager.advice(20)['rows'])==1
    asyncio.run(manager.create(choice()))
    assert not manager.advice(20)['rows']


def test_worker_dispatches_queue_in_creation_order_not_uuid_order(tmp_path,monkeypatch):
    import threading
    from mediaseed import conversion_worker as worker
    queue=tmp_path/'queue';queue.mkdir()
    for ident,created in [('a'*32,200),('f'*32,100)]:
        atomic(queue/(ident+'.request.json'),{'id':ident,'created':created})
    processed=[];stop=threading.Event()
    monkeypatch.setattr(worker,'STOP',stop)
    monkeypatch.setattr(worker,'gpu_test',lambda:None)
    monkeypatch.setattr(worker.threading,'Thread',Mock())
    monkeypatch.setenv('MEDIASEED_CONVERSION_QUEUE',str(queue))
    monkeypatch.setenv('MEDIASEED_CONVERSION_ROOTS','[]')
    monkeypatch.setattr(stop,'wait',lambda delay:stop.set())
    monkeypatch.setattr(worker,'process',lambda queue,job,roots:processed.append(job['id']))
    worker.main()
    assert processed==['f'*32,'a'*32]


def test_advice_endpoints_require_auth_csrf_and_validate_bitrate(tmp_path):
    with TestClient(create_app(tmp_path,'http://testserver')) as client:
        assert client.get('/api/conversions/recommendations').status_code==401
        assert client.post('/api/conversions/analyze',json={}).status_code==401
        client.post('/api/auth/setup',json={'username':'tester','password':'synthetic-password-only'})
        response=client.post('/api/auth/login',json={'username':'tester','password':'synthetic-password-only'})
        assert client.post('/api/conversions/analyze',json={}).status_code==403
        client.headers['X-MediaSeed-CSRF']=response.json()['csrf']
        assert client.post('/api/conversions/analyze',json={}).status_code==409
        assert client.get('/api/conversions/recommendations?bitrate=50').status_code==422
        assert client.get('/api/conversions/recommendations').json()['scanned'] is False


@pytest.mark.parametrize('query,expected',[('',30),('?bitrate=20',20),('?bitrate=30',30),('?bitrate=40',40)])
def test_advice_accepts_real_url_query_values_and_returns_numeric_target(tmp_path,query,expected):
    from mediaseed.conversion_advice import cache_key
    app=create_app(tmp_path/'data','http://testserver')
    queue=tmp_path/'queue';queue.mkdir()
    source=tmp_path/'Film.mkv';source.write_bytes(b'synthetic')
    entry={'hash':HASH,'title':'Synthetic','index':0,'name':'Film.mkv','path':str(source),'identity':fingerprint(source)}
    atomic(queue/'analysis-inventory.json',{'at':time.time(),'files':[entry],'truncated':False})
    atomic(queue/('analysis-'+cache_key(entry)+'.json'),{'metadata':{'duration':100,'codec':'hevc','height':2160,'audioBps':0,'audioEstimated':False}})
    app.state.conversions.queue=queue
    with TestClient(app) as client:
        client.post('/api/auth/setup',json={'username':'tester','password':'synthetic-password-only'})
        client.post('/api/auth/login',json={'username':'tester','password':'synthetic-password-only'})
        response=client.get('/api/conversions/recommendations'+query)
        assert response.status_code==200,response.text
        assert response.json()['rows'][0]['targetMbps']==expected
        for invalid in ('25','50','invalid'):
            assert client.get('/api/conversions/recommendations?bitrate='+invalid).status_code==422


def test_explicit_options_require_new_worker_and_preserve_legacy_jobs(setup):
    manager,root,queue,*_=setup
    with pytest.raises(HTTPException,match='409'):asyncio.run(manager.create(choice(options={'dolby_vision':False})))
    assert not manager.records()
    atomic(queue/'worker.json',{'ready':True,'at':time.time(),'capabilities':['conversion-options-v1']})
    asyncio.run(manager.create(choice(options={'dolby_vision':False,'subtitles':'none'})))
    record=manager.records()[0]
    assert record['options']['dolby_vision'] is False and not record['companions']
    request=json.loads((queue/(record['id']+'.request.json')).read_text())
    assert request['options']==record['options']
    from mediaseed.conversion import ConversionPreferences
    manager.save_preferences('local',ConversionPreferences(options={'dolby_vision':True,'preset':'P4'}))
    assert manager.records()[0]['options']==request['options']
    assert manager.public(record)['options']['dolby_vision'] is False
    assert manager.public({k:v for k,v in record.items() if k!='options'})['options']['dolby_vision'] is True
    assert (root/'Release/Film.da.srt').exists()


def test_preferences_migrate_idempotently_and_are_per_owner(setup):
    from mediaseed.conversion import ConversionPreferences
    manager,root,queue,row,calls,database=setup
    assert manager.preferences('alice')['options']['dolby_vision'] is False
    saved=ConversionPreferences(bitrate=20,options={'dolby_vision':True,'preset':'P4','audio':'languages'})
    manager.save_preferences('alice',saved)
    recreated=Conversions(database,manager.qbit,manager.storage,manager.cleanup,queue)
    assert recreated.preferences('alice')==saved.model_dump()
    assert recreated.preferences('bob')['options']['dolby_vision'] is False
    assert not recreated.records() and not list(queue.glob('*.request.json'))


def test_companions_respect_language_and_attachments_choices():
    from mediaseed.conversion_options import keep_companion
    options={'subtitles':'languages','subtitle_languages':['dan'],'attachments':False}
    assert keep_companion(Path('Film.da.forced.srt'),'Film',options)
    assert not keep_companion(Path('Film.eng.srt'),'Film',options)
    assert not keep_companion(Path('Film.srt'),'Film',options)
    assert not keep_companion(Path('Film.nfo'),'Film',options)
    assert not keep_companion(Path('Film.da.srt'),'Film',{'external_subtitles':False})


def test_publication_rejects_receipt_with_different_options(setup):
    manager,root,queue,*_=setup
    atomic(queue/'worker.json',{'ready':True,'at':time.time(),'capabilities':['conversion-options-v1']})
    asyncio.run(manager.create(choice(options={'dolby_vision':False})))
    record,out=result(manager,queue)
    receipt=out/'.mediaseed-converted.json';data=json.loads(receipt.read_text());data['options']={**record['options'],'dolby_vision':True};atomic(receipt,data)
    asyncio.run(manager.tick())
    assert manager.records()[0]['status']=='error'
    assert not Path(record['destination']).exists() and (root/'Release/Film.mkv').exists()


def test_preferences_api_auth_csrf_validation_and_persistence(tmp_path):
    with TestClient(create_app(tmp_path,'http://testserver')) as client:
        assert client.get('/api/conversions/preferences').status_code==401
        assert client.post('/api/auth/setup',json={'username':'tester','password':'synthetic-password-only'}).status_code==200
        login=client.post('/api/auth/login',json={'username':'tester','password':'synthetic-password-only'})
        value=client.get('/api/conversions/preferences').json()
        assert value['options']['dolby_vision'] is False
        value['options']['preset']='P5'
        assert client.put('/api/conversions/preferences',json=value).status_code==403
        client.headers['X-MediaSeed-CSRF']=login.json()['csrf']
        assert client.put('/api/conversions/preferences',json=value).status_code==200
        assert client.get('/api/conversions/preferences').json()==value
        value['options']['preset']='arbitrary'
        assert client.put('/api/conversions/preferences',json=value).status_code==422


@pytest.mark.parametrize('scenario',['normal','save-fails','collision','interrupted'])
def test_shortened_completed_location_is_reversible_and_preserves_outputs(setup,scenario):
    from mediaseed.conversion_layout import shorten_completed
    from mediaseed.conversion_naming import library_label
    manager,root,queue,*_=setup
    asyncio.run(manager.create(choice()));record,out=result(manager,queue);asyncio.run(manager.tick())
    job=manager.records()[0];current=Path(job['destination'])
    old=current.with_name('Legacy.long.technical.release.title.H265.MDTech ['+job['id'][:8]+']')
    current.rename(old);job['destination']=str(old);manager.save(job)
    target=old.with_name(library_label(job['name'])+' ['+job['id'][:8]+']')
    source=fingerprint(root/'Release/Film.mkv')
    original=fingerprint(old/output_name(job['files'][0]))
    if scenario=='collision':
        target.mkdir();(target/'user-file').write_text('keep')
        with pytest.raises(ValueError,match='already exists'):shorten_completed(job,manager.save)
        assert (target/'user-file').read_text()=='keep' and old.exists()
    elif scenario=='save-fails':
        def failed(_):raise OSError('Synthetic DB write failure')
        with pytest.raises(OSError):shorten_completed(job,failed)
        assert old.exists() and not target.exists() and manager.records()[0]['destination']==str(old)
    else:
        if scenario=='interrupted':old.rename(target)
        updated=shorten_completed(job,manager.save)
        assert updated['destination']==str(target) and not old.exists()
        assert fingerprint(target/output_name(job['files'][0]))==original
        assert shorten_completed(updated,manager.save)==updated
        assert manager.records()[0]['destination']==str(target)
    assert fingerprint(root/'Release/Film.mkv')==source


def test_clear_history_hides_only_terminal_rows_and_keeps_files_and_conversion_proof(tmp_path):
    root=tmp_path/'downloads';root.mkdir();movie=root/'Preserved.mkv';movie.write_bytes(b'converted video')
    app=create_app(tmp_path/'data','http://testserver',downloads_dir=root)
    manager=app.state.conversions
    expected={}
    for index,status in enumerate(['complete','cancelled','error','queued','encoding','verifying','ready']):
        job={'id':str(index)*32,'hash':'a'*40,'created':index+1,'status':status,'name':'Synthetic','bitrate':20,'destination':str(root),'fileCount':1,'files':[{'output':movie.name}],'conversionReceipt':'preserved'}
        manager.save(job);expected[job['id']]=job
    with TestClient(app) as client:
        csrf=login_for_history(client)
        assert client.post('/api/conversions/history/clear',json={}).status_code==403
        client.headers['X-MediaSeed-CSRF']=csrf
        result=client.post('/api/conversions/history/clear',json={});assert result.status_code==200
        assert result.json()=={'cleared':3,'mediaPreserved':True}
        assert {j['status'] for j in manager.visible_records()}=={'queued','encoding','verifying','ready'}
        assert {j['id']:j for j in manager.records()}==expected
        assert client.post('/api/conversions/history/clear',json={}).json()['cleared']==0
        assert movie.read_bytes()==b'converted video'
    # Repeated construction/upgrade preserves the archive and proof records.
    again=create_app(tmp_path/'data','http://testserver',downloads_dir=root).state.conversions
    assert len(again.visible_records())==4 and len(again.records())==7


def login_for_history(client):
    client.post('/api/auth/setup',json={'username':'tester','password':'synthetic-password-only'})
    return client.post('/api/auth/login',json={'username':'tester','password':'synthetic-password-only'}).json()['csrf']


def test_deleted_completed_output_returns_to_advice_without_losing_history(setup):
    manager,root,queue,row,calls,database=setup
    asyncio.run(manager.create(choice()))
    job=manager.records()[0];job['status']='complete';manager.save(job)
    output=Path(job['destination'])/job['files'][0]['output'];output.parent.mkdir(parents=True);output.write_bytes(b'converted')
    assert HASH in manager.advised_excluded()
    manager.clear_history()
    assert not manager.visible_records() and HASH in manager.advised_excluded()
    output.unlink()
    assert HASH not in manager.advised_excluded()
    # Metadata inventory must include the original again on the next analysis.
    result=asyncio.run(manager.analyze());assert result['files']==1
    assert json.loads((queue/'analysis-inventory.json').read_text())['files'][0]['hash']==HASH
    assert manager.records()[0]==job and (root/'Release/Film.mkv').read_bytes()==b'x'*1000


def test_running_or_other_completed_copy_still_excludes_the_original(setup):
    manager,root,queue,row,calls,database=setup
    asyncio.run(manager.create(choice()))
    queued=manager.records()[0];assert HASH in manager.advised_excluded()
    queued['status']='complete';manager.save(queued)
    assert HASH not in manager.advised_excluded()
    another={**queued,'id':'f'*32,'created':queued['created']+1,'destination':str(root/'Renamed output')}
    output=Path(another['destination'])/another['files'][0]['output'];output.parent.mkdir(parents=True);output.write_bytes(b'still present')
    manager.save(another);assert HASH in manager.advised_excluded()


def test_converted_library_uses_root_and_upgrades_pending_jobs_without_restarting_worker(setup,monkeypatch):
    manager,root,queue,row,calls,database=setup
    library=root/'Converted Movies';library.mkdir()
    manager.storage.locations.append({'id':'converted-movies','label':'Konverterede Film','path':'/downloads/Converted Movies'})
    asyncio.run(manager.create(choice(location='converted-movies')))
    job=manager.records()[0]
    assert Path(job['destination']).parent==library
    assert Path(job['destination']).name==Path(output_name(job['files'][0])).stem
    # An older update queued the same job beneath the redundant library level.
    old=library/'Konverteret'/('Synthetic.film ['+job['id'][:8]+']');old.parent.mkdir();job['destination']=str(old);job['status']='encoding';manager.save(job)
    request=(queue/(job['id']+'.request.json'));before=request.read_bytes()
    upgraded=Conversions(database,manager.qbit,manager.storage,manager.cleanup,queue)
    updated=upgraded.records()[0]
    assert Path(updated['destination']).parent==library and updated['status']=='encoding'
    assert updated['work']==job['work'] and updated['files']==job['files']
    assert updated['previousDestinations']==[str(old)] and request.read_bytes()==before
    assert Conversions(database,manager.qbit,manager.storage,manager.cleanup,queue).records()[0]==updated
    assert not old.parent.exists()
    # The unchanged worker request still writes staging; publishing uses the new ledger path.
    out=Path(job['work'])/'result';out.mkdir();converted=out/output_name(job['files'][0]);converted.parent.mkdir(parents=True,exist_ok=True);converted.write_bytes(b'y'*400)
    monkeypatch.setattr('mediaseed.conversion_worker.probe',lambda p:str(p))
    monkeypatch.setattr('mediaseed.conversion_worker.validate',lambda a,b,**kwargs:None)
    process(queue,job,[root,library]);asyncio.run(upgraded.tick())
    complete=upgraded.records()[0]
    assert complete['status']=='complete' and Path(complete['destination']).parent==library
    assert (Path(complete['destination'])/output_name(job['files'][0])).read_bytes()==b'y'*400
    assert not old.exists() and (root/'Release/Film.mkv').read_bytes()==b'x'*1000



def test_output_layout_upgrade_does_not_move_complete_or_colliding_folders(setup):
    from mediaseed.conversion_layout import flatten_pending_library
    manager,root,queue,row,calls,database=setup
    library=root/'Converted Movies';library.mkdir()
    manager.storage.locations.append({'id':'converted-movies','label':'Konverterede Film','path':'/downloads/Converted Movies'})
    asyncio.run(manager.create(choice(location='converted-movies')));job=manager.records()[0]
    old=library/'Konverteret'/Path(job['destination']).name;old.mkdir(parents=True);media=old/'Existing.mkv';media.write_bytes(b'preserved')
    job['destination']=str(old);job['status']='complete';manager.save(job)
    assert flatten_pending_library(manager)==0 and media.read_bytes()==b'preserved'
    job['status']='encoding';manager.save(job)
    assert flatten_pending_library(manager)==0 and media.read_bytes()==b'preserved'


def test_completed_release_folder_rename_preserves_bytes_and_rolls_back_save_failure(setup):
    from mediaseed.conversion_layout import shorten_completed
    manager,root,queue,*_=setup
    asyncio.run(manager.create(choice()));job,out=result(manager,queue);asyncio.run(manager.tick())
    job=manager.records()[0];current=Path(job['destination'])
    old=current.with_name('Synthetic ['+job['id'][:8]+']');current.rename(old);job['destination']=str(old);manager.save(job)
    identity=fingerprint(old/output_name(job['files'][0]));name=Path(output_name(job['files'][0])).stem
    def failed(_):raise OSError('Synthetic DB failure')
    with pytest.raises(OSError):shorten_completed(job,failed,name)
    assert fingerprint(old/output_name(job['files'][0]))==identity
    updated=shorten_completed(job,manager.save,name)
    assert Path(updated['destination']).name==name
    assert fingerprint(Path(updated['destination'])/output_name(job['files'][0]))==identity
