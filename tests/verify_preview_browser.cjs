const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'@playwright/test');
(async()=>{const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
for(const width of [390,1440]){
 const page=await browser.newPage({viewport:{width,height:1000},ignoreHTTPSErrors:true});const errors=[];let scenario='known';page.on('pageerror',error=>errors.push(error.message));
 await page.addInitScript(()=>{window.EventSource=class extends EventTarget{close(){}}});
 await page.route('**/api/**',async route=>{const request=route.request(),path=new URL(request.url()).pathname;let data=[];
 if(path==='/api/auth/status')data={needsSetup:false};
 else if(path==='/api/auth/me')data={username:'synthetic',role:'administrator',language:'en',csrf:'synthetic'};
 else if(path==='/api/dashboard')data={runtime:{ready:true,downloadsEnabled:true,checkedAt:Date.now()/1000},torrents:[],queue:{active:0,limit:8,waiting:0}};
 else if(path==='/api/storage')data={defaultLocation:'local',locations:[{id:'local',label:'Local',available:true,writable:true,freeBytes:1e12,totalBytes:2e12}]};
 else if(path==='/api/storage/folders')data={writable:true,folders:[]};
 else if(path==='/api/client')data={name:'qBittorrent',version:'test'};
 else if(path==='/api/torrent-preview'){
  const second=request.postData()?.includes('second');const id=second?'two':'one';
  data={size:100,lookupStatus:scenario==='unmatched'?'torrent_unmatched':'matched',benefits:{},metadataPending:false,estimates:scenario==='unmatched'?[]:[{accountId:id,name:second?'Tracker B':'Tracker A',current:2,uploaded:1000,downloaded:500,charged:scenario==='unknown'?null:0,chargeKnown:scenario!=='unknown',projected:scenario==='unknown'?null:2}]};
 }
 await route.fulfill({json:data});});
 await page.goto((process.env.MEDIASEED_TEST_ORIGIN||'http://127.0.0.1:15384')+'/static/index.html');await page.locator('#new-torrent').click();
 await page.locator('[name=mode][value=file]').check();
 await page.locator('#add-file').setInputFiles([{name:'first.torrent',mimeType:'application/x-bittorrent',buffer:Buffer.from('first')},{name:'second.torrent',mimeType:'application/x-bittorrent',buffer:Buffer.from('second')}]);
 await page.locator('#add-torrent .wizard-next').click();await page.locator('.ratio-estimate-card').nth(1).waitFor();
 if(await page.locator('.ratio-estimate-card').count()!==2)throw Error('Wrong account count');
 if((await page.locator('.ratio-estimate-numbers strong').allTextContents()).some(value=>value==='Unknown'))throw Error('Mixed trackers must each have their own estimate');
 scenario='unknown';await page.locator('#seed-preview-button').click();await page.waitForFunction(()=>[...document.querySelectorAll('.ratio-estimate-numbers strong')].every(node=>node.textContent==='Unknown'));
 if((await page.locator('.ratio-estimate-card').first().textContent()).includes('∞'))throw Error('Unknown cost rendered as infinity');
 scenario='unmatched';await page.locator('#seed-preview-button').click();await page.getByText('Tracker or torrent could not be verified through its API. No download cost has been assumed.',{exact:true}).first().waitFor();
 if(await page.locator('.ratio-estimate-card').count())throw Error('Invented estimates for unmatched torrent');
 if(errors.length)throw Error(JSON.stringify(errors));console.log(JSON.stringify({width,mixedTrackers:true,unknownCost:true,unmatched:true,errors}));await page.close();
}await browser.close();})().catch(error=>{console.error(error);process.exit(1)});
