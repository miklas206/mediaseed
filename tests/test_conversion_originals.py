import asyncio,json
from pathlib import Path
import httpx,pytest
from fastapi import HTTPException
from mediaseed.conversion import fingerprint,atomic
from mediaseed.conversion_naming import output_name
from mediaseed.conversion_originals import OriginalRemoval,RemoveOriginal
from test_conversion import setup,choice,result


async def prepared(fixture):
 m,root,queue,row,calls,database=fixture
 await m.create(choice());job,out=result(m,queue);await m.tick();job=m.records()[0]
 video=Path(job['destination'])/output_name(job['files'][0]);nfo=video.with_suffix('.nfo');nfo.write_bytes(b'Synthetic NFO')
 seed={'status':'seeding','hash':'b'*40,'savePath':'/downloads','files':[]}
 for path in [video,nfo]:
  stat=path.stat();seed['files'].append({'path':str(path),'size':stat.st_size,'mtime':stat.st_mtime_ns,'inode':stat.st_ino,'device':stat.st_dev})
 with database() as db:
  db.execute('CREATE TABLE uploader_seeding(job TEXT PRIMARY KEY,document TEXT)');db.execute('INSERT INTO uploader_seeding VALUES(?,?)',('c'*24,json.dumps(seed)))
 row['content_path']='/downloads/Release'
 seeded={'hash':seed['hash'],'name':'Converted','save_path':'/downloads','content_path':'/downloads/'+video.parent.relative_to(root).as_posix(),'state':'stalledUP','progress':1}
 state={'rows':[row,seeded],'deleteCalls':[],'deleteFails':False,'filesProgress':1}
 async def qbit(path,method='GET',payload=None,files=None):
  if method=='POST':
   assert path.endswith('/delete') and payload=={'hashes':row['hash'],'deleteFiles':'true'}
   state['deleteCalls'].append(payload)
   if state['deleteFails']:raise HTTPException(503,'Synthetic lost response')
   return httpx.Response(200,text='Ok.')
  if path.endswith('/info'):return httpx.Response(200,json=[r for r in state['rows'] if not payload.get('hashes') or r['hash']==payload['hashes']])
  if payload.get('hash')==seed['hash']:return httpx.Response(200,json=[{'name':p.relative_to(root).as_posix(),'progress':state['filesProgress']} for p in [video,nfo]])
  return httpx.Response(200,json=[{'name':'Release/Film.mkv','size':1000,'progress':1},{'name':'Release/Film.da.srt','size':len(b'subtitle original'),'progress':1}])
 m.qbit=qbit
 return m,root,job,video,seed,state,OriginalRemoval(m)


def test_explicit_remove_deletes_only_original_then_reconciles_without_repeat(setup):
 async def run():
  m,root,job,video,seed,state,remove=await prepared(setup);identity=fingerprint(video)
  plan=await remove.preview(job['id']);assert plan['fileCount']==2 and plan['deleteBytes']>1000
  assert not state['deleteCalls']
  body=RemoveOriginal(confirmed=True,confirmation=plan['confirmation'])
  assert (await remove.remove(job['id'],body))['status']=='pending'
  assert len(state['deleteCalls'])==1 and fingerprint(video)==identity
  assert (await remove.remove(job['id'],body))['status']=='pending' and len(state['deleteCalls'])==1
  state['rows']=state['rows'][1:];await remove.refresh();assert m.records()[0]['originalRemoval']['status']=='pending'
  (root/'Release/Film.mkv').unlink();(root/'Release/Film.da.srt').unlink();await remove.refresh()
  assert m.public(m.records()[0])['originalsRetained'] is False
  assert (await remove.preview(job['id']))['status']=='removed' and fingerprint(video)==identity
 asyncio.run(run())


@pytest.mark.parametrize('problem',['not-confirmed','stale-plan','output-changed','source-changed','reimport','not-seeding','not-complete','shared','active-conversion','seed-files-changed'])
def test_removal_rejects_unsafe_or_stale_state_without_writes(setup,problem):
 async def run():
  m,root,job,video,seed,state,remove=await prepared(setup);plan=await remove.preview(job['id'])
  if problem=='output-changed':video.write_bytes(b'changed')
  elif problem=='source-changed':(root/'Release/Film.mkv').write_bytes(b'changed')
  elif problem=='reimport':state['rows'][0]['added_on']=999
  elif problem=='not-seeding':state['rows'][1]['state']='stoppedUP'
  elif problem=='not-complete':state['filesProgress']=.5
  elif problem=='shared':state['rows'].append({'hash':'d'*40,'content_path':'/downloads/Release'})
  elif problem=='active-conversion':m.save({**job,'id':'f'*32,'created':job['created']+1,'status':'encoding'})
  elif problem=='seed-files-changed':video.with_suffix('.nfo').write_bytes(b'changed')
  confirmation='0'*64 if problem=='stale-plan' else plan['confirmation']
  with pytest.raises(HTTPException):await remove.remove(job['id'],RemoveOriginal(confirmed=problem!='not-confirmed',confirmation=confirmation))
  assert not state['deleteCalls'] and (root/'Release/Film.mkv').exists()
 asyncio.run(run())


def test_uncertain_delete_is_persisted_and_never_replayed_automatically(setup):
 async def run():
  m,root,job,video,seed,state,remove=await prepared(setup);plan=await remove.preview(job['id']);state['deleteFails']=True
  body=RemoveOriginal(confirmed=True,confirmation=plan['confirmation'])
  with pytest.raises(HTTPException):await remove.remove(job['id'],body)
  assert m.records()[0]['originalRemoval']['status']=='pending'
  assert (await remove.remove(job['id'],body))['status']=='pending' and len(state['deleteCalls'])==1
 asyncio.run(run())


def test_removal_routes_require_auth_csrf_and_write_role_and_hide_snapshot(tmp_path,monkeypatch):
 from fastapi.testclient import TestClient
 from mediaseed.app import create_app
 from test_uploader import login
 app=create_app(tmp_path,'http://testserver');path='/api/conversions/'+'a'*32+'/original';body={'confirmed':True,'confirmation':'b'*64};writes=[]
 async def preview(_):return {'status':'ready','snapshot':{'private':'internal'},'storage':{'private':'internal'},'fileCount':1}
 async def remove(identifier,choice):writes.append(identifier);return {'status':'pending'}
 monkeypatch.setattr(app.state.conversion_originals,'preview',preview);monkeypatch.setattr(app.state.conversion_originals,'remove',remove)
 with TestClient(app) as c:
  assert c.get(path).status_code==401 and c.post(path+'/remove',json=body).status_code==401
  csrf=login(c);assert c.post(path+'/remove',json=body).status_code==403 and not writes
  c.headers['X-MediaSeed-CSRF']=csrf
  assert c.get(path).json()=={'status':'ready','fileCount':1}
  assert c.post(path+'/remove',json=body).status_code==202 and len(writes)==1
  route=next(r for r in app.routes if getattr(r,'path',None)==path.replace('a'*32,'{identifier}'))
  app.dependency_overrides[route.dependant.dependencies[0].call]=lambda:{'role':'viewer'}
  assert c.get(path).status_code==403 and c.post(path+'/remove',json=body).status_code==403
  assert len(writes)==1
