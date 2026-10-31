const {chromium}=require(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
 const origin=process.env.MEDIASEED_TEST_ORIGIN || 'http://127.0.0.1:15391';
 for(const [width,role] of [[1440,'administrator'],[390,'administrator'],[320,'viewer']]){
  const page=await browser.newPage({viewport:{width,height:900}}),errors=[],writes=[];
  let unavailable=false;
  let settings={maxConnections:500,maxConnectionsPerTorrent:100,maxUploadSlots:20,maxUploadSlotsPerTorrent:4,maxActiveDownloads:8,maxActiveUploads:-1};
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{window.EventSource=class extends EventTarget{close(){}};});
  await page.route('**/api/**',async route=>{
   const req=route.request(),path=new URL(req.url()).pathname; let result=[];
   if(path==='/api/auth/status')result={needsSetup:false};
   else if(path==='/api/auth/me')result={username:'synthetic',role,language:'da',csrf:'synthetic'};
   else if(path==='/api/dashboard')result={runtime:{ready:true,downloadsEnabled:true},queue:{active:0,limit:8,waiting:0},torrents:[]};
   else if(path==='/api/storage')result={defaultLocation:'local',locations:[]};
   else if(path==='/api/client')result={name:'qBittorrent',version:'v5.2.4'};
   else if(path==='/api/ratio-scout')result={settings:{enabled:false,automatic:false},candidates:[]};
   else if(path==='/api/rss-settings')result={rssRefreshMinutes:5};
   else if(path==='/api/transfer-settings'){
    if(unavailable)return route.fulfill({status:409,json:{detail:'Update MediaVPN to 0.2.35 or newer to manage connections and queues.'}});
    if(req.method()==='PUT'){settings=req.postDataJSON();writes.push(settings);}
    result=settings;
   }
   await route.fulfill({json:result});
  });
  await page.goto(origin+'/static/index.html');
  await page.waitForFunction(()=>!document.querySelector('#workspace').hidden);
  // Open the same dialog through its application button.
  await page.locator('.app-settings-button').click();
  const form=page.locator('#transfer-settings-form'), button=page.locator('#save-transfer-settings');
  await page.waitForFunction(()=>document.querySelector('#transfer-maxUploadSlotsPerTorrent').value==='4');
  assert.equal(await form.locator('[data-transfer-field]').count(),6);
  assert.equal(await form.locator('[data-transfer-field="maxActiveUploads"] [type="checkbox"]').isChecked(),true);
  assert.equal(await page.locator('#transfer-maxActiveUploads').isDisabled(),true);
  if(role==='administrator'){
   await page.locator('#transfer-maxUploadSlotsPerTorrent').fill('16');
   await button.click();
   await page.waitForFunction(()=>document.querySelector('#save-transfer-settings').disabled===false);
   assert.equal(writes.length,1);assert.equal(writes[0].maxUploadSlotsPerTorrent,16);assert.equal(writes[0].maxActiveUploads,-1);
   await form.locator('[data-transfer-field="maxConnections"] [type="checkbox"]').check();
   await button.click();
   await page.waitForFunction(()=>document.querySelector('#save-transfer-settings').disabled===false);
   assert.equal(writes[1].maxConnections,-1);
   await page.locator('.app-settings-close').click();await page.locator('.app-settings-button').click();
   await page.waitForFunction(()=>document.querySelector('#transfer-maxUploadSlotsPerTorrent').value==='16');
  }else{assert.equal(await button.isDisabled(),true);assert.equal(writes.length,0);}
  const overflow=await form.evaluate(el=>el.scrollWidth>el.clientWidth+1);assert.equal(overflow,false);
  await page.screenshot({path:`/tmp/transfer-settings-${width}.png`,fullPage:true});
  await page.locator('.app-settings-close').click();unavailable=true;
  await page.locator('.app-settings-button').click();
  await page.waitForFunction(()=>document.querySelector('#transfer-settings-status').textContent.includes('0.2.35'));
  assert.equal(await button.isDisabled(),true);assert.equal(errors.length,0,errors.join('\n'));
  await page.close();
 }
 await browser.close();console.log('Transfer settings: desktop/mobile, save/reopen, unlimited, viewer, old MediaVPN and overflow passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
