"""Upload preparation runs inside MediaSeed, with its auth and process lifecycle."""
import asyncio,json,os,sqlite3
from contextlib import contextmanager,closing
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit,parse_qs
from fastapi import APIRouter,Depends,HTTPException,Query
from fastapi.responses import FileResponse,JSONResponse
from fastapi.routing import APIRoute
from fastapi.exceptions import RequestValidationError
import httpx
from mediaseed.upload_engine.storage import Storage
from mediaseed.upload_engine.destinations import Destinations
from mediaseed.upload_engine.upload_helper import install_upload_helper
from mediaseed.upload_engine.metadata import MetadataClient,Tmdb,MetadataError
from mediaseed.upload_engine.tvdb import Tvdb,TvdbError
from mediaseed.upload_engine.releases import Releases


class UploadRoute(APIRoute):
    def get_route_handler(self):
        original=super().get_route_handler()
        async def handle(request):
            try:return await original(request)
            except RequestValidationError:
                return JSONResponse({'detail':'Kontrollér uploadens filer og felter. Private nøgler vises aldrig i fejlbeskeder.'},status_code=422)
        return handle


class NativeMetadata(MetadataClient):
    def __init__(self,root):
        self.tmdb=Tmdb(root);self.tmdb.tvdb=Tvdb(root,self.tmdb.opener)
        self.releases=Releases(self.tmdb.opener)
    def request(self,path,body=None):
        parsed=urlsplit(path);query=parse_qs(parsed.query)
        try:
            if parsed.path=='/status':return {'configured':self.tmdb.configured(),'tvdbConfigured':self.tmdb.tvdb.configured()}
            if parsed.path=='/configuration':return self.tmdb.configure(body.get('credential'))
            if parsed.path=='/tvdb/configuration':return self.tmdb.tvdb.configure(body.get('apikey'),body.get('pin',''))
            if parsed.path=='/release':return self.releases.lookup(query['name'][0])
            if parsed.path=='/catalogue':return self.tmdb.catalogue(int(query['id'][0]))
            if parsed.path=='/details':return self.tmdb.details(query['kind'][0],int(query['id'][0]),int(query['season'][0]) if 'season' in query else None,int(query['episode'][0]) if 'episode' in query else None)
        except TvdbError as exc:raise MetadataError(str(exc)) from None
        raise MetadataError('Ukendt metadataforespørgsel')


