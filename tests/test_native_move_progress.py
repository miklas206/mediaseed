import errno
from pathlib import Path
import json
import os
import pytest
from fastapi import HTTPException
from mediaseed.storage import Storage
from mediaseed.native_move_progress import NativeMoveProgress


def fixture(tmp_path):
    parent=tmp_path/'media/tv';parent.mkdir(parents=True)
    storage=Storage(tmp_path,json.dumps([{'id':'tv','label':'TV','path':'/downloads/media/tv'}]))
    clock=[1000.]
    monitor=NativeMoveProgress(storage,clock=lambda:clock[0],interval=0)
    def row(name,identifier='a'*40):return {'hash':identifier,'state':'moving','size':1024**2,'save_path':'/downloads/media/tv','content_path':'/downloads/media/tv/.incomplete/'+name}
    return parent,monitor,clock,row


def test_move_queue_and_written_progress_without_seeding_goal_or_client_writes(tmp_path):
    parent,monitor,clock,row=fixture(tmp_path)
    folder=parent/'Active';folder.mkdir();target=folder/'episode.mkv';target.write_bytes(b'a'*4096);os.utime(target,(900,900))
    active=row('Active');queued=row('Queued','b'*40)
    first=monitor.snapshot([active,queued]);assert first[active['hash']]['phase']=='observing';assert first[queued['hash']]['phase']=='queued'
    clock[0]+=10;target.write_bytes(b'a'*65536)
    result=monitor.snapshot([active,queued]);r=result[active['hash']]
    assert r['phase']=='active';assert result[queued['hash']]['phase']=='queued';assert r['fraction']>first[active['hash']]['fraction'];assert r['speedBps']>0
    assert r['currentFile']=='episode.mkv';assert str(tmp_path) not in json.dumps(result);assert r['estimated']
    target.write_bytes(b'a'*1024**2);clock[0]+=10
    assert monitor.snapshot([active])[active['hash']]['fraction']<1
    assert monitor.snapshot([{**active,'state':'stalledUP'}])=={}


@pytest.mark.parametrize('change',[{'save_path':'/outside'},{'content_path':'/etc/passwd'},{'size':float('inf')}])
def test_unapproved_or_invalid_observation_is_unknown(tmp_path,change):
    _,monitor,_,row=fixture(tmp_path);r=row('Active');r.update(change)
    result=monitor.snapshot([r]);assert result[r['hash']]['phase']=='unavailable';assert 'fraction' not in result[r['hash']]


def test_symlink_target_is_not_followed(tmp_path):
    parent,monitor,_,row=fixture(tmp_path);outside=tmp_path/'secret';outside.mkdir();(outside/'private').write_text('secret');(parent/'Active').symlink_to(outside,target_is_directory=True)
    assert monitor.snapshot([row('Active')])['a'*40]['phase']=='unavailable'


def test_cached_measurement_and_stale_activity(tmp_path):
    parent,monitor,clock,row=fixture(tmp_path);monitor.interval=10
    (parent/'Active').mkdir();file=parent/'Active/f';file.write_bytes(b'a');os.utime(file,(1000,1000))
    r=row('Active');a=monitor.snapshot([r]);assert a[r['hash']]['phase']=='active'
    file.write_bytes(b'a'*10000);assert monitor.snapshot([r])==a
    clock[0]+=11;assert monitor.snapshot([r])[r['hash']]['writtenBytes']>a[r['hash']]['writtenBytes']
    clock[0]+=40;assert monitor.snapshot([r])[r['hash']]['phase']=='observing'


def test_storage_import_probe_reports_cross_disk_pool_and_preserves_files(tmp_path,monkeypatch):
    storage=Storage(tmp_path);(tmp_path/'keep').write_text('keep')
    import mediaseed.storage as module
    def rename(*args):raise OSError(errno.EXDEV,'cross device')
    monkeypatch.setattr(module.os,'rename',rename)
    with pytest.raises(HTTPException) as error:storage.incomplete('/downloads')
    assert error.value.status_code==409;assert 'original disk' in error.value.detail
    assert (tmp_path/'keep').read_text()=='keep'
    assert list(tmp_path.glob('.mediaseed-rename-*'))==[];assert list((tmp_path/'.incomplete').iterdir())==[]


def test_rename_probe_does_not_remove_an_existing_destination(tmp_path,monkeypatch):
    storage=Storage(tmp_path)
    import mediaseed.storage as module
    monkeypatch.setattr(module.secrets,'token_hex',lambda n:'fixed')
    target=tmp_path/'.mediaseed-rename-fixed';target.write_text('keep-existing')
    with pytest.raises(HTTPException):storage.incomplete('/downloads')
    assert target.read_text()=='keep-existing'
    assert list((tmp_path/'.incomplete').iterdir())==[]


def test_authenticated_torrent_list_includes_native_move_measurement(tmp_path,monkeypatch):
    import httpx
    from fastapi.testclient import TestClient
    from mediaseed.app import create_app
    from test_app import sign_in
    downloads=tmp_path/'downloads';parent=downloads/'media/tv/Active';parent.mkdir(parents=True);(parent/'episode.mkv').write_bytes(b'a'*4096)
    monkeypatch.setenv('MEDIASEED_STORAGE_ROOTS',json.dumps([{'id':'tv','label':'TV','path':'/downloads/media/tv'}]))
    row={'hash':'a'*40,'state':'moving','progress':1,'size':1024**2,'name':'Active','save_path':'/downloads/media/tv','content_path':'/downloads/media/tv/.incomplete/Active'}
    calls=[]
    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith('/login'):return httpx.Response(204)
        return httpx.Response(200,json=[row])
    with TestClient(create_app(tmp_path/'state','http://testserver',downloads_dir=downloads,qbit_password='synthetic',transport=httpx.MockTransport(handler))) as client:
        assert client.get('/api/torrents').status_code==401
        sign_in(client);response=client.get('/api/torrents');assert response.status_code==200,response.text
        measured=response.json()[0]['nativeMove'];assert measured['phase']=='active';assert measured['writtenBytes']>0;assert measured['estimated']
        assert '/torrents/files' not in ''.join(calls);assert '/downloads' not in json.dumps(measured)


def test_preallocated_size_is_not_reported_as_written_progress(tmp_path):
    parent,monitor,_,row=fixture(tmp_path);(parent/'Active').mkdir()
    with (parent/'Active/episode.mkv').open('wb') as file:file.truncate(1024**2)
    measured=monitor.snapshot([row('Active')])['a'*40]
    assert measured['writtenBytes']==0;assert measured['fraction']==0
