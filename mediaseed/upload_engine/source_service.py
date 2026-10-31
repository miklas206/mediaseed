"""Explicit source-service hints; encoding tools and programme names are not evidence."""
import re
from pathlib import Path

SERVICES = {'iP':'BBC iPlayer', 'DRTV':'DRTV', 'TV2':'TV 2 Play', 'NF':'Netflix', 'AMZN':'Prime Video', 'DSNP':'Disney+', 'ATVP':'Apple TV+'}
_KEYS = {key.lower():key for key in SERVICES}
_LABELS = {label.lower():key for key,label in SERVICES.items()}


def canonical(value):
    return _KEYS.get(str(value).strip().lower()) or _LABELS.get(str(value).strip().lower())


def filename_services(filename):
    name = Path(filename).name
    tags = re.findall(r'(?i)\[source-([a-z0-9]+)\]', name)
    if re.search(r'(?i)\.BBC[ ._-]+iPlayer(?=\.[^.]+$|$)',name):tags.append('iP')
    tags += re.findall(r'(?i)(?:^|[ ._-])(iP|DRTV|TV2|NF|AMZN|DSNP|ATVP)(?=[ ._-]WEB(?:[ ._-]|Rip|$))', name)
    return {tag for value in tags if (tag:=canonical(value))}


def metadata_services(tracks):
    result=set()
    for track in tracks:
        if track.get('@type')!='General':continue
        values=[track.get(key) for key in ['Source_Service','SourceService','SOURCE_SERVICE','Source']]
        extra=track.get('extra',{})
        if isinstance(extra,dict):values += [v for k,v in extra.items() if k.upper() in {'SOURCE_SERVICE','SOURCESERVICE'}]
        result.update(tag for value in values if value and (tag:=canonical(value)))
    return result


def service_info(body, videos=()):
    choice=getattr(body,'source_service','')
    if not choice or choice=='none':return {'status':'none','tag':'','label':'','evidence':'declared'}
    if choice=='other':return {'status':'ok','tag':'','label':'Other download service','evidence':'declared'}
    if choice!='auto':return {'status':'ok','tag':choice,'label':SERVICES[choice],'evidence':'declared'}
    found=[];evidence=set()
    for index,filename in enumerate(body.videos):
        video=videos[index] if index<len(videos) else {}
        meta=set(video.get('sourceServices',[]))|metadata_services(video.get('technical',[]))
        named=filename_services(filename)
        found.append(meta|named)
        if meta:evidence.add('MediaInfo')
        if named:evidence.add('filename')
    tags=set().union(*found) if found else set()
    if len(tags)==1 and all(len(row)==1 for row in found):
        tag=next(iter(tags));return {'status':'ok','tag':tag,'label':SERVICES[tag],'evidence':' + '.join(sorted(evidence))}
    return {'status':'needs_input','tag':'','label':'','evidence':'', 'message':'Downloadtjenesten er ikke entydig for alle filer. Vælg den korrekte tjeneste, eller opdel filerne.' if tags else 'Downloadtjenesten findes ikke i filnavn eller MediaInfo. Vælg tjenesten eller Ingen · ikke en download under Indstillinger.'}


def english_conversion_credit(value):
    # Normalise only our old generated sentence, including saved draft text.
    return re.sub(r'(?m)^Konverteret med NVIDIA HEVC af MDTech\. Original kilde: ',
                  'Converted with NVIDIA HEVC by MDTech. Original source: ',value)


def source_credit(body, videos=()):
    credit='\n'.join(line for line in english_conversion_credit(body.source_credit).splitlines() if not re.match(r'(?i)^\s*Download service:\s*None(?:\s|$)',line))
    info=service_info(body,videos)
    if info['status']=='ok' or getattr(body,'source_service','')=='none':
        credit='\n'.join(line for line in credit.splitlines() if line.strip()!='Original group not identified')
        if info['status']=='ok':credit += '\nDownload service: '+info['label']+' ('+info['evidence']+')'
    return credit


def strip_service_suffix(value):
    """Remove the recognised human-readable service suffix for episode matching only."""
    return re.sub(r'(?i)\.BBC[ ._-]+iPlayer$', '', value)
