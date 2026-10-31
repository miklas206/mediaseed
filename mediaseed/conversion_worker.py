"""No network, credentials or torrent RPC; read originals and write scoped staging."""
import fcntl
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from fractions import Fraction
from mediaseed.conversion_advice import analyze_inventory
from pathlib import Path

from mediaseed.conversion import atomic, fingerprint
from mediaseed.conversion_naming import output_name
from mediaseed.conversion_decoder import select_decoder
from mediaseed.conversion_options import options_dict
from mediaseed.conversion_media import command, probe, restore_flags, validate, video

STOP = threading.Event()


def terminate(child):
    if child.poll() is None:
        try: os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError: pass
        try: child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL);child.wait()


def contained(path, roots):
    path = Path(path)
    return path.is_absolute() and path.resolve() == path and any(path == r or path.is_relative_to(r) for r in roots)


def process(queue, job, roots):
    identifier = job['id']
    options=options_dict(job.get('options'))
    if not re.fullmatch('[a-f0-9]{32}', identifier): raise ValueError('Invalid identity')
    work, source = Path(job['work']), Path(job['source'])
    if (job['bitrate'] not in (20,30,40) or not contained(source, roots)
            or not contained(work, [r/'.incomplete' for r in roots])
            or work.name != 'mediaseed-conversion-'+identifier):
        raise ValueError('Invalid conversion scope')
    result = work/'result'
    cancellation = queue/(identifier+'.cancel')
    started=time.time();current_file='';metrics={};worker_label='Server · NVIDIA'
    def status(state, progress=0):
        atomic(queue/(identifier+'.status.json'), {'id':identifier, 'state':state, 'progress':progress,
            'updated':time.time(),'started':started,'currentFile':current_file,'worker':worker_label,**metrics})
    def cancelled(): return STOP.is_set() or cancellation.exists()
    if cancellation.exists():
        if work.exists(): shutil.rmtree(work)
        status('cancelled');return
    result.mkdir(exist_ok=True)
    for index, entry in enumerate(job['files']):
        from mediaseed.moves import file_path
        current_file=entry['name'];metrics={}
        original = file_path(source, entry['name'])
        if fingerprint(original) != entry['identity']: raise ValueError('Original changed')
        target = file_path(result, output_name(entry))
        target.parent.mkdir(parents=True, exist_ok=True)
        before = probe(original)
        if target.exists():
            validate(before, probe(target),options=options);continue
        if cancelled(): break
        partial = work/('partial-'+str(index)+'.mkv')
        partial.unlink(missing_ok=True)
        if shutil.disk_usage(work).free < entry['size']+1024**3:
            raise ValueError('Insufficient working space')
        reader=select_decoder(video(before))
        worker_label='Server · NVIDIA · '+('GPU-afkodning' if reader=='avhw' else 'CPU-afkodning')
        status('encoding', 100*index/len(job['files']))
        file_started=time.time()
        with (work/'encoder.log').open('a') as log:
            child = subprocess.Popen(command(original,partial,job['bitrate'],before,reader=reader,options=options), stdout=log, stderr=log, start_new_session=True)
            try:
                expected = max(1, float(before['format']['duration'])*float(Fraction(video(before)['avg_frame_rate'])))
                while child.poll() is None:
                    if cancelled(): break
                    if shutil.disk_usage(work).free < 1024**3: raise ValueError('Insufficient working space')
                    if partial.exists() and partial.stat().st_size >= entry['size']:
                        raise ValueError('The converted result does not save space')
                    with (work/'encoder.log').open('rb') as stream:
                        stream.seek(max(0,os.fstat(stream.fileno()).st_size-8192))
                        tail=stream.read()
                        frames=re.findall(rb'\b(\d+)(?:/\d+)? frames:',tail)
                    if frames:
                        count=int(frames[-1]);elapsed=max(1,time.time()-file_started)
                        metrics={'fps':round(count/elapsed,1),'etaSeconds':round(max(0,expected-count)/(count/elapsed)) if count else None}
                        status('encoding',100*(index+min(.99,count/expected))/len(job['files']))
                    time.sleep(1)
            finally: terminate(child)
            if cancelled(): break
            if child.returncode != 0: raise ValueError('Encoding failed')
        status('verifying', 100*(index+.9)/len(job['files']))
        restore_flags(before, partial,options=options)
        validate(before, probe(partial),options=options)
        if fingerprint(original) != entry['identity']: raise ValueError('Original changed')
        partial.rename(target)
    if cancelled():
        if cancellation.exists():
            shutil.rmtree(work);status('cancelled')
        return  # A shutdown preserves verified per-file output for recovery.
    companions = {}
    for entry in job.get('companions', []):
        original = file_path(source, entry['name'])
        if fingerprint(original) != entry['identity']: raise ValueError('Companion changed')
        target = file_path(result, entry.get('output', entry['name']))
        target.parent.mkdir(parents=True, exist_ok=True)
        # Independent files: edits to library subtitles must not alter seeding bytes.
        shutil.copyfile(original, target)
        if fingerprint(original) != entry['identity']: raise ValueError('Companion changed')
        companions[entry.get('output', entry['name'])] = fingerprint(target)
    identities = {output_name(f): fingerprint(file_path(result, output_name(f))) for f in job['files']}
    if sum(v[0] for v in identities.values()) >= job['originalBytes']:
        raise ValueError('The converted result does not save space')
    atomic(result/'.mediaseed-converted.json', {'id':identifier, 'verified':True, 'files':list(identities), 'identities':identities, 'companionIdentities':companions, 'bitrate':job['bitrate'],'options':options})
    status('ready',100)


