"""Synthetic media and transports only: useful matching, two-source guards and lossless output."""
import asyncio
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import threading
import time
from unittest.mock import Mock

from fastapi import HTTPException
import httpx
import pytest

from mediaseed.conversion import Conversions, atomic, fingerprint
from mediaseed.conversion_advice import cache_key
from mediaseed.conversion_mux import SCHEMA, identity, improvements, measured, metadata
from mediaseed.conversion_mux_jobs import MuxChoice
from mediaseed.conversion_mux_media import process
from mediaseed.storage import Storage


def audio(**changes):
    return {'index': 1, 'ordinal': 0, 'language': 'en', 'codec': 'truehd', 'channels': 8,
            'lossless': True, 'atmos': True, 'label': 'TrueHD Atmos 7.1 · en', **changes}


def pair():
    video = {'duration': 7200, 'fps': 24000/1001, 'width': 3840, 'height': 2160,
             'hdr': True, 'dv': True, 'depth': 10, 'chapters': [0, 3600],
             'audio': [audio(codec='eac3', channels=6, lossless=False, label='Dolby Digital Plus Atmos 5.1 · en')]}
    donor = {**deepcopy(video), 'width': 1920, 'height': 1080, 'audio': [audio()]}
    return [{'name': 'Film.2020.2160p.mkv', 'hash': 'a'*40, 'index': 0, 'identity': [100, 0], 'path': '/Film4k.mkv', 'metadata': video},
            {'name': 'Film.2020.1080p.mkv', 'hash': 'b'*40, 'index': 0, 'identity': [80, 0], 'path': '/FilmHD.mkv', 'metadata': donor}]


def test_only_crossed_improvements_produce_suggestions():
    assert len(improvements(pair())) == 1
    rows = pair()
    rows[1]['metadata']['audio'] = deepcopy(rows[0]['metadata']['audio'])
    assert improvements(rows) == []
    rows = pair()
    rows[1]['metadata'].update(width=3840, height=2160)
    assert improvements(rows) == []
    rows = pair()
    rows[0]['metadata']['audio'] = [audio()]
    assert improvements(rows) == []


@pytest.mark.parametrize('change', ['language', 'commentary', 'cut', 'episode', 'duration', 'fps', 'chapters', 'dv', 'atmos', 'unknown-language'])
def test_ambiguous_or_inferior_combinations_are_not_proposed(change):
    rows = pair()
    if change == 'language': rows[1]['metadata']['audio'][0]['language'] = 'da'
    if change == 'commentary': rows[1]['metadata']['audio'] = []
    if change == 'cut': rows[1]['name'] = 'Film.2020.Extended.1080p.mkv'
    if change == 'episode': rows[1]['name'] = 'Film.S01E02.1080p.mkv'
    if change == 'duration': rows[1]['metadata']['duration'] += 2
    if change == 'fps': rows[1]['metadata']['fps'] = 25
    if change == 'chapters': rows[1]['metadata']['chapters'][1] += 10
    if change == 'dv': rows[0]['metadata']['dv'] = False
    if change == 'atmos': rows[1]['metadata']['audio'][0]['atmos'] = False
    if change == 'unknown-language': rows[0]['metadata']['audio'] = []
    assert improvements(rows) == []


def test_series_match_only_the_same_single_episode_and_edition():
    rows = pair()
    rows[0]['name'] = 'Series.S02E03.2160p.mkv'
    rows[1]['name'] = 'Series.S02E03.1080p.mkv'
    assert len(improvements(rows)) == 1
    for name in ('Series.S02E04.1080p.mkv', 'Series.S03E03.1080p.mkv', 'Series.S02E03E04.1080p.mkv', 'Series.S02.1080p.mkv'):
        rows[1]['name'] = name
        assert improvements(rows) == []
    assert identity('Unknown.mkv') is None
    assert identity('1917.2019.2160p.mkv') == identity('1917.2019.1080p.mkv')
    assert identity('S01E01.mkv', 'Show.S01.2160p') == identity('Show.S01E01.1080p.mkv')


