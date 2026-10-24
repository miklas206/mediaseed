const {chromium}=require(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
 const origin=process.env.MEDIASEED_TEST_ORIGIN || 'http://127.0.0.1:15392';
 for(const width of [1440,390,320]){
  const page=await browser.newPage({ignoreHTTPSErrors:true,viewport:{width,height:1000}}),errors=[],requests=[],mutations=[];
  let failGroups=false,failMembers=false;
  const now=Date.now()/1000,feed='f'.repeat(24),key='a'.repeat(64),hash='a'.repeat(40),GiB=2**30;
  const popular={key,name:'Popular Show',kind:'series',downloads:32,knownDownloads:31,unknownDownloads:1,creditedUploadedBytes:31*GiB,averageUploadedBytes:GiB,latestAdded:now};
  const strong={...popular,key:'b'.repeat(64),name:'Strong Show',downloads:2,knownDownloads:2,unknownDownloads:0,creditedUploadedBytes:8*GiB,averageUploadedBytes:4*GiB};
  const unknown={...popular,key:'c'.repeat(64),name:'Unknown Show',downloads:1,knownDownloads:0,unknownDownloads:1,creditedUploadedBytes:null,averageUploadedBytes:null};
  const movie={...strong,key:'d'.repeat(64),name:'Standalone.Movie.2026.1080p.WEB-DL.DANiSH-VeryLongSyntheticReleaseName',kind:'torrent',downloads:1,knownDownloads:1,creditedUploadedBytes:GiB,averageUploadedBytes:GiB};
  const upload={downloads:36,creditedUploadedBytes:40*GiB,unknownDownloads:2};
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{window.EventSource=class extends EventTarget{close(){}};});
  await page.route('**/api/**',async route=>{
   const req=route.request(),url=new URL(req.url()),path=url.pathname;let result=[];
   if(req.method()!=='GET')mutations.push(path);
   if(path==='/api/auth/status')result={needsSetup:false};
   else if(path==='/api/auth/me')result={username:'synthetic',role:'viewer',language:'da',csrf:'synthetic'};
   else if(path==='/api/dashboard')result={runtime:{ready:true,downloadsEnabled:true,checkedAt:now},queue:{active:0,limit:8,waiting:0},torrents:[{hash,name:'Popular.Show.S01E31.1080p',progress:1,state:'stalledUP',size:GiB,credited_ratio:1.3,ratio_limit:1,seeding_time:5000}]};
   else if(path.includes('/history')){
    requests.push(url.search);const params=url.searchParams,pageNumber=Number(params.get('page')||1),series=params.get('series');
    if((failGroups&&!series)||(failMembers&&series)){failGroups=false;failMembers=false;await route.fulfill({status:503,json:{detail:'Synthetic history failure'}});return;}
    if(series){
     const count=series===key?32:2,from=(pageNumber-1)*25;
     const entries=Array.from({length:Math.max(0,Math.min(25,count-from))},(_,i)=>({hash:i===0&&from===0?hash:(i+from).toString(16).padStart(40,'0'),title:i===0?'Popular.Show.S01E31.1080p':'Popular.Show.S01E'+(from+i+1)+'.2160p',episode:'S01E'+(from+i+1),created:now-i*3600,creditedUploadedBytes:GiB}));
     result={entries,total:count,page:pageNumber,pageSize:25,series:popular,upload};
    }else if(params.get('view')==='series'){
     let groups=[popular,strong,unknown,movie];const term=(params.get('search')||'').toLowerCase();
     if(params.get('sort')==='average')groups=[strong,popular,movie,unknown];
     if(params.get('sort')==='recent')groups=[movie,unknown,strong,popular];
     if(term)groups=groups.filter(g=>g.name.toLowerCase().includes(term));
     result={groups,total:groups.length,page:pageNumber,pageSize:25,upload};
    }else result={entries:[{hash,title:'Popular.Show.S01E31.1080p',created:now,creditedUploadedBytes:GiB}],total:1,page:pageNumber,pageSize:25,upload};
   }else if(path.endsWith('/details'))result={properties:{total_downloaded:GiB,total_uploaded:GiB},credit:{},cleanup:{mode:'never'},files:[],trackers:[],peers:[]};
   else if(path==='/api/storage')result={defaultLocation:'local',locations:[]};
   else if(path==='/api/client')result={name:'qBittorrent',version:'v5.2.4'};
   else if(path==='/api/ratio-scout')result={settings:{enabled:false,automatic:false},candidates:[]};
   await route.fulfill({json:result});
  });
  await page.goto(origin+'/static/index.html');await page.locator('#torrents .torrent').waitFor();
  await page.evaluate(([id])=>richFeedHistory(id,'Synthetic RSS'),[feed]);
  const dialog=page.locator('#feed-history-dialog'),cards=dialog.locator('.rss-series');
  assert.equal(await cards.count(),4);
  assert.equal(requests.filter(r=>r.includes('series=')).length,0,'Collapsed series eagerly loaded all episodes');
  assert.match(await cards.first().textContent(),/Popular Show.*32 hentninger/s);
  assert.match(await cards.first().locator('summary').textContent(),/31(?:,0)? GiB/);
  assert.match(await cards.first().locator('.rss-series-unknown').textContent(),/1 af 32/);
  assert.match(await cards.nth(2).locator('.rss-series-metrics').textContent(),/Ukendt/);
  failMembers=true;await cards.first().locator('summary').click();
  await cards.first().getByText('Synthetic history failure').waitFor();
  await cards.first().getByRole('button',{name:/Prøv igen|Try again/}).click();
  await cards.first().locator('.rss-history-entry').first().waitFor();
  assert.equal(await cards.first().locator('.rss-history-entry').count(),25);
  assert.equal(await cards.first().getByRole('button',{name:'Vis torrent'}).count(),1);
  await cards.first().locator('.rss-history-pages').getByRole('button',{name:/Næste/}).click();
  await page.waitForFunction(()=>document.querySelectorAll('.rss-series-downloads .rss-history-entry').length===7);
  assert.equal(await cards.first().locator('.rss-history-entry').count(),7);
  await cards.first().locator('summary').click();
  const view=dialog.locator('.rss-history-tools select').nth(0),sort=dialog.locator('.rss-history-tools select').nth(1),search=dialog.locator('input[type=search]');
  await sort.selectOption('average');
  await page.waitForFunction(()=>document.querySelector('.rss-series-identity strong')?.textContent==='Strong Show');
  await search.fill('Popular');
  await page.waitForFunction(()=>document.querySelectorAll('.rss-series').length===1&&document.querySelector('.rss-series-identity strong')?.textContent==='Popular Show');
  await search.fill('missing');await dialog.getByText('Ingen matchende serier').waitFor();
  await search.fill('');await page.waitForFunction(()=>document.querySelectorAll('.rss-series').length===4);
  await dialog.screenshot({path:`/tmp/rss-series-${width}.png`});
  assert.equal(await dialog.evaluate(e=>e.scrollWidth>e.clientWidth+1),false,'History dialog overflows');
  failGroups=true;await dialog.locator('.rss-history-header-actions').getByRole('button',{name:/Opdat[eé]r/}).click();
  await dialog.getByText('Synthetic history failure').waitFor();
  assert.equal(await sort.inputValue(),'average');
  await dialog.getByRole('button',{name:/Prøv igen|Try again/}).click();await cards.first().waitFor();
  await view.selectOption('downloads');await dialog.locator('.rss-history-list > .rss-history-entry').waitFor();
  assert.equal(await sort.isDisabled(),true);assert.equal(await search.isDisabled(),true);
  await view.selectOption('series');await cards.first().waitFor();
  await cards.first().locator('summary').click();await cards.first().getByRole('button',{name:'Vis torrent'}).waitFor();
  await cards.first().getByRole('button',{name:'Vis torrent'}).click();
  await page.locator('#torrent-details').waitFor();assert.equal(await dialog.isVisible(),false);
  assert.deepEqual(errors,[]);assert.deepEqual(mutations,[]);
  console.log(JSON.stringify({width,groupedTotals:true,averageSort:true,lazyMembers:true,memberPagination:true,unknownsDistinct:true,search:true,retry:true,activeTorrentShortcut:true,pageErrors:errors}));
  await page.close();
 }
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1);});
