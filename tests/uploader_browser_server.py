"""Native MediaSeed upload UI/API against synthetic media, tools and tracker only."""
import os,json,time,tempfile,hashlib,base64,re
from urllib.parse import parse_qs
from pathlib import Path
import httpx,uvicorn
from fastapi import Request
from starlette.staticfiles import StaticFiles
from mediaseed.app import create_app
from mediaseed.conversion import fingerprint,atomic
from mediaseed.cleanup import source_hashes
from test_upload_comparison import TRACKS
from mediaseed.upload_comparison import measured_facts
from mediaseed.upload_engine.upload_helper import bencode,nordicbytes_download
import mediaseed.upload_engine.upload_helper as engine

with tempfile.TemporaryDirectory(prefix='mediaseed-native-upload-') as tmp:
 root=Path(tmp);downloads=root/'downloads';converted=downloads/'media/converted-movies';converted.mkdir(parents=True)
 os.environ['MEDIASEED_STORAGE_ROOTS']=json.dumps([{'id':'converted-movies','label':'Konverterede Film','path':'/downloads/media/converted-movies'}])
 os.environ['MEDIASEED_UPLOAD_SOURCE_ROOTS']=json.dumps([{'id':'converted-movies','label':'Konverterede Film','path':str(converted),'clientPath':'/downloads/media/converted-movies'}])
 state={'duplicate':True,'variant':'normal','writes':0,'qbitWrites':0,'torrent':None}
 def qb(r):
  path=r.url.path
  if path.endswith('/torrents/add'):
   state['qbitWrites']+=1
   save=re.search(rb'name="savepath"\r\n\r\n([^\r]+)',r.content)[1].decode()
   state['torrent']={'hash':source_hashes(content=torrent)[0],'save_path':save,'state':'stoppedDL','progress':0}
  if path.endswith('/torrents/recheck') and state['torrent']:state['torrent'].update(state='checkingUP',progress=0,recheck_progress=.5,checkedAt=time.monotonic())
  if path.endswith('/torrents/start') and state['torrent']:state['torrent']['state']='uploading'
  if path.endswith('/torrents/stop') and state['torrent']:state['torrent']['state']='stoppedUP' if state['torrent']['progress']==1 else 'stoppedDL'
  if path.endswith('/torrents/info'):
   if state['torrent'] and state['torrent']['state']=='checkingUP' and time.monotonic()-state['torrent']['checkedAt']>3:state['torrent'].update(state='stoppedUP',progress=1)
   return httpx.Response(200,json=[state['torrent']] if state['torrent'] else [])
  if path.endswith('/torrents/files'):return httpx.Response(200,json=[{'name':name,'progress':state['torrent']['progress']}])
  return httpx.Response(200,text='Ok.')

 app=create_app(root/'data','http://127.0.0.1:15412',downloads_dir=downloads,qbit_password='synthetic',transport=httpx.MockTransport(qb))
 ident='a'*32;upload='b'*24;folder=converted/'Konverteret'/'Film.2025 [aaaaaaaa]';folder.mkdir(parents=True);name='Film.2025.2160p.H265.20Mbps-MDTech.mkv';media=folder/name;media.write_bytes(b'x'*1000)
 atomic(folder/'.mediaseed-converted.json',{'id':ident,'identities':{name:fingerprint(media)}})
 job={'id':ident,'hash':'1'*40,'name':'Film.2025.2160p.BluRay.REMUX-Original','created':time.time(),'status':'complete','bitrate':20,'fileCount':1,'originalBytes':2000,'outputBytes':1000,'destination':str(folder),'error':None,'files':[{'name':'Original.mkv','output':name}],'source':str(downloads)}
 app.state.conversions.save(job)
 async def seed_vpn():return {'downloadsEnabled':True}
 app.state.upload_seeding.vpn_status=seed_vpn
 with app.state.uploader_bridge.database() as db:
  db.execute('INSERT INTO settings VALUES(?,?,?)',('trackers','test',json.dumps({'id':'test','name':'Synthetic NordicBytes','provider':'nordicbytes','api_key':'synthetic-api-key'})))
  db.execute('INSERT INTO insight_metadata VALUES(?,?,?)',('1'*40,time.time(),json.dumps({'tmdb_id':12})))
 tracks=TRACKS+[{"@type":"Text","Format":"UTF-8","Language":"ar","Forced":"No"},{"@type":"Text","Format":"UTF-8","Language":"es","Forced":"No"},{"@type":"Text","Format":"PGS","Language":"es","Forced":"No","Title":"Spanish SDH"}]
 engine.command=lambda args,**kwargs:json.dumps({'media':{'track':tracks}}) if '--Output=JSON' in args else 'Full synthetic HEVC MediaInfo'
 helper=app.state.native_uploader.helper;helper.capabilities=lambda:{'mediainfo':True,'ffmpeg':True,'mkvmerge':True}
 originalrequest=helper.metadata.request
 helper.metadata.request=lambda path,body=None:{'configured':True,'tvdbConfigured':False} if path=='/status' else originalrequest(path,body)
 helper.metadata.details=lambda body:{'status':'ok','kind':'movie','tmdb':12,'imdb':'tt1234567','title':'Synthetic film','overview':'Synthetic synopsis'}
 helper.metadata.release=lambda name:{'status':'unknown','message':'Synthetic lookup'}
 ready={'id':upload,'state':'done','title':'Film.2025.2160p.BluRay.HEVC-MDTech','profile':'nordicbytes','releaseName':'Synthetic film','hasTorrent':True,'readyForReview':True,'automaticChecksVersion':1,'automaticChecksAt':time.time(),'checks':[{'rule':'1.12','status':'ok','text':'Reviewed'}],'metadata':{'kind':'movie','tmdb':12,'imdb':'tt1234567','title':'Synthetic film','overview':'Synthetic overview','status':'ok'},'videos':[{'comparison':measured_facts(tracks,1000),'file':name,'sizeBytes':1000,'technical':tracks[1:],'report':'Full synthetic HEVC MediaInfo','languages':['da'],'addedSubtitles':[]}],'source':{'singleFile':True,'clientSavePath':'/downloads/media/converted-movies/Konverteret/Film.2025 [aaaaaaaa]'},'sourceService':{'status':'none'},'description':'Synthetic description with source credit','originalNfoFiles':['Synthetic original.nfo'],'notes':['Synthetic source attribution'],'screenshotCount':1,'mediaMode':'source','seedingFolder':'/synthetic'}
 if os.environ.get('MEDIASEED_TEST_CONVERSION_CHECK')=='1':
  ready.update(mediaMode='in_place',contentFolder=str(folder),description=ready['description']+' Original source: '+job['name'],readyForReview=False,checks=[{'rule':'1.4 MDTECH','status':'missing','text':'Synthetic missing processing proof'}]);ready['source']['singleFile']=False
 with helper.database() as db:db.execute('INSERT INTO upload_jobs VALUES(?,?,?,?,?)',(upload,'studio','done',json.dumps(ready),time.time()))
 torrent=nordicbytes_download(bencode({b'announce':b'https://t.nordicbytes.org/announce/synthetic',b'info':{b'name':name.encode(),b'length':1000,b'piece length':16384,b'pieces':hashlib.sha1(media.read_bytes()).digest(),b'private':1}}))
 artifacts=helper.data/'upload-helper'/upload;artifacts.mkdir(parents=True);(artifacts/'upload.torrent').write_bytes(torrent);(artifacts/'captain-quack.nfo').write_text('Synthetic credits');(artifacts/'description.txt').write_text(ready['description']);(artifacts/'original-001.nfo').write_text('Synthetic original NFO');(artifacts/'screenshot-001-1.png').write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='))
 def tracker(r):
  if r.method=='POST':state['writes']+=1;return httpx.Response(200,json={'success':True,'data':'https://nordicbytes.org/torrents/download/9.synthetic'})
  if r.url.path.endswith('/upload'):return httpx.Response(405,headers={'Allow':'POST'})
  if '/download/' in r.url.path:return httpx.Response(200,content=torrent)
  if r.url.path=='/api/torrents/9':return httpx.Response(200,json={'data':{'attributes':{'download_link':'https://nordicbytes.org/torrents/download/9.synthetic'}}})
  name='Film.2025.2160p.BluRay.'+('x265-GROUP' if state['duplicate'] else 'REMUX.HEVC-GROUP')
  remote_tracks=[t for t in tracks if not (state['variant']=='different' and t.get('Format')=='MLP FBA')]
  payload={'data':[{'id':'2','attributes':{'name':'Film.2025.2160p.BluRay.REMUX-GROUP','tmdb_id':12,'category':'Movies','category_id':1,'size':1000,'type':'BluRay','type_id':14,'resolution':'2160p','resolution_id':2}},{'id':'1','attributes':{'name':name,'tmdb_id':12,'category':'Movies','category_id':1,'size':1000,'media_info':{'media':{'track':remote_tracks}},'type':'WEB-DL','type_id':4,'resolution':'2160p','resolution_id':2}}],'links':{'next':None}};payload['data'].reverse();return httpx.Response(200,json=payload)
 app.state.uploader_bridge.tracker=httpx.AsyncClient(transport=httpx.MockTransport(tracker))
 for route in app.routes:
  if getattr(route,'path',None)=='/static':route.app=StaticFiles(directory=os.environ.get('MEDIASEED_UPLOADER_STATIC','/tmp/mediaseed-native-ui/mediaseed/static'))
 @app.post('/__fixture/mode')
 async def mode(request:Request):
  body=await request.json();state['duplicate']=body['duplicate'];state['variant']=body.get('variant','normal')
  if body.get('resetHistory'):
   with app.state.conversions.database() as db:db.execute('DELETE FROM media_conversion_history_hidden')
  return state
 @app.get('/__fixture/state')
 async def get_state():return state
 uvicorn.run(app,host='127.0.0.1',port=15412,log_level='warning')
