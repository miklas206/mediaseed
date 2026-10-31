"""Synthetic tracker and upload service only; no publication or torrent writes on real services."""
import asyncio,json,sqlite3,time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock
import httpx,pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from mediaseed.app import create_app
from mediaseed.uploader_bridge import UploaderBridge
from test_upload_comparison import TRACKS,LOCAL

KEY='secret-test-key';TOKEN='synthetic-gateway-'+'x'*64
ACCOUNT={'id':'test','provider':'nordicbytes','name':'Test','api_key':KEY}

@pytest.fixture
def bridge(tmp_path):
 @contextmanager
 def database():
  with sqlite3.connect(tmp_path/'db.sqlite') as db:yield db
 with database() as db:
  db.execute('CREATE TABLE settings(kind TEXT,id TEXT,payload TEXT)')
  db.execute('INSERT INTO settings VALUES(?,?,?)',('trackers','test',json.dumps(ACCOUNT)))
 result=UploaderBridge(database,Mock(),Mock(),configuration={'url':'http://uploader.internal:18820','token':TOKEN,'manual_upload':False})
 yield result
 asyncio.run(result.close())

def payload(name='Film.2025.2160p.BluRay.x265-GROUP',tmdb=12,typ='BluRay',resolution='2160p',identifier=1):
 return {'id':str(identifier),'attributes':{'tmdb_id':tmdb,'name':name,'type':typ,'type_id':14,'resolution':resolution,'resolution_id':2,'category':'Movies','category_id':1,'size':LOCAL['size'],'media_info':{'media':{'track':TRACKS}}}}

def client(bridge,items,extra=None):
 requests=[]
 def handler(r):
  requests.append(r)
  assert r.headers['Authorization']=='Bearer '+KEY
  assert KEY not in str(r.url)
  if r.url.path.endswith('/upload'):return httpx.Response(405,headers={'Allow':'POST'})
  data={'data':items,'links':{'next':None}}
  if extra:data.update(extra)
  return httpx.Response(200,json=data)
 bridge.tracker=httpx.AsyncClient(transport=httpx.MockTransport(handler))
 return requests

def test_duplicate_encode_blocks_but_remux_different_codec_or_resolution_do_not(bridge):
 requests=client(bridge,[payload(),payload(name='Film.2025.2160p.BluRay.REMUX.HEVC-GROUP',identifier=2),payload(name='Film.2025.1080p.BluRay.x265-GROUP',resolution='1080p',identifier=3)])
 value=asyncio.run(bridge.check(ACCOUNT,12,'2160p',LOCAL))
 assert value['status']=='duplicate' and len(value['matches'])==1
 assert value['apiUploadRoute'] and all(r.method=='GET' for r in requests)
 assert requests[0].url.params['tmdbId']=='12'
 client(bridge,[payload(name='Film.2025.2160p.BluRay.REMUX.HEVC-GROUP'),payload(name='Film.2025.2160p.BluRay.x264-GROUP',identifier=4)])
 assert asyncio.run(bridge.check(ACCOUNT,12,'2160p',LOCAL))['status']=='clear'

@pytest.mark.parametrize('change',[{'tmdb':99},{'name':'Film.2025.2160p.UnknownCodec-GROUP'}])
def test_unknown_codec_and_ignored_tmdb_filter_never_claim_clear(bridge,change):
 client(bridge,[payload(**change)])
 if 'tmdb' in change:
  with pytest.raises(HTTPException,match='TMDB'):asyncio.run(bridge.check(ACCOUNT,12,'2160p',LOCAL))
 else:assert asyncio.run(bridge.check(ACCOUNT,12,'2160p',LOCAL))['status']=='needs_review'

def test_untrusted_or_looping_pagination_is_not_complete(bridge):
 client(bridge,[],{'links':{'next':'https://attacker.invalid/api/torrents/filter?page=2'}})
 value=asyncio.run(bridge.check(ACCOUNT,12,'2160p',LOCAL))
 assert value['status']=='needs_review' and not value['complete']


def synthetic_upstream(bridge,handler):
 async def upstream(path,method='GET',content=None,**kwargs):
  return handler(httpx.Request(method,'http://synthetic.invalid'+path,content=content))
 bridge.upstream=upstream


