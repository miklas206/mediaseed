"""Match selected TV files to a bounded, verified TMDB catalogue; never guess."""
from datetime import date
from pathlib import Path
import re
import unicodedata

from mediaseed.upload_engine.autofacts import title_complete
from mediaseed.upload_engine.releases import release_name
from mediaseed.upload_engine.description import readable_name
from mediaseed.upload_engine.source_service import strip_service_suffix


def normalized(value):
    return re.sub(r'[^a-z0-9]', '', unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode().lower())


def plan_episodes(body, catalogue, today=None):
    today = today or date.today().isoformat()
    if catalogue.get('status') != 'ok':
        return {'status': 'unavailable', 'mode': 'episodes', 'complete': False, 'entries': [], 'missing': [],
                'message': catalogue.get('message', 'Afsnitslisten kunne ikke hentes. Kontrollér TMDB-forbindelsen.')}
    episodes = catalogue.get('episodes', [])
    by_id = {(e['season'], e['episode']): e for e in episodes}
    overrides = {o.file: o for o in body.episode_overrides}
    entries, unresolved, used = [], [], set()
    series_name = normalized(catalogue.get('title', ''))
    show_names = {name for value in [catalogue.get('title',''),catalogue.get('originalTitle','')] if (name:=normalized(value))}
    for file in body.videos:
        override = overrides.get(file)
        numbers = re.findall(r'(?i)S(\d{1,3})E(\d{1,4})(?!\d)', Path(file).stem)
        multi_episode = bool(re.search(r'(?i)S\d+E\d+(?:E\d+|[-._ ]E\d+|-\d{1,4}(?![a-z0-9]))', Path(file).stem))
        if multi_episode or len(numbers) > 1:
            unresolved.append({'file': file, 'message': 'Filen indeholder flere afsnitsnumre. Dobbeltafsnit kan ikke navngives som ét afsnit; klargør separate afsnitsfiler.'})
            continue
        identity = None
        if override:
            identity = (override.season, override.episode)
        elif len(numbers) == 1 and not multi_episode:
            marker=re.search(r'(?i)S\d{1,3}E\d{1,4}(?!\d)',Path(file).stem)
            prefix=Path(file).stem[:marker.start()].strip(' ._-')
            prefix=re.sub(r'(?:[ ._(-](?:19|20)\d{2}\)?)$','',prefix)
            if prefix and normalized(prefix) not in show_names:
                unresolved.append({'file':file,'message':'Serienavnet i filen passer ikke til den valgte TMDB-serie. Kontrollér serien og angiv eventuelt afsnittet manuelt.'})
                continue
            identity = tuple(map(int, numbers[0]))
        if identity is not None:
            candidates = [by_id[identity]] if identity in by_id else []
            if not candidates and override and override.numbering_source == 'Manual':
                title = override.episode_title.strip()
                if not title or any(c in title for c in '[]') or any(ord(c) < 32 for c in title):
                    unresolved.append({'file': file, 'message': 'Angiv en almindelig afsnitstitel til det manuelle sæson- og afsnitsnummer.'})
                    continue
                candidates = [{'season': override.season, 'episode': override.episode,
                               'title': title, 'airDate': None, 'numberingSource': 'Manual'}]
        elif numbers:
            candidates = []  # Multi-episode files require an explicit mapping.
        else:
            stem = normalized(readable_name(strip_service_suffix(release_name(file))))
            candidates = [e for e in episodes if normalized(e['title']) and stem in {
                normalized(e['title']), series_name + normalized(e['title'])}]
        if len(candidates) != 1:
            message = 'Afsnittet kunne ikke findes entydigt i det hentede katalog. Vælg sæson og afsnit; brug manuel nummerering og en afsnitstitel, hvis afsnittet mangler i kataloget.'
            if (catalogue.get('crossCheck') or {}).get('status') != 'ok':
                message += ' Hvis afsnittet kun findes i TheTVDB, kræves et fungerende krydstjek med API-adgang; et almindeligt login er ikke tilstrækkeligt.'
            unresolved.append({'file': file, 'message': message})
            continue
        episode = candidates[0]
        identity = (episode['season'], episode['episode'])
        if list(identity) in (catalogue.get('crossCheck') or {}).get('conflictingNumbers', []):
            unresolved.append({'file': file, 'message': 'TMDB og TheTVDB er uenige om dette afsnits identitet eller nummerering. Kontrollér forskellene i krydstjekket.'})
            continue
        if identity in used:
            unresolved.append({'file': file, 'message': 'Samme afsnit er valgt flere gange. Vælg én version.'})
            continue
        used.add(identity)
        entries.append({'file': file, **episode, 'special': episode['season'] == 0,
                        'matchedBy': 'manual' if override else 'number' if identity and numbers else 'title'})
    expected = set(by_id)
    selected_seasons = {e['season'] for e in entries}
    missing = [e for e in episodes if (e['season'], e['episode']) not in used and e['season'] in selected_seasons]
    missing_series = [dict(e, releaseStatus=(
        'unknown' if not isinstance(e.get('airDate'), str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', e['airDate'])
        else 'upcoming' if e['airDate'] >= today else 'aired'))
        for e in episodes if (e['season'], e['episode']) not in used]
    coverage = [{'season': number,
                 'selected': sum(e['season'] == number and e.get('numberingSource') != 'Manual' for e in entries),
                 'total': sum(e['season'] == number for e in episodes)}
                for number in sorted({e['season'] for e in episodes})]
    fully_aired = lambda group: bool(group) and all(isinstance(e.get('airDate'),str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}',e['airDate']) and e['airDate'] < today for e in group)
    mode = 'episodes'
    check = catalogue.get('crossCheck')
    verified = (not check or check.get('status') == 'ok' and not check.get('conflicts') and not check.get('datesUnconfirmed')) and not any(e.get('numberingSource') == 'Manual' for e in entries)
    season_checks = []
    for number in sorted(selected_seasons - {0}):
        group = [e for e in episodes if e['season'] == number]
        selected = [e for e in entries if e['season'] == number]
        missing_count = sum((e['season'], e['episode']) not in used for e in group)
        reason = ''
        if unresolved or any(e.get('numberingSource') == 'Manual' for e in selected):
            reason = 'Afsnitsidentiteten skal være bekræftet i kataloget.'
        elif missing_count:
            reason = f'Mangler {missing_count} afsnit i denne sæson.'
        elif not fully_aired(group):
            reason = 'Sæsonen indeholder kommende afsnit eller ukendte udgivelsesdatoer.'
        elif (catalogue.get('nextEpisode') or {}).get('season_number') == number:
            reason = 'Kataloget angiver, at der kommer flere afsnit i denne sæson.'
        elif check and check.get('status') not in {'ok', 'not_configured'}:
            reason = 'Det tilknyttede TheTVDB-krydstjek kunne ikke gennemføres. Prøv at opdatere afsnitslisten.'
        elif check and check.get('status') == 'ok':
            conflicts = check.get('conflictingNumbers', [])
            relevant_conflict = any(n[0] == number for n in conflicts)
            # Older catalogues may lack scoped conflict information.
            relevant_conflict |= bool(check.get('conflicts') and not conflicts)
            date_differences = [d for d in check.get('differences', []) if d.get('type') == 'date']
            uncertain_date = any(e.get('season') == number and not fully_aired([e])
                                 for d in date_differences for e in [d.get('tmdb', {}), d.get('tvdb', {})])
            uncertain_date |= bool(check.get('datesUnconfirmed') and not date_differences)
            if relevant_conflict or uncertain_date:
                reason = 'TMDB og TheTVDB har uafklarede forskelle for denne sæson.'
        season_checks.append({'season': number, 'selected': len(selected), 'total': len(group),
                              'complete': not reason and bool(group), 'reason': reason,
                              'source': 'TMDB + TheTVDB' if check and check.get('status') == 'ok' else 'TMDB'})
    if not unresolved and len(entries) > 1:
        if len(selected_seasons) == 1 and 0 not in selected_seasons:
            if season_checks[0]['complete']:
                mode = 'season'
        elif 0 not in selected_seasons and verified and used == expected and any(e['season'] > 0 for e in entries) and fully_aired(episodes) and catalogue.get('seriesStatus') in {'Ended', 'Canceled'}:
            mode = 'series'
    return {'status': 'needs_review' if unresolved else 'ready', 'mode': mode, 'complete': mode != 'episodes',
            'entries': entries, 'unresolved': unresolved, 'missing': missing,
            'missingSeries': missing_series, 'seasonCoverage': coverage, 'seasonChecks': season_checks, 'title': catalogue.get('title', ''),
            'tmdb': catalogue.get('tmdb'), 'numberingSource': 'TMDB + TheTVDB' if check and check.get('status') == 'ok' else 'TMDB',
            **({'crossCheck': check} if check else {}),
            'message': (f"{len(entries)} af {len(body.videos)} filer identificeret. Kontrollér eller fravælg: " +
                        '; '.join(Path(e['file']).name for e in unresolved)) if unresolved else
                f"Komplet sæson {season_checks[0]['season']} bekræftet mod {season_checks[0]['source']}. Alle {len(entries)} afsnit klargøres som én samlet sæsonupload. Andre sæsoner og specials kræves ikke til denne sæsonpakke." if mode == 'season' else
                'Hele den afsluttede serie er bekræftet som én samlet upload.' if mode == 'series' else
                f'Udvalget klargøres som {len(entries)} separate uploads. Det er ikke en bekræftet komplet pakke.'}


def slug(value):
    value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode()
    return re.sub(r'[^A-Za-z0-9]+', '.', value).strip('.')


def episode_title(body, entry, show):
    original = release_name(entry['file'])
    override = next((o for o in body.episode_overrides if o.file == entry['file']), None)
    if override and override.release_title:
        markers = re.findall(r'(?i)S(\d{1,3})E(\d{1,4})(?!\d)', override.release_title)
        if len(markers)!=1 or tuple(map(int,markers[0]))!=(entry['season'],entry['episode']):
            raise ValueError('En rettet uploadtitel skal indeholde det valgte SxxEyy-nummer. Ret titlen i afsnitsoversigten.')
        return override.release_title
    # Existing release names/groups are retained. A new site title for an
    # untagged source never renames or relabels its media payload.
    if re.search(r'(?i)S\d{1,3}E\d{1,4}(?!\d)', original):
        return re.sub(r'(?i)S\d{1,3}E\d{1,4}(?!\d)', f"S{entry['season']:02}E{entry['episode']:02}", original, count=1)
    result = f"{slug(show)}.S{entry['season']:02}E{entry['episode']:02}.{slug(entry['title'])}"
    return result[:240]


def uploads_for_plan(body, plan):
    if plan['status'] != 'ready':
        raise ValueError(plan['message'])
    if plan['mode'] != 'episodes':
        season_numbers = sorted({e['season'] for e in plan['entries']})
        regular = [number for number in season_numbers if number > 0]
        marker = f'S{season_numbers[0]:02}' if plan['mode'] == 'season' else f'S{regular[0]:02}-S{regular[-1]:02}'
        title = body.title if title_complete(body.title) and re.search(r'(?i)(?:^|[. _-])'+re.escape(marker)+r'(?![0-9]|E[0-9])',body.title) else f"{slug(plan['title'])}.{marker}"
        return [body.model_copy(update={'title': title, 'kind': plan['mode'], 'complete': True,
                 'catalogue_title': plan['title'],
                 'upload_strategy': 'single', 'episode_overrides': [type_ref(body, e) for e in plan['entries']]})]
    return episode_uploads(body, plan)


def episode_uploads(body, plan):
    """Build separate episode requests from matched entries, also for read-only previews.

    Queueing must still pass uploads_for_plan's ready check for the full selection.
    """
    return [body.model_copy(update={'title': episode_title(body, e, plan['title']), 'kind': 'episode',
             'catalogue_title': plan['title'],
             'videos': [e['file']], 'subtitles': [s for s in body.subtitles if s.video == e['file']],
             'complete': False, 'upload_strategy': 'single', 'episode_overrides': [type_ref(body, e)]}) for e in plan['entries']]


def type_ref(body, entry):
    # Local import avoids a cycle with the public request schema.
    from mediaseed.upload_engine.upload_helper import EpisodeReference
    prior = next((o for o in body.episode_overrides if o.file == entry['file']), None)
    return EpisodeReference(file=entry['file'], season=entry['season'], episode=entry['episode'],
                            episode_title=entry['title'], release_title=prior.release_title if prior else '',
                            numbering_source=entry.get('numberingSource', 'TMDB'))
