const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'@playwright/test');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
try{for(const width of [1440,390,320]){
 const page=await browser.newPage({viewport:{width,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{window.EventSource=class extends EventTarget{close(){}};});
 const hashes=['a','b','c','d','e'].map(x=>x.repeat(40));
 const rows=hashes.map((hash,i)=>({hash,name:`Synthetic.${i+1}.MDTECH`,state:'stalledUP',size:1024,progress:1,ratio:0,ratio_limit:1,added_on:100,tracker_provider:'nordicbytes',own_upload:i===0||i===4?true:i===1||i===3?false:null}));
 rows[4].download_history={downloaded:100,unknownBytes:0,observations:[{bytes:100,freeleech:100,doubleUpload:false,green:false}]};
 let language='da';
 await page.route('http://mediaseed.test/**',async route=>{
  const p=new URL(route.request().url()).pathname;
  if(!p.startsWith('/api/')){
   const file=path.join(__dirname,'../mediaseed',p);
   return route.fulfill({body:fs.readFileSync(file),contentType:p.endsWith('.js')?'application/javascript':p.endsWith('.css')?'text/css':'text/html'});
  }
  let result=[];
  if(p==='/api/auth/status')result={needsSetup:false};
  else if(p==='/api/auth/me')result={username:'synthetic',role:'administrator',language,csrf:'synthetic'};
  else if(p==='/api/dashboard')result={runtime:{downloadsEnabled:true,checkedAt:Date.now()/1000},torrents:rows,queue:{active:0,limit:8,waiting:0}};
  else if(p==='/api/storage')result={defaultLocation:'local',locations:[]};
  else if(p==='/api/ratio-scout')result={settings:{enabled:false,automatic:false},candidates:[]};
  else if(p.endsWith('/details'))result={properties:{total_downloaded:0,total_uploaded:0},files:[],trackers:[],peers:[],credit:{}};
  await route.fulfill({json:result});
 });
 await page.goto('http://mediaseed.test/static/index.html');
 const mine=page.locator(`#torrents [data-hash="${hashes[0]}"]`);await mine.getByText('Din egen upload',{exact:true}).waitFor();
 assert.equal(await mine.getByText('Downloadhistorik ukendt',{exact:true}).count(),0);
 for(const hash of hashes.slice(1,4))assert.equal(await page.locator(`#torrents [data-hash="${hash}"]`).getByText('Downloadhistorik ukendt',{exact:true}).count(),1);
 const historic=page.locator(`#torrents [data-hash="${hashes[4]}"]`);
 assert.equal(await historic.getByText('Din egen upload',{exact:true}).count(),1);assert.equal(await historic.getByText('100% Freeleech',{exact:true}).count(),1);
 await mine.locator('.torrent-name').click();await page.locator('#detail-data').getByText('Din egen upload',{exact:true}).waitFor();
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
 language='en';await page.reload();await mine.getByText('Your own upload',{exact:true}).waitFor();
 assert.deepEqual(errors,[]);
 console.log(`Own upload ${width}px: list/details, Danish/English, confirmed vs other/unknown, no tag inference, preserved observed benefits and no overflow/errors.`);
 await page.close();
}}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
