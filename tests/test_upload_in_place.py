import asyncio,hashlib,json,time
from pathlib import Path
import httpx,pytest
from fastapi.testclient import TestClient
from mediaseed.app import create_app
from mediaseed.conversion import atomic,fingerprint
from mediaseed.upload_engine.upload_helper import decode_torrent
from test_uploader import login
from test_upload_comparison import TRACKS

@pytest.mark.parametrize('scenario',['source','in_place','failure','seeded','collision','recovery','permission','link_recovery','credited','folder_collision'])
def test_existing_converted_video_is_not_copied_or_overwritten(tmp_path,monkeypatch,scenario):
 downloads=tmp_path/'downloads';base=downloads/'media/converted-movies';folder=base/'Film [aaaaaaaa]';folder.mkdir(parents=True)
 old=folder/('Old.2025.HEVC-Existing.mkv' if scenario=='credited' else 'Old.2025.HEVC.mkv');old.write_bytes(b'synthetic converted movie')
 original=(old.read_bytes(),old.stat().st_mtime_ns,old.stat().st_ino)
 monkeypatch.setenv('MEDIASEED_STORAGE_ROOTS',json.dumps([{'id':'converted-movies','label':'Converted','path':'/downloads/media/converted-movies'}]))
 monkeypatch.setenv('MEDIASEED_UPLOAD_SOURCE_ROOTS',json.dumps([{'id':'converted-movies','label':'Converted','path':str(base),'clientPath':'/downloads/media/converted-movies'}]))
 def transport(request):
  if request.url.path.endswith('/auth/login'):return httpx.Response(200,text='Ok.')
  return httpx.Response(200,json=[{'hash':'1'*40,'content_path':'/downloads/media/converted-movies/Film [aaaaaaaa]'}] if scenario=='seeded' else [])
 app=create_app(tmp_path/'data','http://testserver',downloads_dir=downloads,qbit_password='synthetic',transport=httpx.MockTransport(transport));manager=app.state.conversions
 job={'id':'a'*32,'hash':'b'*40,'created':time.time(),'status':'complete','name':'Film.2025.REMUX','destination':str(folder),'files':[{'name':'Source.mkv','output':old.name}]}
 manager.save(job);receipt={'id':job['id'],'identities':{old.name:fingerprint(old)}};atomic(folder/'.mediaseed-converted.json',receipt)
 helper=app.state.native_uploader.helper
 monkeypatch.setattr(helper,'capabilities',lambda:{'mediainfo':True,'mkvmerge':True})
 monkeypatch.setattr('mediaseed.upload_engine.upload_helper.command',lambda args,**kw:json.dumps({'media':{'track':TRACKS}}) if '--Output=JSON' in args else 'Synthetic report')
 monkeypatch.setattr(helper.metadata,'details',lambda body:{'status':'ok','kind':'movie','tmdb':12,'title':'Film','year':2025,'overview':'Synthetic synopsis'})
 monkeypatch.setattr(helper.metadata,'release',lambda name:{'status':'unknown'})
 monkeypatch.setattr(helper,'copy_media',lambda *a:pytest.fail('No video copy is allowed'))
 if scenario=='failure':monkeypatch.setattr('mediaseed.upload_engine.upload_helper.make_torrent',lambda *a,**k:(_ for _ in ()).throw(ValueError('Synthetic hashing failure')))
 if scenario=='permission':monkeypatch.setattr(helper.in_place,'begin',lambda *a,**k:(_ for _ in ()).throw(PermissionError(13,'synthetic-private-path-and-secret')))
 if scenario=='collision':old.with_suffix('.nfo').write_bytes(b'Foreign NFO')
 with TestClient(app) as c:
  c.headers['X-MediaSeed-CSRF']=login(c)
  if scenario in ('recovery','link_recovery'):
   edit=helper.in_place.begin('c'*24,helper.storage,old,'New.mkv',True)
   if scenario=='link_recovery':
    import os
    os.link(old,folder/'New.mkv')
   else:edit.apply(b'Synthetic NFO')
   helper.in_place.recover(helper.database)
   assert old.exists() and not (folder/'New.mkv').exists() and not (folder/'New.nfo').exists()
   assert json.loads((folder/'.mediaseed-converted.json').read_text())==receipt
   return
  mode='in_place' if scenario in ('in_place','failure','seeded','credited','folder_collision') else 'source'
  body={'title':'Film.2025.2160p.HEVC','tmdb':12,'location':'converted-movies','folder':folder.name,'videos':[old.name],'source_credit':'Synthetic original','media_mode':mode,'include_nfo':True,'torrent_mode':'local','profile':'generic','announce':'https://tracker.invalid/announce/synthetic','source_service':'none','source_type':'BluRay','screenshots':False}
  if scenario=='credited':
   body.update(profile='nordicbytes',announce='https://t.nordicbytes.org/announce/synthetic',source_credit='Converted with NVIDIA HEVC by MDTech. Original source: '+job['name'],source_verified=True,duplicates_checked=True)
   monkeypatch.setattr(helper,'automatic_release',lambda *a,**k:([],'p2p',{'rule':'1.9','status':'ok','text':'Synthetic verified release'},[]))
  preview=c.post('/api/upload/description/preview',json=body)
  assert preview.status_code==200,preview.text
  assert '20.00 Mbit/s' in preview.json()['description'] and '24.00 Mbit/s' in preview.json()['nfo']
  if scenario=='folder_collision':
   # Exercise the guarded edit directly with a known existing destination.
   foreign=base/'New';foreign.mkdir();(foreign/'keep').write_bytes(b'foreign')
   with pytest.raises(ValueError,match='findes allerede'):helper.in_place.begin('d'*24,helper.storage,old,'New.mkv',True)
   assert old.exists() and (foreign/'keep').read_bytes()==b'foreign'
   return
  response=c.post('/api/upload/upload-helper',json=body)
  if scenario=='seeded':assert response.status_code==409 and old.exists();return
  assert response.status_code==202,response.text
  for _ in range(150):
   result=c.get('/api/upload/upload-helper/'+response.json()['id']).json()
   if result['state'] not in ('queued','working'):break
   time.sleep(.02)
  if scenario in ('collision','failure','permission'):
   assert result['state']=='error',result
   if scenario=='permission':
    assert 'skriveadgang' in result['message'] and result['errorReference']
    assert 'synthetic-private' not in str(result)
   assert old.exists() and (old.read_bytes(),old.stat().st_mtime_ns,old.stat().st_ino)==original
   assert json.loads((folder/'.mediaseed-converted.json').read_text())==receipt
   assert not list(folder.glob('*.MDTECH.mkv'))
   if scenario=='collision':assert old.with_suffix('.nfo').read_bytes()==b'Foreign NFO'
   else:assert not list(folder.glob('*.nfo'))
   return
  assert result['state']=='done',result
  if scenario in ('in_place','credited'):
   folder=Path(next(j for j in manager.records() if j['id']==job['id'])['destination'])
   assert folder.name==next(folder.glob('*.mkv')).stem
  movies=list(folder.glob('*.mkv'));assert len(movies)==1
  final=movies[0];assert (final.read_bytes(),final.stat().st_mtime_ns,final.stat().st_ino)==original
  if scenario in ('in_place','credited'):assert final!=old and 'TrueHD.7.1.Atmos' in final.name and final.name.endswith('.MDTECH.mkv')
  else:assert final==old
  nfo=final.with_suffix('.nfo');assert nfo.is_file()
  torrent=c.get('/api/upload/upload-helper/'+response.json()['id']+'/download')
  info=decode_torrent(torrent.content)[b'info'];assert info[b'name']==folder.name.encode() and len(info[b'files'])==2
  assert {f[b'path'][0].decode() for f in info[b'files']}=={final.name,nfo.name}
  assert result['source']['clientSavePath']=='/downloads/media/converted-movies/.' or result['source']['clientSavePath']=='/downloads/media/converted-movies'
  current=next(j for j in manager.records() if j['id']==job['id']);assert current['files'][0]['output']==final.name
  if scenario=='credited':
   assert result['readyForReview'] and result['conversionProof']['id']==job['id']
   rule=next(c for c in result['checks'] if c['rule']=='1.4 MDTECH');assert rule['status']=='ok'
   original_payload=(final.read_bytes(),nfo.read_bytes(),torrent.content)
   from mediaseed.upload_nfo_repair import original_file_text
   from mediaseed.upload_engine.upload_helper import make_torrent
   artifacts=helper.data/'upload-helper'/result['id']
   legacy=original_file_text(nfo.read_text(),'Source.mkv',old.name).encode('utf-8')
   nfo.write_bytes(legacy);(artifacts/'captain-quack.nfo').write_bytes(legacy)
   (artifacts/'upload.torrent').write_bytes(make_torrent([(final,final.name),(nfo,nfo.name)],folder.name,body['announce'],profile='nordicbytes'))
   result['videos'][0]['originalFile']=old.name
   result['description']=result['description'].replace('[b]Original source files[/b]\nSource.mkv','[b]Original source files[/b]\n'+old.name)
   result.pop('conversionProof');result['automaticChecksVersion']=1;rule['status']='missing';result['readyForReview']=False
   with helper.database() as db:db.execute('UPDATE upload_jobs SET result=? WHERE id=?',(json.dumps(result),result['id']))
   refreshed=c.post('/api/upload/upload-helper/'+result['id']+'/refresh-checks')
   assert refreshed.status_code==200 and refreshed.json()['readyForReview'],refreshed.text
   updated=c.get('/api/upload/upload-helper/'+result['id']).json()
   assert updated['conversionProof']['id']==job['id'] and updated['automaticChecksVersion']==3
   assert (final.read_bytes(),nfo.read_bytes(),c.get('/api/upload/upload-helper/'+result['id']+'/download').content)==original_payload
   assert helper.in_place.credited_conversion(helper.storage,final,'An unrelated source') is None
   receipt_path=folder/'.mediaseed-converted.json';record=json.loads(receipt_path.read_text());record['identities'][final.name][0]+=1;atomic(receipt_path,record)
   assert helper.in_place.credited_conversion(helper.storage,final,job['name']) is None
   refreshed=c.post('/api/upload/upload-helper/'+result['id']+'/refresh-checks')
   assert refreshed.status_code==200 and not refreshed.json()['readyForReview']



def test_pool_without_rename_noreplace_uses_exclusive_hardlink(tmp_path,monkeypatch):
 import ctypes,errno
 from types import SimpleNamespace
 from mediaseed.upload_in_place import rename_exclusive
 def unsupported(*args):ctypes.set_errno(errno.EINVAL);return -1
 monkeypatch.setattr(ctypes,'CDLL',lambda *a,**k:SimpleNamespace(renameat2=unsupported))
 old=tmp_path/'Old.mkv';new=tmp_path/'New.mkv';old.write_bytes(b'synthetic video');identity=fingerprint(old)
 rename_exclusive(old,new)
 assert not old.exists() and fingerprint(new)==identity
 old.write_bytes(b'foreign file')
 with pytest.raises(ValueError,match='findes allerede'):rename_exclusive(new,old)
 assert old.read_bytes()==b'foreign file' and fingerprint(new)==identity
