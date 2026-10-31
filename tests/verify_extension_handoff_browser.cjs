// Real Chromium extension + real app scripts; only loopback/synthetic APIs and torrents.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'@playwright/test');
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const root=path.resolve(__dirname,'..');
(async()=>{
 const server=require('node:http').createServer((req,res)=>{
  const pathname=new URL(req.url,'http://localhost').pathname;
  const target=path.resolve(root,'mediaseed',pathname==='/'?'static/index.html':'.'+pathname);
  if(!target.startsWith(path.join(root,'mediaseed','static')+path.sep)){res.writeHead(404);return res.end();}
  try{const body=fs.readFileSync(target);res.setHeader('Content-Type',target.endsWith('.js')?'text/javascript':target.endsWith('.css')?'text/css':target.endsWith('.html')?'text/html':'application/octet-stream');res.end(body);}catch{res.writeHead(404);res.end();}
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const origin='http://127.0.0.1:'+server.address().port;
 const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'mediaseed-extension-'));
 const extension=path.join(tmp,'extension');fs.cpSync(path.join(root,'browser-extension'),extension,{recursive:true});
 fs.copyFileSync(path.join(root,'mediaseed/static/mediaseed.svg'),path.join(extension,'mediaseed.svg'));
 // Synthetic QA grant only; production packages retain optional per-host grants.
 const manifest=JSON.parse(fs.readFileSync(path.join(extension,'manifest.json')));manifest.host_permissions=['http://127.0.0.1/*'];
 fs.writeFileSync(path.join(extension,'manifest.json'),JSON.stringify(manifest));
 const context=await chromium.launchPersistentContext(path.join(tmp,'profile'),{executablePath:'/usr/bin/chromium',headless:true,args:['--no-sandbox',`--disable-extensions-except=${extension}`,`--load-extension=${extension}`]});
 context.setDefaultTimeout(15000);
 try {
 let authenticated=true, failTorrent=false;const writes=[],errors=[];
 context.on('page',page=>page.on('pageerror',error=>errors.push(error.message)));
 await context.addInitScript(()=>window.EventSource=class extends EventTarget{close(){}});
 await context.route(origin+'/',route=>route.fulfill({status:302,headers:{location:'/static/index.html'}}));
 await context.route(origin+'/source',route=>route.fulfill({body:'<html><title>Synthetic tracker</title><a href="/release.torrent">Torrent</a></html>',contentType:'text/html'}));
 const torrent=Buffer.from('d4:infod6:lengthi1e4:name17:Synthetic.Release12:piece lengthi16384e6:pieces20:aaaaaaaaaaaaaaaaaaaaee');
 await context.route(origin+'/release*.torrent',route=>{
  const suffix=new URL(route.request().url()).pathname.match(/release-(\d+)/)?.[1];
  const name=suffix===undefined?'Synthetic.Release':'Synthetic.'+suffix;
  const body=Buffer.from('d4:infod6:lengthi1e4:name'+Buffer.byteLength(name)+':'+name+'12:piece lengthi16384e6:pieces20:aaaaaaaaaaaaaaaaaaaaee');
  return route.fulfill({body,contentType:'application/x-bittorrent'});
 });
 await context.route('**/api/**',async route=>{
  const req=route.request(),p=new URL(req.url()).pathname;let data=[];
  if(req.method()!=='GET')writes.push(p);
  if(p==='/api/auth/status')data={needsSetup:false};
  if(p==='/api/auth/me'){
   if(!authenticated)return route.fulfill({status:401,json:{detail:'Sign in required'}});
   data={username:'synthetic',role:'administrator',language:'da',csrf:'synthetic'};
  }
  if(p==='/api/dashboard')data={runtime:{ready:true,downloadsEnabled:true,checkedAt:Date.now()/1000},torrents:[],queue:{active:0,limit:8,waiting:0}};
  if(p==='/api/storage')data={defaultLocation:'local',locations:[{id:'local',label:'Downloads',available:true,writable:true,freeBytes:1e12,totalBytes:2e12}]};
  if(p==='/api/storage/folders')data={writable:true,folders:[]};
  if(p==='/api/client')data={name:'qBittorrent',version:'synthetic'};
  if(p==='/api/torrent-preview')data={name:'Synthetic.Release',size:1,lookupStatus:'tracker_unknown',benefits:{},estimates:[]};
  if(p==='/api/insights')data={accounts:[],alerts:[],settings:{lowBufferGiB:50}};
  await route.fulfill({json:data});
 });
 const worker=context.serviceWorkers()[0]||await context.waitForEvent('serviceworker');
 const id=new URL(worker.url()).host;
 await worker.evaluate(origin=>chrome.storage.local.set({origin,defaults:{start:true,cleanup:{delete_files:true}}}),origin);
 const app=await context.newPage();await app.goto(origin+'/static/index.html');await app.locator('#new-torrent').waitFor();
 const source=await context.newPage();await source.goto(origin+'/source');
 const sourceTab=await worker.evaluate(async url=>(await chrome.tabs.query({})).find(tab=>tab.url===url),origin+'/source');
 async function send(value){return worker.evaluate(({value,tab})=>begin(value,tab),{value,tab:sourceTab});}
 await send(origin+'/release.torrent');await app.locator('#add-torrent[open]').waitFor();
 assert.match(await app.locator('#torrent-file-list').textContent(),/Synthetic.Release.torrent/);
 assert.equal(await worker.evaluate(async()=>!!(await state()).pending),false);
 assert.equal((await context.pages()).filter(page=>page.url()===app.url()).length,1);
 await app.locator('#close-add').click();
 // Fifteen tracker links go straight into the app, with no extension page.
 await worker.evaluate(async({origin,tab})=>{
   for(let i=0;i<15;i++)queueContext({menuItemId:'mediaseed-add',linkUrl:origin+'/release-'+i+'.torrent'},tab);
   await contextSends;
 },{origin,tab:sourceTab});
 assert.equal(await app.locator('#torrent-file-list li').count(),15);
 assert.equal(context.pages().some(page=>page.url().startsWith('chrome-extension://')),false);
 await app.evaluate(()=>{const mode=document.querySelector('#add-cleanup-mode');mode.value='ratio';mode.dispatchEvent(new Event('change'));document.querySelector('#add-cleanup-ratio').value='2';});
 await send(origin+'/release.torrent');assert.equal(await app.locator('#torrent-file-list li').count(),16);
 assert.equal(await app.locator('#add-cleanup-ratio').inputValue(),'2');
 await app.screenshot({path:'/tmp/mediaseed-extension-direct.png',fullPage:true});
 await app.locator('#close-add').click();
 // A recognizable already granted MediaSeed tab recovers missing configuration.
 await worker.evaluate(()=>chrome.storage.local.remove('origin'));
 await send(origin+'/release.torrent');assert.equal(await app.locator('#torrent-file-list li').count(),1);
 assert.equal(await worker.evaluate(async()=>(await chrome.storage.local.get('origin')).origin),origin);
 assert.equal(context.pages().some(page=>page.url().startsWith('chrome-extension://')),false);
 await app.locator('#close-add').click();
 await send('https://nordicbytes.org/rss/private?key=synthetic');await app.locator('#feeds-editor[open]').waitFor();
 assert.equal(await app.locator('#feed-form [name=url]').inputValue(),'https://nordicbytes.org/rss/private?key=synthetic');
 assert.equal(writes.some(p=>p.startsWith('/api/torrents')||p.startsWith('/api/feeds')),false);
 // A busy app keeps one source, then explicit Continue uses the same transfer.
 await send('magnet:?xt=urn:btih:'+'a'.repeat(40));
 assert.equal(await worker.evaluate(async()=>!!(await state()).pending),true);
 await app.locator('#feeds-editor .source-editor-close').click();
 const popup=await context.newPage();await popup.goto('chrome-extension://'+id+'/popup.html');
 await popup.locator('#resume').waitFor();await popup.locator('#resume').click();await app.locator('#add-torrent[open]').waitFor();
 await app.locator('#close-add').click();
 // Login is resumed automatically when the app finishes navigating back.
 authenticated=false;await send('magnet:?xt=urn:btih:'+'b'.repeat(40));
 assert.equal(await worker.evaluate(async()=>!!(await state()).pending),true);
 authenticated=true;await app.reload();await app.locator('#add-torrent[open]').waitFor();await app.locator('#close-add').click();
 // Close MediaSeed: sending creates an app tab instead of requiring a manual open.
 await app.close();const newAppPromise=context.waitForEvent('page');await send('magnet:?xt=urn:btih:'+'c'.repeat(40));
 const newApp=await newAppPromise;try{await newApp.locator('#add-torrent[open]').waitFor({timeout:15000});}catch(e){console.log(JSON.stringify({url:newApp.url(),worker:await worker.evaluate(async()=>({s:await state(),working,delivering})),errors}));await newApp.screenshot({path:'/tmp/extension-new-tab-failure.png'});throw e;}
 await newApp.screenshot({path:'/tmp/mediaseed-extension-app-dialog.png',fullPage:true});
 await popup.reload();await popup.setViewportSize({width:560,height:640});await popup.screenshot({path:'/tmp/mediaseed-extension-setup.png',fullPage:true});
 assert.equal(await popup.locator('#connect input').count(),1);assert.equal(await popup.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 await popup.setViewportSize({width:390,height:700});assert.equal(await popup.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 assert.deepEqual(errors,[]);assert.equal(writes.some(p=>p.startsWith('/api/torrents')||p.startsWith('/api/feeds')),false);
 console.log(JSON.stringify({realChromiumExtension:true,directTrackerLinks:15,noExtensionTab:true,recoveredGrantedOrigin:true,existingTab:true,newTab:true,rssAndTorrentDialogs:true,loginResume:true,busyResume:true,automaticDownloads:0,setupWidths:[560,390],errors}));
 }finally{await context.close();await new Promise(resolve=>server.close(resolve));fs.rmSync(tmp,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exit(1)});
