"""Manual tracker publication followed by verified local seeding; no tracker POSTs."""
import asyncio,json,math,re,time
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import Depends,HTTPException,Request,Query
from pydantic import BaseModel,ConfigDict,Field
from mediaseed.cleanup import source_hashes,announce_hosts
from mediaseed.moves import file_path
from mediaseed.tracker_sites import origin,download_address
from mediaseed.upload_engine.upload_helper import decode_torrent


PAUSED={'pausedUP','pausedDL','stoppedUP','stoppedDL','error','missingFiles'}
SEEDING={'uploading','stalledUP','forcedUP','queuedUP'}


def torrent_payload(content):
    try:
        hashes=source_hashes(content=content)
        tree=decode_torrent(content);info=tree[b'info']
        if len(hashes)!=1 or len(hashes[0])!=40 or info.get(b'private')!=1:raise ValueError()
        name=info[b'name'].decode('utf-8')
        files=[('/'.join([name,*[p.decode('utf-8') for p in f[b'path']]]),f[b'length']) for f in info[b'files']] if b'files' in info else [(name,info[b'length'])]
        if not files or len(files)>200 or len(set(n for n,_ in files))!=len(files) or any(type(size) is not int or size<0 for _,size in files):raise ValueError()
        length=info[b'piece length'];pieces=info[b'pieces']
        if type(length) is not int or not 0<length<=16*1024**2 or not isinstance(pieces,bytes) or len(pieces)!=20*math.ceil(sum(size for _,size in files)/length):raise ValueError()
        for name,_ in files:file_path(Path('/'),name)
        return tree,hashes[0],files,(info[b'name'],files,length,pieces)
    except (ValueError,TypeError,KeyError,UnicodeError,IndexError):raise HTTPException(422,'Vælg en gyldig privat v1-torrentfil fra trackeren.') from None



NORDICBYTES_ANNOUNCE_HOSTS=frozenset({'t.nordicbytes.org','t.nordicswarm.org','t.polarswarm.org'})


def matching_announcements(expected,actual,account):
    """Allow verified NordicBytes announce aliases; retain account and URL checks."""
    def address(value):
        if not isinstance(value,bytes):raise ValueError()
        text=value.decode('utf-8')
        if any(ord(c)<33 for c in text):raise ValueError()
        url=urlsplit(text)
        if url.scheme not in {'http','https'} or not url.hostname or url.username or url.password or url.fragment:raise ValueError()
        host=url.hostname.lower()
        if (account.get('provider','nordicbytes')=='nordicbytes' and origin(account)=='https://nordicbytes.org'
                and host in NORDICBYTES_ANNOUNCE_HOSTS):host='nordicbytes-announce'
        return url.scheme,host,url.port or (443 if url.scheme=='https' else 80),url.path,url.query
    try:
        primary=address(expected.get(b'announce'))
        if address(actual.get(b'announce'))!=primary:return False
        allowed={primary}
        for tier in expected.get(b'announce-list',[]):
            if not isinstance(tier,list):return False
            allowed.update(address(v) for v in tier)
        tiers=actual.get(b'announce-list',[])
        if not isinstance(tiers,list) or len(tiers)>100:return False
        for tier in tiers:
            if not isinstance(tier,list) or len(tier)>100 or any(address(v) not in allowed for v in tier):return False
        return True
    except (ValueError,UnicodeError,TypeError,AttributeError):return False

def local_payload(bridge,job,files):
    source=job.get('source') or job.get('destination') or {};savepath=source.get('clientSavePath')
    if not isinstance(savepath,str):raise HTTPException(409,'Pakken mangler en godkendt seedingmappe.')
    selected=bridge.storage.describe(savepath)
    if selected is None:raise HTTPException(409,'Seedingmappen er ikke monteret i MediaSeed.')
    _,root=bridge.storage.directory(selected['location'],selected['folder'])
    fingerprints=[]
    for name,size in files:
        path=file_path(root,name)
        try:
            stat=path.stat()
            if not path.is_file() or stat.st_size!=size:raise OSError()
        except OSError:raise HTTPException(409,'En fil mangler eller har forkert størrelse i seedingmappen. Ingen filer flyttes eller downloades.') from None
        fingerprints.append({'path':str(path),'size':stat.st_size,'mtime':stat.st_mtime_ns,'inode':stat.st_ino,'device':stat.st_dev})
    return savepath,fingerprints


