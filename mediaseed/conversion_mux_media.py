"""Lossless MKV remux with input rechecks, bitstream hashes and restartable receipt."""
import fcntl
import json
import logging
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from mediaseed.conversion import atomic, fingerprint
from mediaseed.conversion_mux import improvements, metadata, probe
from mediaseed.conversion_naming import output_name
from mediaseed.moves import file_path


class Interrupted(Exception):
    pass


def execute(args, work, stop, cancellation, progress=None, timeout=21600):
    """Bound disk output and lifetime; terminate children on shutdown/cancellation."""
    log = work/'mux-command.log'
    started = time.monotonic()
    with log.open('w+') as output:
        child = subprocess.Popen(args, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            while child.poll() is None:
                if stop.is_set() or cancellation.exists():
                    raise Interrupted()
                if time.monotonic()-started > timeout:
                    raise ValueError('Mux command timed out')
                if shutil.disk_usage(work).free < 256*1024**2:
                    raise ValueError('Insufficient working space')
                if log.stat().st_size > 4*1024**2:
                    raise ValueError('Unexpected mux log size')
                if progress:
                    output.seek(0)
                    progress(output.read())
                time.sleep(.2)
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
        if child.returncode != 0:
            raise ValueError('Mux command failed')
        output.seek(0)
        return output.read()


def stream_hash(path, indexes, work, stop, cancellation):
    args = ['ffmpeg', '-v', 'error', '-nostdin', '-i', str(path)]
    for index in indexes:
        args += ['-map', '0:'+str(index)]
    result = execute(args+['-c', 'copy', '-f', 'streamhash', '-hash', 'sha256', '-'], work, stop, cancellation)
    hashes = [line.split('SHA256=', 1)[1].strip() for line in result.splitlines() if 'SHA256=' in line]
    if len(hashes) != len(indexes) or any(len(h) != 64 for h in hashes):
        raise ValueError('Missing stream hash')
    return hashes


def identify(path):
    result = subprocess.run(['mkvmerge', '-J', str(path)], capture_output=True, text=True, check=True, timeout=60)
    return json.loads(result.stdout)


def command(picture, donor, target, audio, delay):
    a, b = identify(picture), identify(donor)
    tracks = a['tracks']
    donor_audio = [t for t in b['tracks'] if t['type'] == 'audio'][audio['ordinal']]['id']
    picture_video = [t for t in tracks if t['type'] == 'video']
    if len(picture_video) != 1:
        raise ValueError('Ambiguous video')
    order = [f"0:{picture_video[0]['id']}", f'1:{donor_audio}'] + [f"0:{t['id']}" for t in tracks if t['type'] != 'video']
    args = ['mkvmerge', '--ui-language', 'en_US', '--output', str(target), '--track-order', ','.join(order)]
    for track in tracks:
        if track['type'] == 'audio':
            args += ['--default-track-flag', str(track['id'])+':0']
    args += [str(picture), '--no-video', '--no-subtitles', '--no-attachments', '--no-chapters',
             '--no-global-tags', '--audio-tracks', str(donor_audio), '--default-track-flag', str(donor_audio)+':1',
             '--sync', str(donor_audio)+':'+str(delay), str(donor)]
    return args


def validate_headers(before, donor, after, audio):
    v = lambda d: next(s for s in d['streams'] if s['codec_type'] == 'video' and not s.get('disposition', {}).get('attached_pic'))
    for key in ('codec_name', 'width', 'height', 'pix_fmt', 'color_transfer', 'color_primaries', 'color_space', 'side_data_list'):
        if v(before).get(key) != v(after).get(key):
            raise ValueError('Kontrol: video metadata changed: '+key)
    if abs(float(before['format']['duration']) - float(after['format']['duration'])) > 6:
        raise ValueError('Kontrol: duration changed')
    audios = [s for s in after['streams'] if s['codec_type'] == 'audio']
    original = [s for s in before['streams'] if s['codec_type'] == 'audio']
    wanted = next(s for s in donor['streams'] if s['index'] == audio['index'])
    def signature(s):
        return tuple(s.get(k) for k in ('codec_name', 'profile', 'channels', 'sample_rate'))
    if [signature(s) for s in audios] != [signature(wanted), *[signature(s) for s in original]]:
        raise ValueError('Kontrol: audio tracks changed')
    if not audios[0].get('disposition', {}).get('default') or any(s.get('disposition', {}).get('default') for s in audios[1:]):
        raise ValueError('Kontrol: default audio changed')
    for left, right in zip([wanted, *original], audios):
        from mediaseed.media_languages import language_code
        if language_code(left.get('tags', {}).get('language')) != language_code(right.get('tags', {}).get('language')):
            raise ValueError('Kontrol: audio language changed')
    for kind in ('subtitle', 'attachment'):
        def signatures(data):
            return [(s.get('codec_name'), s.get('tags', {}).get('language'), s.get('tags', {}).get('filename')) for s in data['streams'] if s['codec_type'] == kind]
        if signatures(before) != signatures(after):
            raise ValueError('Kontrol: '+kind+' changed')
    chapters = lambda d: [(round(float(c['start_time']), 2), c.get('tags', {})) for c in d.get('chapters', [])]
    if chapters(before) != chapters(after):
        raise ValueError('Kontrol: chapters changed')
    return v(before)['index'], v(after)['index'], audios[0]['index']


def process(queue, job, stop):
    identifier = job['id']
    cancellation = queue/(identifier+'.cancel')
    work = Path(job['work'])
    def status(state, progress=0, **extra):
        atomic(queue/(identifier+'.status.json'), {'id': identifier, 'state': state, 'progress': progress,
               'updated': time.time(), 'worker': 'Server · Mux uden genkodning', **extra})
    # The lifetime lock is also used by API cancellation, exactly as for conversion jobs.
    with (queue/'runner.lock').open('a') as runner, (queue/(identifier+'.lock')).open('a') as lock:
        try:
            fcntl.flock(runner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        try:
            if cancellation.exists():
                if work.exists():
                    shutil.rmtree(work)
                status('cancelled')
                return
            if stop.is_set():
                return
            if work.resolve(strict=True) != work or work.name != 'mediaseed-conversion-'+identifier or work.parent.name != '.incomplete':
                raise ValueError('Working directory changed')
            result = work/'result'
            result.mkdir(exist_ok=True)
            if result.resolve(strict=True) != result:
                raise ValueError('Result scope changed')
            sources = []
            for source in (job, job['donor']):
                entry = source['files'][0]
                path = file_path(Path(source['source']), entry['name'])
                if fingerprint(path) != entry['identity']:
                    raise ValueError('Original changed')
                sources.append(path)
            picture, donor = sources
            status('verifying', 0)
            # Reprobe rather than trusting cached advice or client-supplied track IDs.
            proposal = job['proposal']
            checked = [{**proposal[k], 'metadata': metadata(path)} for k, path in zip(('video', 'donor'), sources)]
            if not any(p['id'] == proposal['id'] for p in improvements(checked)):
                raise ValueError('Improvement no longer valid')
            before, donor_data = probe(picture), probe(donor)
            audio = proposal['audio']
            partial = work/'partial-mux.mkv'
            partial.unlink(missing_ok=True)
            target = file_path(result, output_name(job['files'][0]))
            # Rebuild unpublished outputs on restart; neither source is ever an output.
            if target.exists():
                target.unlink()
            if shutil.disk_usage(work).free < picture.stat().st_size+donor.stat().st_size+256*1024**2:
                raise ValueError('Insufficient working space')
            status('muxing', 0)
            def progress(log):
                import re
                values = re.findall(r'Progress: (\d+)%', log)
                if values:
                    status('muxing', min(89, int(values[-1])*.89))
            execute(command(picture, donor, partial, audio, job['delayMs']), work, stop, cancellation, progress)
            status('verifying', 90)
            after = probe(partial)
            old_index, new_index, new_audio = validate_headers(before, donor_data, after, audio)
            # Full compressed bitstream equality proves video/HDR/DV and donor Atmos data are copied.
            expected = [*stream_hash(picture, [old_index], work, stop, cancellation),
                        *stream_hash(donor, [audio['index']], work, stop, cancellation)]
            actual = stream_hash(partial, [new_index, new_audio], work, stop, cancellation)
            if expected != actual:
                raise ValueError('Kontrol: compressed bitstream changed')
            if not metadata(partial)['audio'][0]['label'] == audio['label']:
                raise ValueError('Kontrol: donor audio metadata changed')
            for source, path in zip((job, job['donor']), sources):
                if fingerprint(path) != source['files'][0]['identity']:
                    raise ValueError('Original changed')
            partial.rename(target)
            companions = {}
            for entry in job['companions']:
                path = file_path(Path(job['source']), entry['name'])
                if fingerprint(path) != entry['identity']:
                    raise ValueError('Companion changed')
                out = file_path(result, entry['output'])
                shutil.copyfile(path, out)
                if fingerprint(path) != entry['identity']:
                    raise ValueError('Companion changed')
                companions[entry['output']] = fingerprint(out)
            if stop.is_set() or cancellation.exists():
                raise Interrupted()
            atomic(result/'.mediaseed-converted.json', {'id': identifier, 'kind': 'mux', 'verified': True,
                   'files': [target.name], 'identities': {target.name: fingerprint(target)},
                   'companionIdentities': companions, 'proposal': proposal['id'], 'delayMs': job['delayMs'],
                   'videoHash': actual[0], 'audioHash': actual[1]})
            status('ready', 100)
        except Interrupted:
            if cancellation.exists():
                if work.exists():
                    shutil.rmtree(work)
                status('cancelled')
        except Exception:
            logging.exception('Mux failed; both originals retained')
            status('error', 0, errorCode='mux')
