import sqlite3

import pytest
from fastapi.testclient import TestClient

from mediaseed import feed_series, feed_uploads
from mediaseed.app import create_app
from test_app import sign_in


@pytest.mark.parametrize('title,name,kind,episode', [
    ('The.Show.S01E02.1080p.WEB-DL-GRP', 'The Show', 'series', 'S01E02'),
    ('the_show_1x03_720p', 'the show', 'series', '1X03'),
    ('The.Show.S02E01E02.2160p', 'The Show', 'series', 'S02E01E02'),
    ('The.Show.S01.COMPLETE.1080p', 'The Show', 'series', 'S01'),
    ('Dansk.Æble.S01E01.1080p', 'Dansk Æble', 'series', 'S01E01'),
    ('Daily.Show.2026.10.24.1080p', 'Daily Show', 'series', '2026 10 24'),
    ('Dark.Matter.2024.S01E01.1080p', 'Dark Matter 2024', 'series', 'S01E01'),
    ('Movie.2024.1080p', 'Movie.2024.1080p', 'torrent', None),
    ('S01E01.1080p', 'S01E01.1080p', 'torrent', None),
])
def test_episode_and_pack_names_are_grouped_conservatively(title, name, kind, episode):
    key, actual_name, actual_kind, actual_episode = feed_series.describe('a'*40, title)
    assert (actual_name, actual_kind, actual_episode) == (name, kind, episode)
    assert len(key) == 64


def test_series_identity_ignores_season_quality_and_separators_but_keeps_remake_year():
    key = lambda title: feed_series.describe('a'*40, title)[0]
    assert key('The.Show.S01E01.720p') == key('the_show_S02E10_2160p')
    assert key('The.Show.1x03') == key('THE SHOW S01E04')
    assert key('Dark.Matter.2015.S01E01') != key('Dark.Matter.2024.S01E01')
    assert feed_series.describe('a'*40, 'Movie')[0] != feed_series.describe('b'*40, 'Movie')[0]


def add(db, feed, index, title, credit):
    identifier = f'{index:040x}'
    feed_uploads.record(db, feed, identifier, title, index)
    if credit is not None:
        feed_uploads.observe(db, {'hash': identifier, 'category': 'MediaSeed-RSS-' + feed, 'added_on': index, 'name': title}, credit)
    return identifier


def test_groups_use_the_entire_ledger_before_pagination_and_exclude_unknown_from_average(tmp_path):
    with sqlite3.connect(tmp_path / 'test.sqlite') as db:
        feed_uploads.initialize(db)
        for index in range(1, 32):
            add(db, 'feed', index, f'Popular.Show.S01E{index:02}.1080p', 100)
        add(db, 'feed', 32, 'Popular.Show.S02E01.2160p', None)
        add(db, 'feed', 33, 'Strong.Show.S01E01.1080p', 2000)
        add(db, 'feed', 34, 'Zero.Show.S01E01', 0)
        add(db, 'feed', 35, 'Unknown.Show.S01E01', None)
        add(db, 'other', 36, 'Popular.Show.S01E01.1080p', 99999)
        result = feed_series.groups(db, 'feed', 1, 1, 'upload')
        assert result['total'] == 4
        popular = result['groups'][0]
        assert popular['name'] == 'Popular Show' and popular['downloads'] == 32
        assert popular['creditedUploadedBytes'] == 3100 and popular['averageUploadedBytes'] == 100
        assert popular['unknownDownloads'] == 1 and popular['knownDownloads'] == 31
        assert feed_series.groups(db, 'feed', 1, 25, 'average')['groups'][0]['name'] == 'Strong Show'
        assert feed_series.groups(db, 'feed', 1, 25, 'recent')['groups'][0]['name'] == 'Unknown Show'
        members = feed_series.members(db, 'feed', popular['key'], 1, 25)
        assert members['total'] == 32 and len(members['entries']) == 25
        assert members['series']['creditedUploadedBytes'] == 3100
        assert len(feed_series.members(db, 'feed', popular['key'], 2, 25)['entries']) == 7
        assert feed_series.members(db, 'other', popular['key'], 1, 25)['total'] == 1
        assert feed_series.members(db, 'missing', popular['key'], 1, 25)['total'] == 0
        filtered = feed_series.groups(db, 'feed', 1, 25, 'upload', 'STRONG.show')
        assert filtered['total'] == 1 and filtered['groups'][0]['name'] == 'Strong Show'
        all_groups = feed_series.groups(db, 'feed', 1, 25, 'upload')['groups']
        assert next(row for row in all_groups if row['name'] == 'Zero Show')['averageUploadedBytes'] == 0
        assert next(row for row in all_groups if row['name'] == 'Unknown Show')['creditedUploadedBytes'] is None


