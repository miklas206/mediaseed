"""Native routes, actual preparation queue and synthetic media/tools only."""
import hashlib,json,time,sqlite3
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from mediaseed.app import create_app
from mediaseed.upload_engine.upload_helper import decode_torrent
from test_uploader import login


def test_native_queue_artifacts_and_source_preservation(tmp_path,monkeypatch):
 source=tmp_path/'source';source.mkdir();movie=source/'Film.2025.2160p.H265-MDTech.mkv';movie.write_bytes(b'synthetic-media-only')
 monkeypatch.setenv('MEDIASEED_UPLOAD_SOURCE_ROOTS',json.dumps([{'id':'library','label':'Library','path':str(source),'clientPath':'/downloads'}]))
 app=create_app(tmp_path/'data','http://testserver',downloads_dir=source);helper=app.state.native_uploader.helper
 monkeypatch.setattr(helper,'capabilities',lambda:{'mediainfo':True,'ffmpeg':True,'mkvmerge':True})
 tracks=[{'@type':'General','Format':'Matroska','Duration':'1'},{'@type':'Video','Format':'HEVC','Width':'3840','Height':'2160'}]
 monkeypatch.setattr('mediaseed.upload_engine.upload_helper.command',lambda args,**kw:json.dumps({'media':{'track':tracks}}) if '--Output=JSON' in args else 'Synthetic measured HEVC')
 monkeypatch.setattr(helper.metadata,'details',lambda body:{'status':'ok','kind':'movie','tmdb':12,'title':'Synthetic film','overview':'Synthetic synopsis'})
 monkeypatch.setattr(helper.metadata,'release',lambda name:{'status':'unknown'})
 original=(movie.read_bytes(),movie.stat().st_mtime_ns)
 with TestClient(app) as c:
  csrf=login(c)
  assert c.get('/api/upload/storage').json()['locations'][0]['id']=='library'
  assert c.get('/uploader/').status_code==404
  assert c.get('/').headers['X-Frame-Options']=='DENY'
  body={'title':movie.stem,'tmdb':12,'location':'library','videos':[movie.name],'source_credit':'Synthetic original / source group','media_mode':'source','torrent_mode':'local','profile':'generic','announce':'https://tracker.invalid/announce/synthetic','source_service':'none','source_type':'BluRay','screenshots':False}
  assert c.post('/api/upload/upload-helper',json=body).status_code==403
  c.headers['X-MediaSeed-CSRF']=csrf
  invalid=c.post('/api/upload/upload-helper',json={**body,'tmdb':'invalid','announce':'https://tracker.invalid/announce/private-test-passkey'})
  assert invalid.status_code==422 and 'private-test-passkey' not in invalid.text
  response=c.post('/api/upload/upload-helper',json=body);assert response.status_code==202,response.text
  identifier=response.json()['id']
  for _ in range(100):
   job=c.get('/api/upload/upload-helper/'+identifier).json()
   if job['state'] not in ('queued','working'):break
   time.sleep(.02)
  assert job['state']=='done',job
  import asyncio
  native_result=asyncio.run(app.state.native_uploader.request('/api/upload-helper/'+identifier))
  assert native_result.json()['state']=='done'
  assert c.get('/api/upload/upload-helper/'+identifier).status_code==200
  result=c.get('/api/upload/upload-helper/'+identifier+'/download');assert result.status_code==200
  info=decode_torrent(result.content)[b'info'];assert info[b'name']==movie.name.encode() and info[b'pieces']==hashlib.sha1(movie.read_bytes()).digest()
  report=c.get('/api/upload/upload-helper/'+identifier+'/asset/mediainfo.txt');assert report.status_code==200 and 'Synthetic measured HEVC' in report.text
  assert c.get('/api/upload/upload-helper/'+identifier+'/asset/captain-quack.nfo').status_code==200
  assert (movie.read_bytes(),movie.stat().st_mtime_ns)==original and not list(helper.storage.work.iterdir())
  assert c.delete('/api/upload/upload-helper/'+identifier).status_code==200 and movie.exists()
 assert helper.worker is not None and not helper.worker.is_alive()


def test_upgrade_keeps_history_and_private_settings_without_gateway(tmp_path):
 root=tmp_path/'data/uploader';root.mkdir(parents=True)
 old={'id':'a'*24,'state':'done','title':'Existing upload','mediaMode':'source'}
 with sqlite3.connect(root/'mediaupload.sqlite') as db:
  db.execute('CREATE TABLE upload_jobs(id TEXT PRIMARY KEY,owner TEXT NOT NULL,state TEXT NOT NULL,result TEXT NOT NULL,created REAL NOT NULL)')
  db.execute('INSERT INTO upload_jobs VALUES(?,?,?,?,?)',(old['id'],'studio','done',json.dumps(old),1))
 private=root/'preserved-private.json';private.write_text('{"synthetic":"preserved"}')
 for iteration in range(2):
  with TestClient(create_app(tmp_path/'data','http://testserver')) as c:
   if iteration==0:login(c)
   else:assert c.post('/api/auth/login',json={'username':'tester','password':'synthetic-password-only'}).status_code==200
   jobs=c.get('/api/upload/upload-helper').json()['jobs'];assert jobs[0]['id']==old['id']
   assert c.get('/api/uploader/status').json()['native']
 assert private.read_text()=='{"synthetic":"preserved"}'


def test_native_install_fresh_and_upgrade_preserves_other_configuration():
 import importlib.util
 spec=importlib.util.spec_from_file_location('native_install',Path(__file__).parents[1]/'deploy/install_upload.py')
 m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 for legacy in [False,True]:
  config={'services':{'app':{'image':'base','environment':{'PRESERVE':'synthetic-private'},'volumes':[]},'vpn':{'unchanged':True}}}
  if legacy:
   config['services']['app']['environment']['MEDIASEED_UPLOADER_CONFIG']='/run/secrets/mediaseed-uploader.json'
   config['services']['app']['volumes'].append({'type':'bind','source':'/private/legacy','target':'/run/secrets/mediaseed-uploader.json','read_only':True})
  paths=[Path('/state'),Path('/media'),Path('/downloads')]
  first=m.configuration(config,*paths)
  assert first==m.configuration(first,*paths)
  assert first['services']['vpn']==config['services']['vpn']
  assert first['services']['app']['environment']['PRESERVE']=='synthetic-private'
  assert 'MEDIASEED_UPLOADER_CONFIG' not in first['services']['app']['environment']
  mounts=first['services']['app']['volumes'];assert len(mounts)==9
  assert all(row['read_only'] for row in mounts if row['target'].startswith(('/media/','/upload-sources/')))
