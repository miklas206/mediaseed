const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const width of [1440,390,320]){
  const page=await browser.newPage({viewport:{width,height:1000}}),errors=[];let writes=0,removed=false,blocked=false,role='administrator';
  const id='a'.repeat(32),job={id,name:'Synthetic.Movie.2025.2160p.REMUX-Original',status:'complete',bitrate:20,fileCount:1,destination:'/downloads/media/converted-movies/Synthetic.Movie.2025.2160p.H265.MDTECH',potentialSavingBytes:42*2**30,originalRemoval:{}};
  page.on('pageerror',e=>errors.push(e.message));await page.addInitScript(()=>{window.EventSource=class extends EventTarget{close(){}};});
  await page.route('**/api/**',async route=>{
   const path=new URL(route.request().url()).pathname;let result=[],status=200;
   if(path==='/api/auth/status')result={needsSetup:false};
   else if(path==='/api/auth/me')result={username:'synthetic',role,language:'da',csrf:'synthetic'};
   else if(path==='/api/dashboard')result={runtime:{ready:true,downloadsEnabled:true,checkedAt:Date.now()/1000},torrents:[],queue:{}};
   else if(path==='/api/conversions')result={ready:true,jobs:[{...job,originalRemoval:removed?{status:'pending',message:'Fjernelse bestilt. Venter på bekræftelse fra torrentklienten.'}:{}}]};
   else if(path==='/api/conversions/recommendations')result={scanned:true,rows:[]};
   else if(path.endsWith('/original/remove')){const body=route.request().postDataJSON();assert.equal(body.confirmed,true);assert.equal(body.confirmation,'b'.repeat(64));writes++;removed=true;status=202;result={status:'pending',message:'Fjernelse bestilt. Venter på bekræftelse fra torrentklienten.'};}
   else if(path.endsWith('/original')){status=blocked?409:200;result=blocked?{detail:'Start seeding af den konverterede pakke først.'}:{status:'ready',confirmation:'b'.repeat(64),name:job.name,sourceFolder:'/downloads/media/movies/'+job.name,convertedFolder:job.destination,fileCount:2,deleteBytes:65*2**30,files:[job.name+'/'+job.name+'.mkv',job.name+'/Original.nfo']};}
   else if(path==='/api/client')result={name:'qBittorrent',version:'synthetic'};
   else if(path==='/api/storage')result={locations:[],defaultLocation:null};
   else if(path==='/api/ratio-scout')result={settings:{enabled:false},candidates:[]};
   await route.fulfill({status,json:result});
  });
  await page.goto('http://127.0.0.1:15412/static/index.html');await page.locator('[data-view=conversions]').click();await page.locator('#conversion-completed').evaluate(e=>e.closest('details').open=true);
  const remove=page.getByRole('button',{name:'Fjern original',exact:true});await remove.waitFor();
  blocked=true;await remove.click();let dialog=page.getByRole('dialog',{name:'Fjern original'});await dialog.getByText('Start seeding af den konverterede pakke først.').waitFor();assert.equal(writes,0);await dialog.getByRole('button',{name:'Luk',exact:true}).click();
  blocked=false;await remove.click();dialog=page.getByRole('dialog',{name:'Fjern original'});
  const submit=dialog.getByRole('button',{name:'Fjern original og filer'});await submit.waitFor();assert(await submit.isDisabled());assert.equal(writes,0);
  await dialog.getByRole('checkbox').check();assert(await submit.isEnabled());
  for(const theme of ['midnight-glass','soft-orbit','precision-terminal','floating-layers','edge-signal','classic','light','duck-pond']){
   await page.evaluate(theme=>MediaAppTheme.apply(theme),theme);const bounds=await dialog.boundingBox();assert(bounds.x>=0&&bounds.x+bounds.width<=width+1);assert.equal(await dialog.evaluate(e=>e.scrollWidth>e.clientWidth+1),false);
  }
  if(width===1440||width===390)await page.screenshot({path:'/tmp/mediaseed-original-removal-'+width+'.png',fullPage:true});
  await submit.click();await dialog.getByText('Fjernelse bestilt. Venter på bekræftelse fra torrentklienten.').waitFor();assert.equal(writes,1);assert.equal(await dialog.getByRole('button',{name:'Fjern original og filer'}).count(),0);await dialog.getByRole('button',{name:'Luk',exact:true}).click();assert.equal(await remove.count(),0);
  role='viewer';removed=false;await page.reload();await page.locator('[data-view=conversions]').click();await page.locator('#conversion-completed').evaluate(e=>e.closest('details').open=true);await page.locator('.conversion-job').waitFor();assert.equal(await remove.count(),0);assert.equal(writes,1);assert.deepEqual(errors,[]);
  console.log(JSON.stringify({width,blocked:true,confirmationRequired:true,oneSyntheticWrite:writes,viewerProtected:true,themes:8,errors}));await page.close();
 }
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