def gpu_test():
    with tempfile.TemporaryDirectory() as folder:
        original, target = Path(folder)/'source.mkv', Path(folder)/'result.mkv'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=s=256x144:r=24:d=0.5','-c:v','ffv1',str(original)],check=True,capture_output=True,timeout=30)
        subprocess.run(command(original,target,20,probe(original)),check=True,capture_output=True,timeout=60)
        validate(probe(original),probe(target))
        # A compressed HEVC input is needed to exercise NVDEC, not just NVENC.
        decoded=Path(folder)/'nvdec.mkv'
        before=probe(target)
        subprocess.run(command(target,decoded,20,before),check=True,capture_output=True,timeout=60)
        validate(before,probe(decoded))


def main():
    queue = Path(os.environ['MEDIASEED_CONVERSION_QUEUE'])
    roots = [Path(p) for p in json.loads(os.environ['MEDIASEED_CONVERSION_ROOTS'])]
    queue.mkdir(exist_ok=True)
    for sig in (signal.SIGTERM,signal.SIGINT): signal.signal(sig, lambda *_: STOP.set())
    with (queue/'worker.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try: gpu_test();ready=True
        except Exception:
            logging.exception('NVIDIA HEVC readiness test failed')
            ready=False
        def heartbeat():
            while not STOP.is_set():
                atomic(queue/'worker.json', {'at':time.time(),'ready':ready,'capabilities':['conversion-options-v1']})
                STOP.wait(5)
        threading.Thread(target=heartbeat,daemon=True).start()
        def analysis():
            while not STOP.is_set():
                try:analyze_inventory(queue,roots,STOP.is_set)
                except Exception:logging.exception('Metadata analysis failed')
                STOP.wait(3)
        threading.Thread(target=analysis,daemon=True).start()
        def ordered(path):
            try:return (float(json.loads(path.read_text()).get('created',0)),path.name)
            except (OSError,ValueError,TypeError):return (0,path.name)
        while not STOP.is_set():
            if ready:
                for path in sorted(queue.glob('*.request.json'),key=ordered):
                    identifier = path.name.removesuffix('.request.json')
                    if not re.fullmatch('[a-f0-9]{32}',identifier): continue
                    try:
                        previous = json.loads((queue/(identifier+'.status.json')).read_text()) if (queue/(identifier+'.status.json')).exists() else {}
                        if previous.get('state') in {'ready','error','cancelled'} and not (queue/(identifier+'.cancel')).exists(): continue
                        if previous.get('state') == 'cancelled': continue
                        job=json.loads(path.read_text())
                        if job.get('id') != identifier: raise ValueError('Wrong job identity')
                        with (queue/(identifier+'.lock')).open('a') as job_lock:
                            fcntl.flock(job_lock,fcntl.LOCK_EX)
                            process(queue,job,roots)
                    except Exception as error:
                        if ((queue/(identifier+'.cancel')).exists() and
                                isinstance(error, ValueError) and str(error)=='Invalid conversion scope'):
                            # Legacy jobs could stage under an unmounted nested
                            # folder. Acknowledge cancellation without touching
                            # an unverified path or endlessly retrying the error.
                            atomic(queue/(identifier+'.status.json'), {'id':identifier,'state':'cancelled','progress':0})
                            continue
                        logging.exception('Conversion %s failed; originals preserved',identifier)
                        detail=str(error)
                        code='no_saving' if 'does not save space' in detail else 'space' if 'space' in detail else 'validation' if 'Kontrol' in detail else 'selection' if 'Ingen lydspor' in detail else 'failed'
                        atomic(queue/(identifier+'.status.json'), {'id':identifier,'state':'error','progress':0,'errorCode':code})
                    if STOP.is_set(): break
            STOP.wait(2)


if __name__ == '__main__': main()
