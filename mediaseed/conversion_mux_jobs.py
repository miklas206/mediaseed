"""App-owned mux jobs: shipped tools, durable conversion ledger, no GPU dependency."""
import asyncio
import json
import logging
import shutil
import threading
import time
import uuid
from pathlib import Path

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from mediaseed.conversion import TERMINAL, STABLE, atomic, fingerprint
from mediaseed.conversion_advice import cache_key
from mediaseed.conversion_mux import SCHEMA, improvements, metadata, public_proposal
from mediaseed.conversion_naming import library_label
from mediaseed.moves import file_path


class MuxChoice(BaseModel):
    model_config = ConfigDict(extra='forbid')
    proposal: str = Field(pattern=r'^[a-f0-9]{64}$')
    location: str = Field(max_length=64)
    folder: str = Field(default='', max_length=1000)
    confirmed_same_cut_and_sync: bool = False
    delay_ms: int = Field(default=0, ge=-5000, le=5000)


class MuxJobs:
    def __init__(self, manager, state):
        self.manager = manager
        self.queue = Path(state) if state else None
        self.analysis_task = self.task = None
        self.stop = threading.Event()
        if self.queue:
            self.queue.mkdir(parents=True, exist_ok=True, mode=0o700)

    def readiness(self):
        missing = [name for name in ('ffprobe', 'ffmpeg', 'mediainfo', 'mkvmerge') if not shutil.which(name)]
        reason = 'Opdater MediaSeed for at klargøre mux-værktøjerne: ' + ', '.join(missing) if missing else ''
        if not self.queue:
            reason = 'Mux-arbejdsområdet mangler. Opdater MediaSeed for at klargøre det automatisk.'
        return {'ready': not reason, 'reason': reason}

    def inventory(self):
        if self.queue:
            try:
                return json.loads((self.queue/'inventory.json').read_text())
            except (OSError, ValueError):
                pass
        return {'files': [], 'scanned': False}

    def candidates(self):
        entries, pending, errors = [], 0, 0
        inventory = self.inventory()
        for entry in inventory['files']:
            try:
                value = json.loads((self.queue/('metadata-'+cache_key(entry)+'.json')).read_text())
                if value.get('schema') != SCHEMA:
                    pending += 1
                    continue
                if value.get('error'):
                    errors += 1
                    continue
                if fingerprint(Path(entry['path'])) != entry['identity']:
                    errors += 1
                    continue
                entries.append({**entry, 'metadata': value['metadata']})
            except FileNotFoundError:
                pending += 1
            except (OSError, ValueError, KeyError):
                errors += 1
        busy = {h for j in self.manager.records() if j['status'] not in TERMINAL for h in source_hashes(j)}
        entries = [e for e in entries if e['hash'] not in busy]
        completed = set()
        for job in self.manager.records():
            if job.get('kind') != 'mux' or job['status'] != 'complete': continue
            try:
                fingerprint(file_path(Path(job['destination']), job['files'][0]['output']))
            except FileNotFoundError:
                continue
            except (OSError, ValueError, HTTPException):
                pass  # An unavailable mount is not proof of deletion.
            completed.add(job['proposal']['id'])
        return [p for p in improvements(entries) if p['id'] not in completed], inventory, pending, errors

    def advice(self):
        rows, inventory, pending, errors = self.candidates()
        return {**self.readiness(), 'rows': [public_proposal(p) for p in rows], 'pending': pending,
                'errors': errors + inventory.get('failures', 0), 'total': len(inventory['files']),
                'scanned': inventory.get('scanned', False), 'truncated': inventory.get('truncated', False)}

    async def analyze(self):
        if not self.readiness()['ready']:
            raise HTTPException(409, self.readiness()['reason'])
        async with self.manager.analysis_lock:
            if self.analysis_task and not self.analysis_task.done():
                return {'accepted': True, 'files': len(self.inventory()['files'])}
            rows = await self.manager.read('info', {'filter': 'completed', 'limit': 500})
            rows = [r for r in rows if r.get('progress') == 1 and r.get('state') in STABLE]
            entries, failures = [], 0
            for row in rows[:80]:
                try:
                    observed, root, videos, _ = await self.manager.inspect(row['hash'])
                    for v in videos:
                        if len(entries) >= 240:
                            break
                        if Path(v['name']).suffix.lower() != '.mkv':
                            continue
                        entries.append({**v, 'hash': row['hash'], 'added': observed.get('added_on'),
                                        'title': str(row.get('name', 'Video'))[:500], 'source': str(root),
                                        'path': str(file_path(root, v['name']))})
                except (HTTPException, OSError, ValueError):
                    failures += 1
            atomic(self.queue/'inventory.json', {'files': entries, 'scanned': True, 'at': time.time(),
                   'failures': failures, 'truncated': len(rows) > 80 or len(entries) >= 240})
            self.analysis_task = asyncio.create_task(asyncio.to_thread(self.analyze_files, entries))
            return {'accepted': True, 'files': len(entries)}

    def analyze_files(self, entries):
        import fcntl
        with (self.queue/'analysis.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            for entry in entries:
                if self.stop.is_set():
                    return
                target = self.queue/('metadata-'+cache_key(entry)+'.json')
                try:
                    old = json.loads(target.read_text()) if target.exists() else {}
                    if old.get('schema') == SCHEMA and not old.get('error'):
                        continue
                    path = Path(entry['path'])
                    if fingerprint(path) != entry['identity']:
                        raise ValueError('Original changed')
                    value = metadata(path)
                    if fingerprint(path) != entry['identity']:
                        raise ValueError('Original changed')
                    result = {'schema': SCHEMA, 'metadata': value}
                except Exception:
                    result = {'schema': SCHEMA, 'error': True}
                atomic(target, result)

    async def validate_sources(self, job):
        for source in [job, job['donor']]:
            row, root, videos, _ = await self.manager.inspect(source['hash'])
            if row.get('added_on') != source['added'] or str(root) != source['source']:
                raise ValueError('Original torrent changed')
            current = {v['index']: v for v in videos}
            if any(current.get(v['index'], {}).get('identity') != v['identity'] for v in source['files']):
                raise ValueError('Original changed')

    async def create(self, choice):
        m = self.manager
        if not choice.confirmed_same_cut_and_sync:
            raise HTTPException(422, 'Bekræft samme filmudgave/afsnit og kontrolleret lydsynkronisering før muxing.')
        if not self.readiness()['ready']:
            raise HTTPException(409, self.readiness()['reason'])
        async with m.lock, m.cleanup.lock:
            proposals, _, _, _ = self.candidates()
            proposal = next((p for p in proposals if p['id'] == choice.proposal), None)
            if not proposal:
                raise HTTPException(409, 'Forslaget er ændret eller allerede i kø. Analysér igen.')
            if len([j for j in m.records() if j['status'] not in TERMINAL]) >= 50:
                raise HTTPException(409, 'Afslut eller annullér ventende job først.')
            picture, donor = proposal['video'], proposal['donor']
            _, destination = m.storage.directory(choice.location, choice.folder, writable=True)
            _, staging = m.storage.directory(choice.location, '', writable=True)
            identifier = uuid.uuid4().hex
            stem = library_label(picture['name']) + f".{picture['metadata']['height']}p.MUX-MDTech-{identifier[:8]}"
            library = destination if choice.location == 'converted-movies' or destination.name == 'Konverteret' else destination/'Konverteret'
            target = library/stem
            for source in (picture, donor):
                root = Path(source['source'])
                top = root/Path(source['name']).parts[0]
                if target == top or target.is_relative_to(top) or (root != staging and target.is_relative_to(root)):
                    raise HTTPException(409, 'Vælg en placering uden for begge originalers mapper.')
            required = picture['size'] + donor['size'] + 1024**3
            if shutil.disk_usage(destination).free < required:
                raise HTTPException(409, 'Der er ikke plads til en separat mux-kopi. Begge originaler skal bevares.')
            m.storage.incomplete(m.storage.destination(choice.location, ''))
            work = staging/'.incomplete'/('mediaseed-conversion-'+identifier)
            companions = []
            original = Path(picture['path'])
            for count, path in enumerate(original.parent.iterdir()):
                if count >= 2000:
                    raise HTTPException(409, 'For mange ledsagefiler i originalens mappe.')
                if path.suffix.lower() in {'.srt', '.ass', '.ssa', '.sub', '.idx', '.vtt'} and path.name.startswith(original.stem+'.') and path.is_file() and not path.is_symlink():
                    companions.append({'name': path.relative_to(Path(picture['source'])).as_posix(),
                                       'output': stem+path.name[len(original.stem):], 'identity': fingerprint(path)})
            job = {'id': identifier, 'kind': 'mux', 'created': time.time(), 'status': 'queued',
                   'hash': picture['hash'], 'added': picture['added'], 'name': Path(picture['name']).stem,
                   'source': picture['source'], 'files': [{k: picture[k] for k in ('index', 'name', 'size', 'identity')} | {'output': stem+'.mkv'}],
                   'donor': {k: donor[k] for k in ('hash', 'added', 'source')} | {'files': [{k: donor[k] for k in ('index', 'name', 'size', 'identity')}]},
                   'proposal': proposal, 'delayMs': choice.delay_ms, 'confirmedSync': True,
                   'destination': str(target), 'work': str(work), 'location': choice.location, 'folder': choice.folder,
                   'companions': companions, 'originalBytes': picture['size'], 'bitrate': 0, 'error': None}
            await self.validate_sources(job)
            library.mkdir(exist_ok=True)
            if library.resolve(strict=True) != library or target.exists() or target.is_symlink():
                raise HTTPException(409, 'Målmappen er ændret eller findes allerede.')
            work.mkdir(mode=0o700)
            m.save(job)
            return m.public(job)

    async def tick(self):
        if not self.queue:
            return
        for task in (self.task, self.analysis_task):
            if task and task.done() and not task.cancelled():
                task.exception()  # Consume exceptions; processing writes durable error receipts.
        inventory = self.inventory()
        if inventory.get('scanned') and not (self.analysis_task and not self.analysis_task.done()):
            pending = []
            for entry in inventory['files']:
                try:
                    cached = json.loads((self.queue/('metadata-'+cache_key(entry)+'.json')).read_text())
                    if cached.get('schema') == SCHEMA: continue
                except (OSError, ValueError): pass
                pending.append(entry)
            if pending: self.analysis_task = asyncio.create_task(asyncio.to_thread(self.analyze_files, pending))
        jobs = sorted((j for j in self.manager.records() if j.get('kind') == 'mux' and j['status'] not in TERMINAL), key=lambda j: j['created'])
        if not (self.task and not self.task.done()):
            for job in jobs:
                path = self.queue/(job['id']+'.status.json')
                try: status = json.loads(path.read_text()) if path.exists() else {}
                except (OSError, ValueError):
                    atomic(path, {'id': job['id'], 'state': 'error', 'progress': 0, 'errorCode': 'mux'})
                    continue
                if status.get('state') in {'ready', 'error', 'cancelled'}:
                    continue
                try:
                    await self.validate_sources(job)
                    _, staging = self.manager.storage.directory(job['location'], '', writable=True)
                    _, destination = self.manager.storage.directory(job['location'], job['folder'], writable=True)
                    if Path(job['work']) != staging/'.incomplete'/('mediaseed-conversion-'+job['id']):
                        raise ValueError('Working directory changed')
                    if Path(job['destination']).parent not in {destination, destination/'Konverteret'}:
                        raise ValueError('Destination changed')
                    from mediaseed.conversion_mux_media import process
                    self.task = asyncio.create_task(asyncio.to_thread(process, self.queue, job, self.stop))
                except Exception:
                    logging.exception('Mux sources unavailable')
                    atomic(path, {'id': job['id'], 'state': 'error', 'progress': 0, 'errorCode': 'mux'})
                break

    async def close(self):
        self.stop.set()
        await asyncio.gather(*(t for t in (self.task, self.analysis_task) if t), return_exceptions=True)


def source_hashes(job):
    return {job['hash'], *([job['donor']['hash']] if job.get('kind') == 'mux' else [])}


def install_routes(app, authorized, mux):
    dependency = Depends(authorized)

    def writer(session):
        if session.get('role') == 'viewer':
            raise HTTPException(403, 'Muxing kræver skriveadgang.')

    @app.get('/api/conversions/mux/recommendations')
    async def advice(session=dependency):
        return mux.advice()

    @app.post('/api/conversions/mux/analyze', status_code=202)
    async def analyze(session=dependency):
        writer(session)
        return await mux.analyze()

    @app.post('/api/conversions/mux', status_code=202)
    async def create(body: MuxChoice, session=dependency):
        writer(session)
        try:
            return await mux.create(body)
        except (OSError, ValueError):
            raise HTTPException(409, 'Originalerne eller lageret er ændret. Analysér igen før muxing.') from None
