import httpx
import pytest
from fastapi.testclient import TestClient
from mediaseed.app import create_app
from mediaseed.covers import Covers
from test_app import sign_in

HASH = 'a' * 40

@pytest.mark.parametrize('result,status', [('queued',202),('cooldown',429),('unavailable',409),('busy',503)])
def test_refresh_endpoint_auth_scope_and_no_client_mutations(tmp_path, monkeypatch, result, status):
    calls, refreshed = [], []
    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path.endswith('/auth/login'):
            return httpx.Response(204)
        return httpx.Response(200, json=[{'hash': HASH, 'name': 'Test', 'tracker': 'https://t.nordicbytes.org/private'}])
    async def refresh(self, torrent):
        refreshed.append(torrent)
        return result
    monkeypatch.setattr(Covers, 'refresh', refresh)
    app = create_app(tmp_path, 'http://testserver', qbit_password='synthetic', transport=httpx.MockTransport(handler))
    with TestClient(app) as client:
        path = f'/api/torrents/{HASH}/metadata/refresh'
        assert client.post(path).status_code == 401
        sign_in(client)
        response = client.post(path)
        assert response.status_code == status
        assert len(refreshed) == 1 and refreshed[0]['hash'] == HASH
        assert 'private' not in response.text
        assert client.post('/api/torrents/invalid/metadata/refresh').status_code == 422
        assert client.post(f'/api/torrents/{"b"*40}/metadata/refresh').status_code == 404
        route = next(r for r in app.routes if getattr(r, 'path', '') == '/api/torrents/{identifier}/metadata/refresh')
        dependency = route.dependant.dependencies[0].call
        app.dependency_overrides[dependency] = lambda: {'role': 'viewer'}
        assert client.post(path).status_code == 403
        app.dependency_overrides.clear()
        client.headers['X-MediaSeed-CSRF'] = 'invalid'
        assert client.post(path).status_code == 403
        assert not any(method != 'GET' and '/torrents/' in path for method,path in calls)
