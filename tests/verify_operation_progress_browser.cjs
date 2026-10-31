// Synthetic API only: no live torrents or file deletion.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
 for(const width of [1440,390]){
  const page=await browser.newPage({viewport:{width,height:1000}}),errors=[],writes=[],removed=new Set();
  const now=Date.now()/1000,rows=['a','b','d'].map((h,i)=>({hash:h.repeat(40),name:`Synthetic.Movie.${i+1}.DANiSH.1080p.Long.Release.Name`,size:100,progress:i===2?.5:1,ratio:0,state:i===2?'downloading':'stalledUP',added_on:100,seeding_time:400,tracker_name:'Synthetic'}));
  let job=null,failRemoval=false,early=true,moveUnavailable=false;
  const confirmed=new Set(),presenceFailures=new Set();
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
    }else {if(moveUnavailable)return route.fulfill({status:503,json:{detail:'Synthetic status unavailable'}});result=job?[job]:[];}
   }else if(path==='/api/torrents/remove'){
    const data=req.postDataJSON();writes.push({path,...data});
    if(failRemoval)return route.fulfill({status:503,json:{detail:'Synthetic client unavailable'}});
    if(early&&!data.confirm_early)return route.fulfill({status:409,json:{detail:'Early stop: Explicit confirmation required.'}});
    removed.add(data.torrent_hash);result={accepted:true};
   }else if(path.endsWith('/presence')){const hash=path.split('/')[3];if(presenceFailures.has(hash))return route.fulfill({status:503,json:{detail:'Synthetic confirmation unavailable'}});result={present:!confirmed.has(hash)};}
   await route.fulfill({json:result});
  });
  await page.goto((process.env.MEDIASEED_TEST_ORIGIN || 'http://127.0.0.1:15412')+'/static/index.html');
  const panel=page.locator('#torrent-action-panel'),dialog=page.locator('#torrent-move-dialog'),toast=page.locator('#message');
  const waitText=value=>page.waitForFunction(value=>document.querySelector('#message').textContent.includes(value),value);
  const tick=()=>new Promise(resolve=>setTimeout(resolve,100));
  async function reset(){removed.clear();confirmed.clear();presenceFailures.clear();writes.length=0;await page.reload();await page.locator('#torrents .torrent-move-select input').first().waitFor();}
  async function removeBatch(){
    await page.locator('#torrents .torrent-move-select input').first().waitFor();
    await page.getByRole('button',{name:'Vælg viste',exact:true}).click();
    await panel.getByRole('button',{name:'Fjern / slet',exact:true}).click();
    await panel.getByRole('button',{name:'Slet torrents og filer…',exact:true}).click();
    await dialog.getByRole('checkbox').check();await dialog.getByRole('button',{name:'Slet torrents og filer',exact:true}).click();
    for(let i=0;i<100 && writes.length<3;i++)await tick();
    assert.equal(writes.length,3);
  }
  await removeBatch();
  await waitText('Fjerner: 0 af 3 færdige');
  assert.equal(await toast.getAttribute('data-persistent'),'true');
  confirmed.add(rows[0].hash);await waitText('Fjerner: 1 af 3 færdige');
  await toast.dispatchEvent('mouseleave');
  await new Promise(resolve=>setTimeout(resolve,6200));
  assert.match(await toast.textContent(),/Fjerner: 1 af 3 færdige/,'Active progress survives the normal notification timeout');
  confirmed.add(rows[1].hash);await waitText('Fjerner: 2 af 3 færdige');
  await page.screenshot({path:`/tmp/mediaseed-removal-progress-${width}.png`});
  confirmed.add(rows[2].hash);await waitText('Fjernet: 3 af 3 færdige');
  assert.equal(await toast.getAttribute('data-tone'),'success');assert.equal(await toast.getAttribute('data-persistent'),'false');
  assert.equal(await page.locator('#early-seeding[open]').count(),0);
  await reset();await removeBatch();
  presenceFailures.add(rows[1].hash);confirmed.add(rows[0].hash);confirmed.add(rows[2].hash);
  await waitText('Fjernet: 2 af 3 færdige · 1 ikke bekræftet');
  assert.equal(await toast.getAttribute('data-tone'),'danger');
  // A failed request must not count as completed either.
  await reset();failRemoval=true;await removeBatch();
  await waitText('Fjernet: 0 af 3 færdige · 3 ikke bekræftet');failRemoval=false;
  await reset();
  const boxes=page.locator('#torrents .torrent-move-select input');await boxes.nth(0).check();await boxes.nth(1).check();
  await panel.getByRole('button',{name:'Flyt og kontrollér',exact:true}).click();
  await waitText('Flytter: 0 af 2 færdige');
  job.items[0].phase='complete';job.items[0].verifiedFiles=1;
  await waitText('Flytter: 1 af 2 færdige');
  await panel.getByRole('button',{name:'Luk',exact:true}).click();
  moveUnavailable=true;await waitText('afventer opdateret status');
  assert.match(await toast.textContent(),/1 af 2 færdige/);assert.equal(await toast.getAttribute('data-persistent'),'true');
  moveUnavailable=false;job.status='needs_attention';job.error='Synthetic move error';
  await waitText('Flytter: 1 af 2 færdige · kræver kontrol');assert.equal(await toast.getAttribute('data-tone'),'danger');
  // Resume is picked up through background polling, even with the panel closed.
  job.status='verifying';job.error=null;
  await waitText('Flytter: 1 af 2 færdige');
  job.items[1].phase='complete';job.items[1].verifiedFiles=1;job.status='complete';
  await waitText('Flyttet: 2 af 2 færdige');assert.equal(await toast.getAttribute('data-tone'),'success');
  assert.deepEqual(errors,[]);await page.close();
 }
 await browser.close();console.log('PASS live confirmed removal/move counts, persistence, partial failures, polling errors, closed panel, desktop/mobile');
})().catch(error=>{console.error(error);process.exit(1);});
