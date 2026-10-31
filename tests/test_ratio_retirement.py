"""Retired discovery cannot restart from persisted automatic settings."""
import asyncio
import importlib
import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient
from mediaseed.app import create_app
from test_app import sign_in


@pytest.mark.parametrize('upgrading', [False, True])
def test_ratio_discovery_removed_from_routes_and_workers_without_losing_files(tmp_path, monkeypatch, upgrading):
    from mediaseed import features, ratio_scout
    app_module = importlib.import_module('mediaseed.app')
    db = tmp_path / 'mediaseed.sqlite'
    legacy = {'enabled': True, 'automatic': True, 'tracker': '1' * 24}
    if upgrading:
        with sqlite3.connect(db) as connection:
            connection.execute('CREATE TABLE ratio_settings (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)')
            connection.execute('INSERT INTO ratio_settings VALUES (1,?)', (json.dumps(legacy),))
            connection.execute('CREATE TABLE ratio_imports (hash TEXT PRIMARY KEY, status TEXT)')
            connection.execute('INSERT INTO ratio_imports VALUES (?,?)', ('a' * 40, 'started'))
    downloads = tmp_path / 'downloads'
    downloads.mkdir()
    media = downloads / 'existing-release.mkv'
    media.write_bytes(b'existing media must survive retirement')
    jobs, backgrounds, mutations = [], [], []
    original = app_module.install_features

    def capture(*args, **kwargs):
        background = original(*args, **kwargs)
        backgrounds.append(background)
        return background

    async def capture_jobs(rows, **kwargs):
        jobs.extend(name for name, _ in rows)

    def forbidden(*args, **kwargs):
        raise AssertionError('Retired ratio discovery was instantiated')

    def transport(request):
        if request.method != 'GET' and request.url.path != '/api/v2/auth/login':
            mutations.append(request.url.path)
        return httpx.Response(200, text='Ok.') if request.url.path == '/api/v2/auth/login' else httpx.Response(200, json=[])

    monkeypatch.setattr(app_module, 'install_features', capture)
    monkeypatch.setattr(features, 'run_background', capture_jobs)
    monkeypatch.setattr(ratio_scout, 'RatioScout', forbidden)
    app = create_app(tmp_path, 'http://testserver', qbit_password='synthetic', transport=httpx.MockTransport(transport))
    asyncio.run(backgrounds[0]())
    assert 'rss' in jobs and 'ratio-scout' not in jobs
    with TestClient(app) as client:
        sign_in(client)
        for method, path, kwargs in [
            ('GET', '/api/ratio-scout', {}),
            ('PUT', '/api/ratio-scout/settings', {'json': legacy}),
            ('POST', '/api/ratio-scout/' + 'a' * 32 + '/add', {}),
        ]:
            assert client.request(method, path, **kwargs).status_code == 404
        assert client.get('/api/insights').status_code == 200
        assert client.get('/api/feeds').status_code == 200
        assert client.get('/api/trackers').status_code == 200
    assert media.read_bytes() == b'existing media must survive retirement'
    assert mutations == []
    with sqlite3.connect(db) as connection:
        if upgrading:
            assert json.loads(connection.execute('SELECT payload FROM ratio_settings').fetchone()[0]) == legacy
            assert connection.execute('SELECT hash,status FROM ratio_imports').fetchall() == [('a' * 40, 'started')]
        else:
            assert not connection.execute("SELECT name FROM sqlite_master WHERE name LIKE 'ratio_%'").fetchall()
