// Synthetic API only: no live torrents or file deletion.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
 for(const width of [1440,390]){
  const page=await browser.newPage({viewport:{width,height:1000}}),errors=[],writes=[],removed=new Set();
  const now=Date.now()/1000,rows=['a','b','d'].map((h,i)=>({hash:h.repeat(40),name:`Synthetic.Movie.${i+1}.DANiSH.1080p.Long.Release.Name`,size:100,progress:i===2?.5:1,ratio:0,state:i===2?'downloading':'stalledUP',added_on:100,seeding_time:400,tracker_name:'Synthetic'}));
  let job=null,failRemoval=false,early=true;
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
  const panel=page.locator('#torrent-action-panel'),dialog=page.locator('#torrent-move-dialog');
  await page.locator('#torrents .torrent-move-select input').first().waitFor();
  for(const deleteFiles of [false,true]) {
    await page.getByRole('button',{name:'Vælg viste',exact:true}).click();
    await panel.getByRole('button',{name:'Fjern / slet',exact:true}).click();
    const choice=deleteFiles?'Slet torrents og filer…':'Fjern torrents · behold filer';
    await panel.getByRole('button',{name:choice,exact:true}).click();
    assert.deepEqual(await dialog.locator('.torrent-move-names li').allTextContents(),rows.map(r=>r.name));
    await dialog.getByText('Dette fjerner også torrents',{exact:false}).waitFor();
    await dialog.getByRole('button',{name:'Annuller',exact:true}).click();
    assert.equal(writes.length,0,'Cancel sends no removal requests');
    await panel.getByRole('button',{name:choice,exact:true}).click();
    const submit=dialog.getByRole('button',{name:deleteFiles?'Slet torrents og filer':choice,exact:true});
    if(deleteFiles){assert.equal(await submit.isDisabled(),true);await dialog.getByRole('checkbox').check();}
    await submit.click();
    await page.waitForFunction(()=>document.querySelectorAll('#torrents [data-hash]').length===0);
    assert.equal(await page.locator('#early-seeding[open]').count(),0);
    assert.equal(writes.length,3,'Exactly one request per selected torrent');
    assert.ok(writes.every(w=>w.delete_files===deleteFiles && w.confirm_early===true));
    assert.deepEqual(writes.map(w=>w.torrent_hash),rows.map(r=>r.hash));
    removed.clear();writes.length=0;await page.reload();
    await page.locator('#torrents .torrent-move-select input').first().waitFor();
  }
  assert.deepEqual(errors,[]);await page.close();
 }
 await browser.close();console.log('PASS single batch confirmation, all selected names, early seeding, cancellation, keep/delete files; desktop/mobile');
})().catch(error=>{console.error(error);process.exit(1);});
