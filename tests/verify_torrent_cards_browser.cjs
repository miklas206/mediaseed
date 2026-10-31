// Test only the existing torrent workspace. All APIs and cover images are synthetic.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'@playwright/test');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
 const origin=process.env.MEDIASEED_TEST_ORIGIN||'http://127.0.0.1:15392';
 for(const width of [1440,390,320]){
  const page=await browser.newPage({viewport:{width,height:1000},ignoreHTTPSErrors:true}),errors=[];let failed=false,writes=0;
  const states=['downloading','stalledDL','metaDL','queuedDL','uploading','stalledUP','pausedDL','checkingUP','missingFiles','moving','unknown'];
  const rows=states.map((state,i)=>({hash:(i+1).toString(16).padStart(40,'0'),name:`${String(i+1).padStart(2,'0')}.Northern.Lights.S01E04.DANiSH.2160p.WEB-DL.HDR.Long.Release.Name`,state,progress:['uploading','stalledUP','checkingUP','missingFiles'].includes(state)?1:.64,size:2**30,completed:2**29,dlspeed:state==='downloading'?1024:0,upspeed:state==='uploading'?2048:0,seeding_time:3*3600,credited_ratio:3,cleanup:{mode:'ratio',ratio:2,hours:48,status:'active'},num_leechs:2,num_seeds:1,tracker_name:'Synthetic',cover:i<2?'/api/covers/'+(i?'b':'a').repeat(40):null}));
  await page.addInitScript(()=>window.EventSource=class extends EventTarget{close(){}});page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/**',async route=>{
   const req=route.request(),path=new URL(req.url()).pathname;let data=[];if(req.method()!=='GET')writes++;
   if(path.startsWith('/api/covers/'))return route.fulfill(path.endsWith('a'.repeat(40))?{status:404,body:''}:{contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="44" height="66"><rect width="44" height="66" fill="#087ea3"/></svg>'});
   if(path==='/api/auth/status')data={needsSetup:false};if(path==='/api/auth/me')data={username:'synthetic',role:'administrator',language:'da',csrf:'synthetic'};
   if(path==='/api/dashboard'){
    if(failed)return route.fulfill({status:503,json:{detail:'Synthetic outage'}});
    data={runtime:{provider:'mediavpn',ready:true,downloadsEnabled:true,checkedAt:Date.now()/1000},queue:{active:1,limit:8,waiting:1},torrents:rows};
   }
   if(path==='/api/client')data={name:'qBittorrent',version:'synthetic'};
   if(path==='/api/storage')data={locations:[],defaultLocation:null};
   if(path==='/api/activity')data={entries:[]};
   await route.fulfill({json:data});
  });
  await page.goto(origin+'/static/index.html');await page.locator('#torrents .torrent').first().waitFor();
  const item=i=>page.locator(`#torrents [data-hash="${rows[i].hash}"]`);
  assert.equal(await page.locator('#seed-overview').count(),0,'Skipped step 1 must stay absent');
  assert.equal(await page.locator('#torrents progress').count(),rows.length);
  for(const i of [0,4])assert.equal(await item(i).locator('.seed-progress-track').getAttribute('data-moving'),'true');
  for(const i of [1,2,3,5,6,7,8,9,10])assert.equal(await item(i).locator('.seed-progress-track').getAttribute('data-moving'),'false');
  assert.match(await item(4).locator('.seed-state-live').textContent(),/Mål nået.*uploader stadig/);
  const summary=item(1).locator('.seed-status>summary');await summary.focus();await page.keyboard.press('Enter');
  assert.equal(await item(1).locator('.seed-status').evaluate(e=>e.open),true);
  assert.match(await item(1).locator('.seed-status-popover').textContent(),/manglende dele/);
  const bounds=await item(1).locator('.seed-status-popover').boundingBox();assert(bounds.x>=0&&bounds.x+bounds.width<=width+1,'Status explanation outside viewport');
  // A new observation replaces rows but preserves the status disclosure and focus.
  rows[1].num_seeds=3;await page.evaluate(()=>refresh());
  assert.equal(await item(1).locator('.seed-status').evaluate(e=>e.open),true);
  assert.equal(await summary.evaluate(e=>document.activeElement===e),true);
  await page.keyboard.press('Escape');assert.equal(await item(1).locator('.seed-status').evaluate(e=>e.open),false);
  assert.equal(await item(0).locator('.seed-poster img').count(),0,'Failed cover needs a stable fallback');
  await item(1).locator('.seed-poster img').evaluate(image=>image.decode());
  const poster=await item(1).locator('.seed-poster').boundingBox();assert(poster.width<50);
  const heading=await item(0).locator('.torrent-name').evaluate(e=>({height:e.getBoundingClientRect().height,line:parseFloat(getComputedStyle(e).lineHeight)}));assert(heading.height<=heading.line*2+1);
  await page.evaluate(()=>{lastTorrentSample=Date.now()-31000;expireConnection();});
  assert.equal(await page.locator('#torrents').getAttribute('data-fresh'),'false');
  assert.equal(await item(0).locator('.seed-progress-track').evaluate(e=>getComputedStyle(e,'::after').display),'none');
  assert.equal(await item(0).locator('.seed-state-stale').isVisible(),true);
  await page.evaluate(()=>refresh());assert.equal(await page.locator('#torrents').getAttribute('data-fresh'),'true');
  failed=true;await page.evaluate(()=>refresh());assert.equal(await page.locator('#torrents').getAttribute('data-fresh'),'false');failed=false;await page.evaluate(()=>refresh());
  await page.evaluate(()=>{document.documentElement.dataset.reducedMotion='true';});assert.equal(await item(0).locator('.seed-progress-track').evaluate(e=>getComputedStyle(e,'::after').display),'none');
  for(const theme of ['midnight-glass','soft-orbit','precision-terminal','floating-layers','edge-signal','classic','light']){
   await page.evaluate(value=>MediaAppTheme.apply(value),theme);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,theme+' '+width);
  }
  await page.evaluate(()=>MediaAppTheme.apply('midnight-glass'));await page.locator('#torrents').screenshot({path:`/tmp/mediaseed-step2-cards-${width}.png`});
  await page.locator('#search').fill('does-not-exist');assert.equal(await page.locator('#torrents .torrent').count(),0);await page.locator('#search').fill('');
  await page.locator('#filter').selectOption('checking');assert.equal(await page.locator('#torrents .torrent').count(),1);await page.locator('#filter').selectOption('all');
  await item(0).locator('.torrent-menu>summary').click();assert.equal(await item(0).locator('.torrent-menu').evaluate(e=>e.open),true);rows[0].dlspeed=2048;await page.evaluate(()=>refresh());assert.equal(await item(0).locator('.torrent-menu').evaluate(e=>e.open),true);
  assert.equal(writes,0);assert.deepEqual(errors,[]);
  console.log(JSON.stringify({width,states:11,oneBar:true,disclosures:true,focusPreserved:true,staleStops:true,covers:true,themes:7,filters:true,menuPreserved:true,noWrites:true,errors}));await page.close();
 }
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
