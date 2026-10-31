import httpx
import pytest
from fastapi.testclient import TestClient
from mediaseed.app import create_app
from mediaseed.cleanup import source_hashes
from test_app import sign_in


def encode(value):
    if isinstance(value,dict):return b'd'+b''.join(encode(k)+encode(v) for k,v in sorted(value.items()))+b'e'
    if isinstance(value,list):return b'l'+b''.join(map(encode,value))+b'e'
    if isinstance(value,int):return b'i'+str(value).encode()+b'e'
    return str(len(value)).encode()+b':'+value


@pytest.mark.parametrize('site',['nordicbytes','sagmire'])
@pytest.mark.parametrize('mode,charge,status', [('global',0,'matched'),('partial',50,'matched'),('unknown',None,'matched'),('wronghash',None,'torrent_unmatched'),('failed',None,'api_unavailable'),('unknowntracker',None,'tracker_unknown')])
def test_preview_only_queries_announce_tracker_and_uses_exact_api_evidence(tmp_path,monkeypatch,mode,charge,status,site):
    from mediaseed import features
    original=httpx.AsyncClient;calls=[]
    host=b'unconfigured.org' if mode=='unknowntracker' else ('t.'+site+'.org').encode()
    torrent=encode({b'announce':b'https://'+host+b'/announce/private-passkey',b'info':{b'name':b'Synthetic.mkv',b'length':100}})
    identifier=source_hashes(content=torrent)[0]
    def remote(request):
        calls.append(request)
        assert request.url.host==site+'.org'
        assert request.headers['Authorization']==('Bearer synthetic-nb' if site=='nordicbytes' else 'Bearer synthetic-other')
        if mode=='failed':return httpx.Response(401)
        if request.url.path=='/api/user':
            return httpx.Response(200,json={'username':'uploader','stats':{'uploaded':1000,'downloaded':500},'events':{'global':[{'type':'global_freeleech','ends_at':'2099-01-01T00:00:00Z'}] if mode=='global' else []}})
        assert request.url.params.get('name')=='Synthetic'
        return httpx.Response(200,json={'data':[{'id':'12','attributes':{'name':'Synthetic','info_hash':'b'*40 if mode=='wronghash' else identifier,'size':100,'freeleech':None if mode=='unknown' else 50 if mode=='partial' else 0,'uploader':'uploader'}}]})
    def factory(**kwargs):kwargs.setdefault('transport',httpx.MockTransport(remote));return original(**kwargs)
    monkeypatch.setattr(features.httpx,'AsyncClient',factory)
    with TestClient(create_app(tmp_path,'http://testserver')) as client:
        sign_in(client)
        client.post('/api/trackers',json={'name':'NB','api_key':'synthetic-nb'})
        client.post('/api/trackers',json={'name':'Other','provider':'unit3d','base_url':'https://sagmire.org','api_key':'synthetic-other'})
        response=client.post('/api/torrent-preview',content=torrent,headers={'Content-Type':'application/x-bittorrent'})
        assert response.status_code==200
        data=response.json()
        assert data['lookupStatus']==status
        assert 'private-passkey' not in response.text and 'synthetic-nb' not in response.text
        if status=='matched':
            assert len(data['estimates'])==1 and data['estimates'][0]['name']==('NB' if site=='nordicbytes' else 'Other')
            estimate=data['estimates'][0]
            assert estimate['charged']==charge and estimate['chargeKnown']==(charge is not None)
            assert estimate['projected']==(1000/(500+charge) if charge is not None else None)
            assert data['benefits']['ownUpload'] is True
        else:assert data['estimates']==[]
        if mode=='unknowntracker':assert not calls
        if mode=='global':
            magnet='magnet:?xt=urn:btih:'+identifier+'&dn=Synthetic.mkv&tr=https%3A%2F%2Ft.'+site+'.org%2Fannounce%2Fprivate-passkey'
            data=client.post('/api/torrent-preview',json={'magnet':magnet}).json()
            assert data['size']==100 and data['estimates'][0]['charged']==0
