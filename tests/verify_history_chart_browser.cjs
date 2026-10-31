// All account history and routes are synthetic. Never writes tracker or torrent state.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'@playwright/test');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
 const origin=process.env.MEDIASEED_TEST_ORIGIN||'http://127.0.0.1:15392';
 for(const width of [1440,390,320]){
  const page=await browser.newPage({viewport:{width,height:1000},ignoreHTTPSErrors:true,hasTouch:true});
  const errors=[],periods=[];let empty=false,fail=false,writes=0;
  const now=Math.floor(Date.now()/900000)*900;
  const samples=Array.from({length:8},(_,i)=>({at:now-(7-i)*900,stats:{ratio:i===3?null:i/10,buffer:(i-2)*2**30,uploaded:(i===6?1:100+i)*2**30,downloaded:50*2**30+i*1024,seedbonus:i*10},gapBefore:i===5}));
  const stats=samples.at(-1).stats;
  const data=()=>({accounts:[{id:'a',name:'Synthetic tracker',status:'ok',stats,checkedAt:now}],history:[{account:'a',sampleInterval:900,samples:empty?[]:samples}],traffic:{status:'ok',stats:{real_uploaded:1024,real_downloaded:2048},sampleInterval:900,samples:samples.map(row=>({...row,stats:{real_uploaded:row.stats.uploaded/2,real_downloaded:row.stats.downloaded*2}}))},alerts:[],settings:{lowBufferGiB:10},results:[],topTorrents:[],torrentDataFresh:true,torrentCheckedAt:now,remainingBytes:0,torrentLimit:500});
  await page.addInitScript(()=>{
   window.EventSource=class extends EventTarget{close(){}};
   const original=window.setInterval;window.__chartCycles=[];
   window.setInterval=(fn,delay,...args)=>{if(delay===10000)window.__chartCycles.push(fn);return original(fn,delay,...args);};
  });
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/**',async route=>{
   const request=route.request(),url=new URL(request.url()),path=url.pathname;let response=[];
   if(request.method()!=='GET')writes++;
   if(path==='/api/auth/status')response={needsSetup:false};
   if(path==='/api/auth/me')response={username:'synthetic',role:'administrator',language:'da',csrf:'synthetic'};
   if(path==='/api/dashboard')response={runtime:{},queue:{active:0,limit:8,waiting:0},torrents:[]};
   if(path==='/api/client')response={name:'qBittorrent',version:'synthetic'};
   if(path==='/api/storage')response={locations:[],defaultLocation:null};
   if(path==='/api/activity')response={entries:[]};
   if(path==='/api/insights'){
    periods.push(url.searchParams.get('days'));
    if(fail)return route.fulfill({status:503,json:{detail:'Synthetic outage'}});
    response=data();
   }
   await route.fulfill({json:response});
  });
  await page.goto(origin+'/static/index.html');await page.locator('#workspace').waitFor({state:'visible'});
  await page.locator('[data-view=trackers]').click();
  const svg=page.locator('.insight-chart svg'),readout=page.locator('.history-readout');await svg.waitFor();
  assert.equal(await page.locator('#insight-cycle').getAttribute('aria-checked'),'false');
  assert.equal(await page.locator('.insight-chart .history-line').count(),1);
  assert((await page.locator('.insight-chart .history-line').getAttribute('d')).match(/M/g).length>=3,'Missing observations must break the path');
  await svg.focus();await page.keyboard.press('Home');assert.match(await readout.textContent(),/0×/);
  await page.keyboard.press('ArrowRight');assert.match(await readout.textContent(),/0,1×/);
  await page.keyboard.press('ArrowRight');await page.keyboard.press('ArrowRight');assert.match(await readout.textContent(),/Manglende måling/);
  await page.keyboard.press('End');assert.match(await readout.textContent(),/0,7×/);
  assert.equal(await svg.getAttribute('aria-valuenow'),'7');
  const calls=periods.length;await page.evaluate(()=>MediaSeedIntelligence.refreshIfVisible());assert.equal(periods.length,calls,'Background refresh should pause during inspection');
  const box=await svg.boundingBox();await page.touchscreen.tap(box.x+9,box.y+box.height*.5);assert.match(await readout.textContent(),/0×/);
  for(const metric of ['ratio','buffer','uploaded','downloaded','seedbonus']){await page.locator('.history-metrics [data-metric='+metric+']').click();assert.equal(await page.locator('.insight-chart .history-metric-label').textContent(),await page.locator('#insight-metric option:checked').textContent());}
  const ratioButton=page.locator('.history-metrics [data-metric=ratio]');await ratioButton.click();await ratioButton.focus();await page.keyboard.press('ArrowRight');
  assert.equal(await page.locator('#insight-metric').inputValue(),'buffer');
  assert.equal(await page.locator('.history-metrics [data-metric=buffer]').evaluate(e=>document.activeElement===e),true,'Keyboard selection must retain focus');
  await page.locator('.history-metrics [data-metric=uploaded]') .click();assert.match(await readout.textContent(),/B/);
  assert((await page.locator('.insight-chart .history-line').getAttribute('d')).match(/M/g).length>=3,'Counter resets must not become a joined slope');
  await page.locator('.insight-chart details summary').click();assert.equal(await page.locator('.insight-chart tbody tr').count(),8);
  await page.locator('.insight-chart details summary').click();
  await page.locator('.history-periods [data-period="30"]') .click();await page.waitForFunction(()=>!document.querySelector('#tracker-history-refresh').disabled);
  assert(periods.includes('30'));assert.equal(await page.locator('.history-periods [data-period="30"]').evaluate(e=>document.activeElement===e),true);
  await page.locator('.history-metrics [data-metric=ratio]') .click();
  await page.locator('#insight-cycle').click();await page.mouse.move(0,0);await page.evaluate(()=>document.activeElement.blur());
  await page.evaluate(()=>window.__chartCycles.forEach(fn=>fn()));assert.equal(await page.locator('#insight-metric').inputValue(),'buffer');
  await svg.focus();await page.evaluate(()=>window.__chartCycles.forEach(fn=>fn()));assert.equal(await page.locator('#insight-metric').inputValue(),'buffer');
  await page.locator('#insight-cycle').click();assert.equal(await page.locator('#insight-cycle').getAttribute('aria-checked'),'false');
  await page.locator('.history-metrics [data-metric=seedbonus]') .click();assert.match(await readout.textContent(),/70 Bonuspoint/);
  for(const theme of ['midnight-glass','soft-orbit','precision-terminal','floating-layers','edge-signal','duck-pond','classic','light']){
   await page.evaluate(value=>MediaAppTheme.apply(value),theme);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,theme+' '+width);
  }
  await page.evaluate(()=>MediaAppTheme.apply('midnight-glass'));await page.locator('.history-metrics [data-metric=ratio]') .click();
  assert.equal(await svg.evaluate(e=>Math.abs(e.viewBox.baseVal.width-e.getBoundingClientRect().width)<1),true,'Axis labels must retain readable sizing on mobile');
  assert.equal(await page.locator('.metric-name').first().evaluate(e=>parseFloat(getComputedStyle(e).fontSize)<14),true,'Compact labels');
  await page.locator('.insight-chart').screenshot({path:`/tmp/mediaseed-step4-chart-${width}.png`});
  await page.emulateMedia({reducedMotion:'reduce'});await page.locator('.history-metrics [data-metric=buffer]') .click();
  assert.equal(await page.locator('.history-series').evaluate(e=>e.getAnimations().length),0);
  fail=true;await page.locator('#tracker-history-refresh').click();await page.locator('#trackers').getByText(/Overblikket kunne ikke hentes|Insights unavailable/).first().waitFor();assert.equal(await svg.isVisible(),true);fail=false;
  empty=true;await page.locator('#tracker-history-refresh').click();await page.waitForFunction(()=>document.querySelector('.insight-chart svg').hasAttribute('hidden'));
  assert.equal(await svg.isVisible(),false);
  empty=false;samples.splice(1);await page.locator('#tracker-history-refresh').click();await svg.waitFor();assert.equal(await svg.getAttribute('aria-valuemax'),'0');assert.equal(await page.locator('.history-series circle').count(),1);
  await page.locator('.history-metrics [data-metric=all]').click();assert.equal(await page.locator('.history-line').count(),7);await page.locator('[data-view=insights]').click();assert.equal(await page.locator('#insights .insight-chart').count(),0);assert.equal(await page.locator('#insights .history-metrics').count(),0);assert.equal(writes,0);assert.deepEqual(errors,[]);
  console.log(JSON.stringify({width,metrics:8,keyboard:true,touch:true,gaps:true,resets:true,table:true,cyclePauses:true,period:true,themes:8,reducedMotion:true,empty:true,error:true,noWrites:true}));await page.close();
 }
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