def test_atmos_requires_parsed_bitstream_evidence_and_commentary_is_ignored():
    data = {'format': {'duration': '7200'}, 'streams': [
        {'codec_type': 'video', 'index': 0, 'width': 3840, 'height': 2160, 'avg_frame_rate': '24/1'},
        {'codec_type': 'audio', 'index': 1, 'codec_name': 'truehd', 'channels': 8, 'tags': {'language': 'eng', 'title': 'Atmos'}}]}
    assert not measured(data, {})['audio'][0]['atmos']
    info = {'media': {'track': [{'@type': 'Audio', 'Format_AdditionalFeatures': '16-ch'}]}}
    assert not measured(data, info)['audio'][0]['atmos']
    info['media']['track'][0]['Format_Commercial_IfAny'] = 'Dolby TrueHD with Dolby Atmos'
    assert measured(data, info)['audio'][0]['atmos']
    data['streams'][1]['tags']['title'] = 'Director commentary'
    assert measured(data, info)['audio'] == []


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path/'downloads'
    root.mkdir()
    paths = [root/'Film.2020.2160p.mkv', root/'Film.2020.1080p.mkv']
    for path in paths: path.write_bytes(b'synthetic')
    queue = tmp_path/'queue'
    queue.mkdir()
    @contextmanager
    def database():
        with sqlite3.connect(tmp_path/'jobs.db') as db: yield db
    rows = [{'hash': c*40, 'name': p.name, 'progress': 1, 'state': 'uploading', 'save_path': '/downloads', 'added_on': i+1} for i, (c, p) in enumerate(zip('ab', paths))]
    async def qbit(path, method='GET', payload=None, **kwargs):
        assert method == 'GET'
        if path.endswith('/info'):
            return httpx.Response(200, json=[r for r in rows if not payload.get('hashes') or r['hash'] == payload['hashes']])
        row = next(r for r in rows if r['hash'] == payload['hash'])
        p = paths[rows.index(row)]
        return httpx.Response(200, json=[{'name': p.name, 'size': p.stat().st_size, 'progress': 1}])
    manager = Conversions(database, qbit, Storage(root), Mock(lock=asyncio.Lock()), queue)
    monkeypatch.setattr(manager.mux, 'readiness', lambda: {'ready': True, 'reason': ''})
    entries = []
    for i, p in enumerate(paths):
        entry = {'hash': rows[i]['hash'], 'added': rows[i]['added_on'], 'index': 0, 'name': p.name,
                 'size': p.stat().st_size, 'identity': fingerprint(p), 'path': str(p), 'source': str(root), 'title': p.name}
        entries.append(entry)
        atomic(manager.mux.queue/('metadata-'+cache_key(entry)+'.json'), {'schema': SCHEMA, 'metadata': pair()[i]['metadata']})
    atomic(manager.mux.queue/'inventory.json', {'files': entries, 'scanned': True})
    return manager, paths, rows, database


def test_queue_protects_both_sources_and_recovers_without_gpu_worker_requests(setup):
    manager, paths, _, database = setup
    proposal = manager.mux.advice()['rows'][0]
    choice = MuxChoice(proposal=proposal['id'], location='local')
    with pytest.raises(HTTPException, match='422'):
        asyncio.run(manager.mux.create(choice))
    job = asyncio.run(manager.mux.create(choice.model_copy(update={'confirmed_same_cut_and_sync': True})))
    assert job['kind'] == 'mux' and job['originalsRetained']
    assert len(manager.mux.advice()['rows']) == 0
    for identifier in ('a'*40, 'b'*40):
        with pytest.raises(HTTPException): manager.guard('/delete', {'hashes': identifier})
    restored = Conversions(database, manager.qbit, manager.storage, manager.cleanup, manager.queue)
    assert restored.records()[0]['id'] == job['id']
    assert not list(manager.queue.glob('*.request.json'))
    asyncio.run(restored.cancel(job['id']))
    assert restored.records()[0]['status'] == 'cancelled'
    assert all(path.read_bytes() == b'synthetic' for path in paths)


