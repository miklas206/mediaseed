"""Explicit publication after fresh duplicate checks; never retry uncertain tracker writes."""
import hashlib,json,re,time
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import Depends,HTTPException
from pydantic import BaseModel,ConfigDict,Field
from mediaseed.cleanup import source_hashes
from mediaseed.conversion import atomic
from mediaseed.moves import file_path
from mediaseed.tracker_sites import origin,download_address
from mediaseed.upload_comparison import video_facts
from mediaseed.upload_diagnostics import tracker_diagnostics,diagnostic_message
from mediaseed.upload_external_ids import upload_external_ids

class PublishChoice(BaseModel):
    model_config=ConfigDict(extra='forbid')
    tracker:str=Field(min_length=1,max_length=64)
    category_id:int=Field(gt=0)
    type_id:int=Field(gt=0)
    resolution_id:int=Field(gt=0)
    confirmed:bool=False
    comparison_reviewed:bool=False
    retry_of:str|None=Field(default=None,pattern=r"^[a-f0-9]{64}$")


def retry_token(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def install_publication(app,admin,bridge,qbit,data):
    def state(identifier):
        with bridge.database() as db:row=db.execute('SELECT document FROM uploader_publications WHERE job=?',(identifier,)).fetchone()
        return json.loads(row[0]) if row else None
    def save(identifier,value):
        with bridge.database() as db:db.execute('UPDATE uploader_publications SET document=? WHERE job=?',(json.dumps(value),identifier))
    def seed_path(job):
        source=job.get('source') or job.get('destination') or {}
        path=source.get('clientSavePath')
        if not isinstance(path,str):raise HTTPException(409,'Uploadpakken mangler en MediaSeed-seedingsti. Brug direkte konverterede filer eller en godkendt fælles destination.')
        config=bridge.config()
        for prefix,target in sorted(config.get('client_mappings',{}).items(),key=lambda x:-len(x[0])):
            if path==prefix or path.startswith(prefix+'/'):path=target+path[len(prefix):];break
        selected=bridge.storage.describe(path)
        if selected is None:raise HTTPException(409,'Seedingstien er ikke monteret i MediaSeed. Vælg en fælles mediedestination i uploaderen.')
        _,root=bridge.storage.directory(selected['location'],selected['folder'])
        # The first API flow deliberately accepts one measured film, never an
        # unvalidated multi-movie/TV package with different duplicate semantics.
        videos=job.get('videos',[])
        if len(videos)!=1:raise HTTPException(409,'API-upload kræver én færdig film pr. pakke')
        video=videos[0];name=Path(video['file']).name
        if not source.get('singleFile',False):
            content=source.get('clientContentPath')
            if not isinstance(content,str) or not content.startswith(path+'/'):raise HTTPException(409,'Seedingens indholdsmappe kunne ikke kontrolleres')
            name=str(Path(content).relative_to(path)/name)
        try:
            if file_path(root,name).stat().st_size!=video['sizeBytes']:raise OSError()
        except (OSError,ValueError):raise HTTPException(409,'Mediefilen mangler eller er ændret. Upload er ikke sendt.') from None
        return path

    @app.get('/api/uploader/jobs/{identifier}/publication')
    async def publication(identifier:str,session=Depends(admin)):
        if not re.fullmatch('[a-f0-9]{24}',identifier):raise HTTPException(404)
        value=state(identifier)
        if not value:return {'state':'not_published'}
        return {**value,'retryToken':retry_token(value) if value['state']=='rejected' else None}

    @app.post('/api/uploader/jobs/{identifier}/publish')
    async def publish(identifier:str,body:PublishChoice,session=Depends(admin)):
        if not body.confirmed:raise HTTPException(422,'Gennemgå uploadpakken og bekræft publicering først')
        if bridge.config().get('manual_upload',True):raise HTTPException(409,'Upload foregår nu manuelt på trackeren. Brug Start seeding i MediaSeed efter upload.')
        async with bridge.lock:
            previous=state(identifier)
            if previous:
                if previous['state']=='published':return previous
                if previous['state']!='rejected':raise HTTPException(409,'Uploadens resultat er ukendt eller afventer svar. Kontrollér trackeren; den bliver ikke sendt igen.')
                if body.retry_of!=retry_token(previous):raise HTTPException(409,'Hent status, gennemgå pakken igen og vælg Prøv upload igen efter afslaget.')
            elif body.retry_of is not None:raise HTTPException(409,'Det tidligere afslag findes ikke. Hent uploadstatus igen.')
            job=await bridge.job(identifier);account=bridge.account(body.tracker)
            tmdb,resolution=bridge.identity(job)
            check=await bridge.check(account,tmdb,resolution,video_facts(job['videos'][0]))
            if check['status']!='clear' and not (check.get('canPublishAfterReview') and body.comparison_reviewed):raise HTTPException(409,check['message'])
            if not check['apiUploadRoute']:raise HTTPException(409,'Trackerens upload-API er ikke bekræftet tilgængeligt')
            options=check['options']
            category=options['category'].get(str(body.category_id),'')
            kind=options['type'].get(str(body.type_id),'')
            res=options['resolution'].get(str(body.resolution_id),'')
            if category.lower() not in ('movie','movies','film') or not kind or re.search(r'(?i)remux|disc',kind) or res.lower()!=resolution:
                raise HTTPException(422,'Vælg trackerens filmkategori, en encode-kildetype og den målte opløsning fra det aktuelle opslag')
            if not job.get('readyForReview') or not job.get('hasTorrent') or job.get('profile')!='nordicbytes':
                raise HTTPException(409,'Klargør en lokal NordicBytes-torrent og afslut alle kontroller før API-upload')
            external_ids=await upload_external_ids(bridge,job,tmdb)
            savepath=seed_path(job)
            torrent=await bridge.upstream('/api/upload-helper/'+identifier+'/download')
            if torrent.status_code!=200 or len(torrent.content)>1024**2:raise HTTPException(409,'Den klargjorte torrentfil mangler eller er større end 1 MiB')
            hashes=source_hashes(content=torrent.content)
            nfo=await bridge.upstream('/api/upload-helper/'+identifier+'/asset/captain-quack.nfo')
            info=await bridge.upstream('/api/upload-helper/'+identifier+'/asset/mediainfo.txt')
            if nfo.status_code!=200 or info.status_code!=200:raise HTTPException(409,'Uploadbilagene mangler')
            publication={'state':'publishing','tracker':body.tracker,'at':time.time(),'hash':hashes[0],'externalIds':{'tmdb':tmdb,**external_ids}}
            if previous:publication['history']=[*previous.get('history',[]),{k:v for k,v in previous.items() if k!='history'}]
            # SQLite serializes competing processes before the only external write.
            with bridge.database() as db:
                db.execute('BEGIN IMMEDIATE')
                current=db.execute('SELECT document FROM uploader_publications WHERE job=?',(identifier,)).fetchone()
                if previous:
                    if not current or json.loads(current[0])!=previous:raise HTTPException(409,'Uploadstatus er ændret. Hent status igen før et nyt forsøg.')
                    db.execute('UPDATE uploader_publications SET document=? WHERE job=?',(json.dumps(publication),identifier))
                else:
                    if current:raise HTTPException(409,'Uploaden behandles allerede')
                    db.execute('INSERT INTO uploader_publications VALUES (?,?)',(identifier,json.dumps(publication)))
            fields={'name':job['title'],'description':job['description'],'mediainfo':info.text,'category_id':str(body.category_id),'type_id':str(body.type_id),'resolution_id':str(body.resolution_id),'tmdb':str(tmdb),**external_ids,'anonymous':'0','personal_release':'0' if job.get('releaseOwner')=='other' else '1'}
            try:
                response=await bridge.tracker_request(account,'/api/torrents/upload','POST',files={'torrent':('upload.torrent',torrent.content,'application/x-bittorrent'),'nfo':('MDTech.nfo',nfo.content,'text/plain')},data=fields)
            except Exception:
                save(identifier,{**publication,'state':'uncertain','diagnostics':{'kind':'connection_interrupted'},'message':'Forbindelsen blev afbrudt efter afsendelse. Kontrollér trackeren før yderligere handling.'})
                raise HTTPException(502,'Forbindelsen blev afbrudt efter afsendelse. Uploaden kan være modtaget; kontrollér trackeren. Den bliver ikke sendt igen automatisk.') from None
            if response.status_code not in (200,201):
                status='rejected' if 400<=response.status_code<500 else 'uncertain'
                value={**publication,'state':status,'httpStatus':response.status_code,'message':('Trackeren afviste uploaden (HTTP '+str(response.status_code)+'). Kontrollér trackerens krav og uploadrettigheder.' if status=='rejected' else 'Trackerens resultat er ukendt. Kontrollér hjemmesiden før yderligere handling.')}
                value['diagnostics']=tracker_diagnostics(response,account)
                explanation=diagnostic_message(value['diagnostics'])
                if explanation:value['message']+=' '+explanation
                save(identifier,value);raise HTTPException(409,value['message'])
            try:
                result=response.json()
                if result.get('success') is False:raise ValueError()
                link=result['data']
                if not isinstance(link,str):raise ValueError()
                address=download_address(account,link)
                match=re.search(r'/download/(\d+)',address.path)
                if not match:raise ValueError()
                remote_id=match[1]
            except Exception:
                save(identifier,{**publication,'state':'uncertain','httpStatus':response.status_code,'diagnostics':tracker_diagnostics(response,account),'message':'Trackeren svarede, men uploadens kvittering kunne ikke bekræftes. Kontrollér hjemmesiden.'})
                raise HTTPException(502,'Uploadens resultat skal kontrolleres på trackeren; den bliver ikke sendt igen.') from None
            value={**publication,'state':'published','url':origin(account)+'/torrents/'+remote_id,'torrentId':remote_id,'seeding':'pending','message':'Upload sendt. Trackeren kan kræve staff-godkendelse.'}
            save(identifier,value) # A later client/seed failure must never repeat publication.
            try:
                canonical=await bridge.tracker_request(account,address.path+('?' + address.query if address.query else ''))
                if canonical.status_code!=200 or len(canonical.content)>1024**2 or source_hashes(content=canonical.content)!=hashes:raise ValueError()
                root=Path(data)/'uploader-published';root.mkdir(exist_ok=True,mode=0o700)
                target=root/(identifier+'.torrent');target.write_bytes(canonical.content);target.chmod(0o600)
                from mediaseed.features import add_stopped
                await add_stopped(qbit,1000,content=canonical.content,savepath=savepath,tag='mediaseed-own-upload')
                value.update(seeding='paused',message='Upload sendt. Torrenten er tilføjet på pause i MediaSeed til filkontrol og seeding. Staff-godkendelse kan være nødvendig.')
            except Exception:value.update(seeding='manual',message='Upload sendt, men torrenten skal tilføjes til seeding manuelt. Uploaden bliver ikke sendt igen.')
            save(identifier,value)
            return value
