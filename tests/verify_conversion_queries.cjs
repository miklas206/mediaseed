// Real API query parsing and rendering; all media and credentials are synthetic.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE);
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
 const context=await browser.newContext({viewport:{width:1440,height:1050}});
 const base='http://127.0.0.1:15402';
 assert.equal((await context.request.post(base+'/api/auth/setup',{data:{username:'tester',password:'synthetic-password-only'}})).status(),200);
 const login=await context.request.post(base+'/api/auth/login',{data:{username:'tester',password:'synthetic-password-only'}});assert.equal(login.status(),200);
 const {csrf}=await login.json();
 const page=await context.newPage(),errors=[],responses=[];let broken=false;
 page.on('pageerror',e=>errors.push(e.message));
 page.on('response',r=>{if(r.url().includes('/api/conversions/recommendations'))responses.push(r.status());});
 await page.addInitScript(()=>{window.EventSource=class extends EventTarget{close(){}};});
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname;
  if(path.startsWith('/api/conversions')){
   if(broken&&path.endsWith('/recommendations'))return route.fulfill({status:503,json:{detail:'Synthetic recommendation failure'}});
   return route.continue();
  }
  let data=[];
  if(path==='/api/auth/status')data={needsSetup:false};
  if(path==='/api/auth/me')data={username:'tester',role:'administrator',language:'da',csrf};
  if(path==='/api/dashboard')data={runtime:{ready:true,downloadsEnabled:true},queue:{active:0,limit:8,waiting:0},torrents:[]};
  if(path==='/api/activity')data={entries:[]};
  if(path==='/api/storage')data={defaultLocation:'local',locations:[]};
  await route.fulfill({json:data});
 });
 await page.goto(base+'/');await page.locator('#workspace').waitFor({state:'visible'});
 await page.locator('[data-view=conversions]').click();
 await page.locator('#conversion-recommendations').getByText('Synthetic recommendation',{exact:true}).waitFor();
 for(const value of ['20','30','40']){
  await page.locator('#conversion-advice-rate').selectOption(value);
  await page.locator('#conversion-recommendations').getByText(new RegExp('→ '+value+' Mbit/s video')).waitFor();
 }
 assert(!responses.includes(422),'Real URL query parameters must be accepted');
 broken=true;await page.locator('#conversion-refresh').click();
 await page.locator('#conversion-analysis-state').getByText(/Kunne ikke hente forslag/).waitFor();
 assert.match(await page.locator('#conversion-active').textContent(),/Ingen aktiv konvertering/);
 assert.match(await page.locator('#conversion-queue').textContent(),/Køen er tom/);
 broken=false;await page.locator('#conversion-refresh').click();
 await page.locator('#conversion-analysis-state').getByText(/1 anbefalet/).waitFor();
 assert.equal(await page.locator('#conversion-recommendations .conversion-advice-row').count(),1);
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({realApiQueries:[20,30,40],recommendationsRendered:true,queueVisibleDuringAdviceFailure:true,recovery:true,responses,errors}));
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