def login(client):
 assert client.post('/api/auth/setup',json={'username':'tester','password':'synthetic-password-only'}).status_code==200
 response=client.post('/api/auth/login',json={'username':'tester','password':'synthetic-password-only'})
 return response.json()['csrf']

def test_fresh_install_missing_connection_auth_and_csrf(tmp_path):
 with TestClient(create_app(tmp_path,'http://testserver')) as c:
  assert c.get('/api/uploader/status').status_code==401
  assert c.get('/api/upload/storage').status_code==401
  assert c.get('/uploader/').status_code==404
  csrf=login(c)
  status=c.get('/api/uploader/status').json();assert status['configured'] is True and status['reviewFirst'] and status['native']
  assert c.post('/api/upload/upload-helper',json={}).status_code==403
  c.headers['X-MediaSeed-CSRF']=csrf
  assert c.post('/api/upload/upload-helper',json={}).status_code==422
  assert c.get('/').headers['X-Frame-Options']=='DENY'



def test_media_identity_must_be_measured_and_movie_specific():
 job={'metadata':{'kind':'movie','tmdb':12,'imdb':'tt1234567'},'videos':[{'technical':[{'@type':'Video','Format':'HEVC','Width':'3840','Height':'1600'}]}]}
 assert UploaderBridge.identity(job)==(12,'2160p')
 job['videos'][0]['technical'][0]['Format']='AVC'
 with pytest.raises(HTTPException):UploaderBridge.identity(job)
 job['metadata']['kind']='tv'
 with pytest.raises(HTTPException):UploaderBridge.identity(job)


def bencode(value):
 if isinstance(value,int):return b'i'+str(value).encode()+b'e'
 if isinstance(value,bytes):return str(len(value)).encode()+b':'+value
 if isinstance(value,dict):return b'd'+b''.join(bencode(k)+bencode(v) for k,v in sorted(value.items()))+b'e'
 raise TypeError()


