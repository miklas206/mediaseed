"""Worker requirements for a new install and an existing deployment."""
import importlib.util
import os
import struct
from pathlib import Path
from unittest.mock import patch

import pytest

spec=importlib.util.spec_from_file_location('conversion_install',Path(__file__).parents[1]/'deploy/install_conversion.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)


def test_fresh_and_upgrade_mount_only_staging_writable_and_preserve_grants(tmp_path):
    app={'Mounts':[{'Destination':'/downloads','Source':str(tmp_path/'downloads')},
                   {'Destination':'/downloads/media/movies','Source':str(tmp_path/'movies')},
                   {'Destination':'/secrets','Source':'/private/client-credentials'},
                   {'Destination':'/data','Source':'/private/app-data'}]}
    original=repr(app)
    first,roots=installer.configuration(app,1000,1000,tmp_path/'queue','worker-old',toolkit=True)
    second,_=installer.configuration(app,1000,1000,tmp_path/'queue','worker-new',toolkit=True)
    assert roots==['/downloads','/downloads/media/movies'] and repr(app)==original
    assert first['network_mode']=='none' and first['read_only'] and first['user']=='1000:1000'
    assert not any('/private/' in m['source'] for m in first['volumes'])
    originals=[m for m in first['volumes'] if m['target'] in roots]
    assert all(m['read_only'] for m in originals)
    assert {m['target'] for m in first['volumes'] if not m.get('read_only')}=={'/conversion',*(r+'/.incomplete' for r in roots)}
    assert first['cap_drop']==['ALL']
    assert first|{'image':'worker-new'}==second
    assert installer.configuration(app,1000,1000,tmp_path/'queue','worker-new',toolkit=True)[0]==second


def test_missing_gpu_is_a_concrete_prerequisite_not_a_fake_healthy_worker(tmp_path):
    app={'Mounts':[{'Destination':'/downloads','Source':str(tmp_path)}]}
    with patch.object(Path,'exists',return_value=False),pytest.raises(ValueError,match='Grant an NVIDIA GPU'):
        installer.configuration(app,1000,1000,tmp_path/'queue','worker')


def test_existing_explicit_driver_grant_is_retained_without_docker_socket(tmp_path):
    app={'Mounts':[{'Destination':'/downloads','Source':str(tmp_path)}]}
    with patch.object(Path,'exists',return_value=True):
        worker,_=installer.configuration(app,1000,1000,tmp_path/'queue','worker')
    assert '/dev/nvidia0:/dev/nvidia0' in worker['devices']
    assert worker['environment']['LD_LIBRARY_PATH']=='/usr/local/lib/mediahub-nvidia'
    assert not any('docker.sock' in m['source'] for m in worker['volumes'])


def test_lxc_mount_traversal_acl_preserves_privacy_and_is_idempotent(tmp_path):
    directory=tmp_path/'media';directory.mkdir(mode=0o770)
    installer.stage_access(directory,os.getuid())
    before=os.getxattr(directory,'system.posix_acl_access')
    entries=[struct.unpack('<HHI',before[n:n+8]) for n in range(4,len(before),8)]
    assert (2,1,0) in entries  # root traversal only, no listing or writing
    assert next(e for e in entries if e[0]==32)[1]==0
    assert next(e for e in entries if e[0]==1)[1]==7
    installer.stage_access(directory,os.getuid())
    assert os.getxattr(directory,'system.posix_acl_access')==before
    with pytest.raises(ValueError):installer.stage_access(directory,os.getuid()+1)


def test_explicit_active_worker_upgrade_stops_worker_and_retains_queue_and_partial_outputs(tmp_path):
    import json
    queue=tmp_path/'queue';queue.mkdir()
    job='a'*32
    request=queue/(job+'.request.json');request.write_text(json.dumps({'id':job,'source':'unchanged'}))
    status=queue/(job+'.status.json');status.write_text(json.dumps({'state':'encoding'}))
    partial=tmp_path/'partial.mkv';partial.write_bytes(b'partial output')
    before=request.read_bytes();stop=[]
    with pytest.raises(ValueError,match='Wait for queued'):
        installer.prepare_worker_upgrade(queue,False,lambda:stop.append(True))
    assert not stop
    assert installer.prepare_worker_upgrade(queue,True,lambda:stop.append(True))
    assert stop==[True] and request.read_bytes()==before and partial.read_bytes()==b'partial output'


def test_active_worker_lock_prevents_restarting_two_encoders(tmp_path):
    import json,fcntl
    request=tmp_path/('a'*32+'.request.json');request.write_text('{}')
    lock=tmp_path/('a'*32+'.lock')
    with lock.open('a') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(ValueError,match='old encoder still owns'):
            installer.prepare_worker_upgrade(tmp_path,True,lambda:None)


def test_deferred_upgrade_waits_without_stopping_encoder_or_changing_requests(tmp_path):
    import json
    request=tmp_path/('a'*32+'.request.json');request.write_text('{"options":{"dolby_vision":true}}')
    original=request.read_bytes();status=tmp_path/('a'*32+'.status.json');status.write_text('{"state":"encoding"}')
    sleeps=[]
    def complete(delay):
        sleeps.append(delay)
        assert request.read_bytes()==original
        status.write_text('{"state":"ready"}')
    installer.wait_for_idle(tmp_path,complete)
    assert sleeps==[15] and request.read_bytes()==original
    installer.wait_for_idle(tmp_path,lambda _:pytest.fail('Already idle'))
