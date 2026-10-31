"""Local browser integration fixture: real authenticated conversion API, sparse synthetic media."""
from pathlib import Path
import os
import tempfile
import time

import httpx
import uvicorn
from starlette.staticfiles import StaticFiles
from mediaseed.app import create_app
from mediaseed.conversion import atomic,fingerprint
from mediaseed.conversion_advice import cache_key

with tempfile.TemporaryDirectory(prefix='mediaseed-query-fixture-') as folder:
    root=Path(folder)
    app=create_app(root/'data','http://127.0.0.1:15402',transport=httpx.MockTransport(lambda r:httpx.Response(200,json=[])))
    queue=root/'queue';queue.mkdir()
    source=root/'Film.mkv'
    with source.open('wb') as stream:stream.truncate(60*1024**3)
    entry={'hash':'a'*40,'title':'Synthetic recommendation','index':0,'name':'Film.mkv','path':str(source),'identity':fingerprint(source)}
    atomic(queue/'analysis-inventory.json',{'at':time.time(),'files':[entry],'truncated':False})
    atomic(queue/('analysis-'+cache_key(entry)+'.json'),{'metadata':{'duration':7200,'codec':'hevc','height':2160,'audioBps':4000000,'audioEstimated':False}})
    atomic(queue/'worker.json',{'at':time.time(),'ready':True})
    app.state.conversions.queue=queue
    stage=os.environ['MEDIASEED_QUERY_STATIC']
    for route in app.routes:
        if getattr(route,'path',None)=='/static':route.app=StaticFiles(directory=stage)
    uvicorn.run(app,host='127.0.0.1',port=15402,log_level='warning')
