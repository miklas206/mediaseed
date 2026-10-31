"""Opt-in conversion of completed torrent videos; originals and policies stay intact."""
import asyncio
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from mediaseed.moves import IDENTITY, file_path
from mediaseed.conversion_naming import converted_stem, converted_path, output_name, library_label, library_folder
from mediaseed.conversion_options import ConversionOptions,options_dict,keep_companion

VIDEO = {'.mkv', '.mp4', '.m4v', '.avi', '.mov', '.ts', '.m2ts', '.webm'}
TERMINAL = {'complete', 'cancelled'}
STABLE = {'uploading', 'stalledUP', 'forcedUP', 'stoppedUP', 'pausedUP', 'queuedUP'}


def atomic(path, document):
    temporary = path.with_name('.'+path.name+'-'+uuid.uuid4().hex)
    try:
        with temporary.open('x') as stream:
            json.dump(document, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def fingerprint(path):
    info = path.stat()
    if not path.is_file() or path.resolve(strict=True) != path:
        raise ValueError('Video must be a regular file without symbolic links')
    return [info.st_size, info.st_mtime_ns, info.st_dev, info.st_ino]


class ConversionChoice(BaseModel):
    model_config = ConfigDict(extra='forbid')
    torrent_hash: str = Field(max_length=64)
    indexes: list[int] = Field(min_length=1, max_length=10000)
    bitrate: Literal[20, 30, 40] = 20
    location: str = Field(max_length=64)
    folder: str = Field(default='', max_length=1000)
    release_tag: str = Field(default='MDTech', pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$')
    options: ConversionOptions | None=None


class ConversionPreferences(BaseModel):
    model_config=ConfigDict(extra='forbid')
    bitrate: Literal[20,30,40]=30
    release_tag: str=Field(default='MDTech',pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$')
    options: ConversionOptions=Field(default_factory=lambda:ConversionOptions(dolby_vision=False))


class Conversions:
    def __init__(self, database, qbit, storage, cleanup, queue):
        self.database, self.qbit, self.storage, self.cleanup = database, qbit, storage, cleanup
        self.queue = Path(queue) if queue else None
        self.lock = asyncio.Lock()
        self.analysis_lock = asyncio.Lock()
        with database() as db:
            db.execute('CREATE TABLE IF NOT EXISTS media_conversions (id TEXT PRIMARY KEY, created REAL NOT NULL, status TEXT NOT NULL, document TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS media_conversion_preferences (owner TEXT PRIMARY KEY, document TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS media_conversion_history_hidden (id TEXT PRIMARY KEY, cleared REAL NOT NULL)')
        from mediaseed.conversion_layout import flatten_pending_library
        flatten_pending_library(self)

    def preferences(self,owner):
        with self.database() as db:
            row=db.execute('SELECT document FROM media_conversion_preferences WHERE owner=?',(owner,)).fetchone()
        return ConversionPreferences.model_validate_json(row[0]).model_dump() if row else ConversionPreferences().model_dump()

    def save_preferences(self,owner,value):
        with self.database() as db:
            db.execute('INSERT INTO media_conversion_preferences VALUES(?,?) ON CONFLICT(owner) DO UPDATE SET document=excluded.document',(owner,value.model_dump_json()))
        return value.model_dump()

    def records(self):
        with self.database() as db:
            rows = db.execute("SELECT document FROM media_conversions WHERE status NOT IN ('complete','cancelled') OR id IN (SELECT id FROM media_conversions ORDER BY created DESC LIMIT 50) ORDER BY created DESC")
            return [json.loads(row[0]) for row in rows]

    def visible_records(self):
        with self.database() as db:hidden={row[0] for row in db.execute('SELECT id FROM media_conversion_history_hidden')}
        return [job for job in self.records() if job['id'] not in hidden or job['status'] not in {'complete','cancelled','error'}]

    def clear_history(self):
        with self.database() as db:
            before=db.total_changes
            db.execute("INSERT OR IGNORE INTO media_conversion_history_hidden SELECT id,? FROM media_conversions WHERE status IN ('complete','cancelled','error')",(time.time(),))
            return db.total_changes-before

    def save(self, job):
        with self.database() as db:
            db.execute('INSERT INTO media_conversions VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,document=excluded.document',
                       (job['id'], job['created'], job['status'], json.dumps(job)))

    def readiness(self):
        reason = 'GPU conversion is not installed. Install the MediaSeed conversion worker and grant an NVIDIA GPU.'
        if self.queue:
            try:
                value = json.loads((self.queue/'worker.json').read_text())
                if not 0 <= time.time()-value['at'] < 45:
                    reason = 'The conversion worker is offline. Originals are preserved.'
                elif value.get('ready') is True:
                    return {'ready': True, 'reason': '', 'encoder': 'NVIDIA HEVC','capabilities':value.get('capabilities',[])}
                else:
                    reason = 'The NVIDIA HEVC test failed. Check GPU access, driver and converter tools.'
            except (OSError, ValueError, TypeError, KeyError):
                pass
        return {'ready': False, 'reason': reason, 'encoder': 'NVIDIA HEVC'}

    def guard(self, path, payload):
        if path.rsplit('/', 1)[-1] not in {'delete', 'setLocation', 'recheck', 'renameFile', 'renameFolder'}:
            return
        hashes = str((payload or {}).get('hashes', (payload or {}).get('hash', ''))).lower().split('|')
        if any(j['status'] not in TERMINAL and (j['hash'] in hashes or 'all' in hashes) for j in self.records()):
            raise HTTPException(409, 'Finish or cancel the conversion before removing, moving or checking its originals')

    async def read(self, endpoint, payload):
        response = await self.qbit('/api/v2/torrents/'+endpoint, payload=payload)
        if response.status_code != 200:
            raise HTTPException(503, 'Torrent information is unavailable')
        try:
            result = response.json()
            if not isinstance(result, list): raise ValueError()
            return result
        except (ValueError, TypeError):
            raise HTTPException(503, 'Torrent information is invalid') from None

    async def inspect(self, identifier):
        if not IDENTITY.fullmatch(identifier): raise HTTPException(422, 'Choose a torrent')
        rows = await self.read('info', {'hashes': identifier, 'limit': 1})
        row = next((r for r in rows if str(r.get('hash', '')).lower() == identifier.lower()), None)
        if not row or row.get('progress') != 1 or row.get('state') not in STABLE:
            raise HTTPException(409, 'Choose a completed torrent. Downloading, moving and checking torrents cannot be converted.')
        selected = self.storage.describe(str(row.get('save_path', '')).rstrip('/'))
        if not selected: raise HTTPException(409, 'Original is outside approved storage')
        _, root = self.storage.directory(selected['location'], selected['folder'])
        files = await self.read('files', {'hash': identifier})
        if not 1 <= len(files) <= 20000: raise HTTPException(409, 'Torrent file list is unavailable or too large')
        videos = []
        for index, entry in enumerate(files):
            if Path(str(entry.get('name', ''))).suffix.lower() not in VIDEO: continue
            source = file_path(root, entry['name'])
            if entry.get('progress') != 1 or type(entry.get('size')) is not int:
                continue
            try: identity = fingerprint(source)
            except (OSError, ValueError): continue
            if identity[0] != entry['size']: continue
            videos.append({'index': index, 'name': entry['name'], 'size': entry['size'], 'identity': identity})
        return row, root, videos, selected

    async def create(self, choice):
        if not self.readiness()['ready']: raise HTTPException(409, self.readiness()['reason'])
        if choice.options is not None and 'conversion-options-v1' not in self.readiness().get('capabilities',[]):
            raise HTTPException(409, 'Update the conversion worker before using conversion options. Existing jobs are preserved.')
        async with self.lock, self.cleanup.lock:
            if len([j for j in self.records() if j['status'] not in TERMINAL]) >= 50:
                raise HTTPException(409, 'Finish or cancel pending conversions first')
            row, source_root, videos, source_storage = await self.inspect(choice.torrent_hash)
            indexes = set(choice.indexes)
            chosen = [f for f in videos if f['index'] in indexes]
            if len(indexes) != len(choice.indexes) or len(chosen) != len(indexes):
                raise HTTPException(422, 'Choose available complete video files')
            if any(j['hash'] == row['hash'] and j['status'] not in TERMINAL for j in self.records()):
                raise HTTPException(409, 'This torrent already has a conversion')
            _, destination = self.storage.directory(choice.location, choice.folder, writable=True)
            identifier = uuid.uuid4().hex
            chosen = [{**f, 'output': converted_path(f['name'], choice.bitrate, choice.release_tag)} for f in chosen]
            label = library_folder(chosen,row.get('name','Video'),identifier)
            library = destination if choice.location=='converted-movies' or destination.name=='Konverteret' else destination/'Konverteret'
            target = library/label
            if target.exists() or target.is_symlink():
                raise HTTPException(409, 'A converted release with this name already exists. Choose another tag or destination.')
            if source_storage['folder'] and target.is_relative_to(source_root):
                raise HTTPException(409, 'Choose a destination outside the original torrent folder')
            # Output must remain outside the torrent's release tree so qBittorrent
            # cleanup cannot remove the converted copy with the original release.
            for entry in chosen:
                top = source_root/Path(entry['name']).parts[0]
                if target == top or target.is_relative_to(top):
                    raise HTTPException(409, 'Choose a destination outside the original release folder')
            names = [output_name(f) for f in chosen]
            if len(set(n.casefold() for n in names)) != len(names):
                raise HTTPException(409, 'Converted video names would collide')
            companions = {}
            extensions = {'.srt','.ass','.ssa','.sub','.idx','.vtt','.nfo','.jpg','.jpeg','.png'}
            for entry in chosen:
                video_path = file_path(source_root, entry['name'])
                for count, path in enumerate(video_path.parent.iterdir()):
                    if count >= 2000: raise HTTPException(409, 'Too many companion files in the video folder')
                    if (path.suffix.lower() in extensions and path.name.startswith(video_path.stem+'.')
                            and not path.is_symlink() and path.is_file()
                            and keep_companion(path,video_path.stem,choice.options.model_dump() if choice.options else None)):
                        relative = path.relative_to(source_root).as_posix()
                        output = Path(entry['output']).with_suffix('').as_posix()+path.name[len(video_path.stem):]
                        companions[relative] = {'name': relative, 'output': output, 'identity': fingerprint(path)}
            all_names = names+[f['output'] for f in companions.values()]
            if len({n.casefold() for n in all_names}) != len(all_names):
                raise HTTPException(409, 'Converted file names would collide')
            required = sum(f['size'] for f in chosen)+1024**3
            if shutil.disk_usage(destination).free < required:
                raise HTTPException(409, 'Not enough space for a separate converted copy. Originals must remain available for seeding.')
            # Worker bind mounts are at each approved storage root, not every
            # arbitrary library subfolder. Keep staging in that shared root.
            _, staging_root = self.storage.directory(choice.location, '', writable=True)
            self.storage.incomplete(self.storage.destination(choice.location, ''))
            work = staging_root/'.incomplete'/('mediaseed-conversion-'+identifier)
            library.mkdir(exist_ok=True)
            if library.resolve(strict=True) != library or not library.is_dir():
                raise HTTPException(409, 'Converted library folder must not be a symbolic link')
            work.mkdir(mode=0o700)
            self.queue.mkdir(mode=0o700, parents=True, exist_ok=True)
            job = {'id': identifier, 'created': time.time(), 'status': 'queued', 'hash': row['hash'],
                   'added': row.get('added_on'), 'name': str(row.get('name', ''))[:500], 'bitrate': choice.bitrate,
                   'source': str(source_root), 'work': str(work), 'destination': str(target),
                   'releaseTag': choice.release_tag, 'files': chosen, 'companions': list(companions.values()), 'error': None, 'originalBytes': sum(f['size'] for f in chosen)}
            if choice.options is not None:job['options']=choice.options.model_dump()
            self.save(job)  # Recovery can recreate a missing request after a crash.
            atomic(self.queue/(identifier+'.request.json'), job)
            return self.public(job)

    def advised_excluded(self):
        with self.database() as db:
            jobs=[json.loads(row[0]) for row in db.execute("SELECT document FROM media_conversions WHERE status != 'cancelled'")]
        excluded=set()
        for job in jobs:
            if job['status']!='complete':
                excluded.add(job['hash']);continue
            # History is evidence, not proof that the converted media still exists.
            # Keep hidden/renamed real outputs excluded, but allow deleted copies
            # to appear in a later analysis without changing their old receipt.
            try:
                for entry in job['files']:
                    path=file_path(Path(job['destination']),output_name(entry))
                    try:fingerprint(path)
                    except FileNotFoundError:continue
                    excluded.add(job['hash']);break
            except (OSError,ValueError,KeyError,HTTPException):
                excluded.add(job['hash']) # Unavailable storage is not deletion.
        return excluded

    async def analyze(self):
        if not self.queue or not self.readiness()['ready']:
            raise HTTPException(409, self.readiness()['reason'])
        async with self.analysis_lock:
            rows=await self.read('info', {'filter':'completed','limit':500})
            excluded=self.advised_excluded()
            candidates=[r for r in rows if r.get('progress')==1 and r.get('state') in STABLE and r.get('hash') not in excluded
                        and self.storage.describe(str(r.get('save_path','')).rstrip('/'))]
            if any(r['id']=='movies' for r in self.storage.locations):
                candidates=[r for r in candidates if self.storage.describe(str(r.get('save_path','')).rstrip('/'))['location']=='movies']
            candidates.sort(key=lambda r:int(r.get('size') or r.get('total_size') or 0),reverse=True)
            entries=[];failures=0
            for row in candidates[:40]:
                try:
                    _,root,videos,_=await self.inspect(row['hash'])
                    for video in videos:
                        if len(entries)>=120:break
                        entries.append({'hash':row['hash'],'title':str(row.get('name','Video'))[:500],
                                        'index':video['index'],'name':video['name'],'path':str(root/video['name']),'identity':video['identity']})
                except HTTPException:failures+=1
            atomic(self.queue/'analysis-inventory.json', {'at':time.time(),'files':entries,'truncated':len(candidates)>40 or len(entries)>=120,'failures':failures})
            return {'accepted':True,'files':len(entries)}

    def advice(self, bitrate):
        from mediaseed.conversion_advice import cache_key, estimate
        if not self.queue:return {'rows':[],'total':0,'pending':0,'scanned':False}
        try:inventory=json.loads((self.queue/'analysis-inventory.json').read_text())
        except (OSError,ValueError):return {'rows':[],'total':0,'pending':0,'scanned':False}
        rows=[];pending=0;errors=inventory.get('failures',0)
        excluded=self.advised_excluded()
        for entry in inventory['files']:
            if entry['hash'] in excluded:continue
            try:data=json.loads((self.queue/('analysis-'+cache_key(entry)+'.json')).read_text())
            except (OSError,ValueError):pending+=1;continue
            if data.get('error'):errors+=1;continue
            try:
                if fingerprint(Path(entry['path']))!=entry['identity']:errors+=1;continue
                result=estimate(entry,data['metadata'],bitrate)
            except (OSError,ValueError,KeyError):errors+=1;continue
            rows.append({**{k:entry[k] for k in ('hash','title','index','name')},**result})
        rows.sort(key=lambda r:r['savingBytes'],reverse=True)
        return {'rows':rows,'total':len(inventory['files']),'pending':pending,'errors':errors,'scanned':True,
                'at':inventory['at'],'truncated':inventory['truncated']}

    def public(self, job):
        return {k: job[k] for k in ('id', 'created', 'status', 'hash', 'name', 'bitrate', 'error', 'originalBytes')} | {
            'fileCount': len(job['files']), 'progress': job.get('progress', 0),
            'outputBytes': job.get('outputBytes'), 'potentialSavingBytes': max(0, job['originalBytes']-job.get('outputBytes', job['originalBytes'])),
            'destination': job['destination'], 'originalsRetained': job.get('originalRemoval',{}).get('status')!='removed',
            'originalRemoval':{k:v for k,v in job.get('originalRemoval',{}).items() if k in {'status','message','requestedAt','removedAt'}},'options':options_dict(job.get('options')),
            **{k: job.get(k) for k in ('updated','started','fps','speed','etaSeconds','currentFile','worker')},
            'cancelling': bool(self.queue and (self.queue/(job['id']+'.cancel')).exists() and job['status'] not in TERMINAL)}

    async def tick(self):
        if not self.queue: return
        async with self.lock:
            from mediaseed.conversion_layout import flatten_pending_library
            flatten_pending_library(self)
            for job in self.records():
                if job['status'] in TERMINAL: continue
                try:
                    request = self.queue/(job['id']+'.request.json')
                    if not request.exists(): atomic(request, job)
                    status_path = self.queue/(job['id']+'.status.json')
                    if not status_path.exists(): continue
                    status = json.loads(status_path.read_text())
                    if status.get('id') != job['id']: raise ValueError('Wrong conversion identity')
                    state = status.get('state')
                    if state not in {'encoding', 'verifying', 'ready', 'error', 'cancelled'}: continue
                    job.update(progress=min(100, max(0, float(status.get('progress', 0)))), status=state)
                    for key in ('updated','started','fps','speed','etaSeconds','currentFile','worker'):
                        if key in status: job[key] = status[key]
                    if state == 'error':
                        job['error'] = {'selection':'Ingen lydspor matcher de valgte sprog. Originalen er bevaret. Vælg andre sprog eller alle lydspor.',
                                        'no_saving':'The converted copy is not smaller. Originals are preserved; cancel this job to release its temporary space.',
                                        'space':'Not enough working space. Originals are preserved.',
                                        'validation':'Video, HDR, audio or subtitle validation failed. Originals are preserved.'}.get(status.get('errorCode'),
                                        'Conversion failed. Originals are preserved. Check the conversion log or cancel and try again.')
                    if state == 'cancelled': job['error'] = None
                    if state == 'ready':
                        if (self.queue/(job['id']+'.cancel')).exists(): continue
                        await self.publish(job, status)
                    self.save(job)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    job.update(status='error', error='Could not verify or publish the converted copy. Originals are preserved.')
                    self.save(job)

    async def publish(self, job, status):
        row, source_root, videos, _ = await self.inspect(job['hash'])
        if row.get('added_on') != job['added'] or str(source_root) != job['source']:
            raise ValueError('Torrent changed')
        current = {v['index']: v for v in videos}
        if any(current.get(f['index'], {}).get('identity') != f['identity'] for f in job['files']):
            raise ValueError('Original changed')
        work, target = Path(job['work']), Path(job['destination'])
        output = work/'result'
        # A verified receipt lives inside the complete directory and follows its
        # atomic rename, allowing recovery after publish but before the DB commit.
        existing = target if target.exists() else output
        receipt = json.loads((existing/'.mediaseed-converted.json').read_text())
        if job.get('options') is not None and receipt.get('options')!=options_dict(job['options']):
            raise ValueError('Output settings do not match the requested options')
        if receipt.get('id') != job['id'] or receipt.get('verified') is not True:
            raise ValueError('Missing verified receipt')
        if receipt.get('files') != [output_name(f) for f in job['files']]:
            raise ValueError('Wrong output manifest')
        total = 0
        for name in receipt['files']:
            path = file_path(existing, name)
            identity = fingerprint(path)
            if receipt.get('identities', {}).get(name) != identity: raise ValueError('Output changed')
            total += identity[0]
        for entry in job.get('companions', []):
            name = entry['name']
            if fingerprint(file_path(source_root, name)) != entry['identity']:
                raise ValueError('Companion changed')
            if fingerprint(file_path(existing, entry.get('output', name))) != receipt.get('companionIdentities', {}).get(entry.get('output', name)):
                raise ValueError('Companion output changed')
        if total <= 0 or total >= job['originalBytes']: raise ValueError('Output does not save space')
        if not target.exists(): output.rename(target)
        job.update(status='complete', progress=100, outputBytes=total, error=None)

    async def run(self):
        while True:
            await self.tick()
            await asyncio.sleep(2)

    async def cancel(self, identifier):
        import fcntl
        async with self.lock:
            job = next((j for j in self.records() if j['id'] == identifier), None)
            if job is None: raise HTTPException(404, 'Conversion not found')
            if job['status'] in TERMINAL: return self.public(job)
            if self.queue is None: raise HTTPException(409, 'Restore conversion worker configuration before cancelling this job')
            atomic(self.queue/(identifier+'.cancel'), {'id': identifier})
            with (self.queue/(identifier+'.lock')).open('a') as handle:
                try: fcntl.flock(handle, fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError: return {'accepted': True}
                work = Path(job['work'])
                if (work.parent.name != '.incomplete' or work.name != 'mediaseed-conversion-'+identifier
                        or work.resolve() != work):
                    raise HTTPException(409, 'Conversion workspace changed; review storage before cancellation')
                if work.exists(): await asyncio.to_thread(shutil.rmtree, work)
                job.update(status='cancelled', error=None)
                self.save(job)
            return self.public(job)


def install_conversions(app, authorized, database, qbit, storage, cleanup, queue):
    manager = Conversions(database, qbit, storage, cleanup, queue)
    app.state.conversions = manager
    from mediaseed.conversion_originals import OriginalRemoval,RemoveOriginal
    originals=OriginalRemoval(manager);app.state.conversion_originals=originals
    dependency = Depends(authorized)

    @app.get('/api/conversions')
    async def listing(session=dependency):
        try:await originals.refresh()
        except HTTPException:pass
        jobs=manager.visible_records()
        queued=sorted((j for j in jobs if j['status']=='queued'),key=lambda j:(j['created'],j['id']))
        positions={j['id']:index+1 for index,j in enumerate(queued)}
        return {**manager.readiness(), 'jobs': [{**manager.public(j), 'queuePosition':positions.get(j['id'])} for j in jobs]}

    @app.get('/api/conversions/{identifier}/original')
    async def original_preview(identifier:str,session=dependency):
        if session.get('role')=='viewer':raise HTTPException(403,'Fjernelse kræver skriveadgang.')
        try:return {k:v for k,v in (await originals.preview(identifier)).items() if k not in {'snapshot','storage'}}
        except (OSError,ValueError,KeyError):raise HTTPException(409,'Originalen eller den konverterede kopi kunne ikke kontrolleres. Ingen filer slettes.') from None

    @app.post('/api/conversions/{identifier}/original/remove',status_code=202)
    async def remove_original(identifier:str,body:RemoveOriginal,session=dependency):
        if session.get('role')=='viewer':raise HTTPException(403,'Fjernelse kræver skriveadgang.')
        try:return await originals.remove(identifier,body)
        except (OSError,ValueError,KeyError):raise HTTPException(409,'Originalen eller den konverterede kopi kunne ikke kontrolleres. Kontrollér status før et nyt forsøg.') from None

    @app.post('/api/conversions/history/clear')
    async def clear_history(session=dependency):
        if session.get('role')=='viewer':raise HTTPException(403,'Historik kræver skriveadgang.')
        async with manager.lock:cleared=manager.clear_history()
        return {'cleared':cleared,'mediaPreserved':True}

    @app.get('/api/conversions/preferences')
    async def preferences(session=dependency):
        return manager.preferences(str(session.get('id','local')))

    @app.put('/api/conversions/preferences')
    async def save_preferences(body: ConversionPreferences, session=dependency):
        return manager.save_preferences(str(session.get('id','local')),body)

    @app.get('/api/conversions/recommendations')
    async def advice(bitrate: Literal['20','30','40'] = '30', session=dependency):
        return manager.advice(int(bitrate))

    @app.post('/api/conversions/analyze', status_code=202)
    async def analyze(session=dependency):
        return await manager.analyze()

    @app.get('/api/conversions/torrents/{identifier}')
    async def files(identifier: str, session=dependency):
        row, _, videos, selected = await manager.inspect(identifier)
        return {'name': row.get('name'), 'files': [{k:v[k] for k in ('index','name','size')} for v in videos],
                'storage': selected, 'destinationStorage': {'location': selected['location'], 'folder': ''}}

    @app.post('/api/conversions', status_code=202)
    async def create(body: ConversionChoice, session=dependency):
        try: return await manager.create(body)
        except OSError: raise HTTPException(409, 'Conversion storage is unavailable') from None

    @app.post('/api/conversions/{identifier}/cancel', status_code=202)
    async def cancel(identifier: str, session=dependency):
        return await manager.cancel(identifier)
    return manager
