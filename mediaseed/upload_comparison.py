"""Measured/report-based release comparison. Names never prove stream properties."""
import json,math,re
from mediaseed.media_languages import language_code

LANGUAGES={'dan':'da','danish':'da','dk':'da','eng':'en','english':'en','swe':'sv','swedish':'sv','nor':'no','norwegian':'no','nb':'no','nob':'no','nno':'no','fin':'fi','finnish':'fi','deu':'de','ger':'de','german':'de','fra':'fr','fre':'fr','french':'fr','spa':'es','spanish':'es','jpn':'ja','japanese':'ja'}
FIELDS={'file size':'FileSize','duration':'Duration','overall bit rate':'OverallBitRate','bit rate':'BitRate','bit rate mode':'BitRate_Mode','width':'Width','height':'Height','frame rate':'FrameRate','bit depth':'BitDepth','channel(s)':'Channels','channels':'Channels','channel layout':'ChannelLayout','format':'Format','format profile':'Format_Profile','format additional features':'Format_AdditionalFeatures','commercial name':'Format_Commercial_IfAny','hdr format':'HDR_Format','language':'Language','title':'Title','forced':'Forced','default':'Default'}


def number(value):
    try:
        n=float(str(value).replace(' ','').replace('\u00a0','').replace('\u202f','').replace(',','.'))
        return n if math.isfinite(n) and n>=0 else None
    except (TypeError,ValueError):return None


def quantity(value,kind):
    if isinstance(value,(int,float)):return number(value)
    value=str(value or '').strip()
    if not value:return None
    if kind=='Duration' and re.search(r'\d\s*(?:h|min|s|ms)\b',value):
        return sum(float(n.replace(',','.'))*{'h':3600,'min':60,'s':1,'ms':.001}[u] for n,u in re.findall(r'([\d.,]+)\s*(ms|min|h|s)\b',value))
    match=re.match(r'([\d\s.,\u00a0\u202f]+)\s*([A-Za-z/]+)?',value)
    if not match:return None
    n=number(match[1]);unit=(match[2] or '').lower()
    if n is None:return None
    if kind in ('FileSize','BitRate','OverallBitRate'):
        scales={'k':1000,'m':1000**2,'g':1000**3,'t':1000**4}
        scale=next((v for k,v in scales.items() if unit.startswith(k)),1)
        if kind=='FileSize' and 'i' in unit:scale=1024**('kmgt'.index(unit[0])+1)
        return n*scale
    return n


def report_tracks(value):
    if isinstance(value,str):
        try:value=json.loads(value)
        except ValueError:pass
    if isinstance(value,dict):
        media=value.get('media',value)
        tracks=media.get('track',[]) if isinstance(media,dict) else []
        return [t for t in tracks if isinstance(t,dict)] if isinstance(tracks,list) else []
    if not isinstance(value,str) or len(value)>1024**2:return []
    tracks=[];current={'@type':'General'}
    for line in value.splitlines():
        line=line.strip()
        heading=re.fullmatch(r'(General|Video|Audio|Text|Menu)(?:\s*#\s*\d+)?',line,re.I)
        if heading:
            if len(current)>1:tracks.append(current)
            current={'@type':heading[1].title()};continue
        if ':' not in line:continue
        key,content=line.split(':',1);key=FIELDS.get(key.strip().lower())
        if key:current[key]=content.strip()
    if len(current)>1:tracks.append(current)
    return tracks


def language(value):
    return language_code(value)


def codec(track):
    value=str(track.get('Format',''));extra=' '.join(str(track.get(k,'')) for k in ['Format_Profile','Format_AdditionalFeatures','Format_Commercial_IfAny'])
    if value.startswith(('MLP FBA','TrueHD')):return 'TrueHD'
    if value.startswith('E-AC-3'):return 'DDP'
    if value.startswith('AC-3'):return 'DD'
    if value.startswith('DTS') and re.search('XLL|Master Audio',value+' '+extra,re.I):return 'DTS-HD MA'
    return value or None


