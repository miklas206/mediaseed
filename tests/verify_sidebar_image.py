"""Run in an isolated, read-only app image as its production non-root uid.

Provide a writable /tmp tmpfs and no production volumes or environment.
Verifies the real home response and its scripts/styles, not just /healthz.
"""

import asyncio,os,tempfile,json
from html.parser import HTMLParser
import httpx
from mediaseed.app import create_app
class Assets(HTMLParser):
 def __init__(self):super().__init__();self.paths=[]
 def handle_starttag(self,tag,attrs):
  a=dict(attrs);p=a.get('src') if tag=='script' else a.get('href') if tag=='link' and a.get('rel')=='stylesheet' else None
  if p and p.startswith('/static/'):self.paths.append(p)
async def main():
 assert os.geteuid()!=0
 with tempfile.TemporaryDirectory() as root:
  app=create_app(data_dir=root,base_url='http://testserver',downloads_dir=root+'/downloads')
  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as c:
   r=await c.get('/');assert r.status_code==200
   a=Assets();a.feed(r.text);assert a.paths
   for p in a.paths:
    r=await c.get(p);assert r.status_code==200,(p,r.status_code)
   assert (await c.get('/api/auth/status')).status_code==200
   script=(await c.get('/static/torrent-moves.js')).text
   assert all(marker in script for marker in ['torrent-action-panel','confirmBatchRemoval','torrentAction','confirmRemoval'])
   assert (await c.get('/api/torrent-moves')).status_code==401
   print(json.dumps({'uid':os.geteuid(),'homeStatus':200,'assetsChecked':len(a.paths),'assetStatus':200,'readOnlyRoot':True}),flush=True)
asyncio.run(main())
