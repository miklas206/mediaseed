const {chromium}=require(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
 const origin=process.env.MEDIASEED_TEST_ORIGIN || 'http://127.0.0.1:15388';
 for(const width of [1440,390,320]){
  const page=await browser.newPage({ignoreHTTPSErrors:true,viewport:{width,height:1000}}),errors=[],mutations=[];
  let phase=1,reads=0;
  const now=()=>Date.now()/1000;
  const torrent={hash:'a'.repeat(40),name:'Synthetic API torrent',progress:1,state:'stalledUP',size:2**30,credited_ratio:1.3,ratio_limit:1,seeding_time:5000,cleanup:{mode:'ratio',ratio:3,hours:48},num_complete:120,num_incomplete:30,num_seeds:2,num_leechs:1};
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{window.EventSource=class extends EventTarget{close(){}};});
  await page.route('**/api/**',async route=>{
   const request=route.request(),path=new URL(request.url()).pathname;let result=[];
   if(request.method()!=='GET')mutations.push(path);
   if(path==='/api/auth/status')result={needsSetup:false};
   else if(path==='/api/auth/me')result={username:'synthetic',role:'viewer',language:'da',csrf:'synthetic'};
   else if(path==='/api/dashboard'){
    reads++;
    result={runtime:{ready:true,downloadsEnabled:true,checkedAt:now()},queue:{active:0,limit:8,waiting:0},torrents:[
     {...torrent,num_complete:phase===1?120:127,swarm:{tracker:'Configured tracker',status:'ok',seeders:phase===1?40:47,leechers:7,checkedAt:now()}},
     {...torrent,hash:'b'.repeat(40),name:'Synthetic external tracker',num_complete:15,num_incomplete:4,num_seeds:3,num_leechs:0},
     {...torrent,hash:'c'.repeat(40),name:'Synthetic unknown totals',num_complete:-1,num_incomplete:null,num_seeds:0,num_leechs:0},
     {...torrent,hash:'d'.repeat(40),name:'Synthetic zero counts',num_complete:0,num_incomplete:0,num_seeds:0,num_leechs:0,swarm:{tracker:'Configured tracker',status:'ok',seeders:0,leechers:0,checkedAt:now()}},
     {...torrent,hash:'e'.repeat(40),name:'Synthetic stale API',swarm:{tracker:'Configured tracker',status:'ok',seeders:999,leechers:999,checkedAt:now()-190}}
    ]};
   }else if(path==='/api/storage')result={defaultLocation:'local',locations:[]};
   else if(path==='/api/client')result={name:'qBittorrent',version:'v5.2.4'};
   else if(path==='/api/ratio-scout')result={settings:{enabled:false,automatic:false},candidates:[]};
   await route.fulfill({json:result});
  });
  await page.goto(origin+'/static/index.html');
  const row=hash=>page.locator('#torrents [data-hash="'+hash.repeat(40)+'"]');
  const api=row('a');await api.waitFor();
  const lines=api.locator('.seed-swarm .seed-peer-line');
  assert.equal(await lines.count(),3);
  assert.equal(await lines.nth(0).locator('.seeders strong').textContent(),'120');
  assert.equal(await lines.nth(1).locator('.seeders strong').textContent(),'2');
  assert.equal(await lines.nth(2).locator('.seeders strong').textContent(),'40');
  assert.equal(await row('b').locator('.seed-peer-line').count(),2);
  assert.equal(await row('b').locator('.seed-peer-line').nth(0).locator('.leechers strong').textContent(),'4');
  assert.equal(await row('c').locator('.seed-peer-line').nth(0).locator('.seeders strong').textContent(),'?');
  assert.equal(await row('c').locator('.seed-peer-line').nth(1).locator('.seeders strong').textContent(),'0');
  assert.equal(await row('d').locator('.seed-peer-api .seeders strong').textContent(),'0');
  assert.equal(await row('e').locator('.seed-peer-api .seeders strong').textContent(),'?');
  const geometry=await api.evaluate(e=>{
   const swarm=e.querySelector('.seed-swarm').getBoundingClientRect();
   const others=[...e.children].filter(n=>!n.classList.contains('seed-swarm')).map(n=>n.getBoundingClientRect().bottom);
   return {swarmY:swarm.top,previousBottom:Math.max(...others),swarmWidth:swarm.width,rowWidth:e.getBoundingClientRect().width};
  });
  assert(geometry.swarmY>=geometry.previousBottom-1,'Swarm counts overlap torrent controls');
  assert(geometry.swarmWidth>=geometry.rowWidth-110,'Swarm footer collapsed into the poster column');
  await api.screenshot({path:`/tmp/swarm-row-${width}.png`});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
  phase=2;
  await page.waitForFunction(()=>document.querySelector('#torrents [data-hash="'+ 'a'.repeat(40)+'"] .seed-peer-api .seeders strong')?.textContent==='47',null,{timeout:12000});
  assert.equal(await lines.nth(0).locator('.seeders strong').textContent(),'127');
  assert(reads>=2);assert.deepEqual(errors,[]);assert.deepEqual(mutations,[]);
  console.log(JSON.stringify({width,apiCounts:47,clientTotal:127,automaticRefresh:true,unknownAndZeroDistinct:true,staleApiLabel:true,errors}));
  await page.close();
 }
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1);});
