"""Explicit original-torrent removal after verified converted-copy seeding."""
import hashlib,json,time
from pathlib import Path
from fastapi import HTTPException
from pydantic import BaseModel,ConfigDict,Field
from mediaseed.conversion import fingerprint
from mediaseed.conversion_naming import output_name
from mediaseed.moves import file_path,IDENTITY
from mediaseed.upload_seeding import SEEDING,unchanged


class RemoveOriginal(BaseModel):
    model_config=ConfigDict(extra='forbid')
    confirmed:bool=False
    confirmation:str=Field(pattern=r'^[a-f0-9]{64}$')


class OriginalRemoval:
    def __init__(self,manager):self.manager=manager
    def job(self,identifier):
        job=next((j for j in self.manager.records() if j['id']==identifier),None)
        if not job:raise HTTPException(404,'Konverteringen findes ikke.')
        if job.get('kind')=='mux':raise HTTPException(409,'Begge mux-originaler bevares. Administrér originaltorrents hver for sig efter trackerens seedkrav.')
        if job['status']!='complete':raise HTTPException(409,'Konverteringen skal være færdig først.')
        return job
    async def preview(self,identifier):
        m=self.manager;job=self.job(identifier)
        previous=job.get('originalRemoval',{})
        if previous.get('status') in {'pending','removed'}:return {'status':previous['status'],'message':previous['message']}
        if not IDENTITY.fullmatch(job['hash']):raise HTTPException(409,'Originalens identitet mangler.')
        m.guard('/api/v2/torrents/delete',{'hashes':job['hash']})
        rows=await m.read('info',{})
        original=next((r for r in rows if r.get('hash')==job['hash']),None)
        if not original:raise HTTPException(409,'Originaltorrenten findes ikke i klienten. Ingen filer slettes.')
        if original.get('added_on')!=job['added']:
            raise HTTPException(409,'Originaltorrentens placering eller identitet er ændret. Ingen filer slettes.')
        _,root,videos,selected=await m.inspect(job['hash'])
        if str(root)!=job['source']:raise HTTPException(409,'Originaltorrentens placering er ændret.')
        current={v['index']:v for v in videos}
        if any(current.get(f['index'],{}).get('identity')!=f['identity'] for f in job['files']):raise HTTPException(409,'En originalfil er ændret siden konverteringen.')
        m.storage.directory(selected['location'],selected['folder'],writable=True)
        entries=await m.read('files',{'hash':job['hash']})
        if not entries or len(entries)>20000:raise HTTPException(409,'Originaltorrentens filliste kunne ikke bekræftes.')
        files=[]
        for entry in entries:
            path=file_path(root,entry['name']);identity=fingerprint(path)
            if entry.get('progress')!=1 or type(entry.get('size')) is not int or identity[0]!=entry['size']:raise HTTPException(409,'Originaltorrentens filer er ikke komplette og uændrede.')
            files.append({'name':entry['name'],'path':str(path),'identity':identity})
        destination=Path(job['destination'])
        try:client_destination='/downloads/'+destination.relative_to(m.storage.downloads).as_posix()
        except ValueError:raise HTTPException(409,'Den konverterede placering er ikke godkendt.') from None
        location=m.storage.describe(client_destination)
        if location is None:raise HTTPException(409,'Den konverterede placering er ikke godkendt.')
        _,approved=m.storage.directory(location['location'],location['folder'])
        if approved!=destination:raise HTTPException(409,'Den konverterede placering er ændret.')
        receipt=json.loads((destination/'.mediaseed-converted.json').read_text())
        if receipt.get('id')!=identifier or receipt.get('verified') is not True:raise HTTPException(409,'Konverteringens kvittering kunne ikke bekræftes.')
        outputs={}
        for entry in job['files']:
            name=output_name(entry);path=file_path(destination,name);identity=fingerprint(path)
            if receipt.get('identities',{}).get(name)!=identity:raise HTTPException(409,'Den konverterede video er ændret eller mangler.')
            outputs[str(path)]=identity
        client_root=Path(original['save_path'])
        client_content=Path(original.get('content_path') or client_root/Path(entries[0]['name']).parts[0])
        if not client_content.is_relative_to(client_root):raise HTTPException(409,'Originaltorrentens filplacering kunne ikke bekræftes.')
        source_content=file_path(root,client_content.relative_to(client_root).as_posix())
        if destination==source_content or destination.is_relative_to(source_content):raise HTTPException(409,'Den konverterede kopi ligger i originaltorrentens mappe.')
        for row in rows:
            if row.get('hash')==job['hash']:continue
            content=row.get('content_path')
            if not isinstance(content,str) or not content.startswith('/'):raise HTTPException(409,'Andre torrents placeringer kunne ikke kontrolleres.')
            other=Path(content)
            if (other==client_content or other.is_relative_to(client_content) or client_content.is_relative_to(other)
                    or any(client_root/Path(f['name'])==other or (client_root/Path(f['name'])).is_relative_to(other) for f in files)):
                raise HTTPException(409,'En anden torrent bruger originalens filer eller mappe. Ingen filer slettes.')
        with m.database() as db:
            exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='uploader_seeding'").fetchone()
            seeds=[json.loads(r[0]) for r in db.execute('SELECT document FROM uploader_seeding')] if exists else []
        covered=set();seed_hashes=[]
        for seed in seeds:
            if seed.get('status')!='seeding' or seed.get('hash')==job['hash'] or not unchanged(seed.get('files',[])):continue
            found={f['path'] for f in seed.get('files',[])}&outputs.keys()
            if not found:continue
            row=next((r for r in rows if r.get('hash')==seed.get('hash')),None)
            if not row or row.get('progress')!=1 or row.get('state') not in SEEDING or row.get('save_path','').rstrip('/')!=seed.get('savePath','').rstrip('/'):continue
            details=await m.read('files',{'hash':seed['hash']})
            if not details or len(details)!=len(seed['files']) or any(f.get('progress')!=1 for f in details):continue
            # Bind the client's file list to the exact persisted verified payload.
            expected={f['path'] for f in seed['files']}
            selected_seed=m.storage.describe(seed['savePath'])
            if selected_seed is None:continue
            _,seed_root=m.storage.directory(selected_seed['location'],selected_seed['folder'])
            if {str(file_path(seed_root,f['name'])) for f in details}!=expected:continue
            covered.update(found);seed_hashes.append(seed['hash'])
        if covered!=outputs.keys():raise HTTPException(409,'Start seeding af den konverterede pakke i MediaSeed først. Alle konverterede videoer skal være komplette og seede.')
        snapshot={'conversion':identifier,'hash':job['hash'],'added':job['added'],'source':str(root),'files':files,'outputs':outputs,'seedHashes':sorted(seed_hashes)}
        token=hashlib.sha256(json.dumps(snapshot,sort_keys=True).encode()).hexdigest()
        return {'status':'ready','confirmation':token,'name':original.get('name',job['name']),'sourceFolder':str(source_content),'deleteBytes':sum(f['identity'][0] for f in files),'fileCount':len(files),'files':[f['name'] for f in files[:30]],'convertedFolder':str(destination),'snapshot':snapshot,'storage':selected}
    async def remove(self,identifier,body):
        if body.confirmed is not True:raise HTTPException(422,'Bekræft at originaltorrenten og dens filer skal slettes.')
        m=self.manager
        async with m.lock,m.cleanup.lock:
            plan=await self.preview(identifier)
            if plan['status'] in {'pending','removed'}:return plan
            if body.confirmation!=plan['confirmation']:raise HTTPException(409,'Filer eller seeding er ændret. Åbn gennemgangen igen før sletning.')
            job=self.job(identifier);snapshot=plan['snapshot'];root=Path(snapshot['source']);stat=root.stat()
            job['originalRemoval']={'status':'pending','message':'Fjernelse bestilt. Venter på bekræftelse fra torrentklienten.','requestedAt':time.time(),'snapshot':snapshot,'storage':plan['storage'],'rootIdentity':[stat.st_dev,stat.st_ino]}
            m.save(job)
            try:
                response=await m.qbit('/api/v2/torrents/delete','POST',{'hashes':job['hash'],'deleteFiles':'true'})
                if response.status_code not in {200,204}:raise HTTPException(503,'Torrentklienten kunne ikke bekræfte sletningen. Kontrollér status før et nyt forsøg.')
            except Exception:
                # Delivery may have succeeded. Preserve intent and reconcile; never repeat blindly.
                raise HTTPException(503,'Sletningen kunne ikke bekræftes. Opdater status; der sendes ikke automatisk et nyt slettekald.') from None
            m.cleanup.forget(job['hash'])
            return {'status':'pending','message':job['originalRemoval']['message']}
    async def refresh(self):
        m=self.manager
        async with m.lock:
            pending=[j for j in m.records() if j.get('originalRemoval',{}).get('status')=='pending']
            if not pending:return
            rows=await m.read('info',{});present={r.get('hash') for r in rows}
            for job in pending:
                value=job['originalRemoval']
                if job['hash'] in present:continue
                try:
                    _,root=m.storage.directory(value['storage']['location'],value['storage']['folder'])
                    stat=root.stat()
                    if [stat.st_dev,stat.st_ino]!=value['rootIdentity']:continue
                    if any(Path(f['path']).exists() or Path(f['path']).is_symlink() for f in value['snapshot']['files']):continue
                except (OSError,HTTPException):continue
                value.update(status='removed',removedAt=time.time(),message='Originaltorrenten og dens filer er fjernet. Den konverterede kopi er bevaret.')
                m.save(job)
