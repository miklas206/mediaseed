const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'@playwright/test');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox','--disable-dev-shm-usage']});
 const origin=process.env.MEDIASEED_TEST_ORIGIN||'http://127.0.0.1:15388';
 for(const width of [1440,390,320]){
  const page=await browser.newPage({viewport:{width,height:1000},ignoreHTTPSErrors:true});const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{window.EventSource=class extends EventTarget{close(){}};});
  await page.route('**/api/**',async route=>{
   const path=new URL(route.request().url()).pathname;let result=[];
   if(path==='/api/auth/status')result={needsSetup:false};
   else if(path==='/api/auth/me')result={username:'synthetic',role:'administrator',language:'da',csrf:'synthetic'};
   else if(path==='/api/dashboard')result={runtime:{ready:true,downloadsEnabled:true,checkedAt:Date.now()/1000},queue:{active:0,limit:8,waiting:0},torrents:[{hash:'a'.repeat(40),name:'100.Knive.S01E04.DANiSH.1080p.WEB-DL.H.264-NORViNE',size:3868360606,uploaded:1758667560,downloaded:3868360606,progress:1,credited_ratio:.23,ratio_limit:1,seeding_time:55.84*3600,account_credited_uploaded:901898780,state:'stalledUP',tracker_name:'Synthetic tracker',num_seeds:0,num_leechs:2,cleanup:{mode:'either',ratio:3,hours:72}}]};
   else if(path==='/api/client')result={name:'qBittorrent',version:'synthetic'};
   else if(path==='/api/storage')result={locations:[],defaultLocation:null};
   else if(path==='/api/ratio-scout')result={settings:{enabled:false,automatic:false},candidates:[]};
   await route.fulfill({json:result});
  });
  await page.goto(origin+'/static/index.html');
  const item=page.locator('#torrents [data-hash="'+'a'.repeat(40)+'"]');await item.waitFor();
  const ratio=item.locator('.seed-progress-goal[data-goal=ratio]'),time=item.locator('.seed-progress-goal[data-goal=time]');
  assert.match(await ratio.textContent(),/Ratio 0,23 \/ 3.*8%/);
  assert.match(await time.textContent(),/Aktiv seeding 55,84 \/ 72 h.*78%/);
  assert.equal(await item.locator('progress').count(),1);
  assert(Math.abs(await item.locator('progress').evaluate(e=>e.value)-55.84/72)<1e-9);
  assert.match(await item.locator('.seed-goal-caption').textContent(),/Fremgang mod tidsmålet.*78%.*Ét af målene kræves/);
  assert((await item.locator('progress').getAttribute('aria-valuetext')).includes('%'));
  // Single-goal and unlimited policies must not keep an unrelated goal bar.
  const counts=await page.evaluate(()=>{
   const base={hash:'b'.repeat(40),name:'Synthetic',state:'stalledUP',progress:1,credited_ratio:.5,seeding_time:3600};
   return ['ratio','time','never','both'].map(mode=>{
    const node=seedTorrent({...base,cleanup:{mode,ratio:3,hours:72}},1);
    return {mode,bars:node.querySelectorAll('progress').length,value:node.querySelector('progress').value};
   });
  });
  assert.deepEqual(counts.map(({mode,bars})=>({mode,bars})),['ratio','time','never','both'].map(mode=>({mode,bars:1})));
  assert(Math.abs(counts[0].value-.5/3)<1e-9);
  assert(Math.abs(counts[1].value-1/72)<1e-9);
  assert.equal(counts[2].value,1);
  assert(Math.abs(counts[3].value-.5/3)<1e-9);
  const order=await page.evaluate(()=>[ [.19,16.61], [2.4,16.61], [0,0] ].map(([ratio,hours])=>{
   const row={hash:'c'.repeat(40),name:'Synthetic',state:'stalledUP',progress:1,credited_ratio:ratio,seeding_time:hours*3600,cleanup:{mode:'both',ratio:3,hours:72}};
   const node=seedTorrent(row,1);
   return {first:node.querySelector('.seed-progress-goal').dataset.goal,follows:node.querySelector('progress').dataset.follows,value:node.querySelector('progress').value,cleanup:seedCleanupProgress(row).fraction};
  }));
  assert.equal(order[0].first,'time');assert.equal(order[0].follows,'time');assert(Math.abs(order[0].value-16.61/72)<1e-9);
  assert.equal(order[1].first,'ratio');assert.equal(order[1].follows,'ratio');assert(Math.abs(order[1].value-.8)<1e-9);
  assert(order[1].cleanup<order[1].value,'AND cleanup must retain both requirements');assert.equal(order[2].first,'ratio');
  await page.screenshot({path:`/tmp/mediaseed-ratio-goals-${width}.png`,fullPage:true});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);assert.deepEqual(errors,[]);
  console.log(JSON.stringify({width,ratio:'8%',seeding:'78%',overall:'78%',pageErrors:errors}));await page.close();
 }
 await browser.close();
})().catch(error=>{console.error(error);process.exit(1);});
