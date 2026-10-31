// Synthetic VPN evidence only: no real profiles, tunnels or downloads are changed.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'@playwright/test');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
 const origin=process.env.MEDIASEED_TEST_ORIGIN||'http://127.0.0.1:15392';
 for(const width of [1440,390,320]){
  const page=await browser.newPage({viewport:{width,height:1000},ignoreHTTPSErrors:true}),errors=[];let writes=0,putDelay=0;
  const profile='a'.repeat(24);
  const runtime=()=>({provider:'mediavpn',ready:true,downloadsEnabled:true,checkedAt:Date.now()/1000,country:'Sweden',publicIp:'203.0.113.7',forwardedPort:43210,integrationAvailable:true,profileId:profile,profiles:[{id:profile,name:'Synthetic VPN'}]});
  await page.addInitScript(()=>window.EventSource=class extends EventTarget{close(){}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/**',async route=>{
   const request=route.request(),path=new URL(request.url()).pathname;let data=[];
   if(request.method()!=='GET'){writes++;await new Promise(resolve=>setTimeout(resolve,putDelay));}
   if(path==='/api/auth/status')data={needsSetup:false};
   if(path==='/api/auth/me')data={username:'synthetic',role:'administrator',language:'da',csrf:'synthetic'};
   if(path==='/api/dashboard')data={runtime:runtime(),queue:{active:1,limit:8,waiting:0},torrents:[{hash:'a'.repeat(40),name:'Synthetic torrent',state:'downloading',progress:.5,dlspeed:65536,upspeed:32768,size:2**30}]};
   if(path==='/api/vpn')data=runtime();
   if(path==='/api/client')data={name:'qBittorrent',version:'synthetic'};
   if(path==='/api/storage')data={locations:[],defaultLocation:null};
   if(path==='/api/activity')data={entries:[]};
   await route.fulfill({json:data});
  });
  await page.goto(origin+'/static/index.html');await page.locator('#torrents .torrent').waitFor();
  const flow=page.locator('#download-flow'),vpnFlow=page.locator('#connection-flow');
  assert.equal(await flow.getAttribute('data-state'),'active');
  assert(await flow.locator('.traffic-packet').count()>0);
  assert.equal(await flow.locator('.route-vpn .route-icon').evaluate(e=>getComputedStyle(e,'::before').animationName),'vpn-heartbeat');
  await page.locator('[data-view=vpn]').click();await page.waitForFunction(()=>document.querySelector('#vpn-status').textContent==='Verificeret');
  assert.equal(await flow.isVisible(),false);
  const details=page.locator('.vpn-connection-details');assert.equal(await details.evaluate(e=>e.open),false);
  await details.locator('summary').focus();await page.keyboard.press('Enter');
  assert.equal(await page.locator('#connection-ip').isVisible(),true);
  await page.evaluate(()=>refreshVpn());assert.equal(await details.evaluate(e=>e.open),true);
  assert.equal(await details.locator('summary').evaluate(e=>document.activeElement===e),true);
  assert.equal(await page.locator('#connection-ip').textContent(),'203.0.113.7');
  for(const theme of ['midnight-glass','soft-orbit','precision-terminal','floating-layers','edge-signal','classic','light']){
   await page.evaluate(value=>MediaAppTheme.apply(value),theme);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,theme+' '+width);
  }
  await page.evaluate(()=>MediaAppTheme.apply('midnight-glass'));
  await page.locator('#connection-flow').screenshot({path:`/tmp/mediaseed-step3-vpn-${width}.png`});
  // Expiring evidence must clear *all* verified labels, traffic and shield motion.
  await page.evaluate(()=>{vpnFreshUntil=Date.now()-1;expireConnection();});
  assert.equal(await vpnFlow.getAttribute('data-state'),'unknown');assert.match(await page.locator('#vpn-status').textContent(),/kan ikke verificeres/);
  assert.equal(await vpnFlow.getAttribute('data-download'),'false');
  assert.equal(await vpnFlow.locator('.connection-vpn .connection-icon').evaluate(e=>getComputedStyle(e,'::before').animationName),'none');
  await page.evaluate(()=>renderConnection({provider:'mediavpn',ready:false,downloadsEnabled:false}));assert.equal(await vpnFlow.getAttribute('data-state'),'blocked');
  await page.evaluate(()=>renderConnection({provider:null,ready:false}));assert.equal(await vpnFlow.getAttribute('data-state'),'disconnected');
  assert.equal(writes,0,'Rendering and polling must not change a VPN');
  // Explicit save alone shows connecting, while fresh background checks stay blocked.
  await page.evaluate(()=>refreshVpn());putDelay=1200;
  await page.locator('#vpn-connect').click();
  await page.waitForFunction(()=>document.querySelector('#connection-flow').dataset.state==='connecting');
  await page.evaluate(()=>refresh());assert.equal(await vpnFlow.getAttribute('data-state'),'connecting');
  assert.equal(await page.locator('#vpn-downloads').textContent(),'Blokeret');
  await page.waitForFunction(()=>document.querySelector('#connection-flow').dataset.state==='active');assert.equal(writes,1);
  await page.evaluate(()=>{document.documentElement.dataset.reducedMotion='true';});
  assert.equal(await vpnFlow.locator('.connection-vpn .connection-icon').evaluate(e=>getComputedStyle(e,'::before').animationName),'none');
  await page.evaluate(()=>delete document.documentElement.dataset.reducedMotion);await page.emulateMedia({reducedMotion:'reduce'});
  assert.equal(await vpnFlow.locator('.connection-vpn .connection-icon').evaluate(e=>getComputedStyle(e,'::before').animationName),'none');
  await page.emulateMedia({reducedMotion:'no-preference'});
  await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));});
  assert.equal(await vpnFlow.locator('.connection-vpn .connection-icon').evaluate(e=>getComputedStyle(e,'::before').animationPlayState),'paused');
  assert.equal(await page.locator('#seed-overview').count(),0);assert.deepEqual(errors,[]);
  console.log(JSON.stringify({width,states:5,freshEvidence:true,foldableDetails:true,keyboardFocus:true,themes:7,reducedMotion:true,backgroundPaused:true,explicitSaveOnly:true,errors}));await page.close();
 }
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
