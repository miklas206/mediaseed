const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'@playwright/test');
const assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
try{for(const width of [1440,390,320]){
 const context=await browser.newContext({viewport:{width,height:1000},permissions:['clipboard-read','clipboard-write']});
 const page=await context.newPage(),errors=[],mutations=[];let role='administrator';
 const hash='a'.repeat(40),v2='c'.repeat(64),name='Our War · æøå & Episode #1';
 let row={hash,name,state:'stalledUP',size:1024,progress:1,ratio:0,ratio_limit:1,added_on:100,tracker_provider:'nordicbytes',magnet_uri:'magnet:?xt=urn:btih:'+hash+'&tr=https://private.invalid/secret-passkey'};
 page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{window.EventSource=class extends EventTarget{close(){}};});
 await page.route('**/api/**',async route=>{
  const req=route.request(),p=new URL(req.url()).pathname;let result=[];
  if(req.method()!=='GET')mutations.push(p);
  if(p==='/api/auth/status')result={needsSetup:false};
  else if(p==='/api/auth/me')result={username:'synthetic',role,language:'da',csrf:'synthetic'};
  else if(p==='/api/dashboard')result={runtime:{downloadsEnabled:true,checkedAt:Date.now()/1000},torrents:[row],queue:{active:0,limit:8,waiting:0}};
  else if(p==='/api/storage')result={defaultLocation:'local',locations:[]};
  else if(p==='/api/ratio-scout')result={settings:{enabled:false,automatic:false},candidates:[],scanMinutes:10,resultLimit:10,dailyUsed:0};
  await route.fulfill({json:result});
 });
 async function openMenu(){const menu=page.locator('#torrents .torrent-menu').first();await menu.locator('summary').click();return menu;}
 async function copy(){const menu=await openMenu();await menu.getByRole('button',{name:/Kopiér magnetlink/}).click();await page.waitForFunction(()=>document.querySelector('#message').textContent.includes('Magnetlink kopieret'));return page.evaluate(()=>navigator.clipboard.readText());}
 await page.goto('http://127.0.0.1:15386/static/index.html');
 let link=await copy(),params=new URL(link).searchParams;
 assert.equal(params.get('xt'),'urn:btih:'+hash);assert.equal(params.get('dn'),name);assert.deepEqual([...params.keys()],['xt','dn']);assert(!link.includes('secret-passkey'));
 row={...row,infohash_v2:v2};await page.reload();
 link=await copy();params=new URL(link).searchParams;assert.deepEqual(params.getAll('xt'),['urn:btmh:1220'+v2]);
 row={...row,infohash_v1:hash};await page.reload();
 link=await copy();assert.deepEqual(new URL(link).searchParams.getAll('xt'),['urn:btih:'+hash,'urn:btmh:1220'+v2]);
 assert.equal(await page.evaluate(()=>torrentMagnet({hash:'invalid'})), '');
 await page.evaluate(()=>{Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw Error('denied');}}});});
 const menu=await openMenu();await menu.getByRole('button',{name:/Kopiér magnetlink/}).click();
 const modal=page.locator('#torrent-magnet');await modal.waitFor({state:'visible'});const text=modal.locator('textarea');assert.equal(await text.inputValue(),link);assert(await text.evaluate(e=>e.readOnly&&e.selectionStart===0&&e.selectionEnd===e.value.length));
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
 await modal.getByRole('button',{name:'Luk',exact:true}).click();await modal.waitFor({state:'detached'});assert(await menu.locator('summary').evaluate(e=>e===document.activeElement));
 role='viewer';await page.reload();const viewerMenu=await openMenu();assert(await viewerMenu.getByRole('button',{name:/Kopiér magnetlink/}).isEnabled());assert.equal(await viewerMenu.getByRole('button',{name:/Rediger oprydningsregel/}).count(),0);
 assert.deepEqual(mutations,[]);assert.deepEqual(errors,[]);
 console.log(`Magnet ${width}px: v1/v2/hybrid, exact name encoding, no private tracker data, clipboard copy/fallback, focus and viewer access; no mutations.`);await context.close();
}}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
