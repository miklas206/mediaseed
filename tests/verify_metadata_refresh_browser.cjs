const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'@playwright/test');
const assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
try{for(const width of [1440,390,320]){
 const context=await browser.newContext({viewport:{width,height:1000}}),page=await context.newPage();
 const errors=[],mutations=[],hash='a'.repeat(40);let role='administrator',fail=false;
 const row={hash,name:'Our.War.S02E03.The.Lost.Platoon.MDTECH',state:'stalledUP',size:1024,progress:1,ratio:0,ratio_limit:1,added_on:100,tracker_provider:'nordicbytes'};
 page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{window.EventSource=class extends EventTarget{close(){}};});
 await page.route('**/api/**',async route=>{
  const req=route.request(),p=new URL(req.url()).pathname;let result=[];
  if(req.method()!=='GET')mutations.push(p);
  if(p==='/api/auth/status')result={needsSetup:false};
  else if(p==='/api/auth/me')result={username:'synthetic',role,language:'da',csrf:'synthetic'};
  else if(p==='/api/dashboard')result={runtime:{downloadsEnabled:true,checkedAt:Date.now()/1000},torrents:[row],queue:{active:0,limit:8,waiting:0}};
  else if(p==='/api/storage')result={defaultLocation:'local',locations:[]};
  else if(p==='/api/ratio-scout')result={settings:{enabled:false,automatic:false},candidates:[]};
  else if(p.endsWith('/metadata/refresh')){
    assert.equal(req.method(),'POST');assert.equal(req.headers()['x-mediaseed-csrf'],'synthetic');
    await route.fulfill({status:fail?429:202,json:fail?{detail:'Tracker refresh already requested; try again in five minutes'}:{status:'queued'}});return;
  }
  else if(p.endsWith('/details'))result={properties:{total_downloaded:0,total_uploaded:0},files:[],trackers:[],peers:[],credit:{}};
  await route.fulfill({json:result});
 });
 await page.goto('http://127.0.0.1:15386/static/index.html');
 const menu=page.locator('#torrents .torrent-menu').first();await menu.locator('summary').click();
 await menu.getByRole('button',{name:/Genopfrisk data fra tracker/}).click();
 await page.waitForFunction(()=>document.querySelector('#message').textContent.includes('Genopfriskning er sat i kø'));
 await page.locator('#torrents .torrent-name').click();
 const button=page.locator('#detail-data').getByRole('button',{name:'Genopfrisk data fra tracker',exact:true});await button.waitFor();
 fail=true;await button.click();await page.waitForFunction(()=>document.querySelector('#message').textContent.includes('Prøv igen om fem minutter'));
 assert(await button.isEnabled());assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
 assert.deepEqual(mutations,[`/api/torrents/${hash}/metadata/refresh`,`/api/torrents/${hash}/metadata/refresh`]);
 role='viewer';await page.reload();await menu.locator('summary').click();assert.equal(await menu.getByRole('button',{name:/Genopfrisk data fra tracker/}).count(),0);
 assert.deepEqual(errors,[]);console.log(`Metadata refresh ${width}px: menu/details, CSRF POST, success/error translations, viewer restriction, no horizontal overflow.`);await context.close();
}}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