def unchanged(files):
    for f in files:
        path=Path(f['path'])
        try:
            stat=path.stat()
            if path.resolve()!=path or not path.is_file() or (stat.st_size,stat.st_mtime_ns,stat.st_ino,stat.st_dev)!=(f['size'],f['mtime'],f['inode'],f['device']):return False
        except OSError:return False
    return True


class UploadSeeding:
    def __init__(self,bridge,qbit,vpn_status,data):
        self.bridge,self.qbit,self.vpn_status=bridge,qbit,vpn_status
        self.root=Path(data)/'uploader-seeding';self.root.mkdir(exist_ok=True,mode=0o700)
        with bridge.database() as db:db.execute('CREATE TABLE IF NOT EXISTS uploader_seeding(job TEXT PRIMARY KEY,document TEXT NOT NULL)')

    def read(self,identifier):
        with self.bridge.database() as db:row=db.execute('SELECT document FROM uploader_seeding WHERE job=?',(identifier,)).fetchone()
        return json.loads(row[0]) if row else {'status':'not_started','message':'Upload pakken på trackeren, og start derefter seeding her.'}

    def save(self,identifier,value):
        value['updatedAt']=time.time()
        with self.bridge.database() as db:db.execute('INSERT INTO uploader_seeding VALUES(?,?) ON CONFLICT(job) DO UPDATE SET document=excluded.document',(identifier,json.dumps(value)))

    @staticmethod
    def public(value):return {k:v for k,v in value.items() if k in {'status','message','hash','progress','updatedAt','url'}}

    async def row(self,hash_):
        response=await self.qbit('/api/v2/torrents/info',payload={'hashes':hash_})
        try:rows=response.json()
        except ValueError:rows=None
        if response.status_code!=200 or not isinstance(rows,list):raise HTTPException(503,'Torrentklientens status kunne ikke læses.')
        return next((row for row in rows if row.get('hash')==hash_),None)

    async def command(self,name,hash_):
        response=await self.qbit('/api/v2/torrents/'+name,'POST',{'hashes':hash_})
        if response.status_code not in {200,204}:raise HTTPException(503,'Torrentklienten kunne ikke udføre '+name+'.')

    async def enqueue(self,identifier,account,link,content):
        async with self.bridge.lock:
            job=await self.bridge.job(identifier)
            if not job.get('hasTorrent'):raise HTTPException(409,'Klargør en lokal torrentfil til pakken først.')
            canonical_url=None
            if content is None:
                parsed=urlsplit(link)
                if parsed.scheme!='https' or parsed.netloc!=urlsplit(origin(account)).netloc or parsed.query or parsed.fragment or not re.fullmatch(r'/torrents/\d+',parsed.path):raise HTTPException(422,'Indsæt linket til den færdige upload på den valgte tracker, eller vælg torrentfilen.')
                remote_id=parsed.path.rsplit('/',1)[1]
                response=await self.bridge.tracker_request(account,'/api/torrents/'+remote_id)
                if response.status_code!=200:raise HTTPException(409,'Uploaden kunne ikke læses. Hent i stedet torrentfilen fra trackeren og vælg den her.')
                try:attributes=response.json()['data']['attributes'];address=download_address(account,attributes['download_link'])
                except (ValueError,KeyError,TypeError):raise HTTPException(409,'Trackerens torrentlink mangler. Hent torrentfilen fra trackeren og vælg den her.') from None
                response=await self.bridge.tracker_request(account,address.path+('?' + address.query if address.query else ''))
                if response.status_code!=200:raise HTTPException(409,'Torrentfilen kunne ikke hentes. Vælg filen fra trackeren her i stedet.')
                content=response.content;canonical_url=origin(account)+parsed.path
            if not content or len(content)>1024**2:raise HTTPException(422,'Torrentfilen må højst fylde 1 MiB.')
            prepared=await self.bridge.upstream('/api/upload-helper/'+identifier+'/download')
            if prepared.status_code!=200:raise HTTPException(409,'Den klargjorte torrentfil mangler.')
            expected,_,_,signature=torrent_payload(prepared.content)
            tree,hash_,files,actual=torrent_payload(content)
            if actual!=signature:raise HTTPException(409,'Trackerens torrent svarer ikke til pakkens filnavne, størrelser og filstykker. Ingen filer flyttes eller downloades.')
            if not matching_announcements(expected,tree,account):raise HTTPException(409,'Torrenten bruger en anden trackeradresse end pakken. Hent torrenten fra den samme trackerkonto.')
            savepath,fingerprints=local_payload(self.bridge,job,files)
            existing=self.read(identifier)
            if existing.get('hash')==hash_ and existing.get('status') not in {'failed','paused','not_started','seeding'}:return self.public(existing)
            current=await self.row(hash_)
            if current and current.get('save_path','').rstrip('/')!=savepath.rstrip('/'):raise HTTPException(409,'Torrenten findes allerede i en anden mappe. Placeringen bliver ikke ændret.')
            if current and current.get('state') in SEEDING and current.get('progress')==1:
                value={'status':'seeding','message':'Torrenten seeder allerede fra den valgte mappe.','hash':hash_,'savePath':savepath,'files':fingerprints,'url':canonical_url}
            else:
                target=self.root/(identifier+'.torrent');target.write_bytes(content);target.chmod(0o600)
                value={'status':'queued','message':'Torrenten tilføjes på pause. Derefter kontrolleres alle filer.','hash':hash_,'savePath':savepath,'files':fingerprints,'createdAt':time.time(),'url':canonical_url,'newTorrent':current is None}
            self.save(identifier,value);return self.public(value)

    async def advance(self,identifier,value):
        hash_=value['hash'];row=await self.row(hash_);phase=value['status']
        if not unchanged(value['files']):raise HTTPException(409,'Mediefilerne er ændret. Torrenten forbliver på pause; kontrollér filerne før et nyt forsøg.')
        if row and row.get('save_path','').rstrip('/')!=value['savePath'].rstrip('/'):raise HTTPException(409,'Torrentens mappe er ændret. Automatisk start er afbrudt.')
        if phase=='queued':
            if row is None:
                from mediaseed.features import add_stopped
                await add_stopped(self.qbit,1,content=(self.root/(identifier+'.torrent')).read_bytes(),savepath=value['savePath'])
                value.update(status='awaiting_client',addedByUs=True,message='Torrenten er tilføjet på pause. Venter på torrentklienten.')
            else:
                if not value.get('addedByUs'):value['newTorrent']=False
                if row.get('state') not in PAUSED:await self.command('stop',hash_)
                value.update(status='stopping',message='Torrenten sættes på pause før filkontrol.')
        elif phase=='awaiting_client':
            if row is not None:value.update(status='queued')
            elif time.time()-value['createdAt']>60:raise HTTPException(409,'Torrenten kunne ikke findes i klienten. Prøv igen; ingen download er startet.')
        elif row is None:raise HTTPException(409,'Torrenten er fjernet fra klienten. Start seeding igen for at tilføje den.')
        elif phase=='stopping':
            if row.get('state') in PAUSED:
                value.update(status='checking',message='Kontrollerer filerne. Seeding starter først ved 100 %.',checkAt=time.time(),checkObserved=False)
                self.save(identifier,value)
                await self.command('recheck',hash_)
        elif phase=='checking':
            if str(row.get('state','')).startswith('checking'):
                value.update(checkObserved=True,progress=row.get('recheck_progress',row.get('progress',0)))
            elif value.get('checkObserved') or (value.get('newTorrent') and value.get('addedByUs') and row.get('progress')==1 and row.get('state') in {'pausedUP','stoppedUP'}):
                files=await self.qbit('/api/v2/torrents/files',payload={'hash':hash_})
                try:details=files.json()
                except ValueError:details=None
                if (files.status_code!=200 or not isinstance(details,list) or len(details)!=len(value['files']) or row.get('progress')!=1 or any(f.get('progress')!=1 for f in details)):
                    await self.command('stop',hash_)
                    raise HTTPException(409,'Filkontrollen fandt manglende eller forkerte data. Torrenten bliver på pause og downloader ikke.')
                value.update(status='waiting_vpn',progress=1,message='Filkontrollen er bestået. Kontrollerer VPN før seeding.')
            elif time.time()-value['checkAt']>60:raise HTTPException(409,'Filkontrollen kunne ikke bekræftes. Torrenten bliver på pause; prøv Start seeding igen.')
        elif phase=='waiting_vpn':
            if row.get('progress')!=1:raise HTTPException(409,'Torrenten er ikke længere komplet. Seeding er ikke startet.')
            vpn=await self.vpn_status()
            if not vpn.get('downloadsEnabled'):value.update(message='Filkontrollen er bestået. Seeding afventer en bekræftet VPN-forbindelse.')
            else:
                await self.command('start',hash_)
                value.update(status='starting',message='Filkontrol og VPN er godkendt. Starter seeding.')
        elif phase=='starting':
            if row.get('state') in SEEDING and row.get('progress')==1:value.update(status='seeding',message='Torrenten seeder.' if row['state']!='queuedUP' else 'Torrenten er komplet og står i kø til seeding.')
            elif row.get('progress')!=1:
                await self.command('stop',hash_);raise HTTPException(409,'Torrenten er ikke komplet og er sat på pause.')
        self.save(identifier,value)

    async def tick(self):
        async with self.bridge.lock:
            with self.bridge.database() as db:rows=db.execute('SELECT job,document FROM uploader_seeding').fetchall()
            for identifier,raw in rows:
                value=json.loads(raw)
                if value['status'] in {'failed','paused','seeding'}:continue
                try:await self.advance(identifier,value)
                except Exception as exc:
                    try:await self.command('stop',value['hash'])
                    except Exception:pass
                    value.update(status='failed',message=exc.detail if isinstance(exc,HTTPException) and isinstance(exc.detail,str) else 'Seeding kunne ikke fortsætte. Kontrollér torrentklienten og prøv igen.')
                    self.save(identifier,value)

    async def run(self):
        from mediaseed.background import run_background
        await run_background([('upload-seeding',self.tick)],interval=2)


