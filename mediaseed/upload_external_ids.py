"""Resolve the selected movie's external IDs before reserving a tracker upload."""
import asyncio,re
from types import SimpleNamespace
from fastapi import HTTPException


def imdb_number(value):
    if isinstance(value,bool):return None
    text=str(value).strip()
    if re.fullmatch(r'tt\d{7,10}',text):text=text[2:]
    if re.fullmatch(r'\d{1,10}',text) and int(text)>0:return str(int(text))
    return None


async def upload_external_ids(bridge,job,tmdb):
    metadata=job.get('metadata') or {}
    imdb=imdb_number(metadata.get('imdb') or metadata.get('imdb_id'))
    if imdb is None:
        native=getattr(bridge,'native',None)
        if native is None:raise HTTPException(409,'IMDb-ID mangler. Hent filmens oplysninger fra TMDB før upload.')
        try:found=await asyncio.to_thread(native.helper.metadata.details,SimpleNamespace(kind='movie',tmdb=tmdb))
        except Exception:raise HTTPException(409,'IMDb-ID kunne ikke hentes fra TMDB. Uploaden er ikke sendt; prøv igen når metadata er tilgængelige.') from None
        if not isinstance(found,dict) or found.get('kind')!='movie' or found.get('tmdb')!=tmdb:
            raise HTTPException(409,'IMDb-opslaget kunne ikke knyttes til den valgte film. Uploaden er ikke sendt.')
        imdb=imdb_number(found.get('imdb') or found.get('imdb_id'))
        if imdb is None:raise HTTPException(409,'TMDB har ikke et gyldigt IMDb-ID for denne film. Uploaden er ikke sendt.')
    mal=metadata.get('mal')
    if mal is None:mal=0 # Tracker sentinel: no MAL identity supplied, not an invented ID.
    if isinstance(mal,bool) or not re.fullmatch(r'\d{1,10}',str(mal)) or int(mal)>2147483647:
        raise HTTPException(409,'Filmens MAL-ID er ugyldigt. Uploaden er ikke sendt.')
    return {'imdb':imdb,'mal':str(int(mal))}
