"""Conservative, measured cross-release improvements; never rank by release labels."""
import hashlib
import json
import math
import re
import subprocess
import unicodedata
from fractions import Fraction
from pathlib import Path

from mediaseed.media_languages import language_code

SCHEMA = 1
EDITION = re.compile(r"(?i)\b(extended|unrated|unrated cut|theatrical|director'?s[ ._-]*cut|final[ ._-]*cut|special[ ._-]*edition|imax|open[ ._-]*matte|remastered)\b")


def identity(name, torrent_title=None):
    """Exact title/year or title/episode only. Ambiguous and multi-episode names fail closed."""
    stem = Path(name).stem
    text = re.sub(r'[._-]+', ' ', stem)
    episode = re.search(r'(?i)\bS(\d{1,2})E(\d{1,3})\b', text)
    if episode:
        tail = text[episode.end():]
        if re.match(r'(?i)\s*(?:E\d|\d{1,3}\b)', tail) or re.search(r'(?i)\bS\d+E\d+\b', tail):
            return None
        title = text[:episode.start()].strip()
        if not title and torrent_title:
            pack = re.sub(r'[._-]+', ' ', str(torrent_title))
            season = re.search(r'(?i)\bS\d{1,2}\b|\bSeason\s+\d{1,2}\b', pack)
            if season: title = pack[:season.start()].strip()
        key = ('episode', int(episode[1]), int(episode[2]))
    else:
        years = list(re.finditer(r'\b(?:19|20)\d{2}\b', text))
        year = years[-1] if years else None
        if not year:
            return None
        title, tail = text[:year.start()].strip(), text[year.end():]
        key = ('movie', int(year[0]))
    title = ''.join(c for c in unicodedata.normalize('NFKC', title).casefold() if c.isalnum())
    if not title:
        return None
    edition = tuple(sorted(set(re.sub(r'[^a-z]', '', m[0].lower()) for m in EDITION.finditer(tail))))
    # Explicit theatrical and unlabeled releases still require the user's cut confirmation.
    return (title, *key, edition)


def probe(path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-threads', '1', '-show_format',
                             '-show_streams', '-show_chapters', '-of', 'json', str(path)],
                            capture_output=True, text=True, check=True, timeout=60)
    return json.loads(result.stdout)


