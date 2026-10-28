// Synthetic API only: no live torrents or file deletion.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
 for(const width of [1600,1440,1100,768,390,320]){
  const page=await browser.newPage({viewport:{width,height:1000}}),errors=[],writes=[],removed=new Set();
  const now=Date.now()/1000,rows=['a','b','d'].map((h,i)=>({hash:h.repeat(40),name:`Synthetic.Movie.${i+1}.DANiSH.1080p.Long.Release.Name`,size:100,progress:i===2?.5:1,ratio:0,state:i===2?'downloading':'stalledUP',added_on:100,seeding_time:400,tracker_name:'Synthetic'}));
  let job=null,failRemoval=false,early=false;
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{window.EventSource=class extends EventTarget{close(){}};localStorage.setItem('mediahub-app-theme-v2','duck-pond');});
  await page.route('**/api/**',async route=>{
   const req=route.request(),path=new URL(req.url()).pathname;let result=[];
   if(path==='/api/auth/status')result={needsSetup:false};
   else if(path==='/api/auth/me')result={username:'synthetic',role:'administrator',language:'da',csrf:'synthetic'};
   else if(path==='/api/dashboard')result={runtime:{ready:true,downloadsEnabled:true,checkedAt:now},torrents:rows.filter(r=>!removed.has(r.hash)),queue:{active:0,limit:8,waiting:0}};
   else if(path==='/api/storage')result={defaultLocation:'downloads',locations:[{id:'downloads',label:'Downloads',available:true,writable:true,totalBytes:3e12,freeBytes:2e12},{id:'movies',label:'Film',available:true,writable:true,totalBytes:3e12,freeBytes:2e12}]};
   else if(path==='/api/storage/folders')result={folders:[{name:'Genre',writable:true}],writable:true};
   else if(path==='/api/client')result={name:'qBittorrent',version:'v5.2.4'};
   else if(path==='/api/ratio-scout')result={settings:{enabled:false,automatic:false},candidates:[],scanMinutes:10,resultLimit:10,dailyUsed:0};
   else if(path==='/api/torrent-moves'){
    if(req.method()==='POST'){
     const data=req.postDataJSON();writes.push({path,...data});job={id:'c'.repeat(32),created:now,status:'verifying',location:'movies',folder:'',error:null,items:rows.filter(row=>data.hashes.includes(row.hash)).map(row=>({hash:row.hash,name:row.name,phase:'verifying',bytes:100,checkedBytes:50,fileCount:1,verifiedFiles:0}))};result=job;
    }else result=job?[job]:[];
   }else if(path==='/api/torrents/remove'){
    const data=req.postDataJSON();writes.push({path,...data});
    if(failRemoval)return route.fulfill({status:503,json:{detail:'Synthetic client unavailable'}});
    if(early&&!data.confirm_early)return route.fulfill({status:409,json:{detail:'Early stop: Explicit confirmation required.'}});
    removed.add(data.torrent_hash);result={accepted:true};
   }else if(path.endsWith('/presence'))result={present:!removed.has(path.split('/')[3])};
   await route.fulfill({json:result});
  });
  await page.goto((process.env.MEDIASEED_TEST_ORIGIN || 'http://127.0.0.1:15412')+'/static/index.html');
  const boxes=page.locator('#torrents .torrent-move-select input'),panel=page.locator('#torrent-action-panel');
  await boxes.first().waitFor();assert.equal(await panel.isVisible(),false);
  assert.equal(await boxes.nth(2).isEnabled(),true,'Incomplete downloads can be selected for removal');
  await boxes.nth(0).check();await boxes.nth(1).check();
  await panel.getByRole('button',{name:'Flyt og kontrollér',exact:true}).waitFor();
  const geometry=await boxes.first().evaluate(el=>{const row=el.closest('[data-hash]').getBoundingClientRect(),box=el.getBoundingClientRect();return {rowRight:row.right,boxRight:box.right};});
  assert.ok(geometry.rowRight-geometry.boxRight<35,'Checkbox is at right edge');
  assert.equal(await panel.getByLabel('Lagerplacering').inputValue(),'movies');
  await panel.getByLabel('Undermappe').selectOption('Genre');
  await panel.getByText('Film /Genre',{exact:true}).waitFor();
  await panel.getByRole('button',{name:'Overordnet mappe',exact:true}).click();
  await panel.getByText('Film /',{exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
  await page.screenshot({path:`/tmp/mediaseed-sidebar-${width}.png`,fullPage:true});
  await panel.getByRole('button',{name:'Flyt og kontrollér',exact:true}).click();
  await panel.locator('.torrent-move-progress').waitFor();
  assert.deepEqual(writes[0],{path:'/api/torrent-moves',hashes:rows.slice(0,2).map(r=>r.hash),location:'movies',folder:''});
  assert.equal(await panel.locator('progress').count(),2);
  job.status='complete';for(const row of job.items){row.phase='complete';row.verifiedFiles=1;}
  await panel.getByText('Flyttet og kontrolleret',{exact:true}).first().waitFor();
  await panel.getByRole('button',{name:'Luk',exact:true}).click();
  const menu=page.locator('#torrents .torrent-menu').first();await menu.locator('summary').click();
  await menu.getByRole('button',{name:'Flyt filer…',exact:true}).click();
  assert.equal(await panel.locator('.torrent-move-names li').count(),1);
  await panel.getByRole('button',{name:'Luk',exact:true}).click();
  await page.getByRole('button',{name:'Vælg viste',exact:true}).click();
  await panel.getByText('Flytning kræver færdige torrents.',{exact:false}).waitFor();
  await panel.getByRole('button',{name:'Fjern / slet',exact:true}).click();
  await page.screenshot({path:`/tmp/mediaseed-sidebar-remove-${width}.png`,fullPage:true});
  await panel.getByRole('button',{name:'Fjern torrents · behold filer',exact:true}).click();
  let dialog=page.locator('#torrent-move-dialog');
  assert.equal(await dialog.locator('.torrent-move-names li').count(),3);
  const before=writes.length;await dialog.getByRole('button',{name:'Annuller',exact:true}).click();assert.equal(writes.length,before);
  // Partial failures retain selected rows so they can be retried; no automatic file deletion.
  failRemoval=true;
  await panel.getByRole('button',{name:'Fjern torrents · behold filer',exact:true}).click();
  await dialog.getByRole('button',{name:'Fjern torrents · behold filer',exact:true}).click();
  await panel.getByText('3 valgte · 300 B',{exact:true}).waitFor();
  await panel.locator('.torrent-removal-results').waitFor();assert.equal(await panel.locator('.torrent-removal-results li').count(),3);
  assert.ok(writes.filter(w=>w.path==='/api/torrents/remove').every(w=>w.delete_files===false));
  failRemoval=false;
  // Keep-files operation on two; then destructive choice on the unfinished third.
  await boxes.nth(2).uncheck();
  await panel.getByRole('button',{name:'Fjern torrents · behold filer',exact:true}).click();
  await dialog.getByRole('button',{name:'Fjern torrents · behold filer',exact:true}).click();
  await page.waitForFunction(()=>document.querySelectorAll('#torrents [data-hash]').length===1);
  assert.equal(removed.size,2);
  await page.locator('#torrents .torrent-move-select input').check();
  await panel.getByRole('button',{name:'Slet torrents og filer…',exact:true}).click();
  const submit=dialog.getByRole('button',{name:'Slet torrents og filer',exact:true});
  assert.equal(await submit.isDisabled(),true);
  const consent=dialog.getByRole('checkbox');await consent.check();assert.equal(await submit.isEnabled(),true);
  await page.screenshot({path:`/tmp/mediaseed-sidebar-confirm-${width}.png`,fullPage:true});
  const beforeDelete=writes.length;await dialog.getByRole('button',{name:'Annuller',exact:true}).click();assert.equal(writes.length,beforeDelete);
  early=true;
  await panel.getByRole('button',{name:'Slet torrents og filer…',exact:true}).click();await dialog.getByRole('checkbox').check();await submit.click();
  await page.locator('#early-seeding[open]').waitFor();await page.locator('#confirm-early').click();
  await page.waitForFunction(()=>document.querySelectorAll('#torrents [data-hash]').length===0);
  assert.equal(removed.size,3);
  const destructive=writes.filter(w=>w.delete_files===true);assert.equal(destructive.length,2);assert.equal(destructive[1].confirm_early,true);assert.equal(destructive[1].torrent_hash,rows[2].hash);
  assert.deepEqual(errors,[]);await page.close();
 }
 // Viewer must not receive selection or bulk removal controls.
 const viewer=await browser.newPage();
 await viewer.addInitScript(()=>{window.EventSource=class extends EventTarget{close(){}};});
 await viewer.route('**/api/**',route=>{const path=new URL(route.request().url()).pathname;return route.fulfill({json:path.endsWith('/auth/status')?{needsSetup:false}:path.endsWith('/auth/me')?{username:'viewer',role:'viewer',language:'da',csrf:'synthetic'}:path.endsWith('/dashboard')?{runtime:{},torrents:[{hash:'a'.repeat(40),name:'Synthetic',progress:1,state:'stalledUP'}],queue:{}}:[]});});
 await viewer.goto((process.env.MEDIASEED_TEST_ORIGIN || 'http://127.0.0.1:15412')+'/static/index.html');
 await viewer.locator('#torrents [data-hash]').waitFor();assert.equal(await viewer.locator('#torrents .torrent-move-select').isVisible(),false);assert.equal(await viewer.locator('#torrent-action-panel').isVisible(),false);
 await browser.close();console.log('PASS sidebar, right-side selection, folder browser, verified moves, keep/delete confirmation, cancellation, partial failure, early-seeding gate, viewer restrictions; desktop/tablet/mobile');
})().catch(error=>{console.error(error);process.exit(1);});
