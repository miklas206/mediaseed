from types import SimpleNamespace
import pytest
from mediaseed.upload_engine.media_tags import subtitle_languages, primary_audio
from mediaseed.upload_engine.release_naming import release_names, NamingError
from mediaseed.upload_engine.naming_checks import naming_checks
from mediaseed.upload_engine.episode_plan import plan_episodes
from test_tracker_release_naming import body, video


def test_commentary_default_does_not_override_main_sound():
    v = video()
    v['technical'][1].update(Title='Director commentary', Default='Yes')
    title, _ = release_names(body(), [v], {'year': 2015})
    assert 'DTS-HD.MA7.1' in title and 'HRA' not in title


@pytest.mark.parametrize('flag', [{'Title': 'Audio Description'}, {'Commentary': 'Yes'}, {'VisualImpaired': 'Yes'}])
def test_only_supplementary_audio_requires_correction(flag):
    with pytest.raises(ValueError, match='hovedlydspor'):
        primary_audio([{'@type': 'Audio', **flag}])


def test_ambiguous_primary_audio_is_not_guessed():
    with pytest.raises(ValueError, match='standardspor'):
        primary_audio([{'@type': 'Audio', 'Format': 'AAC'}, {'@type': 'Audio', 'Format': 'DTS'}])


@pytest.mark.parametrize('flags', [{'Forced': 'Yes'}, {'Title': 'Forced'}, {'Title': 'Signs and songs'}, {'Title': 'Commentary'}])
def test_partial_subtitles_do_not_establish_nordic(flags):
    tracks = [{'@type': 'Text', 'Language': lang} for lang in ['da', 'sv', 'no']]
    tracks.append({'@type': 'Text', 'Language': 'fi', **flags})
    assert subtitle_languages(tracks) == {'da', 'sv', 'no'}


def test_full_sdh_subtitles_are_valid_language_evidence():
    assert subtitle_languages([{'@type': 'Text', 'Language': 'da', 'Title': 'Danish SDH'}]) == {'da'}


def test_catalogue_title_overrides_stale_title_and_preserves_numeric_title():
    title, _ = release_names(body(), [video()], {'title': '1917', 'year': 2019})
    assert title.startswith('1917.2019.') and 'Furious' not in title


def test_fallback_title_does_not_duplicate_year():
    b = body(); b.catalogue_title = 'Furious 7 2015'
    title, _ = release_names(b, [video()], {'year': 2015})
    assert title.startswith('Furious.7.2015.NORDiC')


def request(**changes):
    values = dict(media_mode='in_place', tmdb=12, kind='movie', edition='', source_verified=True,
                  source_type='BluRay', videos=['Movie.2025.BluRay.mkv'])
    return SimpleNamespace(**(values | changes))


def checks(b):
    return {c['rule']: c['status'] for c in naming_checks(b, {'status': 'ok', 'tmdb': 12, 'title': 'Movie', 'year': 2025})}


def test_edition_requires_explicit_choice_and_source_confirmation():
    b = request(videos=['Movie.2025.EXTENDED.BluRay.mkv'])
    assert checks(b)['navn udgave'] == 'missing'
    b.edition = 'EXTENDED.EDITION'
    assert checks(b)['navn udgave'] == 'ok'
    b.source_verified = False
    assert checks(b)['navn udgave'] == 'missing'


def test_source_conflict_and_unconfirmed_source_are_reported():
    assert checks(request(source_type='WEB-DL'))['navn kilde'] == 'missing'
    assert checks(request(source_verified=False))['navn kilde'] == 'missing'
    assert checks(request(source_type='WEBRip', videos=['Movie.WEB-DL.mkv']))['navn kilde'] == 'ok'


def test_wrong_catalogue_identity_blocks_review():
    assert checks(request(tmdb=13))['navn identitet'] == 'missing'


def test_other_group_is_not_relabelled_or_subject_to_own_release_name_rules():
    assert naming_checks(request(media_mode='source'), {}) == []


@pytest.mark.parametrize('filename', ['Show.S01E01E02.mkv', 'Show.S01E01-E02.mkv', 'Show.S01E01-S01E02.mkv'])
def test_double_episode_cannot_be_disguised_by_single_episode_override(filename):
    b = SimpleNamespace(videos=[filename], episode_overrides=[SimpleNamespace(file=filename, season=1, episode=1)])
    plan = plan_episodes(b, {'status': 'ok', 'title': 'Show', 'episodes': [{'season': 1, 'episode': 1, 'title': 'Pilot', 'airDate': '2025-01-01'}]})
    assert plan['status'] == 'needs_review' and not plan['complete']
    assert 'Dobbeltafsnit' in plan['unresolved'][0]['message']


def test_partial_season_is_not_a_complete_package():
    b = SimpleNamespace(videos=['Show.S01E01.mkv'], episode_overrides=[])
    catalogue = {'status': 'ok', 'title': 'Show', 'episodes': [
        {'season': 1, 'episode': i, 'title': f'Episode {i}', 'airDate': '2025-01-01'} for i in [1, 2]]}
    result = plan_episodes(b, catalogue)
    assert not result['complete'] and result['mode'] == 'episodes'
    assert result['seasonChecks'][0]['reason'] == 'Mangler 1 afsnit i denne sæson.'


def test_specials_keep_s00_numbering_and_are_not_hidden_in_series_range():
    b = SimpleNamespace(videos=['Show.S00E01.mkv', 'Show.S01E01.mkv'], episode_overrides=[])
    catalogue = {'status': 'ok', 'title': 'Show', 'seriesStatus': 'Ended', 'episodes': [
        {'season': i, 'episode': 1, 'title': f'Episode {i}', 'airDate': '2025-01-01'} for i in [0, 1]]}
    result = plan_episodes(b, catalogue)
    assert result['mode'] == 'episodes' and not result['complete']
    assert result['entries'][0]['special'] and result['entries'][0]['season'] == 0
