// Real UI, synthetic responses only. No torrent/VPN/production access.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'@playwright/test');
const assert=require('node:assert/strict');
const origin=process.env.MEDIASEED_TEST_ORIGIN||'http://127.0.0.1:15398';
(async()=>{
 const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
 for(const width of [1440,390,320]){
  const page=await browser.newPage({viewport:{width,height:1000}}),errors=[];
  let writes=0,queued=false,role='administrator',mode='rows';
  const proposal={id:'a'.repeat(64),video:{name:'Synthetic.Movie.2020.2160p.HDR.mkv',resolution:'3840×2160'},donor:{name:'Synthetic.Movie.2020.1080p.mkv',resolution:'1920×1080'},audio:'TrueHD Atmos 7.1 · en',replaces:['Dolby Digital Plus Atmos 5.1 · en']};
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{window.EventSource=class extends EventTarget{close(){}};});
  await page.route('**/api/**',async route=>{
   const req=route.request(),path=new URL(req.url()).pathname;
   if(req.method()!=='GET')writes++;
   let data=[];
   if(path==='/api/auth/status')data={needsSetup:false};
   if(path==='/api/auth/me')data={username:'synthetic',role,language:'da',csrf:'synthetic'};
   if(path==='/api/dashboard')data={runtime:{ready:true,downloadsEnabled:true},queue:{active:0,limit:8,waiting:0},torrents:[]};
   if(path==='/api/conversions')data={ready:false,reason:'Synthetic GPU is unavailable',jobs:queued?[{id:'b'.repeat(32),kind:'mux',name:proposal.video.name,status:'muxing',fileCount:1,progress:42,bitrate:0,donorName:proposal.donor.name,muxAudio:proposal.audio,destination:'/synthetic/Konverteret/Mux',originalBytes:1000}]:[]};
   if(path==='/api/conversions/recommendations')data={scanned:false,rows:[]};
   if(path==='/api/conversions/mux/recommendations'){
    if(mode==='error')return route.fulfill({status:503,json:{detail:'Synthetic unavailable storage'}});
    data={ready:true,rows:!queued&&mode==='rows'?[proposal]:[],scanned:true,total:2,pending:mode==='pending'?2:0,errors:0};
   }
   if(path==='/api/conversions/mux/analyze')data={accepted:true,files:2};
   if(path==='/api/conversions/mux'){
    const body=req.postDataJSON();assert.equal(body.proposal,proposal.id);assert.equal(body.confirmed_same_cut_and_sync,true);assert.equal(body.location,'local');assert.equal(body.delay_ms,150);queued=true;data={id:'b'.repeat(32)};
   }
   if(path==='/api/storage')data={defaultLocation:'local',locations:[{id:'local',label:'Synthetic local storage',available:true,writable:true}]};
   if(path==='/api/activity')data={entries:[]};
   await route.fulfill({json:data});
  });
  await page.goto(origin+'/static/index.html');
  await page.locator('[data-view=conversions]').click();
  await page.locator('#conversion-mux-recommendations').getByText(proposal.video.name,{exact:true}).waitFor();
  assert.equal(writes,0,'Suggestions must not queue or mutate torrents');
  await page.locator('#conversion-mux-analyze').click();
  await page.getByRole('button',{name:'Vælg kombination',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'Kombinér billede og lyd'});
  await dialog.waitFor();
  await dialog.getByRole('button',{name:'Opret mux-kopi'}).click();
  assert.equal(writes,1,'Required cut/sync confirmation prevents accidental submission');
  assert.equal(await dialog.isVisible(),true);
  await dialog.locator('input[type=number]').fill('150');
  await dialog.locator('input[type=checkbox]').check();
  assert.equal(await dialog.evaluate(e=>e.scrollWidth>e.clientWidth+1),false,'Dialog must fit mobile');
  if(width!==320)await dialog.screenshot({path:'/tmp/mediaseed-mux-dialog-'+width+'.png'});
  await dialog.getByRole('button',{name:'Opret mux-kopi'}).click();
  await dialog.waitFor({state:'hidden'});
  await page.locator('#conversion-active').getByText(/Muxer · Mux uden genkodning/).waitFor();
  assert.equal(await page.locator('#conversion-active').getByText(/0 Mbit/).count(),0);
  assert.equal(await page.locator('#conversion-active progress').getAttribute('value'),'42');
  await page.locator('#conversion-mux-panel > summary').click();
  assert.equal(await page.locator('#conversion-mux-analyze').isVisible(),false);
  await page.locator('#conversion-mux-panel > summary').focus();await page.keyboard.press('Enter');
  assert.equal(await page.locator('#conversion-mux-analyze').isVisible(),true);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,'Page must fit mobile');
  assert.equal(writes,2);assert.deepEqual(errors,[]);
  // Polling errors and analysis progress remain visible, without suggesting an unsafe action.
  mode='pending';await page.evaluate(()=>window.dispatchEvent(new Event('conversion-refresh')));
  await page.locator('#conversion-mux-state').getByText(/2 afventer metadata/).waitFor();
  mode='error';await page.evaluate(()=>window.dispatchEvent(new Event('conversion-refresh')));
  await page.locator('#conversion-mux-state').getByText(/Kunne ikke hente mux-forslag/).waitFor();
  mode='rows';queued=false;role='viewer';
  await page.reload();await page.locator('[data-view=conversions]').click();
  await page.locator('#conversion-mux-recommendations').getByText(proposal.video.name,{exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'Vælg kombination',exact:true}).count(),0);
  assert.equal(await page.locator('#conversion-mux-analyze').isVisible(),false);
  assert.deepEqual(errors,[]);console.log(JSON.stringify({width,mux:true,explicitConfirmation:true,viewer:true,writes,errors}));
  await page.close();
 }
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
