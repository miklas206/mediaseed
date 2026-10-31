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
  const errors=[];
  const common={state:'moving',progress:1,size:1000000,ratio:0,seeding_time:0,name:'Synthetic very long title for move progress',cleanup:{mode:'time',hours:50,status:'active'}};
  const rows=[
   {...common,hash:'a'.repeat(40),nativeMove:{phase:'active',fraction:.42,writtenBytes:420000,totalBytes:1000000,speedBps:100000,currentFile:'episode-with-a-long-name-'.repeat(4)+'.mkv',estimated:true}},
   {...common,hash:'b'.repeat(40),nativeMove:{phase:'queued',fraction:0,writtenBytes:0,totalBytes:1000000,estimated:true}},
   {...common,hash:'c'.repeat(40),nativeMove:{phase:'unavailable'}},
   {...common,hash:'d'.repeat(40),nativeMove:{phase:'active',fraction:.999,writtenBytes:1000000,totalBytes:1000000,estimated:true}}
  ];
  page.on('pageerror',e=>errors.push(e.message));await page.addInitScript(()=>window.EventSource=class extends EventTarget{close(){}});
  await page.route('**/api/**',async route=>{
   const req=route.request(),p=new URL(req.url()).pathname;let data=[];
   if(p==='/api/auth/status')data={needsSetup:false};
   if(p==='/api/auth/me')data={username:'synthetic',role:'administrator',csrf:'synthetic',language:'da'};
   if(p==='/api/dashboard')data={runtime:{ready:true,downloadsEnabled:true,checkedAt:Date.now()/1000},torrents:rows,queue:{active:1,waiting:1,limit:8}};
   if(p==='/api/client')data={name:'qBittorrent',version:'synthetic'};
   if(p==='/api/storage')data={locations:[],defaultLocation:'local'};
   await route.fulfill({json:data});
  });
  await page.goto(origin+'/static/index.html');
  await page.locator('#torrents progress').first().waitFor();
  for(const layout of ['list','cards'])for(const theme of ['midnight-glass','soft-orbit','precision-terminal','floating-layers','edge-signal','classic','light','duck-pond']){
   await page.evaluate(({layout,theme})=>{document.querySelector('#workspace').dataset.torrentLayout=layout;MediaAppTheme.apply(theme);},{layout,theme});
   const active=page.locator('#torrents [data-hash="'+rows[0].hash+'"]');
   assert.match(await active.locator('.seed-progress-cell').innerText(),/42%.*Aktiv flytning/);
   assert(!/Mål 0|Seedmål/.test(await active.locator('.seed-progress-cell').innerText()));
   assert.match(await page.locator('#torrents [data-hash="'+rows[1].hash+'"] .seed-progress-cell').innerText(),/I flyttekø/);
   assert.match(await page.locator('#torrents [data-hash="'+rows[2].hash+'"] .seed-progress-cell').innerText(),/fremdrift ukendt/);
   assert.match(await page.locator('#torrents [data-hash="'+rows[3].hash+'"] .seed-progress-cell').innerText(),/99%/);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,JSON.stringify({width,layout,theme}));
   const boxes=await active.locator('.seed-progress-track').evaluate(track=>{const a=track.getBoundingClientRect(),b=track.querySelector('progress').getBoundingClientRect();return {a:{top:a.top,bottom:a.bottom},b:{top:b.top,bottom:b.bottom}};});
   assert(boxes.b.top>=boxes.a.top-.1&&boxes.b.bottom<=boxes.a.bottom+.1);
  }
  await page.screenshot({path:'/tmp/mediaseed-native-move-'+width+'.png',fullPage:true});
  assert.deepEqual(errors,[]);await page.close();
 }
 console.log(JSON.stringify({widths:[1440,390,320],layouts:2,themes:8,activeAndQueued:true,unknownProgress:true,neverPremature100:true,liveTorrentWrites:0}));
 }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(e=>{console.error(e);process.exit(1)});
