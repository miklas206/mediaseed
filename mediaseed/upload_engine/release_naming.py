"""MDTECH names derived from observed streams and explicitly identified content."""
import re
import unicodedata
from pathlib import Path
from mediaseed.upload_engine.source_service import service_info
from mediaseed.upload_engine.media_tags import language_tag, dynamic_range_tags, audio_tokens


class NamingError(ValueError):
    pass


def slug(text):
    text = unicodedata.normalize('NFKD', str(text)).encode('ascii', 'ignore').decode()
    return re.sub('[^A-Za-z0-9]+', '.', text).strip('.')


def tagged_folder_name(name, group='MDTECH'):
    name = re.sub(r'(?i)[.-](?:MDTECH|'+re.escape(group)+r')$', '', str(name).strip())
    if not name or name.startswith('.') or any(c in name for c in '/\\:<>"|?*') or any(ord(c) < 32 for c in name):
        raise NamingError('Vælg et almindeligt mappenavn')
    result = name + '-' + group
    if len(result.encode('utf-8')) > 200:
        raise NamingError('Mappenavnet er for langt med MDTECH-tagget')
    return result


def suggested_folder_name(body, metadata=None):
    name = body.catalogue_title or (metadata or {}).get('title') if body.kind != 'movie' else body.title
    if not name:
        return ''
    base = slug(re.sub(r'(?i)[.-]MDTECH$', '', name))[:193]
    return tagged_folder_name(base, body.original_group if body.release_owner == 'other' else 'MDTECH')


def dotted_folder_name(name, group='MDTECH'):
    return tagged_folder_name(name, group).removesuffix('-'+group) + '.'+group


def folder_suggestions(body, filenames, metadata=None):
    name = body.catalogue_title or (metadata or {}).get('title') if body.kind != 'movie' else ''
    group = body.original_group if body.release_owner == 'other' else 'MDTECH'
    series = dotted_folder_name(slug(re.sub(r'(?i)[.-]MDTECH$', '', name))[:193], group) if name else ''
    episode = Path(filenames[0]).stem if len(filenames) == 1 else ''
    return {'suggestedSeriesFolderName': series, 'suggestedEpisodeFolderName': episode}


def technical_tokens(videos):
    resolutions, codecs = set(), set()
    for video in videos:
        tracks = [t for t in video.get('technical', []) if t.get('@type') == 'Video']
        if len(tracks) != 1:
            raise NamingError('MDTECH-navngivning kræver ét identificeret videospor i hver fil.')
        track = tracks[0]
        try:
            width, height = int(track['Width']), int(track['Height'])
        except (KeyError, TypeError, ValueError):
            raise NamingError('MediaInfo kunne ikke fastslå opløsningen. Filen navngives ikke ved gæt.') from None
        if not 0 < width <= 32768 or not 0 < height <= 32768:
            raise NamingError('MediaInfo returnerede en ugyldig opløsning.')
        scan = {'progressive': 'p', 'interlaced': 'i'}.get(str(track.get('ScanType', '')).lower())
        # Never call a cropped 1920x800 stream 1080p, or guess progressive scan.
        resolutions.add(str(height) + scan if scan and height in {480, 576, 720, 1080, 2160, 4320} else f'{width}x{height}')
        codec = {'AVC': 'h264', 'HEVC': 'h265', 'AV1': 'AV1', 'VP9': 'VP9'}.get(track.get('Format'))
        if not codec:
            raise NamingError('MediaInfo kunne ikke fastslå en understøttet videocodec. Filen navngives ikke ved gæt.')
        codecs.add(codec)
    if len(resolutions) != 1 or len(codecs) != 1:
        raise NamingError('En samlet MDTECH-pakke kræver ens opløsning og videocodec. Vælg separate uploads ved blandet kvalitet.')
    return resolutions.pop(), codecs.pop()


def release_title(body, videos, metadata=None):
    from mediaseed.upload_engine.description import readable_name
    metadata = metadata or {}
    name = slug(body.catalogue_title or metadata.get('title') or readable_name(body.title))
    if not name:
        raise NamingError('Vælg en entydig film eller serie før MDTECH-navngivning.')
    tokens = [name]
    if body.kind == 'movie':
        year = metadata.get('year')
        if not year:
            match = re.search(r'(?<!\d)((?:19|20)\d{2})(?!\d)', body.title)
            year = match[1] if match else None
        if not year:
            raise NamingError('Filmens udgivelsesår mangler. Tilknyt TMDB eller angiv året i titlen.')
        tokens.append(str(year))
    elif body.kind == 'episode':
        if len(body.episode_overrides) != 1:
            raise NamingError('Identificér afsnittet før MDTECH-navngivning.')
        episode = body.episode_overrides[0]
        tokens.append(f'S{episode.season:02}E{episode.episode:02}')
        if episode.episode_title:
            tokens.append(slug(episode.episode_title))
    else:
        seasons = sorted({e.season for e in body.episode_overrides if e.season > 0})
        if not seasons:
            raise NamingError('Identificér sæsonerne før MDTECH-navngivning.')
        tokens.append(f'S{seasons[0]:02}' if len(seasons) == 1 else f'S{seasons[0]:02}-S{seasons[-1]:02}')
    if body.edition:
        tokens.append(body.edition)
    language = language_tag(videos)
    if language:
        tokens.append(language)
    resolution, codec = technical_tokens(videos)
    if body.source_type == 'Other':
        raise NamingError('Angiv den kendte kildetype under Flere indstillinger. WEB/BluRay kan ikke udledes sikkert af videosporet.')
    source=service_info(body,videos)
    if source['status']=='needs_input':raise NamingError(source['message'])
    tokens.append(resolution)
    if source['tag']:tokens.append(source['tag'])
    if body.source_type == 'BluRay' and resolution == '2160p':
        tokens.append('UHD')
    tokens.append(body.source_type)
    if body.release_format:
        if body.source_type != 'BluRay':
            raise NamingError('REMUX kræver en bekræftet BluRay-kilde uden genkodning.')
        tokens.append(body.release_format)
    tokens.extend(dynamic_range_tags(videos))
    tokens.append(codec)
    tokens.extend(audio_tokens(videos))
    title = '.'.join(tokens) + '-MDTECH'
    if len(title.encode('utf-8')) > 200:
        raise NamingError('MDTECH-navnet er for langt. Forkort afsnitstitlen før klargøring.')
    return title


def video_filename(title):
    return title.removesuffix('-MDTECH') + '.MDTECH.mkv'


def release_names(body, videos, metadata=None):
    if body.media_mode=='in_place':
        selected=[]
        for video in videos:
            audio=[t for t in video.get('technical',[]) if t.get('@type')=='Audio']
            primary=next((t for t in audio if t.get('Default')=='Yes'),audio[0] if audio else None)
            selected.append({**video,'technical':[t for t in video.get('technical',[]) if t.get('@type')!='Audio']+([primary] if primary else [])})
        videos=selected
    title = release_title(body, videos, metadata)
    if body.kind in {'movie', 'episode'}:
        if len(videos) != 1:
            raise NamingError('Vælg én videofil pr. MDTECH-film eller afsnit.')
        return title, {videos[0]['file']: video_filename(title)}
    names = {}
    for video in videos:
        reference = next((e for e in body.episode_overrides if e.file == video['file']), None)
        if not reference:
            raise NamingError('Et afsnit i pakken mangler verificeret nummerering.')
        child = body.model_copy(update={'kind': 'episode', 'videos': [video['file']], 'episode_overrides': [reference]})
        names[video['file']] = video_filename(release_title(child, [video], metadata))
    return title, names
