"""Captain Quack upload attachment, built only from this job's observed facts."""
from mediaseed.upload_engine.source_service import source_credit
from pathlib import Path
import re
import textwrap

from mediaseed.upload_engine.description import LANGUAGES, generate_description, plain, bitrate, subtitle_text
from mediaseed.upload_comparison import video_facts

HEADER = Path(__file__).parent / 'assets' / 'captain-quack.txt'


def clean(value):
    return re.sub(r'[\x00-\x1f\x7f-\x9f]', ' ', str(value)).strip()


def box(title, paragraphs):
    lines = ['  ╓' + ('─ ' + title + ' ').ljust(70, '─') + '╖', '  ║' + ' ' * 70 + '║']
    for paragraph in paragraphs:
        for logical in str(paragraph).splitlines() or ['']:
            indent = '    ' if re.match(r'^\d+\. ', logical) else ''
            for line in textwrap.wrap(clean(logical), width=66, subsequent_indent=indent) or ['']:
                lines.append('  ║' + ('  ' + line).ljust(70) + '║')
    return lines + ['  ║' + ' ' * 70 + '║', '  ╙' + '─' * 70 + '╜', '']


def credit_paragraphs(value):
    groups, releases, notes = [], [], []
    chunks = re.split(r'(?i)(?=Original release:|Original group:|Original group not identified|Download service:|TMDB:|TheTVDB:)', value)
    for chunk in chunks:
        original = chunk.strip()
        chunk = ' '.join(chunk.split())
        if not chunk:
            continue
        if chunk.lower().startswith('original group:'):
            value = original.split(':', 1)[1].strip()
            first, *rest = value.splitlines() or ['']
            group = first.strip()
            if group and group.casefold() not in {g.casefold() for g in groups}: groups.append(group)
            for note in rest:
                if note.strip() and note.strip() not in notes: notes.append(note.strip())
        elif chunk.lower().startswith('original release:'):
            release = chunk.split(':', 1)[1].strip()
            if release and release not in releases: releases.append(release)
        else:
            for note in original.splitlines():
                if note.strip() and note.strip() not in notes: notes.append(note.strip())
    paragraphs = []
    if groups:
        paragraphs.append(('Original group: ' if len(groups) == 1 else 'Original groups: ') + ', '.join(groups))
        notes = [n for n in notes if n.lower() != 'original group not identified']
    if releases:
        if paragraphs: paragraphs.append('')
        if len(releases) == 1:
            paragraphs.append('Original release: ' + releases[0])
        else:
            paragraphs.append('Original releases:')
            for index, release in enumerate(releases, 1):
                paragraphs.extend(['', f'{index:02}. {release}'])
    if notes:
        if paragraphs: paragraphs.append('')
        for note in notes: paragraphs.extend([note, ''])
    return paragraphs


def readable_credits(text):
    """Format a downloaded legacy attachment without changing seeded bytes."""
    pattern = r'(?ms)^  ╓─ ORIGINAL SOURCE AND CREDITS[^\n]*\n.*?^  ╙[^\n]*(?:\n|$)'
    match = re.search(pattern, text)
    if not match:
        return text
    # A legacy dense block always contains repeated source/group labels. Leave
    # already readable or free-form credits exactly as written.
    if match[0].lower().count('original release:') < 2 and match[0].lower().count('original group:') < 2:
        return text
    content = '\n'.join(line[3:-1].strip() for line in match[0].splitlines()
                        if line.startswith('  ║') and line.endswith('║'))
    if 'Original releases:' in content or content.lstrip().startswith(('Original group:', 'Original groups:')):
        return text
    replacement = '\n'.join(box('ORIGINAL SOURCE AND CREDITS', credit_paragraphs(' '.join(content.split()))))
    return text[:match.start()] + replacement + text[match.end():]


def plain_description(body, videos, metadata=None):
    return re.sub(r'\[/?[a-zA-Z][^\]\r\n]*\]', '', generate_description(body, videos, metadata=metadata))


