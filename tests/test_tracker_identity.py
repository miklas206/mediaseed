import httpx
import pytest
from fastapi.testclient import TestClient
from mediaseed.app import create_app
from test_app import sign_in


@pytest.mark.parametrize('host,expected', [('t.nordicbytes.org','NordicBytes'),('t.nordicswarm.org','NordicBytes'),('t.polarswarm.org','NordicBytes'),('other.invalid',None),('nordicbytes.org.attacker.invalid',None)])
def test_announce_identity_does_not_require_tracker_api_metadata(tmp_path,host,expected):
    def handler(request):
        if request.url.path=='/api/v2/auth/login':return httpx.Response(200,text='Ok.')
        return httpx.Response(200,json=[{'hash':'a'*40,'name':'Own upload','tracker':f'https://{host}/announce/private-passkey','progress':1}])
    with TestClient(create_app(tmp_path,'http://testserver',qbit_password='synthetic',transport=httpx.MockTransport(handler))) as client:
        sign_in(client)
        response=client.get('/api/torrents');assert response.status_code==200
        row=response.json()[0]
        assert row.get('tracker_name')==expected
        assert row.get('tracker_benefits') is None
        assert 'private-passkey' not in response.text and 'announce/' not in response.text
        assert row['tracker_provider']==('nordicbytes' if expected else None)


@pytest.mark.parametrize('fields,expected', [
    ({'infohash_v1':'B'*40,'infohash_v2':'C'*64},{'infohash_v1':'b'*40,'infohash_v2':'c'*64}),
    ({'infohash_v1':'https://tracker.invalid/secret','infohash_v2':'invalid'},{}),
    ({'infohash_v1':'','infohash_v2':'D'*64},{'infohash_v2':'d'*64}),
])
def test_full_magnet_hashes_are_exposed_without_private_magnet_or_tracker_url(tmp_path,fields,expected):
    def handler(request):
        if request.url.path=='/api/v2/auth/login':return httpx.Response(200,text='Ok.')
        return httpx.Response(200,json=[{'hash':'a'*40,'name':'Own upload','tracker':'https://t.nordicbytes.org/announce/secret-key','magnet_uri':'magnet:?xt=urn:btih:'+'a'*40+'&tr=https://t.nordicbytes.org/secret-key',**fields}])
    with TestClient(create_app(tmp_path,'http://testserver',qbit_password='synthetic',transport=httpx.MockTransport(handler))) as client:
        sign_in(client);response=client.get('/api/torrents');assert response.status_code==200
        row=response.json()[0]
        assert {k:row[k] for k in ('infohash_v1','infohash_v2') if k in row}==expected
        assert 'secret-key' not in response.text and 'magnet_uri' not in row
