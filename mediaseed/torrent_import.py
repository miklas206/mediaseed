"""Read-only confirmation of file imports after a lost browser response."""
from fastapi import Depends,HTTPException,Query,Request
from mediaseed.cleanup import source_hashes


def install_import_status(app,authorized,qbit,storage,cleanup):
    @app.post('/api/torrent-import-status')
    async def status(request:Request,location:str=Query(default='local',max_length=64),folder:str=Query(default='',max_length=1000),session=Depends(authorized)):
        destination=storage.destination(location,folder)
        content=bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content)>4*1024**2:raise HTTPException(413,'Torrent file exceeds 4 MiB')
        try:hashes=source_hashes(content=bytes(content))
        except (ValueError,TypeError,KeyError,IndexError):raise HTTPException(422,'Invalid torrent file') from None
        if not hashes:raise HTTPException(422,'Invalid torrent file')
        response=await qbit('/api/v2/torrents/info',payload={'hashes':'|'.join(hashes)})
        try:rows=response.json()
        except ValueError:raise HTTPException(503,'Invalid torrent client response') from None
        if response.status_code!=200 or not isinstance(rows,list):raise HTTPException(503,'Torrent information is unavailable')
        if any(not isinstance(r,dict) or r.get('hash','').lower() not in hashes for r in rows):raise HTTPException(503,'Torrent information is invalid')
        pending=any(set(record['hashes'])&set(hashes) and record.get('status') in {'preparing','waiting'} for record in cleanup.records()) if cleanup else False
        return {'present':bool(rows),'sameDestination':bool(rows) and all(r.get('save_path','').rstrip('/')==destination.rstrip('/') for r in rows),'pending':pending}
