"""Release claims supported by streams, never by an old release filename."""
import re

from mediaseed.media_languages import ALIASES, language_code
NORDIC = {'da', 'sv', 'no', 'fi'}
LANGUAGE_TAGS = {'da':'DANiSH', 'sv':'SWEDiSH', 'no':'NORWEGiAN', 'fi':'FiNNiSH', 'ja':'JAPANESE'}


def subtitle_languages(tracks):
    return {code for t in tracks if t.get('@type') == 'Text'
            if (code := language_code(t.get('Language', ''))) }


def language_tag(videos):
    common = set.intersection(*(set(v.get('languages', [])) for v in videos)) if videos else set()
    if NORDIC <= common:
        return 'NORDiC'
    if 'da' in common:
        return 'DANiSH'
    # A uniform known audio language can identify local-language material.
    # Unknown or mixed audio must not be promoted to a single language claim.
    audio = [[language_code(t.get('Language', '')) for t in v.get('technical', [])
              if t.get('@type') == 'Audio'] for v in videos]
    if audio and all(group and None not in group for group in audio):
        languages = set(code for group in audio for code in group)
        if len(languages) == 1:
            return LANGUAGE_TAGS.get(next(iter(languages)), '')
    return ''


def dynamic_range_tags(videos):
    groups = []
    for video in videos:
        tags = set()
        for track in video.get('technical', []):
            if track.get('@type') != 'Video':
                continue
            evidence = ' '.join(str(track.get(k, '')) for k in ('HDR_Format', 'HDR_Format_Compatibility')).lower()
            if 'dolby vision' in evidence:
                tags.add('DV')
            if 'hdr10+' in evidence or 'smpte st 2094' in evidence:
                tags.add('HDR10Plus')
            elif 'hdr10' in evidence or 'smpte st 2086' in evidence:
                tags.add('HDR')
            if 'hlg' in evidence:
                tags.add('HLG')
        groups.append(tags)
    common = set.intersection(*groups) if groups else set()
    # Tracker names permit only ordinary letters, dots, dashes and underscores.
    return [tag for tag in ('DV', 'HDR', 'HDR10Plus', 'HLG') if tag in common]


def audio_tokens(videos):
    groups = [[t for t in v.get('technical', []) if t.get('@type') == 'Audio'] for v in videos]
    formats = [{t.get('Format') for t in group} for group in groups]
    if not formats or not formats[0] or any(f != formats[0] for f in formats) or len(formats[0]) != 1:
        return []
    label = {'AAC':'AAC', 'AC-3':'DD', 'E-AC-3':'DDP', 'DTS':'DTS', 'TrueHD':'TrueHD', 'MLP FBA':'TrueHD', 'MLP FBA 16-ch':'TrueHD', 'FLAC':'FLAC', 'PCM':'PCM'}.get(next(iter(formats[0])))
    if not label:
        return []
    result = [label]
    tracks = [t for group in groups for t in group]
    layouts = {t.get('ChannelLayout', '') for t in tracks}
    channels = {str(t.get('Channels', '')) for t in tracks}
    if len(channels) == 1:
        count = next(iter(channels))
        if count in {'1', '2'}:
            result.append(count + '.0')
        elif count in {'6', '8'} and all('LFE' in layout.split() for layout in layouts):
            result.append(str(int(count) - 1) + '.1')
    def atmos(track):
        evidence = ' '.join(str(track.get(k, '')) for k in ('Format', 'Format_AdditionalFeatures', 'Format_Commercial_IfAny'))
        return bool(re.search(r'\b(?:Atmos|JOC)\b', evidence, re.I))
    if all(atmos(t) for t in tracks):
        result.append('Atmos')
    return result


def tag_summary(videos):
    tag = language_tag(videos)
    common = set.intersection(*(set(v.get('languages', [])) for v in videos)) if videos else set()
    missing = [label for code,label in [('da','DK'),('sv','SE'),('no','NO'),('fi','FI')] if code not in common]
    message = ('Sprogtag: ' + tag + '. ') if tag else 'Intet dansk eller nordisk sprogtag. '
    if tag == 'DANiSH':
        message += 'Danske undertekster i hver fil. ' if 'da' in common else 'Dansk lyd i hver fil. '
    message += 'NORDiC: DK, SE, NO og FI findes i hver fil.' if not missing else 'NORDiC udelades: mangler undertekster på ' + ', '.join(missing) + '.'
    extra = dynamic_range_tags(videos)
    if extra:
        message += ' Videospor: ' + ', '.join(extra) + '.'
    sound = audio_tokens(videos)
    if sound:
        message += ' Lydspor: ' + '.'.join(sound) + '.'
    return {'languageTag':tag, 'audioTags':sound, 'dynamicRangeTags':extra, 'missingNordicSubtitles':missing, 'message':message}
