"""Generate upload prose from observed media facts, without inventing a plot."""
from mediaseed.upload_engine.source_service import source_credit, english_conversion_credit
import re
from mediaseed.upload_comparison import quantity, video_facts
from mediaseed.media_languages import NAMES, language_code

LANGUAGES = {'da':'Danish','sv':'Swedish','no':'Norwegian','fi':'Finnish','en':'English','dan':'Danish','swe':'Swedish','nor':'Norwegian','nob':'Norwegian','nno':'Norwegian','fin':'Finnish','eng':'English','de':'German','deu':'German','fr':'French','fra':'French','es':'Spanish','spa':'Spanish','ja':'Japanese','jpn':'Japanese'}

LANGUAGES.update(NAMES)


def plain(value):
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(value)).replace('[','(').replace(']',')')


def joined(values):
    values=list(dict.fromkeys(values))
    return values[0] if len(values)==1 else ', '.join(values[:-1])+' and '+values[-1] if values else ''


def readable_name(title):
    title=re.sub(r'\[[^\]]*\]', '', title)
    marker=re.search(r'(?i)(?:^|[ ._-])S\d{2}(?:E\d{2})?|(?:^|[ ._-])\d{3,4}[pi](?:[ ._-]|$)',title)
    prefix=title[:marker.start()] if marker else title
    years=[m for m in re.finditer(r'(?<!\d)(?:19|20)\d{2}(?!\d)',prefix) if m.start()>0]
    if years:prefix=prefix[:years[0].start()]
    return re.sub(r'[._]+',' ',prefix).strip(' -') or title


def bitrate(value):
    value=quantity(value,'BitRate')
    if value is None or value<=0:return None
    return f"{value/1000000:.2f} Mbit/s" if value>=1000000 else f"{value/1000:.0f} kbit/s"


def subtitle_text(video,preview=False):
    tracks=video_facts(video).get('subtitles')
    if tracks is None:tracks=[{'language':code} for code in video.get('languages',[])]
    tracks=list(tracks)
    if preview:
        for extra in video.get('addedSubtitles',[]):
            fmt=str(extra.get('file','')).rsplit('.',1)[-1].upper()
            tracks.append({'language':extra['language'],'format':fmt if fmt in {'SRT','ASS','SSA','VTT'} else None})
    groups={}
    for track in tracks:
        code=language_code(track.get('language'));label=NAMES.get(code,code) or 'Unknown language'
        formats=groups.setdefault(label,[])
        fmt=track.get('format')
        if fmt:
            value=str(fmt)+(' forced' if track.get('forced') else '')+(' SDH' if track.get('sdh') else '')
            if value not in formats:formats.append(value)
    return '; '.join(label+(' ('+', '.join(formats)+')' if formats else '') for label,formats in groups.items()) or 'No languages identified'


