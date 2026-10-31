import json
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient
from test_app import sign_in
from mediaseed.app import create_app
from mediaseed.transfer_settings import TransferSettings

VALUES = dict(maxConnections=500, maxConnectionsPerTorrent=100, maxUploadSlots=20,
              maxUploadSlotsPerTorrent=4, maxActiveUploads=-1, maxActiveDownloads=8)

@pytest.mark.parametrize('apply', [True, False])
def test_native_settings_auth_readback_and_narrow_write(tmp_path, apply):
    preferences = {**TransferSettings(**VALUES).preferences(), 'listen_port': 54321, 'up_limit': 0}
    writes = []
    def handler(request):
        if request.url.path.endswith('/login'): return httpx.Response(200, text='Ok.')
        if request.url.path.endswith('/setPreferences'):
            fields = json.loads(parse_qs(request.content.decode())['json'][0]);writes.append(fields)
            if apply: preferences.update(fields)
        if request.url.path.endswith('/preferences'): return httpx.Response(200, json=preferences)
        return httpx.Response(200, json=[])
    with TestClient(create_app(tmp_path, 'http://testserver', qbit_password='synthetic', transport=httpx.MockTransport(handler))) as client:
        url = '/api/transfer-settings'
        assert client.get(url).status_code == 401
        assert client.put(url, json=VALUES).status_code == 401
        sign_in(client)
        assert client.get(url).json() == VALUES
        desired = {**VALUES, 'maxUploadSlotsPerTorrent': 16}
        result = client.put(url, json=desired)
        assert result.status_code == (200 if apply else 503), result.text
        if apply:
            assert result.json() == desired
            assert writes == [TransferSettings(**desired).preferences()]
        else: assert len(writes) == 2
        assert preferences['listen_port'] == 54321 and preferences['up_limit'] == 0
        count = len(writes)
        for value in [0, -2, 1001, True, 1.5, '16']:
            assert client.put(url, json={**VALUES, 'maxUploadSlotsPerTorrent': value}).status_code == 422
        assert client.put(url, json={**VALUES, 'up_limit': 0}).status_code == 422
        client.headers['X-MediaSeed-CSRF'] = 'wrong'
        assert client.put(url, json=VALUES).status_code == 403
        assert len(writes) == count


def test_vpn_capability_and_verified_response(tmp_path, monkeypatch):
    import mediaseed.app as module
    token = tmp_path/'token'; token.write_text('synthetic-token')
    monkeypatch.setenv('MEDIASEED_VPN_URL', 'http://mediavpn:18802')
    monkeypatch.setenv('MEDIASEED_VPN_TOKEN_FILE', str(token))
    original = httpx.AsyncClient
    capable = False
    stored = dict(VALUES)
    writes = []
    def handler(request):
        if request.url.path.endswith('/status'):
            return httpx.Response(200,json={'enabled':True,'profileId':'a'*24,'ready':True,'downloadsEnabled':True,'torrentCapabilities':['transfer-settings'] if capable else []})
        if request.url.path.endswith('/settings'): return httpx.Response(200,json={})
        assert request.url.path.endswith('/transfer')
        if request.method == 'PUT':
            writes.append(json.loads(request.content));stored.update(writes[-1])
        return httpx.Response(200,json=stored)
    monkeypatch.setattr(module.httpx, 'AsyncClient', lambda **kwargs: original(**{**kwargs, "transport": httpx.MockTransport(handler)}))
    with TestClient(create_app(tmp_path/'state', 'http://testserver')) as client:
        sign_in(client)
        assert client.put('/api/vpn',json={'provider':'mediavpn','enabled':True,'profileId':'a'*24}).status_code == 200
        assert client.get('/api/transfer-settings').status_code == 409
        assert client.put('/api/transfer-settings',json=VALUES).status_code == 409
        assert not writes
        capable = True
        assert client.get('/api/transfer-settings').json() == VALUES
        desired = {**VALUES,'maxUploadSlotsPerTorrent':16}
        assert client.put('/api/transfer-settings',json=desired).json() == desired
        assert writes == [desired]
