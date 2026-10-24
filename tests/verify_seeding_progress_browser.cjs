const {chromium} = require(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({executablePath:'/usr/bin/chromium', args:['--no-sandbox']});
  const origin = process.env.MEDIASEED_TEST_ORIGIN || 'http://127.0.0.1:15386';
  for (const width of [1440, 390, 320]) {
    const page = await browser.newPage({ignoreHTTPSErrors:true,viewport:{width,height:1000}}), errors=[];
    const policy={mode:'ratio',ratio:3,hours:48,status:'active'};
    const hash='a'.repeat(40), now=Date.now()/1000, added=100;
    const torrent={hash,name:'Synthetic.Long.Show.S01.DANiSH.1080p.WEB-DL.H.264.DD5.1-Group',size:2**30,uploaded:0,downloaded:2**30,progress:1,ratio:0,credited_ratio:0,ratio_limit:1,state:'stalledUP',added_on:added,seeding_time:1200,tracker_name:'Synthetic tracker'};
    page.on('pageerror',error=>errors.push(error.message));
    await page.addInitScript(()=>{window.EventSource=class extends EventTarget{close(){}};});
    await page.route('**/api/**',async route=>{
      const req=route.request(), url=new URL(req.url()), path=url.pathname;let result=[];
      if(path==='/api/auth/status')result={needsSetup:false};
      else if(path==='/api/auth/me')result={username:'synthetic',role:'administrator',language:'da',csrf:'synthetic'};
      else if(path==='/api/dashboard')result={runtime:{ready:true,downloadsEnabled:true,checkedAt:now},torrents:[{...torrent,credited_ratio:1.3,ratio_limit:1,seeding_time:72*3600,cleanup:{mode:'ratio',ratio:3,hours:48},account_credited_uploaded:123456789}],queue:{active:0,limit:8,waiting:0}};
      else if(path.endsWith('/details'))result={properties:{total_downloaded:2**30,total_uploaded:0,addition_date:added},credit:{credited_ratio:1.3,ratio_limit:1},cleanup:policy,files:[],trackers:[],peers:[]};
      else if(path==='/api/storage')result={defaultLocation:'local',locations:[{id:'local',label:'Local',available:true,writable:true,totalBytes:3e12,freeBytes:2e12}]};
      else if(path==='/api/client')result={name:'qBittorrent',version:'v5.2.4'};
      else if(path==='/api/ratio-scout')result={settings:{enabled:false,automatic:false},candidates:[],scanMinutes:10,resultLimit:10,dailyUsed:0};
      await route.fulfill({json:result});
    });
    await page.goto(origin+'/static/index.html');
    const item=page.locator('#torrents [data-hash="'+hash+'"]');
    await item.waitFor();
    const bar=item.locator('progress');
    assert.match(await item.locator('.seed-progress-cell strong').textContent(),/Ratio 1,3 \/ 3/);
    assert.ok(Math.abs(await bar.evaluate(e=>e.value)-1.3/3)<1e-9);
    assert((await bar.boundingBox()).height>=16);
    assert.match(await item.locator('.seed-upload-credit').textContent(),/117/);
    await item.locator('.torrent-name').click();
    await page.locator('#torrent-details .seed-detail-tabs [role=tab]').nth(1).click();
    const detail=page.locator('#torrent-details .tracker-credit');
    await detail.waitFor();
    assert.match(await detail.textContent(),/Ratio 1,3 \/ 3/);
    assert.match(await detail.textContent(),/Oprydningsmål/);
    await page.keyboard.press('Escape');
    const checks=await page.evaluate(()=>{
      const cases=[
        ['downloading',.5,'downloading'],['uploading',1,'seeding'],['stalledUP',1,'waiting'],
        ['pausedUP',1,'paused'],['checkingUP',1,'checking'],['missingFiles',1,'error']
      ];
      const results=[];
      for(const [state,progress,tone] of cases){
        const row=seedTorrent({hash:'c'.repeat(40),name:'Synthetic state '+state,state,progress,credited_ratio:1.3,seeding_time:3600,cleanup:{mode:'ratio',ratio:3}},0);
        document.querySelector('#torrents').append(row);
        const bar=row.querySelector('progress');
        results.push({tone:bar.dataset.tone,expected:tone,color:getComputedStyle(bar).getPropertyValue('--progress-color').trim(),value:bar.value});
      }
      return results;
    });
    for(const check of checks)assert.equal(check.tone,check.expected);
    assert.equal(new Set(checks.map(check=>check.color)).size,checks.length);
    assert.equal(checks[0].value,.5);
    await page.screenshot({path:`/tmp/seeding-progress-${width}.png`,fullPage:true});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({width,ratio:'1,3 / 3',percent:Math.round(await bar.evaluate(e=>e.value)*100),colors:checks.map(check=>check.tone),pageErrors:errors}));
    await page.close();
  }
  await browser.close();
})().catch(error=>{console.error(error);process.exit(1);});