def measured_facts(tracks,size=None,source='measured'):
    general=next((t for t in tracks if t.get('@type')=='General'),{})
    video=next((t for t in tracks if t.get('@type')=='Video'),{})
    full=bool(general and video)
    hdr=' '.join(str(video.get(k,'')) for k in ['HDR_Format','HDR_Format_Compatibility']).lower()
    ranges=[]
    if 'dolby vision' in hdr:ranges.append('Dolby Vision')
    if 'hdr10+' in hdr or '2094' in hdr:ranges.append('HDR10+')
    if 'hdr10' in hdr or '2086' in hdr:ranges.append('HDR10')
    if 'hlg' in hdr:ranges.append('HLG')
    if video and not ranges:ranges=['SDR']
    audio=[];subtitles=[]
    for track in tracks:
        if track.get('@type')=='Audio':
            extras=' '.join(str(track.get(k,'')) for k in ['Format','Format_Commercial_IfAny','Format_AdditionalFeatures'])
            audio.append({'codec':codec(track),'channels':quantity(track.get('Channels'),'Channels'),'channelLayout':track.get('ChannelLayout'),'language':language(track.get('Language')),'atmos':bool(re.search(r'Atmos|JOC',extras,re.I)),'bitrate':quantity(track.get('BitRate'),'BitRate')})
        if track.get('@type')=='Text':
            fmt={'UTF-8':'SRT','SubRip':'SRT'}.get(track.get('Format'),track.get('Format'))
            forced=track.get('Forced')
            subtitles.append({'language':language(track.get('Language')),'format':fmt,'forced':str(forced).lower()=='yes' if forced is not None else None,'sdh':bool(re.search(r'\bSDH\b|hearing impaired',str(track.get('Title','')),re.I))})
    result={'source':source,'complete':full,'size':quantity(size,'FileSize') or quantity(general.get('FileSize'),'FileSize'),'duration':quantity(general.get('Duration'),'Duration'),'videoCodec':video.get('Format'),'width':quantity(video.get('Width'),'Width'),'height':quantity(video.get('Height'),'Height'),'videoBitrate':quantity(video.get('BitRate'),'BitRate'),'totalBitrate':quantity(general.get('OverallBitRate'),'OverallBitRate'),'bitDepth':quantity(video.get('BitDepth'),'BitDepth'),'frameRate':quantity(video.get('FrameRate'),'FrameRate'),'hdr':ranges or None,'audio':audio if full else None,'subtitles':subtitles if full else None}
    return result


def video_facts(video):
    if isinstance(video.get('comparison'),dict):return video['comparison']
    tracks=report_tracks(video.get('report',''))
    return measured_facts(tracks or video.get('technical',[]),video.get('sizeBytes'),source='saved-report')


def audio_key(track):return (track.get('language'),track.get('codec'),track.get('channels'),tuple(sorted(str(track.get('channelLayout') or '').split())),track.get('atmos'))
def subtitle_key(track):return (track.get('language'),track.get('format'),track.get('forced'),track.get('sdh'))


def compare(local,remote):
    """Tolerances identify similarity, never perceptual quality or tracker permission."""
    rows=[]
    def add(field,known,equal):rows.append({'field':field,'status':'unknown' if not known else 'equal' if equal else 'different'})
    for key,tolerance in [('size',.05),('videoBitrate',.10),('totalBitrate',.10),('duration',.01),('frameRate',.005)]:
        a,b=local.get(key),remote.get(key)
        add(key,a is not None and b is not None,bool(a and b and abs(a-b)/max(a,b)<=tolerance))
    for key in ['videoCodec','width','height','bitDepth','hdr']:
        a,b=local.get(key),remote.get(key);add(key,a is not None and b is not None,a==b)
    for key,keyfn in [('audio',audio_key),('subtitles',subtitle_key)]:
        a,b=local.get(key),remote.get(key)
        known=a is not None and b is not None and all(t.get('language') and t.get('codec' if key=='audio' else 'format') and (t.get('channels') is not None if key=='audio' else t.get('forced') is not None) for t in (a or [])+(b or []))
        add(key,known,known and {keyfn(t) for t in a}=={keyfn(t) for t in b})
    # Audio bitrate is compared only for corresponding stream identities.
    a,b=local.get('audio'),remote.get('audio')
    if a is not None and b is not None and {audio_key(t) for t in a}=={audio_key(t) for t in b}:
        rates={audio_key(t):t.get('bitrate') for t in b}
        known=all(t.get('bitrate') is not None and rates.get(audio_key(t)) is not None for t in a)
        equal=known and all(abs(t['bitrate']-rates[audio_key(t)])/max(t['bitrate'],rates[audio_key(t)],1)<=.10 for t in a)
        add('audioBitrate',known,equal)
    status='different' if any(r['status']=='different' for r in rows) else 'unknown' if any(r['status']=='unknown' for r in rows) else 'equivalent'
    return {'status':status,'fields':rows,'facts':remote}