def test_changed_donor_is_rejected_before_queuing(setup):
    manager, paths, _, _ = setup
    proposal = manager.mux.advice()['rows'][0]
    paths[1].write_bytes(b'changed donor')
    with pytest.raises(HTTPException):
        asyncio.run(manager.mux.create(MuxChoice(proposal=proposal['id'], location='local', confirmed_same_cut_and_sync=True)))
    assert manager.records() == []


def test_completed_combination_is_hidden_until_output_is_deleted(setup):
    manager, _, _, _ = setup
    proposal = manager.mux.advice()['rows'][0]
    asyncio.run(manager.mux.create(MuxChoice(proposal=proposal['id'], location='local', confirmed_same_cut_and_sync=True)))
    job = manager.records()[0]
    target = Path(job['destination'])
    target.mkdir()
    output = target/job['files'][0]['output']
    output.write_bytes(b'verified synthetic output')
    job['status'] = 'complete'
    manager.save(job)
    assert manager.mux.advice()['rows'] == []
    output.unlink()
    assert len(manager.mux.advice()['rows']) == 1


def test_publishing_rechecks_donor_even_after_worker_receipt(setup):
    manager, paths, _, _ = setup
    proposal = manager.mux.advice()['rows'][0]
    asyncio.run(manager.mux.create(MuxChoice(proposal=proposal['id'], location='local', confirmed_same_cut_and_sync=True)))
    paths[1].write_bytes(b'different donor')
    with pytest.raises(ValueError, match='Original changed'):
        asyncio.run(manager.publish(manager.records()[0], {}))


def test_mux_endpoints_enforce_server_side_writer_role(setup):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from mediaseed.conversion_mux_jobs import install_routes
    manager, *_ = setup
    app = FastAPI()
    async def viewer(): return {'role': 'viewer'}
    install_routes(app, viewer, manager.mux)
    client = TestClient(app)
    assert client.get('/api/conversions/mux/recommendations').status_code == 200
    assert client.post('/api/conversions/mux/analyze').status_code == 403
    assert client.post('/api/conversions/mux', json={'proposal': 'a'*64, 'location': 'local', 'confirmed_same_cut_and_sync': True}).status_code == 403
    assert manager.records() == []


def test_automatic_state_preparation_for_fresh_and_legacy_installations(tmp_path):
    from mediaseed.app import create_app
    for legacy in (False, True):
        data = tmp_path/('legacy' if legacy else 'fresh')
        data.mkdir()
        if legacy:
            # No mux tables, queue, settings or GPU installation in the old app.
            (data/'preserve-user-file').write_text('keep')
        app = create_app(data, 'http://localhost', transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[])))
        assert app.state.conversions.mux.queue == data/'mux-state'
        assert (data/'mux-state').is_dir()
        second = create_app(data, 'http://localhost', transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[])))
        assert second.state.conversions.mux.queue == data/'mux-state'
        if legacy: assert (data/'preserve-user-file').read_text() == 'keep'
    dockerfile = (Path(__file__).parents[1]/'Dockerfile').read_text()
    assert all(tool in dockerfile for tool in ('ffmpeg', 'mkvtoolnix', 'mediainfo'))


def test_shutdown_preserves_and_resumes_pending_metadata_after_restart(setup, monkeypatch):
    manager, _, _, database = setup
    inventory = manager.mux.inventory()
    entry = inventory['files'][0]
    (manager.mux.queue/('metadata-'+cache_key(entry)+'.json')).unlink()
    monkeypatch.setattr('mediaseed.conversion_mux_jobs.metadata', lambda p: pair()[0]['metadata'])
    restored = Conversions(database, manager.qbit, manager.storage, manager.cleanup, manager.queue)
    async def resume():
        await restored.mux.tick()
        await restored.mux.analysis_task
        await restored.mux.close()
    asyncio.run(resume())
    assert restored.mux.advice()['pending'] == 0