class SeedLink(BaseModel):
    model_config=ConfigDict(extra='forbid')
    tracker:str=Field(min_length=1,max_length=64)
    confirmed:bool=False
    url:str=Field(max_length=2048)


def install_seeding(app,admin,bridge,qbit,vpn_status,data):
    manager=UploadSeeding(bridge,qbit,vpn_status,data);app.state.upload_seeding=manager
    @app.get('/api/uploader/jobs/{identifier}/seeding')
    async def status(identifier:str,session=Depends(admin)):
        if not re.fullmatch('[a-f0-9]{24}',identifier):raise HTTPException(404)
        value=manager.read(identifier)
        if value.get('status')=='seeding':
            row=await manager.row(value['hash'])
            if row is None or row.get('state') not in SEEDING:
                value.update(status='paused',message='Torrenten er ikke aktiv. Tryk Start seeding for at kontrollere og starte den igen.');manager.save(identifier,value)
        return manager.public(value)
    @app.post('/api/uploader/jobs/{identifier}/seed-file',status_code=202)
    async def seed_file(identifier:str,request:Request,tracker:str=Query(min_length=1,max_length=64),confirmed:bool=False,session=Depends(admin)):
        if not re.fullmatch('[a-f0-9]{24}',identifier):raise HTTPException(404)
        if not confirmed:raise HTTPException(422,'Bekræft først, at pakken er uploadet manuelt på trackeren.')
        if request.headers.get('content-type','').split(';')[0] not in {'application/x-bittorrent','application/octet-stream'}:raise HTTPException(415,'Vælg en torrentfil fra trackeren.')
        content=bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content)>1024**2:raise HTTPException(413,'Torrentfilen må højst fylde 1 MiB.')
        return await manager.enqueue(identifier,bridge.account(tracker),'',bytes(content))
    @app.post('/api/uploader/jobs/{identifier}/seed',status_code=202)
    async def seed(identifier:str,body:SeedLink,session=Depends(admin)):
        if not re.fullmatch('[a-f0-9]{24}',identifier):raise HTTPException(404)
        if not body.confirmed:raise HTTPException(422,'Bekræft først, at pakken er uploadet manuelt på trackeren.')
        return await manager.enqueue(identifier,bridge.account(body.tracker),body.url,None)
    return manager
