import json
from pathlib import Path
import pytest
from deploy.rename_prepared_conversion import prepare,apply
from mediaseed.conversion import atomic,fingerprint
from mediaseed.upload_engine.upload_helper import make_torrent,decode_torrent


@pytest.mark.parametrize('failure',[False,True])
def test_unpublished_folder_repair_updates_torrent_and_rolls_back(tmp_path,failure):
 old=tmp_path/'Film [aaaaaaaa]';old.mkdir();artifacts=tmp_path/'artifacts';artifacts.mkdir()
 video=old/'Film.2025.H265.MDTECH.mkv';video.write_bytes(b'synthetic movie');nfo=video.with_suffix('.nfo');nfo.write_bytes(b'synthetic nfo')
 job={'id':'a'*32,'status':'complete','destination':str(old),'files':[{'name':'Source.mkv','output':video.name}]}
 atomic(old/'.mediaseed-converted.json',{'id':job['id'],'verified':True,'files':[video.name],'identities':{video.name:fingerprint(video)}})
 result={'id':'b'*24,'source':{'torrentName':old.name,'folder':old.name,'clientContentPath':'/downloads/media/converted-movies/'+old.name},'contentFolder':str(old),'videos':[{'file':video.name}],'payloadNfoFile':nfo.name}
 before=make_torrent([(video,video.name),(nfo,nfo.name)],old.name,'https://tracker.invalid/announce/synthetic',profile='generic')
 (artifacts/'upload.torrent').write_bytes(before);(artifacts/'checklist.json').write_text(json.dumps(result))
 plan=prepare(job,result,artifacts);saved=[];identity=fingerprint(video)
 def save(j,r):
  saved.append((j,r))
  if failure and len(saved)==1:raise OSError('Synthetic save failure')
 if failure:
  with pytest.raises(OSError):apply(plan,save)
  assert old.exists() and fingerprint(video)==identity
  assert (artifacts/'upload.torrent').read_bytes()==before and saved[-1]==(job,result)
 else:
  apply(plan,save);new=Path(plan['new']);assert new.name==video.stem and not old.exists()
  assert fingerprint(new/video.name)==identity and (new/nfo.name).read_bytes()==b'synthetic nfo'
  after=decode_torrent((artifacts/'upload.torrent').read_bytes())[b'info'];original=decode_torrent(before)[b'info']
  assert after[b'name']==video.stem.encode() and after[b'pieces']==original[b'pieces'] and after[b'files']==original[b'files']
  assert saved[-1][0]['destination']==str(new) and saved[-1][1]['source']['torrentName']==video.stem
