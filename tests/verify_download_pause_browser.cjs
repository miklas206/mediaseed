// Actual app scripts with loopback assets and synthetic APIs; zero live writes.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'@playwright/test');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
(async()=>{
 const root=path.resolve(__dirname,'../mediaseed/static');
 const server=require('node:http').createServer((req,res)=>{
  const name=new URL(req.url,'http://local').pathname.replace(/^\/static\//,'');const target=path.resolve(root,name);
  if(!target.startsWith(root+path.sep)){res.writeHead(404);return res.end();}
  try{res.setHeader('Content-Type',target.endsWith('.js')?'text/javascript':target.endsWith('.css')?'text/css':'text/html');res.end(fs.readFileSync(target));}catch{res.writeHead(404);res.end();}
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const origin='http://127.0.0.1:'+server.address().port;
 const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
 try{
 for(const width of [1440,390,320]){
  const page=await browser.newPage({viewport:{width,height:1000}});page.setDefaultTimeout(15000);
  let viewer=false;const requests=[],errors=[];
  const rows=['downloading','queuedDL'].map((state,i)=>({hash:String(i+1).repeat(40),name:'Synthetic '+state,state,progress:i?.0:.2,size:1e9,downloaded:2e8,ratio:0,seeding_time:0,added_on:100}));
  page.on('pageerror',e=>errors.push(e.message));await page.addInitScript(()=>window.EventSource=class extends EventTarget{close(){}});
  await page.route('**/api/**',async route=>{
   const req=route.request(),p=new URL(req.url()).pathname;let data=[];
   if(p==='/api/auth/status')data={needsSetup:false};
   if(p==='/api/auth/me')data={username:'synthetic',role:viewer?'viewer':'administrator',csrf:'synthetic',language:'da'};
   if(p==='/api/dashboard')data={runtime:{ready:true,downloadsEnabled:true,checkedAt:Date.now()/1000},torrents:rows,queue:{active:1,waiting:1,limit:8}};
   if(p==='/api/client')data={name:'qBittorrent',version:'synthetic'};
   if(p==='/api/storage')data={locations:[],defaultLocation:'local'};
   if(p==='/api/torrents/stop'||p==='/api/torrents/start'){
    const body=req.postDataJSON();requests.push({path:p,body});assert.equal(body.delete_files,false);assert.equal(body.confirm_early,undefined);
    const row=rows.find(r=>r.hash===body.torrent_hash);row.state=p.endsWith('/stop')?'stoppedDL':'downloading';data={completed:true};
   }
   await route.fulfill({json:data});
  });
  await page.goto(origin+'/static/index.html');
  for(const row of rows){
   const item=page.locator('#torrents [data-hash="'+row.hash+'"]');const menu=item.locator('.torrent-menu');await menu.locator('summary').click();
   await menu.getByRole('button',{name:/^Pause download/}).click();
   await page.waitForFunction(hash=>document.querySelector('#torrents [data-hash="'+hash+'"] .torrent-toggle')?.title==='Start',row.hash);
   assert.equal(await page.locator('#early-seeding').evaluate(e=>e.open),false);
  }
  assert.equal(requests.length,2);
  const first=page.locator('#torrents [data-hash="'+rows[0].hash+'"] .torrent-menu');await first.locator('summary').click();
  await first.getByRole('button',{name:/^Start/}).click();
  await page.waitForFunction(()=>document.querySelector('#torrents .torrent-toggle')?.title==='Stop');
  assert.equal(requests.length,3);assert.equal(requests[2].path,'/api/torrents/start');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
  viewer=true;await page.reload();await page.locator('#torrents .torrent-menu summary').first().click();
  assert.equal(await page.getByRole('button',{name:/^Pause download/}).count(),0);
  assert.deepEqual(errors,[]);await page.close();
 }
 console.log(JSON.stringify({widths:[1440,390,320],queuedPause:true,activePause:true,resume:true,noSeedingDialog:true,viewerHidden:true,liveTorrentWrites:0}));
 }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(e=>{console.error(e);process.exit(1)});
