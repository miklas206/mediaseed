"""Fill missing site-title facts without renaming source or torrent payload files."""
import re
from pathlib import Path
from mediaseed.upload_engine.media_tags import language_tag

SOURCE = r'(?:^|[ ._-])(?:WEB(?:[ .-]?(?:DL|Rip))?|BluRay|HDTV|DVD)(?=[ ._-]|$)'
CODEC = r'(?:[xh][ .]?26[45]|HEVC|AVC|AV1|VP9|MPEG[ .-]?2)'
RESOLUTION = r'(?:\d{3,5}x\d{3,5}|\d{3,4}[pi]|SD)'


def title_complete(title):
    return bool(re.search(RESOLUTION,title,re.I) and re.search(SOURCE,title,re.I) and re.search(CODEC,title,re.I))


def complete_title(body,videos):
    title=body.title;tokens=[]
    # Copy/source mode preserves video filenames, but the site's title still
    # needs language claims supported by every selected file. Partial previews
    # cannot establish the language coverage of a full season.
    language = language_tag(videos) if len(videos) == len(body.videos) else ''
    if language and not re.search(r'(?:^|[ ._-])(?:NORDIC|DANISH|SWEDISH|NORWEGIAN|FINNISH|JAPANESE)(?:[ ._-]|$)',title,re.I):
        quality = re.search(RESOLUTION,title,re.I)
        if quality:
            title=title[:quality.start()].rstrip(' ._-')+'.'+language+'.'+title[quality.start():]
        else:
            tokens.append(language)
    tracks=[t for v in videos for t in v.get('technical',[]) if t.get('@type')=='Video']
    if not re.search(RESOLUTION,title,re.I) and tracks:
        known=set()
        for track in tracks:
            try:width,height=int(track.get('Width',0)),int(track.get('Height',0))
            except (ValueError,TypeError):continue
            level={1280:720,1920:1080,3840:2160}.get(width,height)
            scan={'Progressive':'p','Interlaced':'i'}.get(track.get('ScanType'))
            if scan and level in {480,576,720,1080,2160} and 0<height<=level:known.add(str(level)+scan)
        if len(known)==1:tokens.append(known.pop())
    if not re.search(SOURCE,title,re.I):
        if body.source_type!='Other':tokens.append(body.source_type)
        else:
            values={match[0].strip(' ._-') for file in body.videos if (match:=re.search(SOURCE,Path(file).stem,re.I))}
            if len(values)==1:tokens.append(values.pop())
    if not re.search(CODEC,title,re.I) and tracks:
        formats={str(t.get('Format','')).upper() for t in tracks}
        if formats=={'AVC'}:tokens.append('h264')
        elif formats=={'HEVC'}:tokens.append('h265')
    if tokens:
        match=re.search(r'-[A-Za-z0-9]+$',title)
        at=match.start() if match else len(title)
        title=title[:at].rstrip('.')+'.'+'.'.join(tokens)+title[at:]
    return title
