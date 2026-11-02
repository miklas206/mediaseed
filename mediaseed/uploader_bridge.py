"""Native upload review and tracker checks inside MediaSeed."""
import asyncio,json,re,time
from pathlib import Path
from urllib.parse import urlsplit,parse_qs,urlencode
import httpx
from fastapi import Depends,HTTPException
from pydantic import BaseModel,ConfigDict,Field
from mediaseed.tracker_sites import origin,secure_client
from mediaseed.upload_comparison import compare,measured_facts,report_tracks,video_facts

class TrackerChoice(BaseModel):
    model_config=ConfigDict(extra='forbid')
    tracker: str=Field(min_length=1,max_length=64)

class UploaderBridge:
    def __init__(self,database,conversions,storage,configuration=None,transport=None):
        self.database,self.conversions,self.storage=database,conversions,storage
        self.configuration=configuration
        self.native=None
        self.tracker=secure_client(httpx.AsyncClient(timeout=30,follow_redirects=False,trust_env=False,transport=transport))
        self.lock=asyncio.Lock()
        with database() as db:
            db.execute('CREATE TABLE IF NOT EXISTS uploader_publications (job TEXT PRIMARY KEY, document TEXT NOT NULL)')

    def config(self):
        return self.configuration or {'converted_location':'converted-movies','converted_folder':'','client_mappings':{'/downloads/media/Konverterede Film':'/downloads/media/converted-movies'}}

    def accounts(self):
        with self.database() as db:rows=db.execute("SELECT id,payload FROM settings WHERE kind='trackers'").fetchall()
        return [dict(json.loads(payload),id=identifier) for identifier,payload in rows if json.loads(payload).get('provider','nordicbytes')=='nordicbytes']

    def account(self,identifier):
        a=next((a for a in self.accounts() if a['id']==identifier),None)
        if not a:raise HTTPException(404,'NordicBytes-kontoen findes ikke')
        return a

    async def upstream(self,path,method='GET',content=None,content_type=None,query=''):
        return await self.native.request(path,method,content)

    async def upstream_json(self,path,**kwargs):
        r=await self.upstream(path,**kwargs)
        if r.status_code!=200:raise HTTPException(502,'Uploaderens oplysninger kunne ikke læses')
        try:return r.json()
        except ValueError:raise HTTPException(502,'Uploaderen returnerede et ugyldigt svar') from None

    async def tracker_request(self,account,path,method='GET',params=None,files=None,data=None):
        try:
            async with self.tracker.stream(method,origin(account)+path,headers={'Authorization':'Bearer '+account['api_key'],'Accept':'application/json','Cookie':''},params=params,files=files,data=data) as r:
                body=bytearray()
                async for part in r.aiter_bytes():
                    body.extend(part)
                    if len(body)>4*1024**2:raise HTTPException(502,'Trackerens svar er for stort')
                return httpx.Response(r.status_code,headers={k:v for k,v in r.headers.items() if k in ['allow','content-type']},content=bytes(body))
        except httpx.HTTPError:raise HTTPException(503,'Trackerforbindelsen fejlede. Der er ikke godkendt en dubletkontrol.') from None

    async def check(self,account,tmdb,resolution,local=None):
        if type(tmdb) is not int or tmdb<=0 or resolution not in ['480p','576p','720p','1080p','1080i','2160p','4320p']:
            raise HTTPException(409,'Bekræft filmens TMDB-ID og målte opløsning før dubletkontrol')
        params={'tmdbId':tmdb,'perPage':100};seen=set();rows=[];complete=False
        for _ in range(5):
            r=await self.tracker_request(account,'/api/torrents/filter',params=params)
            if r.status_code!=200:raise HTTPException(503,'Dubletkontrollen kunne ikke gennemføres. Kontrollér trackerens API-adgang.')
            try:d=r.json();items=d['data']
            except (ValueError,KeyError,TypeError):raise HTTPException(502,'Ugyldigt trackersvar') from None
            if not isinstance(d.get('links'),dict) or 'next' not in d['links']:raise HTTPException(502,'Trackeren oplyste ikke, om søgningen er komplet. Manuel kontrol er nødvendig.')
            if not isinstance(items,list):raise HTTPException(502,'Ugyldigt trackersvar')
            for item in items:
                a=item.get('attributes',{})
                if a.get('tmdb_id')!=tmdb:raise HTTPException(502,'Trackeren respekterede ikke TMDB-filteret. Manuel kontrol er nødvendig.')
                if str(item.get('id','')).isdigit():rows.append((str(item['id']),a))
            nxt=(d.get('links') or {}).get('next')
            if not nxt:complete=True;break
            u=urlsplit(nxt)
            if (u.netloc and u.netloc!=urlsplit(origin(account)).netloc) or u.path!='/api/torrents/filter':break
            pagination={k:v[0] for k,v in parse_qs(u.query).items() if k in ('cursor','page') and len(v)==1}
            signature=tuple(sorted(pagination.items()))
            if not pagination or signature in seen:break
            seen.add(signature);params={'tmdbId':tmdb,'perPage':100,**pagination}
        matches=[];unknown=[];different=[];local=local or {};options={'category':{},'type':{},'resolution':{}}
        for identifier,a in rows:
            for key in options:
                if type(a.get(key+'_id')) is int and isinstance(a.get(key),str):options[key][str(a[key+'_id'])]=a[key]
            name=str(a.get('name',''));kind=str(a.get('type',''))
            if str(a.get('resolution','')).lower()!=resolution:continue
            if re.search(r'(?i)(?<![a-z0-9])(?:remux|full[ ._-]?disc|complete|bdmv)(?![a-z0-9])',name+' '+kind):continue
            item={'id':identifier,'name':name[:300],'url':origin(account)+'/torrents/'+identifier,'type':kind,'resolution':a.get('resolution')}
            if re.search(r'(?i)(?<![a-z0-9])(?:[hx][ ._-]?265|hevc)(?![a-z0-9])',name):
                facts=measured_facts(report_tracks(a.get('media_info') or a.get('mediainfo') or ''),a.get('size'),source='tracker-report')
                item['comparison']=compare(local,facts)
                bucket=matches if item['comparison']['status']=='equivalent' else different if item['comparison']['status']=='different' else unknown
                bucket.append(item)
            elif not re.search(r'(?i)(?<![a-z0-9])(?:[hx][ ._-]?264|avc)(?![a-z0-9])',name):unknown.append(item)
        # Discover site-specific IDs from observed API rows, never assume UNIT3D defaults.
        if not rows:
            catalog=await self.tracker_request(account,'/api/torrents')
            if catalog.status_code==200:
                try:
                    for item in catalog.json().get('data',[]):
                        a=item.get('attributes',{})
                        for key in options:
                            if type(a.get(key+'_id')) is int and isinstance(a.get(key),str):options[key][str(a[key+'_id'])]=a[key]
                except (ValueError,TypeError):pass
        status='duplicate' if matches else 'needs_review' if unknown or not complete else 'different' if different else 'clear'
        can_review=bool(complete and not matches and (different or unknown) and local.get('complete'))
        route=await self.tracker_request(account,'/api/torrents/upload')
        advertised=route.status_code==405 and 'POST' in route.headers.get('allow','').upper()
        return {'status':status,'checkedAt':time.time(),'tmdb':tmdb,'resolution':resolution,'complete':complete,'local':local,'canPublishAfterReview':can_review,'matches':matches,'different':different,'uncertain':unknown,'options':options,'apiUploadRoute':advertised,'message':{
            'duplicate':'Der findes en teknisk tilsvarende udgave med sammenlignelige spor, HDR, størrelse og bitrate. Upload er blokeret.',
            'needs_review':'Sammenligningen mangler oplysninger. Gennemgå forskelle, ukendte felter og trackerens regler før eventuel publicering.' if can_review else 'Sammenligningen kunne ikke bekræftes. Upload er blokeret.',
            'different':'De eksisterende udgaver har tekniske forskelle. Gennemgå dem og trackerens regler før eventuel publicering.',
            'clear':'Ingen tilsvarende HEVC/x265-udgave fundet i det komplette opslag.'}[status]}


    async def job(self,identifier):
        if not re.fullmatch('[a-f0-9]{24}',identifier):raise HTTPException(404,'Uploadpakken findes ikke')
        job=await self.upstream_json('/api/upload-helper/'+identifier)
        if job.get('state')!='done':raise HTTPException(409,'Vent til uploadpakken er færdig')
        return self.mapped_paths(job)

    @staticmethod
    def identity(job):
        meta=job.get('metadata') or {}
        if meta.get('kind')!='movie':raise HTTPException(409,'Trackerupload fra MediaSeed understøtter film. Brug uploaderens eksisterende TV-forløb til serier.')
        videos=job.get('videos') or []
        heights=[]
        for video in videos:
            tracks=[t for t in video.get('technical',[]) if t.get('@type')=='Video']
            if len(tracks)!=1 or tracks[0].get('Format') not in ('HEVC','H.265'):raise HTTPException(409,'Denne kontrol kræver målt HEVC-video')
            track=tracks[0]
            try:level={3840:'2160p',1920:'1080p',1280:'720p',7680:'4320p'}.get(int(track.get('Width',0)))
            except (ValueError,TypeError):level=None
            if not level:raise HTTPException(409,'Opløsningen kunne ikke bestemmes entydigt')
            heights.append(level)
        if not heights or len(set(heights))!=1:raise HTTPException(409,'Vælg film med samme målte opløsning')
        return meta.get('tmdb'),heights[0]

    def handoff(self,identifier):
        if not re.fullmatch('[a-f0-9]{32}',identifier):raise HTTPException(404,'Konverteringen findes ikke')
        job=next((j for j in self.conversions.records() if j['id']==identifier),None)
        if not job or job['status']!='complete':raise HTTPException(409,'Konverteringen er ikke færdig')
        from mediaseed.conversion import fingerprint
        from mediaseed.conversion_naming import output_name
        config=self.config();root=self.storage.downloads/'media'/'converted-movies';dest=Path(job['destination'])
        try:relative=dest.relative_to(root)
        except ValueError:raise HTTPException(409,'Uploadgenvejen kræver placeringen Konverterede Film') from None
        files=[]
        receipt=json.loads((dest/'.mediaseed-converted.json').read_text())
        for entry in job['files']:
            name=output_name(entry)
            if fingerprint(dest/name)!=receipt['identities'].get(name):raise HTTPException(409,'Den konverterede fil er ændret. Kontrollér den før upload.')
            files.append(name)
        parents={str(Path(name).parent) for name in files}
        if len(parents)!=1:raise HTTPException(409,'Åbn uploaderen og vælg filmene fra deres mapper enkeltvis')
        with self.database() as db:row=db.execute('SELECT payload FROM insight_metadata WHERE hash=?',(job['hash'],)).fetchone()
        meta=json.loads(row[0]) if row else {}
        folder='/'.join(p for p in [config.get('converted_folder','Konverterede Film'),relative.as_posix(),next(iter(parents))] if p and p!='.')
        title=Path(files[0]).stem
        source='BluRay' if re.search(r'(?i)blu[ ._-]?ray',job['name']) else 'WEB-DL' if 'WEB-DL' in job['name'] else 'Other'
        if source!='Other':title=re.sub(r'(?i)(\.H265\.)','.'+source+r'\1',title)
        return {'location':config.get('converted_location','library'),'folder':folder,'files':[Path(n).name for n in files],'title':title,'tmdb':meta.get('tmdb_id'),'source_type':source,'source_credit':('Muxed without re-encoding by MDTech. Picture source: '+job['name']+'. Audio source: '+job['proposal']['donor']['name']) if job.get('kind')=='mux' else 'Converted with NVIDIA HEVC by MDTech. Original source: '+job['name'],'reviewOnly':True}

    def mapped_paths(self,value):
        if isinstance(value,list):return [self.mapped_paths(v) for v in value]
        if not isinstance(value,dict):return value
        result={k:self.mapped_paths(v) for k,v in value.items()}
        for key in ('clientSavePath','clientContentPath'):
            path=result.get(key)
            if not isinstance(path,str):continue
            for prefix,target in sorted(self.config().get('client_mappings',{}).items(),key=lambda x:-len(x[0])):
                if path==prefix or path.startswith(prefix+'/'):
                    result[key]=target+path[len(prefix):];break
        return result

    async def close(self):await self.tracker.aclose()


