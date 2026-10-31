// Serve mediaseed/ over HTTP and run with MEDIASEED_TEST_ORIGIN; all APIs are synthetic.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'@playwright/test');
const assert=require('node:assert/strict');
const origin=process.env.MEDIASEED_TEST_ORIGIN||'http://127.0.0.1:15392';
(async()=>{
 const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
 for(const width of [1440,390,320]){
  const page=await browser.newPage({viewport:{width,height:1000}}),errors=[];let reject=false,changes=0,empty=false;
  const feeds=[{id:'feed-a',name:'Nordiske serier · 1080p og 2160p med et meget langt feednavn',paused:false,status:'Afventer nye feedposter',location:'local',folder:'Serier',ratio:2,include:['2160p'],exclude:['CAM'],badges:['freeleech'],upload:{creditedUploadedBytes:123*1024**3}}, {id:'feed-b',name:'Film · REMUX',paused:true,status:'Pauset',location:'local',folder:'Film',ratio:1}];
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>window.EventSource=class extends EventTarget{close(){}});
  await page.route('**/api/**',async route=>{
   const path=new URL(route.request().url()).pathname;let data=[];
   if(path==='/api/auth/status')data={needsSetup:false};
   if(path==='/api/auth/me')data={username:'synthetic',role:'administrator',language:'da',csrf:'synthetic'};
   if(path==='/api/dashboard')data={runtime:{provider:'mediavpn',ready:true,downloadsEnabled:true,checkedAt:Date.now()/1000,country:'Denmark',publicIp:'192.0.2.1'},queue:{active:2,limit:8,waiting:3},torrents:empty?[]:[{hash:'a'.repeat(40),name:'Northern.Lights.S01E04.2160p.WEB-DL',size:12*1024**3,progress:.64,dlspeed:32100000,upspeed:1048576,state:'downloading',tracker_name:'Synthetic'}]};
   if(path==='/api/client')data={name:'qBittorrent',version:'synthetic'};
   if(path==='/api/storage')data={locations:[{id:'local',label:'Local downloads',available:true,writable:true,freeBytes:1e12,totalBytes:2e12}],defaultLocation:'local'};
   if(path==='/api/storage/folders')data={writable:true,folders:[]};
   if(path==='/api/feeds')data=empty?[]:feeds;
   if(path==='/api/feeds/feed-a')data={...feeds[0],url:'',cleanup:{mode:'never'}};
   if(path==='/api/feeds/feed-a/state'){
    changes++;await new Promise(resolve=>setTimeout(resolve,150));
    if(reject)return route.fulfill({status:409,json:{detail:'Starting requires a verified VPN. Choose add on pause instead.'}});
    const enabled=route.request().postDataJSON().enabled;
    assert.equal(route.request().method(),'PUT');assert.equal(route.request().headers()['x-mediaseed-csrf'],'synthetic');
    feeds[0].paused=!enabled;data={...feeds[0]};
   }
   if(path==='/api/vpn')data={provider:'mediavpn',ready:true,downloadsEnabled:true,checkedAt:Date.now()/1000,profiles:[]};
   if(path==='/api/ratio-scout')data={settings:{enabled:false,automatic:false,tracker:'',query:'',location:'local',folder:'',dailyLimit:5,scanMinutes:10},candidates:[]};
   if(path==='/api/insights')data={accounts:[],alerts:[],settings:{lowBufferGiB:50}};
   if(path==='/api/activity')data={entries:[]};
   await route.fulfill({json:data});
  });
  await page.goto(origin+'/static/index.html');await page.locator('#torrents .torrent').waitFor();
  assert.equal(await page.locator('#torrents .torrent progress').count(),1);
  assert.equal(await page.locator('.seed-list-actions #new-torrent').isVisible(),true);
  const views=['downloads','feeds','ratio-scout','trackers','insights','vpn'];
  for(const view of views){await page.locator(`[data-view="${view}"]`).click();await page.locator('#'+view).waitFor();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,'overflow: '+view+' '+width);}
  await page.locator('[data-view=feeds]').click();await page.locator('.feed-card').first().waitFor();
  assert.equal(await page.locator('.seed-stats').isVisible(),false);
  const toggle=()=>page.locator('.feed-card').first().getByRole('switch');
  await page.locator('.feed-card').first().locator('summary').click();
  await toggle().focus();await page.keyboard.press('Space');
  await page.waitForFunction(()=>document.querySelector('.feed-state-switch')?.getAttribute('aria-checked')==='false');
  assert.equal(changes,1);assert.equal(feeds[0].paused,true);
  await page.waitForFunction(()=>document.activeElement?.getAttribute('role')==='switch');
  assert.equal(await page.locator('.feed-card').first().locator('details').getAttribute('open')!==null,true);
  reject=true;await toggle().focus();await page.keyboard.press('Space');await page.waitForFunction(()=>document.querySelector('#message').textContent.includes('VPN'));
  assert.equal(await toggle().getAttribute('aria-checked'),'false');assert.equal(await toggle().isEnabled(),true);assert.equal(changes,2);
  reject=false;await toggle().click();await page.waitForFunction(()=>document.querySelector('.feed-state-switch')?.getAttribute('aria-checked')==='true');
  // Editors still open with stored filters and storage selection.
  await page.locator('.feed-card').first().getByRole('button',{name:'Rediger',exact:true}).click();
  await page.locator('#feeds-editor').waitFor();assert.equal(await page.locator('#feed-form [name=include]').inputValue(),'2160p');
  await page.locator('#feeds-editor .source-editor-close').click();
  await page.evaluate(()=>{showNotification('');window.scrollTo(0,0);});
  for(const theme of ['midnight-glass','soft-orbit','precision-terminal','floating-layers','edge-signal','classic','light']){
   await page.evaluate(value=>MediaAppTheme.apply(value),theme);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,'theme overflow '+theme+' '+width);
   if(['midnight-glass','light'].includes(theme))await page.screenshot({path:`/tmp/mediaseed-polish-rss-${theme}-${width}.png`,fullPage:true});
  }
  for(const mode of ['sidebar','topbar','dock-top','dock-bottom','launcher']){
   await page.evaluate(value=>{const select=document.querySelector('.app-navigation-setting select');select.value=value;select.dispatchEvent(new Event('change'));},mode);
   assert.equal(await page.evaluate(()=>document.documentElement.dataset.appNavigation),mode);
   if(!await page.locator('[data-view=downloads]').isVisible())await page.locator('.app-navigation-toggle').click();
   await page.locator('[data-view=downloads]').click();
   assert.equal(await page.locator('#downloads').isVisible(),true);
   if(!await page.locator('[data-view=feeds]').isVisible())await page.locator('.app-navigation-toggle').click();
   await page.locator('[data-view=feeds]').click();
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,'navigation overflow '+mode);
  }
  await page.evaluate(()=>{appRole='viewer';});await page.evaluate(()=>loadSources('feeds'));
  assert.equal(await page.locator('.feed-state-switch').count(),0);assert.equal(await page.locator('.feed-state-badge').count(),2);
  empty=true;await page.evaluate(()=>loadSources('feeds'));assert.equal(await page.locator('.feed-card').count(),0);
  assert.deepEqual(errors,[]);console.log(JSON.stringify({width,oneProgressBar:true,switchSuccessAndFailure:true,keyboard:true,editPreserved:true,themes:7,navigationModes:5,viewer:true,empty:true,errors}));await page.close();
 }
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