@pytest.mark.parametrize('runner', ['direct', 'automatic'])
@pytest.mark.skipif(not all(shutil.which(t) for t in ('ffmpeg', 'ffprobe', 'mkvmerge', 'mediainfo')), reason='Media tools are required for real synthetic remux')
def test_real_remux_preserves_compressed_video_and_audio_publishes_larger_output_and_keeps_originals(setup, runner):
    manager, paths, rows, _ = setup
    subtitle = paths[0].with_suffix('.en.srt')
    subtitle.write_text('1\n00:00:00,000 --> 00:00:01,500\nSynthetic subtitle\n')
    chapters = paths[0].parent/'chapters.txt'
    chapters.write_text(';FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=1000\ntitle=Start\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=1000\nEND=2000\ntitle=End\n')
    attachment = paths[0].parent/'attachment.txt'
    attachment.write_text('Synthetic attachment')
    for i, path in enumerate(paths):
        args = ['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'color=s={640 if i == 0 else 320}x{360 if i == 0 else 180}:r=24:d=2',
                '-f', 'lavfi', '-i', f'anullsrc=r=48000:cl={"5.1" if i == 0 else "7.1"}']
        if i == 0:
            args += ['-i', str(subtitle), '-f', 'ffmetadata', '-i', str(chapters), '-map', '0:v', '-map', '1:a', '-map', '2:s',
                     '-map_chapters', '3', '-c:s', 'srt', '-metadata:s:s:0', 'language=eng', '-attach', str(attachment), '-metadata:s:t', 'mimetype=text/plain']
        args += ['-t', '2', '-c:v', 'libx264', '-c:a', 'ac3' if i == 0 else 'truehd' if runner == 'direct' else 'flac',
                 '-strict', '-2', '-metadata:s:a:0', 'language=eng', str(path)]
        subprocess.run(args, check=True, capture_output=True, timeout=30)
    entries = manager.mux.inventory()['files']
    for entry, path in zip(entries, paths):
        entry.update(size=path.stat().st_size, identity=fingerprint(path))
        atomic(manager.mux.queue/('metadata-'+cache_key(entry)+'.json'), {'schema': SCHEMA, 'metadata': metadata(path)})
    atomic(manager.mux.queue/'inventory.json', {'files': entries, 'scanned': True})
    before = [p.read_bytes() for p in paths]
    proposal = manager.mux.advice()['rows'][0]
    public = asyncio.run(manager.mux.create(MuxChoice(proposal=proposal['id'], location='local', confirmed_same_cut_and_sync=True)))
    job = manager.records()[0]
    if runner == 'direct':
        process(manager.mux.queue, job, threading.Event())
    else:
        async def automatic():
            await manager.tick()
            await manager.mux.task
            await manager.tick()
            await manager.mux.close()
        asyncio.run(automatic())
    status = json.loads((manager.mux.queue/(job['id']+'.status.json')).read_text())
    assert status['state'] == 'ready'
    if runner == 'direct':
        asyncio.run(manager.publish(job, status))
    else:
        job = manager.records()[0]
    assert job['outputBytes'] > job['originalBytes']
    assert job['status'] == 'complete'
    assert (Path(job['destination'])/job['files'][0]['output']).exists()
    assert [p.read_bytes() for p in paths] == before
    assert manager.public(job)['kind'] == 'mux'
    receipt = json.loads((Path(job['destination'])/'.mediaseed-converted.json').read_text())
    assert len(receipt['videoHash']) == len(receipt['audioHash']) == 64
    assert receipt['companionIdentities']
    assert subtitle.read_text().endswith('Synthetic subtitle\n')