def generate_nfo(body, videos, metadata=None, preview=False):
    lines = HEADER.read_text(encoding='utf-8').splitlines() + ['', 'P R E S E N T S'.center(76), '']
    lines += [part.center(76) for part in textwrap.wrap(clean(body.title), 72)] + ['']
    kind = {'movie': 'Movie', 'episode': 'TV episode', 'season': 'Season pack', 'series': 'Series pack'}[body.kind]
    if body.kind=='episode' and body.episode_overrides and body.episode_overrides[0].season==0:
        kind='TV special'
    lines += box('UPLOAD INFO', [f'TYPE         {kind}', f'FILES        {len(videos)}', 'STATUS       Preview - final media not prepared yet' if preview else 'STATUS       Prepared media', f'SOURCE       {body.source_type} (declared source)'])
    if body.episode_overrides:
        episodes = [f"S{e.season:02}E{e.episode:02}" + (' (Special)' if e.season == 0 else '') + ' - ' + e.episode_title for e in body.episode_overrides]
        numbering = ' + '.join(dict.fromkeys(e.numbering_source for e in body.episode_overrides))
        lines += box('INCLUDED EPISODES - ' + numbering.upper() + ' NUMBERING', episodes)
        if any(e.numbering_source == 'Manual' for e in body.episode_overrides):
            lines += box('MANUAL EPISODE MAPPING', ['Episode title and numbering supplied by the uploader.', 'Not verified against the episode catalogue.'])
    if metadata and metadata.get('status') == 'ok' and metadata.get('language') == 'en' and str(metadata.get('overview', '')).strip():
        scope = {'movie': 'MOVIE SYNOPSIS', 'series': 'SERIES SYNOPSIS', 'episode': 'EPISODE SYNOPSIS'}.get(metadata.get('scope'), 'SYNOPSIS')
        plot = [metadata.get('title', ''), metadata.get('episodeTitle', ''), '', metadata['overview'], '', 'Synopsis: TMDB (English).']
    else:
        scope, plot = 'SYNOPSIS', ['English synopsis unavailable. Select the correct TMDB entry and check the TMDB connection before uploading.']
    lines += box(scope, plot)
    for video in videos:
        details = ['FILE         ' + clean(video['file']), 'CONTAINER    ' + clean(video.get('container') or Path(video['file']).suffix.lstrip('.').upper() or 'Unknown')]
        duration = video.get('durationSeconds')
        if isinstance(duration, (float, int)) and 0 < duration < 10**9:
            seconds = int(duration)
            details.append(f'RUNTIME      {seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}')
        for track in video.get('technical', []):
            if track.get('@type') == 'Video':
                parts = [track.get('Format', 'Unknown codec')]
                if track.get('Width') and track.get('Height'):
                    parts.append(f"{track['Width']} x {track['Height']}")
                if track.get('FrameRate'):
                    parts.append(f"{track['FrameRate']} fps")
                rate=bitrate(track.get('BitRate'))
                details.append('VIDEO        ' + ' | '.join(map(clean, parts)))
                if rate:details.append('VIDEO BITRATE '+rate)
            elif track.get('@type') == 'Audio':
                parts = [track.get('Format', 'Unknown codec')]
                if track.get('Language'):
                    language = clean(track['Language'])
                    parts.append(LANGUAGES.get(language.lower(), language))
                if track.get('Channels'):
                    parts.append(str(track['Channels']) + ' channels')
                rate=bitrate(track.get('BitRate'))
                details.append('AUDIO        ' + ' | '.join(map(clean, parts)))
                if rate:details.append('AUDIO BITRATE '+rate)
        total=bitrate(video_facts(video).get('totalBitrate'))
        if total:details.append('TOTAL BITRATE '+total)
        languages = list(video.get('languages', []))
        if preview:
            languages = sorted(set(languages) | {s['language'] for s in video.get('addedSubtitles', [])})
        details.append(('PLANNED SUBS ' if preview else 'SUBTITLES    ') + subtitle_text(video,preview))
        if video.get('sizeBytes'):
            details.append(f"FILE SIZE    {video['sizeBytes'] / 1024**3:.2f} GiB")
        lines += box('MEDIA INFO', details)
    credits = credit_paragraphs(source_credit(body, videos)) + ['', f"TMDB: https://www.themoviedb.org/{'movie' if body.kind == 'movie' else 'tv'}/{body.tmdb}"]
    credits += ['Original file: ' + video['originalFile'] for video in videos if video.get('originalFile')]
    if any(e.numbering_source == 'TheTVDB' for e in body.episode_overrides):
        credits.append('TheTVDB: https://www.thetvdb.com/')
    if body.synopsis.strip():
        lines += box('ADDITIONAL NOTES', [plain(body.synopsis)])
    if body.description_override is not None:
        lines += box('UPLOADER DESCRIPTION', [plain_description(body, videos, metadata)])
    lines += box('ORIGINAL SOURCE AND CREDITS', credits)
    lines += box('AFTER HOURS', ['Built for the bathtub. Dressed for trouble.', 'Small duck. Big booty.'])
    lines += ['MDTECH / BOOTY CALL / CAPTAIN QUACK / PLAY NICE.'.center(76), '']
    return '\n'.join(lines)