@pytest.mark.parametrize('scenario',['success','duplicate','timeout','changed','seed-fails','bad-receipt','different','different-reviewed','rejected','rejected-fields','permission','rejected-data','legacy-rejected','missing-imdb'])
def test_explicit_publication_is_fresh_idempotent_and_never_retries_uncertain_writes(tmp_path,scenario):
 identifier='a'*24;media=tmp_path/'downloads';media.mkdir();(media/'Film.mkv').write_bytes(b'abc')
 torrent=bencode({b'announce':b'https://t.nordicbytes.org/announce/synthetic',b'info':{b'name':b'Film.mkv',b'length':3,b'piece length':16384,b'pieces':b'x'*20,b'private':1}})
 job={'id':identifier,'state':'done','title':'Film.2025.2160p.BluRay.HEVC-MDTech','metadata':{'kind':'movie','tmdb':12,'imdb':'tt1234567'},'videos':[{'comparison':LOCAL,'file':'Film.mkv','sizeBytes':3,'technical':[{'@type':'Video','Format':'HEVC','Width':'3840'}]}], 'readyForReview':True,'hasTorrent':True,'profile':'nordicbytes','description':'Measured encode. Original source credited.','source':{'singleFile':True,'clientSavePath':'/downloads'}}
 writes=[];qbit=[];retry_blocked=False
 def qb(r):
  if r.url.path=='/api/v2/torrents/add':
   qbit.append(r)
   if scenario=='seed-fails':return httpx.Response(503)
  return httpx.Response(200,text='Ok.')
 app=create_app(tmp_path/'data','http://testserver',downloads_dir=media,qbit_password='synthetic',transport=httpx.MockTransport(qb))
 if scenario in ('legacy-rejected','missing-imdb'):
  job['metadata'].pop('imdb',None)
  app.state.native_uploader.helper.metadata.details=lambda body:({'kind':'movie','tmdb':12,'imdb':'tt1234567'} if scenario=='legacy-rejected' else {'status':'not_configured'})
 bridge=app.state.uploader_bridge;bridge.configuration={'url':'http://uploader.internal:18820','token':TOKEN,'manual_upload':False}
 with bridge.database() as db:db.execute('INSERT INTO settings VALUES(?,?,?)',('trackers','test',json.dumps(ACCOUNT)))
 def up(r):
  if r.url.path.endswith('/download'):return httpx.Response(200,content=torrent)
  if r.url.path.endswith('/asset/captain-quack.nfo'):return httpx.Response(200,content=b'credit')
  if r.url.path.endswith('/asset/mediainfo.txt'):return httpx.Response(200,text='Measured HEVC report')
  return httpx.Response(200,json=job)
 synthetic_upstream(bridge,up)
 def tracker(r):
  if r.method=='POST':
   writes.append(r)
   assert b'Measured HEVC report' in r.content and b'api_key' not in r.content
   assert b'name="imdb"\r\n\r\n1234567' in r.content and b'name="mal"\r\n\r\n0' in r.content
   if scenario=='rejected-data':return httpx.Response(422,json={'success':False,'data':{'imdb':['The imdb field is required.'],'trace':'private trace'}})
   if scenario=='rejected-fields':return httpx.Response(422,json={'message':'The given data was invalid.','errors':{'imdb':['The imdb field is required.'],'api_key':['secret-test-key']}})
   if scenario=='permission':return httpx.Response(403,json={'message':'You do not have permission to upload.'})
   if scenario=='rejected':return httpx.Response(422,text='<html>Invalid tracker request</html>')
   if scenario=='timeout':raise httpx.ReadTimeout('synthetic',request=r)
   if scenario=='bad-receipt':return httpx.Response(200,json={'success':True,'data':'https://attacker.invalid/torrents/download/9.secret'})
   return httpx.Response(200,json={'success':True,'data':'https://nordicbytes.org/torrents/download/9.synthetic'})
  if r.url.path.endswith('/filter') and retry_blocked:return httpx.Response(200,json={'data':[payload()],'links':{'next':None}})
  if r.url.path.endswith('/filter') and scenario.startswith('different'):
   row=payload();row['attributes']['size']=LOCAL['size']*.6
   return httpx.Response(200,json={'data':[row],'links':{'next':None}})
  if r.url.path.endswith('/filter'):
   return httpx.Response(200,json={'data':[payload() if scenario=='duplicate' else payload(name='Film.2025.2160p.BluRay.REMUX.HEVC-GROUP')],'links':{'next':None}})
  if r.url.path.endswith('/upload'):return httpx.Response(405,headers={'Allow':'POST'})
  if '/download/' in r.url.path:return httpx.Response(200,content=torrent)
  return httpx.Response(404)
 bridge.tracker=httpx.AsyncClient(transport=httpx.MockTransport(tracker))
 if scenario=='legacy-rejected':
  with bridge.database() as db:db.execute('INSERT INTO uploader_publications VALUES(?,?)',(identifier,json.dumps({'state':'rejected','message':'Trackeren afviste uploaden.','at':1,'tracker':'test'})))
 with TestClient(app) as c:
  csrf=login(c);body={'tracker':'test','category_id':1,'type_id':14,'resolution_id':2,'confirmed':True,'comparison_reviewed':scenario=='different-reviewed'}
  assert c.post('/api/uploader/jobs/'+identifier+'/publish',json=body).status_code==403
  c.headers['X-MediaSeed-CSRF']=csrf
  assert c.post('/api/uploader/jobs/'+identifier+'/publish',json=body|{'confirmed':False}).status_code==422
  if scenario=='changed':(media/'Film.mkv').write_bytes(b'changed')
  response=c.post('/api/uploader/jobs/'+identifier+'/publish',json=body)
  second=c.post('/api/uploader/jobs/'+identifier+'/publish',json=body)
  if scenario in ('duplicate','changed','different','missing-imdb'):
   assert response.status_code==409 and not writes and not qbit
   if scenario=='missing-imdb':assert c.get('/api/uploader/jobs/'+identifier+'/publication').json()['state']=='not_published'
  elif scenario=='legacy-rejected':
   assert response.status_code==409 and second.status_code==409 and not writes
   old=c.get('/api/uploader/jobs/'+identifier+'/publication').json()
   retry=c.post('/api/uploader/jobs/'+identifier+'/publish',json=body|{'retry_of':old['retryToken']})
   assert retry.status_code==200 and len(writes)==1
   assert retry.json()['history'][0]['state']=='rejected' and retry.json()['history'][0]['at']==1
   assert c.post('/api/uploader/jobs/'+identifier+'/publish',json=body|{'retry_of':old['retryToken']}).status_code==200 and len(writes)==1
  elif scenario in ('rejected','rejected-fields','permission','rejected-data'):
   assert response.status_code==409 and second.status_code==409 and len(writes)==1 and not qbit
   saved=c.get('/api/uploader/jobs/'+identifier+'/publication').json()
   assert saved['state']=='rejected' and saved['httpStatus']==(403 if scenario=='permission' else 422)
   assert saved['diagnostics']['kind']==('permission' if scenario=='permission' else 'validation')
   assert saved['diagnostics']['responseFormat']==('html' if scenario=='rejected' else 'json')
   if scenario in ('rejected-fields','rejected-data'):
    assert saved['diagnostics']['errors']=={'imdb':['The imdb field is required.']}
    assert 'imdb: The imdb field is required.' in response.json()['detail']
   assert 'secret-test-key' not in json.dumps(saved)
   retry_body=body|{'retry_of':saved['retryToken']}
   assert c.post('/api/uploader/jobs/'+identifier+'/publish',json=retry_body|{'confirmed':False}).status_code==422 and len(writes)==1
   retry_blocked=True
   assert c.post('/api/uploader/jobs/'+identifier+'/publish',json=retry_body).status_code==409 and len(writes)==1
   retry_blocked=False
   assert c.post('/api/uploader/jobs/'+identifier+'/publish',json=retry_body).status_code==409 and len(writes)==2
   after=c.get('/api/uploader/jobs/'+identifier+'/publication').json()
   assert len(after['history'])==1 and after['history'][0]['diagnostics']==saved['diagnostics']
   assert after['retryToken']!=saved['retryToken']
   assert c.post('/api/uploader/jobs/'+identifier+'/publish',json=retry_body).status_code==409 and len(writes)==2

   assert '<html>' not in response.text
  elif scenario in ('timeout','bad-receipt'):
   assert response.status_code in (502,503) and second.status_code==409 and len(writes)==1 and not qbit
   assert c.get('/api/uploader/jobs/'+identifier+'/publication').json()['state']=='uncertain'
   assert c.post('/api/uploader/jobs/'+identifier+'/publish',json=body|{'retry_of':'a'*64}).status_code==409 and len(writes)==1
  else:
   assert response.status_code==200,response.text
   assert response.json()==second.json() and len(writes)==1
   assert response.json()['state']=='published'
   assert response.json()['seeding']==('manual' if scenario=='seed-fails' else 'paused')
   assert len(qbit)==1 and b'name="stopped"\r\n\r\ntrue' in qbit[0].content
  assert 'secret-test-key' not in response.text and TOKEN not in response.text
  assert (media/'Film.mkv').read_bytes()==(b'changed' if scenario=='changed' else b'abc')