def install_uploader(app,authorized,database,conversions,storage,qbit,data,vpn_status=None):
    from mediaseed.upload_native import NativeUploader
    bridge=UploaderBridge(database,conversions,storage);app.state.uploader_bridge=bridge
    native=NativeUploader(app,authorized,data,storage,qbit,conversions,bridge.lock);bridge.native=native;app.state.native_uploader=native
    async def admin(session=Depends(authorized)):
        if session.get('role')!='administrator':raise HTTPException(403,'Upload kræver administratoradgang.')
        return session
    @app.get('/api/uploader/status')
    async def status(session=Depends(admin)):
        try:
            bridge.config();health=await bridge.upstream('/healthz');configured=health.status_code==200;reason='' if configured else 'Uploaderen svarede ikke med klar status. Kontrollér forbindelsen.'
        except HTTPException as e:configured=False;reason=e.detail
        return {'configured':configured,'native':True,'reason':reason,'reviewFirst':True,'manualUpload':True,'trackers':[{'id':a['id'],'name':a['name'],'uploadUrl':origin(a)+'/upload'} for a in bridge.accounts()]}
    @app.get('/api/uploader/conversions/{identifier}')
    async def handoff(identifier:str,session=Depends(admin)):return bridge.handoff(identifier)
    @app.post('/api/uploader/jobs/{identifier}/check')
    async def check(identifier:str,body:TrackerChoice,session=Depends(admin)):
        job=await bridge.job(identifier);tmdb,res=bridge.identity(job)
        return await bridge.check(bridge.account(body.tracker),tmdb,res,video_facts(job['videos'][0]))
    @app.get('/api/uploader/conversions/{identifier}/check')
    async def check_conversion(identifier:str,tracker:str,session=Depends(admin)):
        hint=bridge.handoff(identifier);tmdb=hint.get('tmdb')
        if type(tmdb) is not int or tmdb<=0:raise HTTPException(409,'Bekræft TMDB-ID i uploadværkstedet, før dubletkontrollen kan gennemføres')
        body={'location':hint['location'],'folder':hint['folder'],'title':hint['title'],'kind':'movie','tmdb':tmdb,'source_credit':hint['source_credit'],'source_type':hint['source_type'],'source_service':'none','videos':hint['files'],'media_mode':'source','screenshots':False}
        preview=await bridge.upstream_json('/api/description/preview',method='POST',content=json.dumps(body).encode(),content_type='application/json')
        _,resolution=bridge.identity({'metadata':{'kind':'movie','tmdb':tmdb},'videos':preview.get('observedVideos',[])})
        return await bridge.check(bridge.account(tracker),tmdb,resolution,video_facts(preview['observedVideos'][0]))
    from mediaseed.uploader_publish import install_publication
    install_publication(app,admin,bridge,qbit,data)
    from mediaseed.upload_seeding import install_seeding
    async def unavailable_vpn():return {'downloadsEnabled':False}
    install_seeding(app,admin,bridge,qbit,vpn_status or unavailable_vpn,data)
    return bridge

