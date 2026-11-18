const {chromium,expect} = require(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
(async()=>{
 const browser=await chromium.launch({executablePath:'/usr/bin/chromium'});
 try {
 for (const width of [320,390,1440]) for (const language of ['da','en']){
  const page=await browser.newPage({viewport:{width,height:850}}),da=language==='da',errors=[],writes=[];
  page.setDefaultTimeout(4000);page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>window.EventSource=class extends EventTarget{close(){}});
  const feed={id:'abc',name:'Nordic test',location:'local',folder:'',include:['2160p'],exclude:[],badges:['freeleech'],paused:true,cleanup:{mode:'never'}};
  await page.route('**/api/**',async r=>{
   const path=new URL(r.request().url()).pathname;let data=[];
   if(path==='/api/auth/status')data={needsSetup:false};
   if(path==='/api/auth/me')data={username:'fixture',role:'administrator',language,csrf:'fixture'};
   if(path==='/api/dashboard')data={runtime:{provider:null,ready:false,downloadsEnabled:false},queue:{active:0,limit:2,waiting:0},torrents:[]};
   if(path==='/api/storage')data={locations:[{id:'local',available:true,writable:true,label:'Local downloads'}],defaultLocation:'local'};
   if(path==='/api/storage/folders')data={writable:true,folders:[]};
   if(path==='/api/feeds')data=[feed];
   if(path==='/api/feeds/abc')data=feed;
   if(path==='/api/vpn')data={provider:null,ready:false,downloadsEnabled:false,profiles:[]};
   if(path==='/api/ratio-scout')data={settings:{enabled:false},candidates:[]};
   if(path==='/api/insights')data={accounts:[],alerts:[],settings:{}};
   if(path==='/api/activity')data={entries:[]};
   if(path.startsWith('/api/feeds') && ['POST','PUT','PATCH'].includes(r.request().method()))writes.push({path,body:r.request().postDataJSON()});
   await r.fulfill({json:data});
  });
  const origin = process.env.MEDIASEED_TEST_ORIGIN || 'http://127.0.0.1:15392';
  await page.goto(origin + '/static/index.html');
  const nav=page.locator('[data-view=feeds]');await nav.click();
  await expect(page.locator('#feed-vpn-status')).toHaveText(da?'Ingen VPN valgt':'No VPN selected');
  await page.getByRole('button',{name:da?'Vis VPN-forbindelse':'View VPN connection'}).click();
  await expect(page.locator('#vpn')).toBeVisible();await nav.click();
  const add=page.getByRole('button',{name:da?'Tilføj feed':'Add feed',exact:true});
  await add.click();const dlg=page.locator('#feeds-editor'),advanced=dlg.locator('#feed-advanced'),next=dlg.locator('.wizard-next');
  await expect(advanced).not.toHaveAttribute('open','');
  await next.click();await expect(dlg.locator('[name=name]')).toBeFocused();
  await dlg.locator('[name=name]').fill('Sample');await dlg.locator('[name=url]').fill('https://nordicbytes.org/rss/example');
  await advanced.locator('summary').focus();await page.keyboard.press('Enter');await expect(advanced).toHaveAttribute('open','');
  await dlg.locator('[name=include]').fill('2160p');await advanced.locator('summary').click();
  await next.click();await expect(dlg.locator('#feed-save')).toBeVisible();
  await dlg.locator('.wizard-back').click();await expect(dlg.locator('[name=name]')).toHaveValue('Sample');await next.click();
  await dlg.locator('#feed-save').click();await expect(dlg).not.toBeVisible();
  if(writes.length!==1||writes[0].body.include[0]!=='2160p')throw Error('save failed');
  await expect(page.locator('#message')).toContainText(da?'Feedet er gemt':'Feed saved');
  const card=page.locator('.feed-card').first();await card.locator('summary').click();
  await card.getByRole('button',{name:da?'Rediger':'Edit',exact:true}).click();
  await expect(dlg).toHaveAccessibleName(da?'Redigér feed':'Edit feed');await expect(advanced).toHaveAttribute('open','');
  await expect(dlg.locator('[name=include]')).toHaveValue('2160p');
  await expect(dlg.locator('[type=reset]')).not.toBeVisible();
  if(await dlg.evaluate(el=>el.scrollWidth>el.clientWidth+1))throw Error('dialog overflow');
  await page.keyboard.press('Escape');await expect(dlg).not.toBeVisible();
  if(errors.length)throw Error(errors.join(','));
  console.log(width,language,'OK');await page.close();
 }
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1})
