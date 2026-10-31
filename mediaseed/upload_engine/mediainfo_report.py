"""One download/copy of the genuine saved MediaInfo reports for a whole job."""


def combined_report(result):
    videos = result.get('videos', [])
    if not videos or any(not v.get('report', '').strip() for v in videos):
        raise ValueError('MediaInfo mangler for en eller flere filer i pakken')
    sections = [str(result.get('title') or 'MediaInfo') + '\n' + f'MediaInfo for all {len(videos)} files\n']
    total = 0
    for index, video in enumerate(videos, 1):
        section = '\n' + '=' * 76 + '\n' + f'File {index:02}/{len(videos):02}: {video["file"]}\n' + '=' * 76 + '\n\n' + video['report'].rstrip() + '\n'
        total += len(section.encode('utf-8'))
        if total > 8 * 1024**2:
            raise ValueError('Samlet MediaInfo overstiger 8 MiB. Brug rapporterne pr. fil')
        sections.append(section)
    return ''.join(sections)
