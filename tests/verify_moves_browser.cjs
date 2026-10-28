const {chromium}=require(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
const assert=require('node:assert/strict');

(async()=>{
 const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
 for(const width of [1440,390,320]){
  const page=await browser.newPage({viewport:{width,height:1000}}),errors=[],writes=[];
  const now=Date.now()/1000,rows=['a','b'].map((h,i)=>({hash:h.repeat(40),name:`Synthetic.Movie.${i+1}.DANiSH.1080p.Long.Release.Name`,size:100,progress:1,ratio:0,state:'stalledUP',added_on:100,seeding_time:400,tracker_name:'Synthetic'}));
  let job=null;
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{window.EventSource=class extends EventTarget{close(){}};});
  await page.route('**/api/**',async route=>{
   const req=route.request(),path=new URL(req.url()).pathname;let result=[];
   if(path==='/api/auth/status')result={needsSetup:false};
   else if(path==='/api/auth/me')result={username:'synthetic',role:'administrator',language:'da',csrf:'synthetic'};
   else if(path==='/api/dashboard')result={runtime:{ready:true,downloadsEnabled:true,checkedAt:now},torrents:rows,queue:{active:0,limit:8,waiting:0}};
   else if(path==='/api/storage')result={defaultLocation:'downloads',locations:[{id:'downloads',label:'Downloads',available:true,writable:true,totalBytes:3e12,freeBytes:2e12},{id:'movies',label:'Film',available:true,writable:true,totalBytes:3e12,freeBytes:2e12}]};
   else if(path==='/api/storage/folders')result={folders:[{name:'Genre',writable:true}],writable:true};
   else if(path==='/api/client')result={name:'qBittorrent',version:'v5.2.4'};
   else if(path==='/api/ratio-scout')result={settings:{enabled:false,automatic:false},candidates:[],scanMinutes:10,resultLimit:10,dailyUsed:0};
   else if(path==='/api/torrent-moves'){
    if(req.method()==='POST'){
     writes.push(req.postDataJSON());job={id:'c'.repeat(32),created:now,status:'verifying',location:'movies',folder:'',error:null,items:rows.map(row=>({hash:row.hash,name:row.name,phase:'verifying',bytes:100,checkedBytes:50,fileCount:1,verifiedFiles:0}))};result=job;
    }else result=job?[job]:[];
   }
   await route.fulfill({json:result});
  });
  await page.goto((process.env.MEDIASEED_TEST_ORIGIN || 'http://127.0.0.1:15412')+'/static/index.html');
  await page.locator('#torrents .torrent-move-select input').first().waitFor();
  assert.equal(await page.getByRole('button',{name:'Flyt valgte',exact:true}).isDisabled(),true);
  await page.locator('#torrents .torrent-move-select input').nth(0).check();
  await page.locator('#torrents .torrent-move-select input').nth(1).check();
  await page.getByRole('button',{name:'Flyt valgte (2)',exact:true}).click();
  const dialog=page.locator('#torrent-move-dialog');
  await dialog.getByRole('button',{name:'Flyt og kontrollér',exact:true}).waitFor();
  assert.equal(await dialog.getByLabel('Lagerplacering').inputValue(),'movies');
  await dialog.getByLabel('Undermappe').selectOption('Genre');
  await dialog.getByText('Film /Genre',{exact:true}).waitFor();
  await dialog.getByRole('button',{name:'Overordnet mappe',exact:true}).click();
  await dialog.getByText('Film /',{exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
  await page.screenshot({path:`/tmp/mediaseed-move-${width}.png`,fullPage:true});
  await dialog.getByRole('button',{name:'Flyt og kontrollér',exact:true}).click();
  await dialog.locator('.torrent-move-progress').waitFor();
  assert.equal(writes.length,1);assert.deepEqual(writes[0],{hashes:rows.map(r=>r.hash),location:'movies',folder:''});
  assert.equal(await dialog.locator('progress').count(),2);
  job.status='complete';for(const row of job.items){row.phase='complete';row.verifiedFiles=1;}
  await dialog.getByText('Flyttet og kontrolleret',{exact:true}).first().waitFor();
  await page.getByRole('button',{name:'Flyt valgte',exact:true}).waitFor();
  await dialog.getByRole('button',{name:'Luk',exact:true}).click();
  const menu=page.locator('#torrents .torrent-menu').first();await menu.locator('summary').click();
  await menu.getByRole('button',{name:'Flyt filer…',exact:true}).click();
  assert.equal(await page.locator('#torrent-move-dialog .torrent-move-names li').count(),1);
  assert.deepEqual(errors,[]);await page.close();
 }
 await browser.close();console.log('PASS move selection, one/multiple torrents, folder browser, submitted identities, verification, desktop/mobile layout');
})().catch(error=>{console.error(error);process.exit(1);});
