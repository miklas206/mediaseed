"""Review evidence which cannot be derived reliably from media streams."""
import re
from pathlib import Path


def naming_checks(body, metadata):
    checks = []
    def add(rule, ok, message):
        checks.append({'rule': rule, 'status': 'ok' if ok else 'missing', 'text': message})
    own = body.media_mode in {'release', 'in_place'}
    if own:
        add('navn identitet', metadata.get('status') == 'ok' and metadata.get('tmdb') == body.tmdb
            and bool(metadata.get('title')) and (body.kind != 'movie' or bool(re.fullmatch(r'(?:18|19|20)\d{2}', str(metadata.get('year', ''))))),
            'Titel og år skal være hentet fra det valgte TMDB-ID. Kontrollér film/serie og opdatér forhåndsvisningen.')
    # A source name can flag a conflict, but can never prove an edition.
    editions = set()
    sources = set()
    for filename in body.videos:
        name = Path(filename).stem.upper()
        editions.update(re.findall(r'(?<![A-Z0-9])(?:EXTENDED(?:[ ._-]EDITION)?|UNRATED|THEATRICAL|FINAL[ ._-]CUT|DIRECTORS[ ._-]CUT|REDUX)(?![A-Z0-9])', name))
        sources.update(re.findall(r'(?<![A-Z0-9])(?:BLURAY|WEB-DL|WEBRIP|HDTV|DVD)(?![A-Z0-9])', name))
    normalize = lambda value: re.sub(r'[ _-]', '.', value).replace('EXTENDED.EDITION', 'EXTENDED')
    editions = {normalize(e) for e in editions}
    if own and (editions or body.edition):
        add('navn udgave', body.source_verified and (not editions or editions == {normalize(body.edition)}),
            'Bekræft udgaven fra originalkilden under Indstillinger og markér kildekontrollen. Fundet i kildenavn: ' + (', '.join(sorted(editions)) or 'ingen sikker udgave') + '. Udgaven udledes ikke fra længde eller videospor.')
    if own:
        expected = body.source_type.upper()
        # WEB-DL -> WEBRip is legitimate after re-encoding; BluRay remains provenance.
        compatible = not sources or sources <= ({'WEB-DL', 'WEBRIP'} if expected == 'WEBRIP' else {expected})
        add('navn kilde', body.source_verified and expected != 'OTHER' and compatible,
            'Bekræft den oprindelige kildetype og kvalitet. Kildenavn: ' + (', '.join(sorted(sources)) or 'ingen entydig kildetype') + '; valgt: ' + body.source_type + '.')
    return checks
