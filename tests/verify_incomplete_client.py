import hashlib,json,os,pathlib,subprocess,time,urllib.request,urllib.parse
root=pathlib.Path('/probe');profile=root/'profile'; final=root/'downloads/Film æ';staging=final/'.incomplete';staging.mkdir(parents=True)
config=profile/'qBittorrent/config/qBittorrent.conf';config.parent.mkdir(parents=True)
config.write_text('[Preferences]\nWebUI\\Address=127.0.0.1\nWebUI\\Port=18080\nWebUI\\LocalHostAuth=false\n[LegalNotice]\nAccepted=true\n[BitTorrent]\nSession\\DHTEnabled=false\nSession\\PeXEnabled=false\nSession\\LSDEnabled=false\nSession\\UPnP=false\n')
def enc(x):
 if isinstance(x,int):return b'i'+str(x).encode()+b'e'
 if isinstance(x,bytes):return str(len(x)).encode()+b':'+x
 return b'd'+b''.join(enc(k)+enc(v) for k,v in sorted(x.items()))+b'e'
data=b'isolated synthetic data\n'*1024;name='probe.bin';info={b'name':name.encode(),b'length':len(data),b'piece length':32768,b'pieces':hashlib.sha1(data).digest()};torrent=enc({b'info':info});identifier=hashlib.sha1(enc(info)).hexdigest()
partial=staging/name;partial.write_bytes(data);original_inode=partial.stat().st_ino
proc=subprocess.Popen(['/app/qbittorrent-nox','--profile='+str(profile),'--webui-port=18080','--confirm-legal-notice'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
base='http://127.0.0.1:18080/api/v2/'
def call(path,fields=None,raw=None,ctype=None):
 request=urllib.request.Request(base+path,data=raw if raw is not None else urllib.parse.urlencode(fields).encode() if fields is not None else None,headers={'Referer':'http://127.0.0.1:18080','Content-Type':ctype or 'application/x-www-form-urlencoded'})
 return urllib.request.urlopen(request,timeout=3).read()
try:
 for _ in range(60):
  try:version=call('app/version').decode();break
  except Exception:
   if proc.poll() is not None:raise RuntimeError('Isolated client exited')
   time.sleep(.2)
 else:raise RuntimeError('Isolated client unavailable')
 fields={'savepath':str(final),'downloadPath':str(staging),'useDownloadPath':'true','autoTMM':'false','stopped':'true','paused':'true'}
 boundary='synthetic-incomplete-check';parts=[]
 for key,val in fields.items():parts.append(('--'+boundary+'\r\nContent-Disposition: form-data; name="'+key+'"\r\n\r\n'+val+'\r\n').encode())
 parts.append(('--'+boundary+'\r\nContent-Disposition: form-data; name="torrents"; filename="probe.torrent"\r\nContent-Type: application/x-bittorrent\r\n\r\n').encode()+torrent+b'\r\n--'+boundary.encode()+b'--\r\n')
 result=call('torrents/add',raw=b''.join(parts),ctype='multipart/form-data; boundary='+boundary).decode()
 for _ in range(60):
  rows=json.loads(call('torrents/info'))
  if rows:break
  time.sleep(.2)
 assert rows and rows[0]['download_path'].rstrip('/')==str(staging),(result,rows)
 call('torrents/recheck',{'hashes':identifier})
 for _ in range(100):
  rows=json.loads(call('torrents/info'))
  if rows[0]['progress']==1 and (final/name).exists():break
  time.sleep(.2)
 assert rows[0]['progress']==1 and (final/name).exists(),{'state':rows[0]['state'],'progress':rows[0]['progress'],'files':list(map(str,final.rglob('*')))}
 assert (final/name).read_bytes()==data
 assert (final/name).stat().st_ino==original_inode
 assert not partial.exists()
 print(json.dumps({'client':version,'network':'none','downloadPathAccepted':True,'completionMovedToDestination':True,'inodePreserved':True,'bytes':len(data)}),flush=True)
finally:
 proc.terminate();proc.wait(timeout=10)
