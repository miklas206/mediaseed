"""Run only in an isolated GPU container with temporary synthetic media."""
import asyncio
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time
from unittest.mock import Mock

import httpx
from mediaseed.conversion import Conversions, ConversionChoice, atomic
from mediaseed.conversion_worker import process,gpu_test
from mediaseed.conversion_advice import analyze_inventory
from mediaseed.storage import Storage

gpu_test()

with tempfile.TemporaryDirectory(prefix='mediaseed-gpu-qualification-') as folder:
    temp=Path(folder);root=temp/'downloads';root.mkdir();release=root/'Synthetic';release.mkdir()
    source=release/'Movie.mkv';subtitle=release/'Movie.da.srt'
    subtitle.write_text('1\n00:00:00,000 --> 00:00:02,000\nSynthetic subtitle\n')
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=s=1920x1080:r=24:d=3',
        '-f','lavfi','-i','sine=frequency=440:duration=3','-i',str(subtitle),'-map','0:v','-map','1:a','-map','2:s',
        '-c:v','ffv1','-c:a','aac','-c:s','srt','-metadata:s:a:0','language=dan','-metadata:s:s:0','language=dan',str(source)],
        check=True,capture_output=True,timeout=90)
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    queue=temp/'queue';queue.mkdir();atomic(queue/'worker.json',{'ready':True,'at':time.time()})
    @contextmanager
    def database():
        with sqlite3.connect(temp/'jobs.db') as db:yield db
    writes=[]
    async def qbit(path,method='GET',payload=None,files=None):
        if method!='GET':writes.append(path);raise AssertionError('Synthetic test must not mutate torrents')
        rows=[{'hash':'a'*40,'name':'Synthetic GPU movie','progress':1,'state':'uploading','added_on':123,'save_path':'/downloads/Synthetic'}]
        if path.endswith('/files'):rows=[{'name':'Movie.mkv','size':source.stat().st_size,'progress':1}]
        return httpx.Response(200,json=rows)
    (root/'Library/Subfolder').mkdir(parents=True)
    manager=Conversions(database,qbit,Storage(root),Mock(lock=asyncio.Lock()),queue)
    asyncio.run(manager.analyze())
    analyze_inventory(queue,[root],lambda:False)
    advice=manager.advice(20)
    assert len(advice['rows'])==1 and advice['pending']==0
    assert abs(advice['rows'][0]['duration']-3)<1
    asyncio.run(manager.create(ConversionChoice(torrent_hash='a'*40,indexes=[0],bitrate=20,location='local',folder='Library/Subfolder')))
    job=manager.records()[0]
    assert Path(job['work']).parent == root/'.incomplete'
    assert Path(job['destination']).parent == root/'Library/Subfolder/Konverteret'
    process(queue,job,[root]);asyncio.run(manager.tick())
    final=manager.records()[0];assert final['status']=='complete',final
    output=Path(final['destination'])/job['files'][0]['output']
    assert output.name=='Movie.H265.20Mbps-MDTech.mkv'
    probe=subprocess.run(['ffprobe','-v','error','-show_streams','-of','json',str(output)],capture_output=True,text=True,check=True)
    streams=json.loads(probe.stdout)['streams'];assert next(s for s in streams if s['codec_type']=='video')['codec_name']=='hevc'
    assert {'audio','subtitle'} <= {s['codec_type'] for s in streams}
    subprocess.run(['ffmpeg','-v','error','-xerror','-i',str(output),'-map','0:v','-map','0:a','-f','null','-'],check=True,capture_output=True,timeout=60)
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before
    assert (Path(final['destination'])/job['companions'][0]['output']).read_bytes()==subtitle.read_bytes()
    assert final['outputBytes']<final['originalBytes'] and not writes
    print(json.dumps({'passed':True,'nvdecReadiness':True,'metadataAnalysis':True,'convertedFilename':output.name,'matchingSubtitleName':job['companions'][0]['output'],'nestedSourceAndDestination':True,'rootStaging':True,'originalBytes':final['originalBytes'],'outputBytes':final['outputBytes'],
        'potentialSavingBytes':final['originalBytes']-final['outputBytes'],'originalUnchanged':True,
        'audioAndSubtitlesPreserved':True,'videoAndAudioDecoded':True,'torrentWrites':len(writes)}))
