import json
import sqlite3

import httpx
from fastapi.testclient import TestClient

from mediaseed.app import create_app
from test_app import sign_in


def test_cleanup_progress_on_fresh_install_and_restart_preserves_identity(tmp_path):
    identifier = 'a' * 40
    row = {'hash': identifier, 'added_on': 100, 'state': 'stoppedUP',
           'tags': 'synthetic-cleanup', 'progress': 1, 'ratio': 1.3,
           'ratio_limit': 1, 'seeding_time': 72 * 3600}

    def handler(request):
        if request.url.path.endswith('/info'):
            return httpx.Response(200, json=[row])
        if request.url.path.endswith('/app/preferences'):
            return httpx.Response(200, json={})
        return httpx.Response(204)

    settings = dict(qbit_password='synthetic', transport=httpx.MockTransport(handler))
    rule = dict(id='synthetic', hashes=[identifier], tag='synthetic-cleanup',
                mode='ratio', ratio=3, hours=48, delete_files=False, status='active')
    with TestClient(create_app(tmp_path, 'http://testserver', **settings)) as client:
        sign_in(client)
        assert client.get('/api/torrents').json()[0]['cleanup']['mode'] == 'never'
        # Existing imported policies are stored in the same format before this change.
        with sqlite3.connect(tmp_path / 'mediaseed.sqlite') as db:
            db.execute('INSERT INTO torrent_cleanup VALUES (?,?)', (rule['id'], json.dumps(rule)))
        public = client.get('/api/torrents').json()[0]['cleanup']
        assert public['ratio'] == 3 and 'tag' not in public and 'hashes' not in public
        cookies = client.cookies
    with TestClient(create_app(tmp_path, 'http://testserver', **settings)) as client:
        client.cookies.update(cookies)
        assert client.get('/api/torrents').json()[0]['cleanup']['ratio'] == 3
        assert client.get('/api/dashboard').json()['torrents'][0]['cleanup']['ratio'] == 3
        row['tags'] = ''
        assert client.get('/api/torrents').json()[0]['cleanup']['mode'] == 'never'
        # Edited policies may additionally bind the client add timestamp.
        rule['added_on'] = 100
        with sqlite3.connect(tmp_path / 'mediaseed.sqlite') as db:
            db.execute('UPDATE torrent_cleanup SET payload=? WHERE id=?', (json.dumps(rule), rule['id']))
        row['tags'] = rule['tag']
        row['added_on'] = 200
        assert client.get('/api/torrents').json()[0]['cleanup']['mode'] == 'never'
