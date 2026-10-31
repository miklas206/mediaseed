const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox','--disable-dev-shm-usage']});
 const origin=process.env.MEDIASEED_TEST_ORIGIN||'http://127.0.0.1:15412';
 for(const width of [1920,1440,1024,768,390,320]){
  const page=await browser.newPage({viewport:{width,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{window.EventSource=class extends EventTarget{close(){}};});
  await page.route('**/api/**',async route=>{
   const path=new URL(route.request().url()).pathname;let result=[];
   if(path==='/api/auth/status')result={needsSetup:false};
   else if(path==='/api/auth/me')result={username:'synthetic',role:'administrator',language:'da',csrf:'synthetic'};
   else if(path==='/api/dashboard')result={runtime:{ready:true,downloadsEnabled:true,checkedAt:Date.now()/1000},queue:{active:0,limit:8,waiting:0},torrents:[{hash:'a'.repeat(40),name:'Synthetic.Movie.2025.H265.MDTECH',size:2**30,progress:.09,state:'checkingDL',eta:8640000}]};
   else if(path==='/api/client')result={name:'qBittorrent',version:'synthetic'};
   else if(path==='/api/storage')result={locations:[],defaultLocation:null};
   else if(path==='/api/ratio-scout')result={settings:{enabled:false,automatic:false},candidates:[]};
   await route.fulfill({json:result});
  });
  await page.goto(origin+'/static/index.html');await page.locator('#torrents progress').first().waitFor();
  const results=await page.evaluate(()=>{
   const results=[];
   for(const layout of ['list','cards'])for(const theme of ['midnight-glass','soft-orbit','precision-terminal','floating-layers','edge-signal','classic','light','duck-pond']){
    document.querySelector('#workspace').dataset.torrentLayout=layout;MediaAppTheme.apply(theme);
    for(const row of [
     {state:'checkingDL',progress:.09,expected:.09},
     {state:'checkingUP',progress:1,recheck_progress:.09,expected:.09},
     {state:'checkingDL',progress:.09,recheck_progress:null,expected:.09},
     {state:'downloading',progress:.35,eta:120,expected:.35},
     {state:'stalledUP',progress:1,expected:1}
    ]){
     const item=seedTorrent({hash:'b'.repeat(40),name:'Synthetic movie',size:2**30,eta:8640000,...row},1);
     const root=document.querySelector('#torrents');root.replaceChildren(item);
     const track=item.querySelector('.seed-progress-track'),meter=track.querySelector('progress');
     const a=track.getBoundingClientRect(),b=meter.getBoundingClientRect();
     if(row.state.startsWith('checking')){
      if(getComputedStyle(track,'::after').animationName!=='seed-progress-flow')throw Error('Checking motion missing');
      if(parseFloat(getComputedStyle(track,'::after').width)>a.width*meter.value+1)throw Error('Highlight extends beyond measured fill');
      if(!meter.getAttribute('aria-valuetext').includes('Kontrollerer filer'))throw Error('Wrong checking label');
      if(item.querySelector('.seed-progress-cell').textContent.includes('Afventer beregning'))throw Error('Download ETA shown during recheck');
     }
     results.push({layout,theme,state:row.state,value:meter.value,expected:row.expected,trackHeight:a.height,meterHeight:b.height,within:b.top>=a.top-.01&&b.bottom<=a.bottom+.01});
    }
   }
   return results;
  });
  for(const r of results){assert(r.within&&r.trackHeight>0,JSON.stringify({width,...r}));assert(Math.abs(r.value-r.expected)<1e-8,JSON.stringify(r));}
  assert.deepEqual(errors,[]);
  await page.evaluate(()=>{document.querySelector('#workspace').dataset.torrentLayout='list';MediaAppTheme.apply('duck-pond');document.querySelector('#torrents').replaceChildren(seedTorrent({hash:'b'.repeat(40),name:'Synthetic.Movie.2025.H265.MDTECH',size:2**30,state:'checkingDL',progress:.09,eta:8640000},1));});
  if(width===1440||width===390)await page.screenshot({path:`/tmp/mediaseed-progress-${width}.png`,fullPage:true});
  await page.emulateMedia({reducedMotion:'reduce'});
  assert.equal(await page.locator('.seed-progress-track').evaluate(e=>getComputedStyle(e,'::after').display),'none');
  console.log(JSON.stringify({width,cases:results.length,clipping:false,reducedMotion:true,consoleErrors:errors.length}));await page.close();
 }
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
