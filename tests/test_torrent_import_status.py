import httpx,pytest
from fastapi.testclient import TestClient
from mediaseed.app import create_app
from mediaseed.cleanup import source_hashes
from test_uploader import login

CONTENT=b'd4:infod6:lengthi1e4:name9:Syntheticee'


@pytest.mark.parametrize('scenario',['absent','present','other-folder','invalid-response'])
def test_import_status_is_authenticated_read_only_and_bound_to_destination(tmp_path,scenario):
 hash_=source_hashes(content=CONTENT)[0];calls=[]
 def transport(request):
  if request.url.path.endswith('/auth/login'):return httpx.Response(200,text='Ok.')
  calls.append(request)
  assert request.method=='GET' and request.url.path.endswith('/torrents/info')
  assert request.url.params['hashes']==hash_
  if scenario=='invalid-response':return httpx.Response(200,json={'bad':'shape'})
  return httpx.Response(200,json=[] if scenario=='absent' else [{'hash':hash_,'save_path':'/downloads/other' if scenario=='other-folder' else '/downloads'}])
 app=create_app(tmp_path/'data','http://testserver',qbit_password='synthetic',transport=httpx.MockTransport(transport))
 with TestClient(app) as c:
  path='/api/torrent-import-status';headers={'Content-Type':'application/x-bittorrent'}
  assert c.post(path,content=CONTENT,headers=headers).status_code==401
  csrf=login(c);assert c.post(path,content=CONTENT,headers=headers).status_code==403
  c.headers['X-MediaSeed-CSRF']=csrf
  r=c.post(path,content=CONTENT,headers=headers)
  if scenario=='invalid-response':assert r.status_code==503
  else:assert r.json()=={'present':scenario!='absent','sameDestination':scenario=='present','pending':False}
  count=len(calls)
  assert c.post(path,content=b'not a torrent',headers=headers).status_code==422
  assert c.post(path,content=b'x'*(4*1024**2+1),headers=headers).status_code==413
  assert len(calls)==count


def test_import_status_detects_pending_intent_without_sending_an_add(tmp_path):
 from fastapi import FastAPI
 from types import SimpleNamespace
 from mediaseed.storage import Storage
 from mediaseed.torrent_import import install_import_status
 root=tmp_path/'downloads';root.mkdir();hash_=source_hashes(content=CONTENT)[0]
 async def qbit(path,payload=None):
  assert path=='/api/v2/torrents/info' and payload=={'hashes':hash_}
  return httpx.Response(200,json=[])
 app=FastAPI();install_import_status(app,lambda:{'role':'administrator'},qbit,Storage(root),SimpleNamespace(records=lambda:[{'hashes':[hash_],'status':'preparing'}]))
 with TestClient(app) as c:assert c.post('/api/torrent-import-status',content=CONTENT).json()=={'present':False,'sameDestination':False,'pending':True}
