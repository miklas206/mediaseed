// Real app and browser engines; all tracker/client APIs are synthetic.
const {chromium,webkit}=require(process.env.PLAYWRIGHT_MODULE||'@playwright/test');
const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
(async()=>{
 const root=path.resolve(__dirname,'../mediaseed/static');
 let uploadSink;
 const server=require('http').createServer((req,res)=>{
  const url=new URL(req.url,'http://local');
  if(url.pathname==='/api/torrents/upload'&&req.method==='POST'){
   const chunks=[];req.on('data',chunk=>chunks.push(chunk));req.on('end',()=>{
    uploadSink.push({url:url.href,body:Buffer.concat(chunks)});
    const start=url.searchParams.get('start')==='true';res.setHeader('Content-Type','application/json');
    res.end(JSON.stringify({added:true,stopped:!start,downloadsEnabled:start,startRequested:start}));
   });return;
  }
  const file=path.resolve(root,new URL(req.url,'http://local').pathname.replace(/^\/static\//,''));
  if(!file.startsWith(root+path.sep)){res.writeHead(404);return res.end();}
  try{res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html');res.end(fs.readFileSync(file));}catch{res.writeHead(404);res.end();}
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const base='http://127.0.0.1:'+server.address().port;
 const torrent=Buffer.from('d4:infod6:lengthi1024e4:name4:testee');
 try{
 for(const [engine,type] of [['Chromium',chromium],['WebKit',webkit]]){
  const browser=await type.launch(engine==='Chromium'?{executablePath:'/usr/bin/chromium',args:['--no-sandbox']}:{headless:true,env:{...process.env,WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS:'1'}});
  try{
  for(const width of [1440,390,320]){
   const page=await browser.newPage({viewport:{width,height:1000},isMobile:width<800,hasTouch:width<800});page.setDefaultTimeout(12000);
   const errors=[],writes=[],prepared=[];let vpn=false,outcome='success',release;
   uploadSink=writes;
   page.on('pageerror',e=>errors.push(e.message));
   await page.addInitScript(()=>window.EventSource=class extends EventTarget{close(){}});
   await page.route('**/api/**',async route=>{
    const request=route.request(),url=new URL(request.url()),p=url.pathname;let data=[];
    if(p==='/api/auth/status')data={needsSetup:false};
    else if(p==='/api/auth/me')data={username:'synthetic',role:'administrator',language:'da',csrf:'synthetic'};
    else if(p==='/api/dashboard')data={runtime:{ready:true,downloadsEnabled:vpn},torrents:[],queue:{active:0,waiting:0}};
    else if(p==='/api/storage')data={defaultLocation:'local',locations:['local','movies'].map(id=>({id,label:id,available:true,writable:true,totalBytes:1e12,freeBytes:1e12}))};
    else if(p==='/api/storage/folders')data={folders:url.searchParams.get('folder')?[]:[{name:'Genre',writable:true}],writable:true};
    else if(p==='/api/trackers')data=[{id:'synthetic',name:'Synthetic tracker'}];
    else if(p.endsWith('/search'))data={results:[{id:'12',name:'Synthetic legal torrent',size:1024,seeders:1,leechers:0}]};
    else if(p==='/api/trackers/synthetic/torrents/12/prepare'){
     prepared.push(p);assert.equal(request.method(),'POST');
     if(outcome==='blank')return route.fulfill({status:403,body:''});
     if(outcome==='proxy')return route.fulfill({status:502,contentType:'text/html',body:'<h1>private-passkey proxy error</h1>'});
     if(outcome==='delayed')await new Promise(resolve=>{release=resolve;});
     data={name:'Synthetic.legal.release.torrent',base64:torrent.toString('base64')};
    }
    else if(p==='/api/trackers/synthetic/torrents/12')throw Error('Legacy direct-add route must not be used');
    else if(p==='/api/torrent-preview')data={size:1024,name:'Synthetic legal torrent',estimates:[],benefits:{},lookupStatus:'tracker_unknown'};
    else if(p==='/api/torrent-import-status')data={present:false,pending:false,sameDestination:false};
    else if(p==='/api/torrents/upload')return route.continue();
    return route.fulfill({json:data});
   });
   await page.goto(base+'/static/index.html');await page.locator('#workspace').waitFor({state:'visible'});
   await page.locator('[data-view=trackers]').click();
   await page.locator('summary').filter({hasText:'Søg efter udgivelser'}).click();
   await page.locator('#tracker-account').selectOption('synthetic');
   await page.locator('#tracker-search [name=query]').fill('Synthetic');
   await page.locator('#tracker-search-submit').click();
   const download=page.locator('#tracker-results').getByRole('button',{name:'Download',exact:true});await download.waitFor();
   assert.equal(await page.locator('#tracker-search [name=ratio]').count(),0);
   await download.click();await page.locator('#add-torrent').waitFor({state:'visible'});
   assert.equal(await page.locator('#file-name').innerText(),'Synthetic.legal.release.torrent');
   assert.equal(writes.length,0,'opening Download only prepares the file');
   await page.locator('#cancel-add').click();assert.equal(writes.length,0,'cancel never adds');
   await download.click();await page.waitForFunction(()=>document.querySelector('#add-location').value==='local');
   await page.locator('#add-location').selectOption('movies');
   await page.locator('#add-folder').selectOption('Genre');
   await page.waitForFunction(()=>document.querySelector('#add-folder-path').textContent==='/Genre');
   await page.locator('#add-torrent .wizard-next').click();
   await page.locator('#add-cleanup-mode').selectOption('time');await page.locator('#add-cleanup-hours').fill('60');
   assert(await page.locator('#add-start').isDisabled(),'VPN must still gate Start');
   assert.equal(writes.length,0);
   await page.screenshot({path:path.resolve(__dirname,'../docs/previews/tracker-download-'+engine.toLowerCase()+'-'+width+'.png')});
   await page.locator('#add-submit').click();await page.waitForFunction(()=>!addingTorrent);
   assert.equal(writes.length,1);
   let query=new URL(writes[0].url).searchParams;
   for(const [key,value] of Object.entries({location:'movies',folder:'Genre',start:'false',cleanup_mode:'time',cleanup_hours:'60',cleanup_delete_files:'false'}))assert.equal(query.get(key),value);
   assert(writes[0].body.equals(torrent));
   vpn=true;await page.evaluate(()=>refresh());await download.click();
   await page.waitForFunction(()=>document.querySelector('#add-location').value==='local');
   await page.locator('#add-torrent .wizard-next').click();
   await page.waitForFunction(()=>!document.querySelector('#add-start').disabled);
   await page.locator('#add-start').click();await page.waitForFunction(()=>!addingTorrent);
   assert.equal(writes.length,2);assert.equal(new URL(writes[1].url).searchParams.get('start'),'true');
   for(outcome of ['blank','proxy']){
    await download.click();
    await page.waitForFunction(text=>document.querySelector('#message').textContent.includes(text),outcome==='blank'?'Sessionen':'HTTP 502');
    assert(!await page.locator('#add-torrent').evaluate(e=>e.open));
    assert.doesNotMatch(await page.locator('#message').innerText(),/private-passkey|expected pattern/);
   }
   // A late preparation reply must not overwrite a manually opened draft.
   outcome='delayed';await download.click();
   await page.waitForTimeout(100);await page.locator('[data-view=downloads]').click();await page.locator('#new-torrent').click();
   await page.locator('#add-torrent-form [name=mode][value=file]').check();
   await page.locator('#add-file').setInputFiles({name:'Manual.draft.torrent',mimeType:'application/x-bittorrent',buffer:torrent});
   assert(release);release();
   await page.waitForFunction(()=>document.querySelector('#message').textContent.includes('Afslut den åbne dialog'));
   assert.equal(await page.locator('#file-name').innerText(),'Manual.draft.torrent');
   assert.equal(writes.length,2);
   await page.locator('#cancel-add').click();
   assert.deepEqual(errors,[]);
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
   console.log(JSON.stringify({engine,width,downloadOpensNormalDialog:true,cancelWithoutAdd:true,folderAndSettingsPreserved:true,pause:true,startWithVpn:true,vpnGated:true,noDirectAdd:true,lateReplyPreservesDraft:true,errorHandling:true,pageErrors:errors,liveTorrentWrites:0}));
   await page.close();
  }
  }finally{await browser.close();}
 }
 }finally{server.close();}
})().catch(e=>{console.error(e);process.exit(1);});
