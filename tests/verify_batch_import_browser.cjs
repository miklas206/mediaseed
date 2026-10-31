const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
(async()=>{
 const root=path.resolve(__dirname,'../mediaseed/static');
 const server=require('node:http').createServer((req,res)=>{const target=path.resolve(root,new URL(req.url,'http://local').pathname.replace(/^\/static\//,''));if(!target.startsWith(root+path.sep)){res.writeHead(404);return res.end();}try{res.setHeader('Content-Type',target.endsWith('.js')?'text/javascript':target.endsWith('.css')?'text/css':'text/html');res.end(fs.readFileSync(target));}catch{res.writeHead(404);res.end();}});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const origin='http://127.0.0.1:'+server.address().port;
 const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const width of [1440,390])for(const scenario of ['success','lost-receipt','connection-down','already-present','other-folder','mixed-18','all-duplicates','single-duplicate','race-duplicate']){
  const count=scenario==='mixed-18'?18:scenario==='single-duplicate'?1:15;
  const page=await browser.newPage({viewport:{width,height:1000}}),errors=[],uploads=[],accepted=new Set(['already-present','other-folder'].includes(scenario)?['file-01']:[]);let down=false;
  if(['mixed-18','all-duplicates','single-duplicate'].includes(scenario))for(let i=1;i<=(scenario==='mixed-18'?15:count);i++)accepted.add('file-'+String(i).padStart(2,'0'));
  page.on('pageerror',e=>errors.push(e.message));await page.addInitScript(()=>{window.EventSource=class extends EventTarget{close(){}};});
  await page.route('**/api/**',async route=>{
   const request=route.request(),url=new URL(request.url()),path=url.pathname;let result=[];
   if(path==='/api/auth/status')result={needsSetup:false};
   else if(path==='/api/auth/me')result={username:'synthetic',role:'administrator',language:'da',csrf:'synthetic'};
   else if(path==='/api/dashboard')result={runtime:{ready:true,downloadsEnabled:true,checkedAt:Date.now()/1000},torrents:[],queue:{active:0,limit:8,waiting:0}};
   else if(path==='/api/storage')result={defaultLocation:'local',locations:[{id:'local',label:'Lokale downloads',available:true,writable:true,freeBytes:1e12,totalBytes:2e12}]};
   else if(path==='/api/storage/folders')result={writable:true,folders:[]};
   else if(path==='/api/client')result={name:'qBittorrent',version:'synthetic'};
   else if(path==='/api/torrent-preview')result={size:100,lookupStatus:'torrent_unmatched',benefits:{},metadataPending:false,estimates:[]};
   else if(path==='/api/torrent-import-status'){
    if(down)return route.abort('connectionfailed');
    const id=request.postData();result={present:accepted.has(id),sameDestination:accepted.has(id)&&scenario!=='other-folder',pending:false};
   }else if(path==='/api/torrents/upload'){
    const id=request.postData();uploads.push(id);
    if(scenario==='connection-down'&&id==='file-02'&&!accepted.has(id)){down=true;return route.abort('connectionfailed');}
    accepted.add(id);
    if(scenario==='lost-receipt'&&id==='file-02')return route.fulfill({status:502,contentType:'text/html',body:'<!DOCTYPE html><title>Synthetic proxy failure</title>'});
    result={added:true,stopped:true,existing:scenario==='race-duplicate'&&id==='file-01'};
   }
   await route.fulfill({json:result});
  });
  await page.goto(origin+'/static/index.html');await page.locator('#new-torrent').click();await page.locator('[name=mode][value=file]').check();
  const files=Array.from({length:count},(_,i)=>({name:`Synthetic.Episode.${i+1}.torrent`,mimeType:'application/x-bittorrent',buffer:Buffer.from('file-'+String(i+1).padStart(2,'0'))}));
  await page.locator('#add-file').setInputFiles(files);await page.locator('#add-torrent .wizard-next').click();await page.locator('#add-submit').click();
  await page.waitForFunction(()=>!addingTorrent&&torrentFiles.some(i=>i.state==='added'));
  if(scenario==='mixed-18'){assert.match(await page.locator('.notifications').textContent(),/3 tilføjet · 15 sprunget over som dubletter/);assert.equal(uploads.length,3);}
  if(['all-duplicates','single-duplicate'].includes(scenario)){assert.match(await page.locator('.notifications').textContent(),new RegExp('0 tilføjet · '+count+' sprunget over som dubletter'));assert.equal(uploads.length,0);}
  if(scenario==='lost-receipt'){assert.match(await page.locator('.notifications').textContent(),/15 tilføjet/);assert.equal(await page.evaluate(()=>torrentFiles.filter(i=>i.outcome==='duplicate').length),0);}
  if(scenario==='race-duplicate')assert.match(await page.locator('.notifications').textContent(),/14 tilføjet · 1 sprunget over som dubletter/);
  if(scenario==='connection-down'){
   const counts=await page.evaluate(()=>({added:torrentFiles.filter(i=>i.state==='added').length,unconfirmed:torrentFiles.filter(i=>i.state==='unconfirmed').length,pending:torrentFiles.filter(i=>i.state==='pending').length}));
   assert.deepEqual(counts,{added:1,unconfirmed:1,pending:13});assert.equal(uploads.length,2);
   assert((await page.locator('.notifications').textContent()).includes('13 ikke sendt'));
   down=false;
   // The server is reachable again; a user retries the retained queue.
   await page.locator('#new-torrent').click();const next=page.locator('#add-torrent .wizard-next');if(await next.isVisible())await next.click();
   // Re-enable only the synthetic transport failure condition for this retry.
   accepted.add('file-02');
   await page.locator('#add-submit').click();await page.waitForFunction(()=>!addingTorrent&&torrentFiles.every(i=>i.state==='added'));
  }
  if(scenario==='mixed-18')assert.match(await page.locator('#torrent-batch-status').textContent(),/3 tilføjet · 15 sprunget over som dubletter · 18 valgt/);
  const states=await page.evaluate(()=>torrentFiles.map(i=>({state:i.state,message:i.message})));
  if(scenario==='other-folder'){assert.equal(states.filter(i=>i.state==='added').length,15);assert.equal(states[0].state,'added');assert.match(await page.locator('.notifications').textContent(),/14 tilføjet · 1 sprunget over som dubletter/);assert.match(await page.locator('#torrent-file-list li').first().textContent(),/anden mappe/);}else assert(states.every(i=>i.state==='added'),JSON.stringify(states));
  assert.equal(accepted.size,count);assert.equal(uploads.filter(id=>id==='file-01').length,['already-present','other-folder','mixed-18','all-duplicates','single-duplicate'].includes(scenario)?0:1);
  assert.equal(new Set(uploads).size,uploads.length,'No confirmed file may be resent');assert.deepEqual(errors,[]);
  console.log(JSON.stringify({width,scenario,selected:count,confirmed:states.filter(i=>i.state==='added').length,uploadWrites:uploads.length,errors}));await page.close();
 }
 await browser.close();await new Promise(resolve=>server.close(resolve));
})().catch(e=>{console.error(e);process.exit(1)});