def generate_description(body, videos, preview=False, metadata=None):
    if body.description_override is not None:
        return english_conversion_credit(body.description_override)
    name=plain(readable_name(body.title))
    episode=re.search(r'(?i)S(\d{2})E(\d{2})',body.title)
    season=re.search(r'(?i)S(\d{2})',body.title)
    count=len(body.videos)
    numbering = ' + '.join(dict.fromkeys(e.numbering_source for e in body.episode_overrides)) or 'TMDB'
    if body.kind=='episode' and episode and int(episode[1])==0:
        opening=f'This upload contains a special from {name}, listed as S00E{int(episode[2]):02} in {numbering} numbering.'
    elif body.kind=='episode' and episode:
        opening=f'This upload contains episode {int(episode[2])} from season {int(episode[1])} of {name}.'
    elif body.kind=='season':
        opening=f'This upload is a season pack of {name} containing {count} video files.'
        if season:opening+=f' The pack is labelled season {int(season[1])}.'
    elif body.kind=='series':
        opening=f'This upload is a series pack of {name} containing {count} video files.'
    else:
        opening=f'This upload contains {name}.' if count==1 else f'This upload contains {count} video files under the title {name}.'
    lines=[f'[b]{name}[/b]','',opening]
    originals = [video['originalFile'] for video in videos if video.get('originalFile')]
    if originals:
        lines += ['', '[b]Original source files[/b]', *map(plain, originals)]
    if body.episode_overrides:
        lines += ['', f'[b]Included episodes ({numbering} numbering)[/b]']
        for episode in body.episode_overrides:
            label = f'S{episode.season:02}E{episode.episode:02}'
            if episode.season == 0: label += ' (Special)'
            lines.append(label + (' - ' + plain(episode.episode_title) if episode.episode_title else ''))
        if any(e.numbering_source == 'TheTVDB' for e in body.episode_overrides):
            lines.append('Episode numbering from TheTVDB: https://www.thetvdb.com/')
        if any(e.numbering_source == 'Manual' for e in body.episode_overrides):
            lines.append('Episode title and numbering supplied manually by the uploader; not verified against the episode catalogue.')
    if metadata and metadata.get('status')=='ok':
        scope={'movie':'Movie synopsis','series':'Series synopsis','episode':'Episode synopsis'}.get(metadata.get('scope'),'Synopsis')
        lines+=['',f'[b]{scope}: {plain(metadata.get("title",name))}[/b]',plain(metadata['overview'])]
        if metadata.get('episodeTitle'):lines.append('Episode title: '+plain(metadata['episodeTitle']))
        lines.append('Synopsis from TMDB (English).')
    lines+=['','[b]Video, audio and subtitles[/b]']
    for video in videos:
        if len(videos)>1:lines+=['',plain(video['file'])]
        tracks=video.get('technical',[])
        for track in tracks:
            fmt=plain(track.get('Format','unknown format'))
            if track.get('@type')=='Video':
                codec={'AVC':'H.264 (AVC)','HEVC':'H.265 (HEVC)'}.get(fmt,fmt)
                width,height=track.get('Width'),track.get('Height')
                resolution=f'{width} × {height} pixels' if width and height else 'resolution not available'
                line=f'Video: {resolution} with {codec}'
                if track.get('FrameRate'):line+=f", {plain(track['FrameRate'])} frames per second"
                rate=bitrate(track.get('BitRate'))
                if rate:line+=', bitrate '+rate
                lines.append(line+'.')
            elif track.get('@type')=='Audio':
                details=[fmt]
                if track.get('Channels'):details.append(plain(track['Channels'])+' channels')
                if track.get('Language'):details.append(LANGUAGES.get(str(track['Language']).lower(),plain(track['Language'])))
                rate=bitrate(track.get('BitRate'))
                if rate:details.append('bitrate '+rate)
                lines.append('Audio: '+', '.join(details)+'.')
        total=bitrate(video_facts(video).get('totalBitrate'))
        if total:lines.append('Overall bitrate: '+total+'.')
        languages=video.get('languages',[])
        extras=video.get('addedSubtitles',[])
        if preview:languages=sorted(set(languages)|{s['language'] for s in extras})
        label='Expected subtitles' if preview and extras else 'Identified subtitles'
        lines.append(label+': '+subtitle_text(video,preview)+'.')
        if extras:
            action='Additional subtitles will be added in' if preview else 'Additional subtitles have been added in'
            lines.append(action+' '+joined([LANGUAGES.get(s['language'],s['language']) for s in extras])+'.')
        if video.get('sizeBytes'):
            lines.append(f"File size: {video['sizeBytes']/1024**3:.2f} GiB.")
    if preview and len(videos)<count:lines+=['',f'This preview shows the first {len(videos)} of {count} files. The final description covers all selected videos.']
    if body.synopsis.strip():lines+=['','[b]Additional notes[/b]',plain(body.synopsis.strip())]
    lines+=['','[b]Original source and credits[/b]',plain(source_credit(body, videos))]
    if body.tmdb:lines+=['',f"TMDB: https://www.themoviedb.org/{'movie' if body.kind=='movie' else 'tv'}/{body.tmdb}"]
    return '\n'.join(lines)