def test_viewer_and_non_admin_cannot_access_shared_uploader(tmp_path):
 app=create_app(tmp_path,'http://testserver');route=next(r for r in app.routes if getattr(r,'path',None)=='/api/uploader/status')
 admin=route.dependant.dependencies[0].call
 for role in ['viewer','user']:
  # Override the authenticated user, retaining the bridge's administrator check.
  user=route.dependant.dependencies[0].dependencies[0].call
  app.dependency_overrides[user]=lambda role=role:{'role':role,'csrf':'synthetic'}
  with TestClient(app) as c:
   assert c.get('/api/uploader/status').status_code==403
   assert c.get('/api/upload/storage').status_code==403



def test_job_maps_both_seeding_paths_without_altering_reported_media(bridge):
 bridge.configuration['client_mappings']={'/downloads/media/Konverterede Film':'/downloads/media/converted-movies'}
 source={'clientSavePath':'/downloads/media/Konverterede Film/Film','clientContentPath':'/downloads/media/Konverterede Film/Film/Release','singleFile':False}
 job={'state':'done','source':source,'videos':[{'file':'/library/Film.mkv'}]}
 synthetic_upstream(bridge,lambda r:httpx.Response(200,json=job))
 result=asyncio.run(bridge.job('a'*24))
 assert result['source']['clientSavePath']=='/downloads/media/converted-movies/Film'
 assert result['source']['clientContentPath']=='/downloads/media/converted-movies/Film/Release'
 assert result['videos']==job['videos'] and job['source']==source

