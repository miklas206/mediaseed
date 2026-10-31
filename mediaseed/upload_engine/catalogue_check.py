"""Compare verified series IDs; retain provenance and reject ambiguous identities."""
import copy
import re
import unicodedata
from datetime import date


def title_key(value):
    return re.sub('[^a-z0-9]', '', unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode().lower())


def crosscheck(tmdb, tvdb):
    result = copy.deepcopy(tmdb)
    episodes = result['episodes']
    check = {'status': 'ok', 'tvdb': tvdb['tvdb'], 'tmdbCount': len(episodes),
             'tvdbCount': len(tvdb['episodes']), 'differences': [], 'conflicts': False,
             'conflictingNumbers': [],
             'datesUnconfirmed': False,
             'message': 'Afsnitslisterne er krydstjekket mod TMDB og TheTVDB.'}
    by_number = {(e['season'], e['episode']): e for e in episodes}
    by_title = {}
    for episode in episodes:
        episode['numberingSource'] = 'TMDB'
        episode['sources'] = ['TMDB']
        by_title.setdefault(title_key(episode['title']), []).append(episode)
    for other in tvdb['episodes']:
        key = (other['season'], other['episode'])
        numbered = by_number.get(key)
        titled = by_title.get(title_key(other['title']), [])
        if numbered and title_key(numbered['title']) == title_key(other['title']):
            numbered['sources'].append('TheTVDB')
            numbered['tvdbEpisode'] = other['tvdbEpisode']
            if numbered.get('airDate') != other.get('airDate'):
                check['differences'].append({'type': 'date', 'tmdb': dict(numbered), 'tvdb': other})
                check['datesUnconfirmed'] |= any(not isinstance(e.get('airDate'), str) or
                    not re.fullmatch(r'\d{4}-\d{2}-\d{2}', e['airDate']) or e['airDate'] >= date.today().isoformat()
                    for e in [numbered, other])
        elif numbered or titled:
            check['conflicts'] = True
            check['conflictingNumbers'].append(list(key))
            check['conflictingNumbers'].extend([[e['season'], e['episode']] for e in titled])
            check['differences'].append({'type': 'identity', 'tmdb': dict(numbered or titled[0]), 'tvdb': other})
        else:
            episode = dict(other, numberingSource='TheTVDB', sources=['TheTVDB'])
            episodes.append(episode)
            check['differences'].append({'type': 'tvdb_only', 'tvdb': other})
    for episode in episodes:
        if episode['sources'] == ['TMDB']:
            check['differences'].append({'type': 'tmdb_only', 'tmdb': dict(episode)})
    if len(episodes) > 20000:
        raise ValueError('Det samlede afsnitskatalog er for stort')
    result['crossCheck'] = check
    return result