class NativeUploader:
    def __init__(self,app,authorized,data,storage,qbit=None,conversions=None,publication_lock=None):
        self.data=Path(data)/'uploader';self.data.mkdir(exist_ok=True,mode=0o700)
        work=Path(os.environ.get('MEDIASEED_UPLOAD_WORK',str(self.data/'work')));work.mkdir(parents=True,exist_ok=True,mode=0o700)
        @contextmanager
        def database():
            with closing(sqlite3.connect(self.data/'mediaupload.sqlite',timeout=10)) as db:
                db.row_factory=sqlite3.Row
                with db:yield db
        self.database=database
        with database() as db:db.execute('PRAGMA journal_mode=WAL')
        defaults=[{'id':r['id'],'label':r['label'],'path':r['path'],'clientPath':r['path']} for r in storage.locations]
        sources=os.environ.get('MEDIASEED_UPLOAD_SOURCE_ROOTS',json.dumps(defaults))
        self.storage=Storage(sources,work)
        destinations=Destinations(os.environ.get('MEDIASEED_UPLOAD_DESTINATION_ROOTS','[]'))
        async def admin(session=Depends(authorized)):
            if session.get('role')!='administrator':raise HTTPException(403,'Upload kræver administratoradgang')
            return {**session,'id':'studio'} # Existing shared preparation history.
        router=APIRouter(prefix='/api/upload',route_class=UploadRoute);router.state=SimpleNamespace()
        install_upload_helper(router,admin,self.data,self.storage,database,destinations)
        self.helper=router.state.upload_helper
        from mediaseed.upload_in_place import InPlaceFiles
        self.loop=None
        def unseeded(path):
            if self.loop is None:raise ValueError('Uploadkøen er ikke startet')
            async def check():
                response=await qbit('/api/v2/torrents/info')
                rows=response.json()
                if response.status_code!=200 or not isinstance(rows,list):raise ValueError('Seedingstatus kunne ikke kontrolleres')
                for row in rows:
                    content=row.get('content_path') or (str(row.get('save_path','')).rstrip('/')+'/'+str(row.get('name','')))
                    if not content.startswith('/') or not row.get('hash'):raise ValueError('Torrentklientens filplacering kunne ikke kontrolleres')
                    if path==content or path.startswith(content.rstrip('/')+'/'):
                        raise ValueError('Den konverterede fil bruges allerede af en torrent. Vælg separat kopi eller fjern torrenten uden at slette filerne først; videoen bliver ikke ændret.')
            future=asyncio.run_coroutine_threadsafe(check(),self.loop)
            try:future.result(timeout=30)
            except Exception as exc:
                future.cancel()
                if isinstance(exc,ValueError):raise
                raise ValueError('Torrentklienten kunne ikke kontrolleres. Ingen filer er ændret.') from None
        self.helper.in_place=InPlaceFiles(self.data,conversions,unseeded)
        self.helper.in_place.recover(database)
        @contextmanager
        def publication_guard():
            if publication_lock is None:
                yield;return
            if self.loop is None:raise ValueError('Uploadkøen er ikke startet')
            future=asyncio.run_coroutine_threadsafe(publication_lock.acquire(),self.loop)
            future.result()
            try:yield
            finally:self.loop.call_soon_threadsafe(publication_lock.release)
        self.helper.publication_guard=publication_guard
        self.helper.metadata=NativeMetadata(self.data/'metadata')
        self.endpoints={(method,r.path):r.endpoint for r in router.routes for method in r.methods}
        @router.get('/storage')
        def roots(session=Depends(admin)):
            return {'locations':[{'id':r['id'],'label':r['label']} for r in self.storage.locations]}
        @router.get('/storage/folders')
        def folders(location:str=Query(max_length=64),folder:str=Query(default='',max_length=1000),session=Depends(admin)):
            return self.storage.folders(location,folder)
        @router.get('/rules/nordicbytes')
        def rules(session=Depends(admin)):
            root=Path(__file__).parent/'upload_engine/rules'
            return {'source':'Dine gemte NordicBytes-regler. Kontrollér ændringer før upload; staff har sidste ord.',**{key:(root/name).read_text() for key,name in [('upload','nordicbytes-upload.txt'),('general','nordicbytes-general.txt'),('faq','nordicbytes-faq.txt')]}}
        app.include_router(router)
    def start(self):
        self.loop=asyncio.get_running_loop();self.helper.start()
    async def stop(self):await asyncio.to_thread(self.helper.stop)
    async def request(self,path,method='GET',content=None,**kwargs):
        """Internal publication adapter; no HTTP client, gateway or remote service."""
        session={'id':'studio','role':'administrator'}
        if path=='/healthz':return httpx.Response(200,json={'status':'healthy','native':True})
        if path=='/api/description/preview' and method=='POST':
            from mediaseed.upload_engine.upload_helper import DescriptionRequest
            body=DescriptionRequest.model_validate_json(content)
            value=await asyncio.to_thread(self.endpoints['POST','/api/upload/description/preview'],body,session)
            return httpx.Response(200,json=value)
        import re
        match=re.fullmatch(r'/api/upload-helper/([a-f0-9]{24})(?:/(download|asset/(?:captain-quack\.nfo|mediainfo\.txt)))?',path)
        if not match:raise HTTPException(404)
        identifier,suffix=match.groups()
        key='/api/upload/upload-helper/{identifier}'
        if suffix=='download':value=await asyncio.to_thread(self.endpoints['GET',key+'/download'],identifier,session)
        elif suffix:
            value=await asyncio.to_thread(self.endpoints['GET',key+'/asset/{name}'],identifier,suffix.split('/')[1],session)
        else:return httpx.Response(200,json=await asyncio.to_thread(self.endpoints['GET',key],identifier,session))
        if isinstance(value,FileResponse):
            p=Path(value.path)
            if p.stat().st_size>16*1024**2:raise HTTPException(413,'Uploadbilaget er for stort')
            body=await asyncio.to_thread(p.read_bytes)
        else:body=value.body
        return httpx.Response(value.status_code,content=body,headers={'content-type':value.media_type})
