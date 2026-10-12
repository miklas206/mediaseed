import httpx
from fastapi.testclient import TestClient

from mediaseed.app import create_app
from test_app import sign_in


def test_removal_acknowledgement_is_separate_from_confirmation(tmp_path):
    present = [True]
    calls = []
    identifier = 'a' * 40
    def handler(request):
        calls.append(request)
        if request.url.path.endswith('/login'):
            return httpx.Response(200, text='Ok.')
        if request.url.path.endswith('/info'):
            assert request.url.params['hashes'] == identifier
            return httpx.Response(200, json=[{'hash': identifier}] if present[0] else [])
        return httpx.Response(200, text='')
    with TestClient(create_app(tmp_path, 'http://testserver', qbit_password='synthetic', transport=httpx.MockTransport(handler))) as client:
        endpoint = f'/api/torrents/{identifier}/presence'
        assert client.get(endpoint).status_code == 401
        sign_in(client)
        response = client.post('/api/torrents/remove', json={'torrent_hash': identifier, 'confirm_early': True})
        assert response.json() == {'accepted': True}
        assert client.get(endpoint).json() == {'present': True}
        present[0] = False
        assert client.get(endpoint).json() == {'present': False}
        assert client.get('/api/torrents/all/presence').status_code == 422