def test_upgrade_backfills_in_batches_without_modifying_credit_and_repeated_start_is_idempotent(tmp_path):
    path = tmp_path / 'old.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE rss_downloads (feed TEXT, hash TEXT, title TEXT, added REAL, credited REAL, last_value REAL, identity REAL, PRIMARY KEY(feed,hash))')
        for index in range(1201):
            db.execute('INSERT INTO rss_downloads VALUES (?,?,?,?,?,?,?)', ('feed', f'{index:040x}', f'Old.Show.S01E{index%99+1:02}', index, None if index == 0 else index, index, 42))
        before = db.execute('SELECT * FROM rss_downloads ORDER BY hash').fetchall()
        feed_uploads.initialize(db)
        feed_uploads.initialize(db)
        assert db.execute('SELECT COUNT(*) FROM rss_series').fetchone()[0] == 1201
        assert db.execute('SELECT * FROM rss_downloads ORDER BY hash').fetchall() == before
    with sqlite3.connect(path) as db:
        feed_uploads.initialize(db)
        row = feed_series.groups(db, 'feed', 1, 25, 'upload')['groups'][0]
        assert row['downloads'] == 1201 and row['unknownDownloads'] == 1
        assert row['creditedUploadedBytes'] == sum(range(1201))


def test_later_recovered_titles_rejoin_series_and_duplicate_hash_does_not_duplicate_credit(tmp_path):
    with sqlite3.connect(tmp_path / 'test.sqlite') as db:
        feed_uploads.initialize(db)
        identifier = add(db, 'feed', 1, '', 100)
        add(db, 'feed', 2, 'Recovered.Show.S01E02', 50)
        assert feed_series.groups(db, 'feed', 1, 25, 'upload')['total'] == 2
        feed_uploads.observe(db, {'hash': identifier, 'category': 'MediaSeed-RSS-feed', 'added_on': 1, 'name': 'Recovered.Show.S01E01'}, 100)
        assert feed_series.groups(db, 'feed', 1, 25, 'upload')['total'] == 1
        feed_uploads.record(db, 'feed', identifier, 'Recovered.Show.S01E01', 10)
        row = feed_series.groups(db, 'feed', 1, 25, 'upload')['groups'][0]
        assert row['downloads'] == 2 and row['creditedUploadedBytes'] == 150
        # Removing a runtime torrent does not remove the durable feed ledger.
        assert feed_uploads.summary(db, 'feed')['creditedUploadedBytes'] == 150


def test_unicode_search_uses_casefold_and_treats_sql_patterns_as_literal(tmp_path):
    with sqlite3.connect(tmp_path / 'test.sqlite') as db:
        feed_uploads.initialize(db)
        add(db, 'feed', 1, 'Dansk.Æble.S01E01', 100)
        assert feed_series.groups(db, 'feed', 1, 25, 'upload', 'dansk æBLE')['total'] == 1
        assert feed_series.groups(db, 'feed', 1, 25, 'upload', '%')['total'] == 0


def test_group_api_is_authenticated_scoped_and_backward_compatible(tmp_path):
    app = create_app(tmp_path, 'http://testserver')
    with TestClient(app) as client:
        assert client.get('/api/feeds/no/history?view=series').status_code == 401
        sign_in(client)
        identifier = client.post('/api/feeds', json={'name': 'Synthetic', 'url': 'https://nordicbytes.org/rss/private'}).json()['id']
        with sqlite3.connect(tmp_path / 'mediaseed.sqlite') as db:
            add(db, identifier, 1, 'Show.S01E01', 100)
            add(db, identifier, 2, 'Show.S01E02', 200)
            add(db, 'other', 3, 'Other.S01E01', 9999)
        old = client.get(f'/api/feeds/{identifier}/history').json()
        assert len(old['entries']) == 2 and old['total'] == 2
        grouped = client.get(f'/api/feeds/{identifier}/history?view=series').json()
        assert grouped['total'] == 1 and grouped['groups'][0]['creditedUploadedBytes'] == 300
        key = grouped['groups'][0]['key']
        assert len(client.get(f'/api/feeds/{identifier}/history?series={key}').json()['entries']) == 2
        assert client.get('/api/feeds/missing/history?view=series').status_code == 404
        assert client.get(f'/api/feeds/{identifier}/history?sort=bad').status_code == 422
        assert client.get(f'/api/feeds/{identifier}/history?series=../../private').status_code == 422
        assert client.get(f'/api/feeds/{identifier}/history?search='+('x'*121)).status_code == 422
        assert 'private' not in str(grouped) and 'Other' not in str(grouped)