def measured(data, info):
    videos = [s for s in data.get('streams', []) if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic')]
    if len(videos) != 1:
        raise ValueError('Choose one video stream')
    v = videos[0]
    duration = float(data['format']['duration'])
    fps = float(Fraction(v['avg_frame_rate']))
    if not math.isfinite(duration) or not 0 < duration < 7 * 86400 or not 1 <= fps <= 240:
        raise ValueError('Invalid media timing')
    # MediaInfo's parsed bitstream information establishes Atmos; a track title cannot.
    mi_audio = [s for s in info.get('media', {}).get('track', []) if s.get('@type') == 'Audio']
    audio = []
    raw_audio = [s for s in data.get('streams', []) if s.get('codec_type') == 'audio']
    for ordinal, s in enumerate(raw_audio):
        tags, flags = s.get('tags', {}), s.get('disposition', {})
        language = language_code(tags.get('language'))
        title = str(tags.get('title', ''))
        if not language or any(flags.get(k) for k in ('comment', 'visual_impaired', 'hearing_impaired')) or re.search(r'(?i)commentary|audio description|descriptive', title):
            continue
        codec = s.get('codec_name')
        if codec not in {'truehd', 'dts', 'flac', 'ac3', 'eac3', 'aac', 'opus', 'pcm_s16le', 'pcm_s24le'}:
            continue
        m = mi_audio[ordinal] if len(mi_audio) == len(raw_audio) else {}
        features = ' '.join(str(m.get(k, '')) for k in ('Format_AdditionalFeatures', 'Format_Commercial_IfAny', 'CommercialName'))
        atmos = bool(re.search(r'(?i)\bAtmos\b|\bJOC\b', features))
        lossless = codec in {'truehd', 'flac', 'pcm_s16le', 'pcm_s24le'} or (codec == 'dts' and s.get('profile') in {'DTS-HD MA', 'DTS-HD MA + DTS:X'})
        channels = int(s.get('channels') or 0)
        if channels < 1:
            continue
        channel_label = {8: '7.1', 6: '5.1', 2: '2.0', 1: '1.0'}.get(channels, str(channels) + ' kanaler')
        label = {'truehd': 'TrueHD', 'eac3': 'Dolby Digital Plus', 'ac3': 'Dolby Digital', 'dts': s.get('profile', 'DTS')}.get(codec, codec.upper())
        audio.append({'index': s['index'], 'ordinal': ordinal, 'language': language, 'codec': codec,
                      'channels': channels, 'lossless': lossless, 'atmos': atmos,
                      'label': label + (' Atmos' if atmos else '') + ' ' + channel_label + ' · ' + language})
    dv = any(s.get('side_data_type') == 'DOVI configuration record' for s in v.get('side_data_list', []))
    hdr = dv or v.get('color_transfer') in {'smpte2084', 'arib-std-b67'}
    return {'duration': duration, 'fps': fps, 'width': int(v['width']), 'height': int(v['height']),
            'hdr': bool(hdr), 'dv': dv, 'depth': 10 if '10' in v.get('pix_fmt', '') else 8,
            'audio': audio, 'chapters': [float(c['start_time']) for c in data.get('chapters', [])]}


def metadata(path):
    data = probe(path)
    result = subprocess.run(['mediainfo', '--Output=JSON', str(path)], capture_output=True, text=True, check=True, timeout=60)
    return measured(data, json.loads(result.stdout))


def dominates(a, b):
    fields = ('channels', 'lossless', 'atmos')
    return a['language'] == b['language'] and all(a[k] >= b[k] for k in fields) and any(a[k] > b[k] for k in fields)


def compatible(a, b):
    if abs(a['duration'] - b['duration']) > 1 or abs(a['fps'] - b['fps']) > .0001:
        return False
    if a['chapters'] and b['chapters']:
        if len(a['chapters']) != len(b['chapters']) or any(abs(x-y) > 1 for x, y in zip(a['chapters'], b['chapters'])):
            return False
    return True


def improvements(entries):
    groups = {}
    for entry in entries:
        key = identity(entry['name'], entry.get('title'))
        if key:
            groups.setdefault(key, []).append(entry)
    found = {}
    for group in groups.values():
        for picture in group:
            a = picture['metadata']
            for donor in group:
                b = donor['metadata']
                if picture['path'] == donor['path'] or not compatible(a, b):
                    continue
                # A strictly higher resolution, with no loss of HDR/DV/depth.
                if not (a['width'] > b['width'] and a['height'] > b['height'] and all(a[k] >= b[k] for k in ('hdr', 'dv', 'depth'))):
                    continue
                for audio in b['audio']:
                    current = [t for t in a['audio'] if t['language'] == audio['language']]
                    if not current or not all(dominates(audio, t) for t in current):
                        continue
                    evidence = [picture['hash'], picture['index'], picture['identity'], donor['hash'], donor['index'], donor['identity'], audio]
                    identifier = hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()
                    key = (picture['path'], audio['language'])
                    frontier = found.setdefault(key, [])
                    quality = lambda t: tuple(t[k] for k in ('channels', 'lossless', 'atmos'))
                    if any(dominates(q['audio'], audio) or quality(q['audio']) == quality(audio) for q in frontier):
                        continue
                    frontier[:] = [q for q in frontier if not dominates(audio, q['audio'])]
                    frontier.append({'id': identifier, 'video': picture, 'donor': donor, 'audio': audio,
                                     'replaces': [t['label'] for t in current]})
    return [p for frontier in found.values() for p in frontier][:120]


def public_proposal(proposal):
    def source(entry):
        m = entry['metadata']
        return {k: entry[k] for k in ('hash', 'index', 'name')} | {'resolution': f"{m['width']}×{m['height']}", 'duration': m['duration']}
    return {'id': proposal['id'], 'video': source(proposal['video']), 'donor': source(proposal['donor']),
            'audio': proposal['audio']['label'], 'replaces': proposal['replaces'], 'syncVerified': False}
