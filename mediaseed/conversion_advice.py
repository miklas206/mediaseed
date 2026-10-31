"""Bounded, metadata-only advice. No conversion or torrent mutation."""
import hashlib
import json
import math
import subprocess
import time
from pathlib import Path


def cache_key(entry):
    return hashlib.sha256(json.dumps([entry['path'],entry['identity']],sort_keys=True).encode()).hexdigest()


def metadata(path):
    response=subprocess.run(['ffprobe','-v','error','-threads','1','-show_format','-show_streams','-of','json',str(path)],capture_output=True,text=True,check=True,timeout=60)
    data=json.loads(response.stdout)
    stream=next(s for s in data['streams'] if s['codec_type']=='video' and not s.get('disposition',{}).get('attached_pic'))
    duration=float(data['format']['duration'])
    if not math.isfinite(duration) or not 0<duration<7*86400:raise ValueError('Invalid video duration')
    audio=0;estimated=False
    for track in data['streams']:
        if track['codec_type']!='audio':continue
        tags=track.get('tags',{})
        rate=track.get('bit_rate') or tags.get('BPS') or tags.get('BPS-eng')
        if rate:
            try:audio+=max(0,int(rate));continue
            except (ValueError,TypeError):pass
        estimated=True
        audio+= {'truehd':8_000_000,'dts':6_000_000,'flac':4_000_000,'pcm_s16le':3_000_000,'ac3':640_000,'eac3':1_536_000}.get(track.get('codec_name'),512_000)
    return {'duration':duration,'codec':stream.get('codec_name','unknown'),'width':stream.get('width'),
            'height':stream.get('height'),'audioBps':audio,'audioEstimated':estimated}


def estimate(entry, data, target):
    size=entry['identity'][0];duration=data['duration'];audio=data['audioBps']
    expected=duration*(target*1_000_000+audio)/8*1.02
    low=duration*(target*.8*1_000_000+audio*.85)/8
    high=duration*(target*1.2*1_000_000+audio*1.15)/8*1.03
    saving=max(0,int(size-expected))
    return {'originalBytes':size,'estimatedBytes':int(expected),'savingBytes':saving,
            'savingLowBytes':max(0,int(size-high)),'savingHighBytes':max(0,int(size-low)),
            'currentMbps':round(size*8/duration/1e6,1),'targetMbps':target,'duration':duration,
            'codec':data['codec'],'height':data.get('height'),'audioEstimated':data['audioEstimated'],
            'recommended':saving>=1024**3 and saving/size>=.15}


def analyze_inventory(queue, roots, stopped):
    """Read only app-approved exact files. Never recurse over a media library."""
    from mediaseed.conversion import atomic, fingerprint
    try:inventory=json.loads((queue/'analysis-inventory.json').read_text())
    except (OSError,ValueError):return
    for entry in inventory.get('files',[])[:120]:
        if stopped():return
        target=queue/('analysis-'+cache_key(entry)+'.json')
        if target.exists():
            try:
                old=json.loads(target.read_text())
                if not old.get('error') or time.time()-old.get('at',0)<60:continue
            except (OSError,ValueError):pass
        path=Path(entry['path'])
        try:
            if not path.is_absolute() or path.resolve()!=path or not any(path.is_relative_to(r) for r in roots):raise ValueError('Unapproved analysis path')
            if fingerprint(path)!=entry['identity']:raise ValueError('Original changed')
            data=metadata(path)
            if fingerprint(path)!=entry['identity']:raise ValueError('Original changed')
            value={'metadata':data,'at':time.time()}
        except Exception:value={'error':'Kunne ikke læse videometadata. Originalen er uændret.','at':time.time()}
        atomic(target,value)
