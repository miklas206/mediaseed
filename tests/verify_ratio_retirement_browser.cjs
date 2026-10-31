const {chromium}=require(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
(async()=>{const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
const origin=process.env.MEDIASEED_TEST_ORIGIN||'http://127.0.0.1:15392';
for(const width of [1440,390]){
 const page=await browser.newPage({ignoreHTTPSErrors:true,viewport:{width,height:1000}}),errors=[],calls=[];let trackerSaved=null;page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{window.EventSource=class extends EventTarget{close(){}}});
 const now=Math.floor(Date.now()/900000)*900,hash='a'.repeat(40),torrent={hash,name:'Synthetic release',category:'MediaSeed-Ratio',size:2**30,uploaded:2**31,downloaded:2**30,progress:1,ratio:2,credited_ratio:2,ratio_limit:1,cleanup:{mode:'ratio',ratio:1},state:'stalledUP',added_on:now-86400,num_leechs:0,tracker_provider:'nordicbytes',download_history:{downloaded:2**30,unknownBytes:0,observations:[{bytes:2**30,globalFreeleech:true,freeleech:100,doubleUpload:true,green:false}]}};
 const stats={ratio:2,buffer:10*2**30,uploaded:100*2**30,downloaded:50*2**30,seedbonus:12,seeding:90,leeching:1,warnings:1,hit_and_runs:0,seeding_size:300*2**30,total_uploads:3,avg_seedtime:20,total_seedtime:2000};
 const accounts=[{id:'bad',name:'Broken account',status:'error'}, {id:'good',name:'NordicBytes · Eksempelkonto',status:'ok',checkedAt:now,stats,ranks:{automatic:'Member'},notifications:{dm:2,notifications:1,tickets:0},premium:{type:null,expiresAt:null},events:[],globalEvents:[]}];
 const data={accounts,history:[{account:'good',samples:Array.from({length:10},(_,i)=>({at:now-(9-i)*900,stats:{...stats,ratio:3.2+i*.074,uploaded:(713+i*3.79)*2**30,buffer:(1.46+i*.02)*2**40,downloaded:(183+i*1.12)*2**30,seedbonus:1971750+i*22000}}))}],alerts:[{account:'good',kind:'warnings',value:1,createdAt:now}],settings:{lowBufferGiB:10},torrentCheckedAt:now,torrentDataFresh:true,keepSeeding:[{...torrent,seeders:2,leechers:5,reasons:['rare_seed','tracker_demand']}],reviewStorage:[{...torrent,seeders:5,leechers:0,reasons:['local_goal_met','no_observed_demand']}],results:[{source:'manual',torrents:1,size:2**30,totalUpload:2**31,observedUpload:2**29,uploadPerGiB:2}],topTorrents:[{...torrent,observedUpload:2**29}],remainingBytes:2**30,torrentLimit:500};
 await page.route('**/api/**',async route=>{const req=route.request(),url=new URL(req.url()),path=url.pathname;calls.push(path+url.search);let result=[];
 if(path==='/api/auth/status')result={needsSetup:false};
 else if(path==='/api/auth/me')result={username:'synthetic',role:'administrator',language:'da',csrf:'synthetic'};
 else if(path==='/api/dashboard')result={runtime:{ready:true,downloadsEnabled:true,checkedAt:now},torrents:[torrent],queue:{active:0,limit:8,waiting:0}};
 else if(path==='/api/trackers/test'){const body=req.postDataJSON();if(body.provider!=='unit3d'||body.base_url!=='https://sagmire.org'||body.api_key!=='synthetic-key')throw Error('Wrong tracker test payload');result={connected:true};}
 else if(path==='/api/trackers'&&req.method()==='POST'){trackerSaved=req.postDataJSON();result={saved:true,id:'synthetic-tracker'};}
 else if(path==='/api/trackers')result=accounts;
 else if(path==='/api/insights')result=data;
 else if(path==='/api/insights/settings')result={lowBufferGiB:5};
 else if(path==='/api/storage')result={defaultLocation:'tv',locations:[{id:'tv',label:'TV',filesystemId:'1',available:true,writable:true,totalBytes:3e12,freeBytes:2e12}]};
 else if(path==='/api/activity')result={entries:[{source:'ratio',title:'Earlier automatic release',status:'started',created:now}]};
 else if(path==='/api/client')result={name:'qBittorrent',version:'v5.2.4'};
 else if(path.endsWith('/details'))result={properties:{total_downloaded:2**30,total_uploaded:2**31},credit:{},cleanup:{mode:'never'},files:[],trackers:[],peers:[]};
 else if(path.endsWith('/media'))result={metadata:{name:'Synthetic release',category:'TV',resolution:'1080p',genres:'Drama',release_year:'2026',seeders:4,leechers:2,times_completed:100,checkedAt:now,description:'Safe plain text',media_info:'Video: H.264',labels:[{name:'Danish'}],screenshots:[{url:'https://example.org/screenshot.png'}],files:[{name:'test.mkv',size:2**30}],imdb_id:123}};
 else if(path.includes('/search')){if(url.searchParams.get('free')!=='100'||url.searchParams.get('double')!=='true')throw Error('Search filters missing');result={results:[{id:'1',name:'Test search release',size:2**30,seeders:5,leechers:4,category:'TV',resolution:'1080p',freeleech:100,double_upload:true}]};}
 else if(path==='/api/ratio-scout')result={settings:{enabled:false,automatic:false},candidates:[],scanMinutes:10,resultLimit:10,dailyUsed:0};
 await route.fulfill({json:result});
 });
 await page.goto(origin+'/static/index.html');await page.locator('#workspace').waitFor({state:'visible'});


 const assert=require('node:assert/strict');
 assert.deepEqual(await page.locator('#app-navigation [data-view]').evaluateAll(rows=>rows.map(row=>row.dataset.view)),['downloads','feeds','trackers','insights','vpn']);
 assert.equal(await page.locator('#ratio-scout,#ratio-form,[data-view="ratio-scout"],script[src*="ratio-scout"]').count(),0);
 assert.equal(await page.locator('#source-filter option[value=ratio]').count(),0);
 await page.locator('#torrents').getByText('Synthetic release',{exact:true}).waitFor();
 assert.equal(await page.evaluate(()=>seedSource({category:'MediaSeed-Ratio'})),'Tidligere automatiske downloads');
 await page.getByText('Earlier automatic release',{exact:true}).waitFor({state:'attached'});
 await page.locator('#automatic-activity').evaluate(node=>{for(let el=node;el;el=el.parentElement)if(el.tagName==='DETAILS')el.open=true;});
 await page.getByText('Earlier automatic release',{exact:true}).waitFor();
 await page.locator('.automation-event button').click();
 assert.equal(await page.locator('#downloads').isVisible(),true);
 for(const view of ['trackers','insights','feeds','downloads']){
  await page.locator('[data-view='+view+']').click();await page.locator('#'+view).waitFor({state:'visible'});
 }
 assert.equal(calls.some(path=>path.startsWith('/api/ratio-scout')),false);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
 assert.deepEqual(errors,[]);
 await page.screenshot({path:'/tmp/mediaseed-ratio-removed-'+width+'.png',fullPage:false});
 console.log(JSON.stringify({width,removed:true,noDiscoveryCalls:true,legacyTorrentsPreserved:true,activityWorks:true,otherViews:true,pageErrors:errors}));await page.close();
}
await browser.close();})().catch(e=>{console.error(e);process.exit(1)});
