const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'@playwright/test');
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const root=path.join(__dirname,'../mediaseed');
const current=fs.readFileSync(path.join(root,'static/index.html'),'utf8');
const rich=fs.readFileSync(path.join(root,'static/rich-ui.js'),'utf8'),lang=fs.readFileSync(path.join(root,'static/i18n.js'),'utf8');
const start=rich.indexOf('  if (!preview && row.own_upload === true)'),end=rich.indexOf('  const history = row.download_history;',start);
assert(start>=0&&end>start);
const oldRich=rich.slice(0,start)+rich.slice(end);
const oldLang=lang.replace('  "Your own upload": "Din egen upload",\n  "Uploader confirmed against your linked tracker account": "Uploaderen er bekræftet mod din tilknyttede tracker-konto",\n','');
const digest=s=>crypto.createHash('sha256').update(s).digest('hex').slice(0,12);
let oldIndex=current.replace('/static/rich-ui.js?v='+digest(rich),'/static/rich-ui.js?v='+digest(oldRich)).replace('/static/i18n.js?v='+digest(lang),'/static/i18n.js?v='+digest(oldLang));
for(const [name,source] of [['rich-ui.js',rich],['i18n.js',lang]])assert(current.includes('/static/'+name+'?v='+digest(source)),name+' URL must match source digest');
let upgraded=false,fixed=false,requests=[];
const server=http.createServer((req,res)=>{
 const url=new URL(req.url,'http://localhost'),p=url.pathname;requests.push(p+url.search);
 if(p.startsWith('/api/')){
  let data=[];
  if(p==='/api/auth/status')data={needsSetup:false};
  else if(p==='/api/auth/me')data={username:'synthetic',role:'administrator',language:'da',csrf:'synthetic'};
  else if(p==='/api/dashboard')data={runtime:{downloadsEnabled:true,checkedAt:Date.now()/1000},torrents:[{hash:'a'.repeat(40),name:'Synthetic.Own.Upload.MDTECH',state:'stalledUP',progress:1,size:1024,ratio:0,ratio_limit:1,tracker_provider:'nordicbytes',own_upload:true}],queue:{active:0,limit:8,waiting:0}};
  else if(p==='/api/storage')data={locations:[],defaultLocation:'local'};
  else if(p==='/api/ratio-scout')data={settings:{enabled:false,automatic:false},candidates:[]};
  res.writeHead(200,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end(JSON.stringify(data));return;
 }
 if(p==='/static/index.html'){
  res.writeHead(200,{'Content-Type':'text/html','Cache-Control':'no-store'});res.end(fixed?current:oldIndex);return;
 }
 const file=path.join(root,p);
 if(!fs.existsSync(file)||!fs.statSync(file).isFile()){res.writeHead(404);res.end();return;}
 let body=fs.readFileSync(file);
 if(!upgraded&&p==='/static/rich-ui.js')body=oldRich;
 if(!upgraded&&p==='/static/i18n.js')body=oldLang;
 res.writeHead(200,{'Content-Type':p.endsWith('.js')?'application/javascript':p.endsWith('.css')?'text/css':p.endsWith('.svg')?'image/svg+xml':'application/octet-stream','Cache-Control':'public,max-age=31536000,immutable'});res.end(body);
});
(async()=>{
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const base='http://127.0.0.1:'+server.address().port;
 const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
 try{for(const width of [1440,390]){
  upgraded=false;fixed=false;requests=[];
  const context=await browser.newContext({viewport:{width,height:1000}}),page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{window.EventSource=class extends EventTarget{close(){}};});
  await page.goto(base+'/static/index.html?step=old');await page.locator('#torrents').getByText('Downloadhistorik ukendt',{exact:true}).waitFor();
  upgraded=true;requests=[];
  await page.goto(base+'/static/index.html?step=broken-update');await page.locator('#torrents').getByText('Downloadhistorik ukendt',{exact:true}).waitFor();
  assert(!requests.some(p=>p.startsWith('/static/rich-ui.js')),'Reproduce cached old script despite updated server');
  fixed=true;
  await page.goto(base+'/static/index.html?step=fixed-update');await page.locator('#torrents').getByText('Din egen upload',{exact:true}).waitFor();
  assert.equal(await page.locator('#torrents').getByText('Downloadhistorik ukendt',{exact:true}).count(),0);
  assert(requests.includes('/static/rich-ui.js?v='+digest(rich)));
  assert(requests.includes('/static/i18n.js?v='+digest(lang)));
  assert.deepEqual(errors,[]);await context.close();
  console.log(`Ownership cache ${width}px: old cached code reproduces unknown badge, unchanged URLs stay stale, source-versioned URLs load Danish own-upload badge.`);
 }}finally{await browser.close();await new Promise(r=>server.close(r));}
})().catch(e=>{console.error(e);server.close();process.exit(1)});
