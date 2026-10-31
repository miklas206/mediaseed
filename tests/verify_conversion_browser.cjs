// Synthetic API observations only: exercise the actual list and its existing actions.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
const assert = require('node:assert/strict');
const origin = process.env.MEDIASEED_TEST_ORIGIN || 'http://127.0.0.1:15392';
(async () => {
 const browser = await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
 for (const width of [1672,1440,1024,768,390,320]) {
  const page = await browser.newPage({viewport:{width,height:1050}}), errors=[];
  let writes=0, requests=0, failure=false, queued=false, bulkQueued=false,preferences={bitrate:30,release_tag:'MDTech',options:{dolby_vision:false}};
  const rows=['Sintel · 4K','Big Buck Bunny · 1080p','Debian · amd64'].map((name,i)=>({hash:String(i+1).repeat(40),name,state:i===0?'downloading':i===1?'uploading':'pausedUP',progress:i===0?.62:1,eta:480,dlspeed:i===0?12400000:0,upspeed:i===1?1300000:0,ratio:i===0?.22:3.96,size:24800000000,completed:15400000000,downloaded:15400000000,uploaded:3400000000,seeding_time:172800,num_seeds:18,num_leechs:7,added_on:1793200000,tracker_name:i===2?'LinuxTracker':'Synthetic',cleanup:{mode:'either',ratio:1,hours:48},cover:'/api/covers/'+String(i+1).repeat(40)}));
  await page.addInitScript(()=>{localStorage.setItem('mediaseed.torrentLayout','cards');window.EventSource=class extends EventTarget {close(){}};});
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/**',async route=>{
   const req=route.request(),url=new URL(req.url()),path=url.pathname;let data=[];
   if(req.method()!=='GET')writes++;
   if(path.startsWith('/api/covers/'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="54" height="78"><rect width="54" height="78" fill="#176a65"/><circle cx="27" cy="30" r="18" fill="#f4ca65"/></svg>'});
   if(path==='/api/auth/status')data={needsSetup:false};
   if(path==='/api/auth/me')data={username:'synthetic',role:'administrator',language:'da',csrf:'synthetic'};
   if(path==='/api/dashboard')data={runtime:{provider:'mediavpn',ready:true,downloadsEnabled:true,checkedAt:Date.now()/1000},queue:{active:1,limit:8,waiting:0},torrents:rows};
   if(path==='/api/conversions') {
    if(req.method()==='POST'){const body=req.postDataJSON();assert.equal(body.options.dolby_vision,false);assert.equal(body.options.preset,'P5');assert.equal(body.folder,'');assert.equal(body.release_tag,'MDTech');if(body.torrent_hash===rows[1].hash){assert.equal(body.bitrate,20);assert.deepEqual(body.indexes,[0]);queued=true;}else{assert.equal(body.torrent_hash,rows[2].hash);assert.equal(body.bitrate,30);assert.deepEqual(body.indexes,[0,1]);bulkQueued=true;}data={id:'synthetic-job'};}
    else data={ready:true,capabilities:['conversion-options-v1'],jobs:queued?[{id:'synthetic-job',name:'Synthetic conversion',status:'complete',bitrate:20,fileCount:1,destination:'/media/tv/HEVC/Synthetic.mkv',potentialSavingBytes:1000000000}]:[]};
   }
   if(path==='/api/conversions'&&req.method()==='GET')data.jobs.push({id:'active',name:'Aktiv testfilm',status:'encoding',bitrate:30,fileCount:1,progress:62,fps:42,etaSeconds:400,currentFile:'Synthetic.mkv',destination:'/synthetic/Konverteret',updated:Date.now()/1000},{id:'waiting',name:'Ventende testfilm',status:'queued',bitrate:30,fileCount:1,queuePosition:1,destination:'/synthetic/Konverteret'});
   if(path==='/api/conversions/analyze')data={accepted:true,files:2};
   if(path==='/api/conversions/recommendations')data={scanned:true,total:2,pending:0,rows:bulkQueued?[]:[0,1].map(index=>({hash:rows[2].hash,index,title:'Synthetic recommendation',name:'Synthetic.Part'+index+'.mkv',originalBytes:60e9,estimatedBytes:30e9,savingBytes:30e9,savingLowBytes:25e9,savingHighBytes:35e9,currentMbps:80,targetMbps:30,codec:'hevc',recommended:true}))};
   if(path.startsWith('/api/conversions/torrents/'))data={name:'Synthetic conversion',files:[{index:0,name:'Synthetic movie.mkv',size:24000000000}],storage:{location:'local',folder:'OriginalRelease'},destinationStorage:{location:'local',folder:''}};
   if(path==='/api/conversions/preferences'){if(req.method()==='PUT')preferences=req.postDataJSON();data=preferences;}
   if(path==='/api/client')data={name:'qBittorrent',version:'synthetic'};
   if(path==='/api/storage')data={defaultLocation:'local',locations:[{id:'local',label:'Local downloads',available:true,writable:true,freeBytes:453e9,totalBytes:1e12}]};
   if(path==='/api/activity')data={entries:[]};
   if(path.endsWith('/details')){
    requests++;if(failure)return route.fulfill({status:503,json:{detail:'Synthetic detail failure'}});
    const section=url.searchParams.get('section');
    data=section==='files'?{files:[{name:'Synthetic episode.mkv',size:1e9,progress:1}]}:section==='connections'?{trackers:[{host:'synthetic.invalid',status:2,num_peers:18}],peers:[]}:{properties:{total_downloaded:154e8,total_uploaded:34e8,addition_date:1793200000},downloadFolder:{path:'/media/tv',label:'Local downloads'},cleanup:{mode:'either',ratio:1,hours:48}};
   }
   await route.fulfill({json:data});
  });
  await page.goto(origin+'/static/index.html');
  const row=()=>page.locator('#torrents [data-hash="'+rows[0].hash+'"]');
  await row().waitFor();await page.locator('#downloads.torrent-actions-ready').waitFor();
  assert.equal(requests,0,'Collapsed rows must not load details');
  assert.equal(await page.locator('#torrents .seed-poster img').count(),3);
  assert.equal(await row().locator('.seed-list-speeds .seed-list-metric').count(),2);
  assert.equal(await row().locator('.seed-list-percent').isVisible(),true);
  assert.match(await row().locator('.seed-list-percent').textContent(),/62%.*8 min/);
  await row().locator('.seed-expand-toggle').click();
  await row().locator('.seed-inline-folder').getByText(/\/media\/tv/).waitFor();
  assert.equal(await row().locator('.seed-expand-toggle').getAttribute('aria-expanded'),'true');
  assert.equal(await row().locator('.seed-inline-facts section').count(),4);
  const tabs=()=>row().locator('.seed-inline-tabs [role=tab]');
  await tabs().nth(1).click();await row().locator('.seed-inline-content').getByText('Synthetic episode.mkv',{exact:true}).waitFor();
  await tabs().nth(2).click();await row().locator('.seed-inline-content').getByText('synthetic.invalid',{exact:true}).waitFor();
  await tabs().nth(2).focus();await page.keyboard.press('Home');
  assert.equal(await tabs().nth(0).getAttribute('aria-selected'),'true');
  await tabs().nth(1).click();await row().locator('.seed-inline-content').getByText('Synthetic episode.mkv',{exact:true}).waitFor();
  const before=requests;rows[0].dlspeed+=123;await page.evaluate(()=>refresh());
  await row().locator('.seed-inline-content').getByText('Synthetic episode.mkv',{exact:true}).waitFor();
  assert.equal(await tabs().nth(1).getAttribute('aria-selected'),'true','Open tab survives torrent updates');
  assert.equal(requests,before,'Live row updates share the existing detail cache');
  await tabs().nth(0).click();
  if(width===1440||width===390){await page.evaluate(()=>{MediaAppTheme.apply('duck-pond');document.querySelector('#sort').value='name';document.querySelector('#sort-direction').value='desc';renderTorrents();});await row().locator('.seed-inline-facts').waitFor();await page.locator('#downloads').screenshot({path:'/tmp/mediaseed-torrent-accordion-'+width+'.png',style:'.topbar,.app-navigation,.pond-motion,.pond-play-controls{visibility:hidden!important}'});}
  for(const theme of ['midnight-glass','soft-orbit','precision-terminal','floating-layers','edge-signal','classic','light','duck-pond']){
   await page.evaluate(theme=>MediaAppTheme.apply(theme),theme);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,'Page overflow '+width+' '+theme);
   const bounds=await row().locator('.seed-inline-details').boundingBox();assert(bounds.x>=0&&bounds.x+bounds.width<=width+1,'Expanded details overflow');
  }
  if(width>=1440){
   const geometry=await row().evaluate(e=>['.torrent-move-select','.seed-expand-toggle','.seed-poster','.seed-identity','.seed-activity','.seed-progress-cell','.seed-list-speeds','.seed-list-ratio','.actions'].map(s=>{const r=e.querySelector(s).getBoundingClientRect();return {s,x:r.x,right:r.right}}));
   const speedBounds=await row().locator('.seed-list-speeds').evaluate(e=>({right:e.getBoundingClientRect().right,children:[...e.children].map(c=>c.getBoundingClientRect().right)}));
   assert(speedBounds.children.every(right=>right<=speedBounds.right+1),'Speed values overflow their column');
   for(let i=1;i<geometry.length;i++)assert(geometry[i-1].right<=geometry[i].x+1,'Column overlap '+width+' '+JSON.stringify(geometry));
  }
  // Batch selection still opens the current move/remove flow and survives polling.
  await row().locator('.torrent-move-select input').check();await page.locator('#torrent-action-panel').waitFor();
  rows[0].upspeed+=1;await page.evaluate(()=>refresh());assert.equal(await row().locator('.torrent-move-select input').isChecked(),true);
  await row().locator('.torrent-move-select input').uncheck();
  // Full detail actions remain available; their existing modal is unchanged.
  await row().locator('.seed-inline-footer').getByRole('button',{name:'Alle detaljer',exact:true}).click();
  await page.locator('#torrent-details[open],dialog[open]').first().waitFor();await page.locator('#close-details').click();
  await tabs().nth(0).focus();await page.keyboard.press('Escape');assert.equal(await row().locator('.seed-expand-toggle').getAttribute('aria-expanded'),'false');
  // A failed detail read is retryable without hiding the row or claiming an empty table.
  failure=true;await row().locator('.seed-expand-toggle').click();await row().locator('.seed-inline-footer').getByRole('button',{name:'Opdater',exact:true}).click();
  await row().locator('.seed-inline-status').getByRole('button',{name:'Prøv igen'}).waitFor();failure=false;
  await row().locator('.seed-inline-status').getByRole('button',{name:'Prøv igen'}).click();await row().locator('.seed-inline-folder').getByText(/\/media\/tv/).waitFor();
  await page.locator('#tracker-filter').selectOption('LinuxTracker');assert.equal(await page.locator('#torrents>.torrent').count(),1);await page.locator('#tracker-filter').selectOption('');
  await page.locator('#search').fill('No such title');assert.equal(await page.locator('#torrents>.torrent').count(),0);await page.locator('#search').fill('');
  assert.equal(await page.locator('[data-layout],.seed-layout-buttons').count(),0);
  assert.equal(await page.locator('#workspace').getAttribute('data-torrent-layout'),'list');
  assert.equal(await page.evaluate(()=>localStorage.getItem('mediaseed.torrentLayout')),null);
  assert.equal(await row().locator('.seed-inline-details').isVisible(),true);
  // Only synthetic transport receives this deliberate conversion request.
  const completed=page.locator('#torrents [data-hash="'+rows[1].hash+'"]');
  await completed.locator('[data-convert-video]').waitFor({state:'attached'});
  await completed.locator('.torrent-menu').evaluate(node=>node.open=true);
  await completed.locator('[data-convert-video]').click();
  await page.locator('#media-conversion-dialog').getByText('Synthetic movie.mkv',{exact:true}).waitFor();
  await page.locator('#media-conversion-dialog .conversion-file input[type=checkbox]').check();
  await page.locator('#media-conversion-dialog select').first().selectOption('20');
  await page.locator('#media-conversion-dialog [data-option=preset]').selectOption('P5');
  assert.equal(await page.locator('#media-conversion-dialog [data-option=dolby_vision]').isChecked(),false);
  await page.locator('#media-conversion-dialog [data-option=audio]').selectOption('languages');
  await page.locator('#media-conversion-dialog [data-option=audio_languages]').fill('dan, eng');
  await page.locator('#media-conversion-dialog [data-option=audio]').selectOption('all');
  await page.locator('#media-conversion-dialog').getByRole('button',{name:'Gem som standard'}).click();
  await page.locator('#media-conversion-dialog').getByText('Standardvalg gemt.',{exact:true}).waitFor();
  assert.equal(preferences.options.preset,'P5');
  if(width===390)await page.locator('#media-conversion-dialog').screenshot({path:'/tmp/mediaseed-conversion-options-390.png'});
  await page.locator('#media-conversion-dialog button[type=submit]').click();
  await page.locator('#conversions').waitFor({state:'visible'});
  assert.equal(await page.locator('#conversion-page-title').isVisible(),true);
  await page.locator('#conversion-active progress').waitFor();
  assert.equal(await page.locator('#conversion-active progress').getAttribute('value'),'62');
  await page.locator('#conversion-queue').getByText(/Plads 1 i køen/).waitFor();
  await page.locator('#conversions details summary').click();
  await page.locator('#conversion-completed').getByText('Færdig · 20 Mbit/s · 1 videoer',{exact:true}).waitFor();
  assert.equal(await page.locator('#conversion-recommendations .conversion-advice-row').count(),2);
  await page.locator('#conversion-analyze').click();
  for(const check of await page.locator('#conversion-recommendations input').all())await check.check();
  await page.locator('#conversion-queue-selected').click();
  await page.getByRole('dialog').locator('[data-option=preset]').selectOption('P5');
  await page.getByRole('dialog').getByRole('button',{name:'Tilføj til kø',exact:true}).click();
  await page.getByRole('dialog').waitFor({state:'hidden'});
  assert.equal(bulkQueued,true);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,'Conversion dashboard overflow '+width);
  if(width===1440||width===390)await page.locator('#conversions').screenshot({path:'/tmp/mediaseed-conversion-dashboard-'+width+'.png',style:'.topbar,.app-navigation,.pond-motion,.pond-play-controls{visibility:hidden!important}'});
  await page.locator('[data-view=downloads]').click();
  await page.evaluate(()=>{appRole='viewer';renderTorrents();MediaSeedWorkspace.render();});assert.equal(await page.locator('#torrents .torrent-toggle').count(),0);assert.equal(await row().locator('.torrent-move-select').isVisible(),false);
  assert.equal(writes,4,'Only explicit single conversion, analysis and bulk conversion are written');assert.deepEqual(errors,[]);
  console.log(JSON.stringify({width,inlineTabs:true,posters:true,updatesAndSelectionPreserved:true,themes:8,retry:true,viewer:true,writes,errors}));await page.close();
 }
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
