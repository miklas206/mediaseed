// All accounts, transfers, covers and VPN observations are synthetic.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
const assert = require('node:assert/strict');
const origin = process.env.MEDIASEED_TEST_ORIGIN || 'http://127.0.0.1:15392';
(async () => {
  const browser = await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
  for (const width of [1672,1440,1024,768,390,320]) {
    const page = await browser.newPage({ignoreHTTPSErrors:true,viewport:{width,height:1050}}), errors = [];
    let valid = true, idle = false, failed = false, writes = 0;
    const names = ['Sintel · 4K','Big Buck Bunny · 1080p','Debian 13 · amd64','Blender · Assets','Northern Lights · S04E02 · 1080p','Open Movie · 1080p','Island Wildlife · S21E25 · 1080p'];
    const trackers = ['NordicBytes','SceneTime','LinuxTracker'];
    const rows = names.map((name,i) => ({
      hash:(i+1).toString(16).padStart(40,'0'),name,
      state:i===0||i===3?'downloading':i===1?'checkingUP':i===2?'stalledUP':i===4?'error':i===6?'pausedUP':'uploading',progress:i===0?.42:i===3?.67:1,
      dlspeed:i===0?490086:i===3?114995:0,upspeed:i===6?0:12345+i*30000,
      ratio:[.12,4.37,1.98,.41,3.21,6.18,.83][i],size:(i+1)*512*1024**2,
      seeding_time:(i+1)*43200,num_seeds:3+i,num_leechs:i%3,
      tracker_name:trackers[i%3],category:i%2?'MediaSeed-RSS-demo':'',
      added_on:1793052000-i*3600,cover:'/api/covers/'+(i+1).toString().repeat(40),
    }));
    const runtime = () => ({provider:'mediavpn',ready:valid,downloadsEnabled:valid,checkedAt:Date.now()/1000,country:'Denmark',publicIp:'192.0.2.3',forwardedPort:43210});
    const accounts = [{id:'demo',name:'NordicBytes',provider:'nordicbytes',status:'ok',checkedAt:Date.now()/1000,stats:{ratio:2.86,buffer:5*1024**4,uploaded:10*1024**4,downloaded:3.5*1024**4,seeding:42,warnings:0}}];
    const storage = {defaultLocation:'nas',locations:[{id:'local',label:'Local downloads',available:true,writable:true,freeBytes:12*1024**3,totalBytes:50*1024**3},{id:'nas',label:'NAS · Movies',available:true,writable:true,freeBytes:2.24*1024**4,totalBytes:10*1024**4}]};
    await page.addInitScript(() => {window.EventSource=class extends EventTarget {close(){}};});
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/**', async route => {
      const request = route.request(), path = new URL(request.url()).pathname; let data = [];
      if (request.method() !== 'GET') writes++;
      if (path.startsWith('/api/covers/')) {
        const n = Number(path.slice(-1));
        return route.fulfill({contentType:'image/svg+xml',body:`<svg xmlns="http://www.w3.org/2000/svg" width="44" height="60"><rect width="44" height="60" fill="${['#15475d','#136e69','#34375c','#805125','#1e5861','#22627b','#4e735c'][n-1]}"/><circle cx="22" cy="21" r="11" fill="#ffffff35"/><path d="M5 60V47Q22 23 39 47V60" fill="#ffffff45"/><text x="22" y="26" text-anchor="middle" font-family="sans-serif" font-size="14" fill="white">${n}</text></svg>`});
      }
      if (path === '/api/auth/status') data={needsSetup:false};
      if (path === '/api/auth/me') data={username:'synthetic',role:'administrator',language:'da',csrf:'synthetic'};
      if (path === '/api/dashboard') {
        if (failed) return route.fulfill({status:503,json:{detail:'Synthetic outage'}});
        data={runtime:runtime(),queue:{active:2,limit:8,waiting:0},torrents:rows.map(row=>idle?{...row,dlspeed:0,upspeed:0}:row)};
      }
      if (path === '/api/vpn') data=runtime();
      if (path === '/api/client') data={name:'qBittorrent',version:'synthetic'};
      if (path === '/api/storage') data=storage;
      if (path === '/api/trackers') data=accounts;
      if (path === '/api/activity') data={entries:[]};
      if (path.includes('/details')) data={properties:{total_size:1e9,total_downloaded:1e9,total_uploaded:2e9},files:[{name:'Synthetic file.mkv',size:1e9,progress:1}],trackers:[],peers:[]};
      await route.fulfill({json:data});
    });
    await page.goto(origin+'/static/index.html');
    await page.locator('#torrents .torrent').first().waitFor();
    await page.waitForFunction(()=>document.querySelector('.seed-flow-metrics').textContent.includes('2,24'));
    if (width===1672) await page.evaluate(()=>{document.documentElement.dataset.appNavigation='dock-top';});
    assert.equal(await page.locator('#workspace').getAttribute('data-torrent-layout'),'list');
    assert.equal(await page.locator('#torrents .torrent').count(),7);
    assert.equal(await page.locator('#torrents progress').count(),7);
    assert.equal(await page.locator('.seed-column-tracker,.seed-column-size,.seed-list-tracker,.seed-list-size').count(),0);
    assert.match(await page.locator('#torrents .seed-identity>.seed-subline').first().textContent(),/(NordicBytes|SceneTime|LinuxTracker)/);
    const rowSize=await page.locator('#torrents .torrent').first().boundingBox();assert(rowSize.height>=110);
    assert.equal(await page.locator('#torrents .seed-poster').first().evaluate(e=>Math.round(e.getBoundingClientRect().height)),78);
    const statusColour=async tone=>page.locator(`#torrents [data-tone=${tone}] .torrent-state`).first().evaluate(e=>getComputedStyle(e).color);
    assert.notEqual(await statusColour('downloading'),await statusColour('checking'));assert.notEqual(await statusColour('checking'),await statusColour('waiting'));assert.notEqual(await statusColour('waiting'),await statusColour('error'));
    if(width===1672)assert((await page.locator('#torrents .seed-progress-cell').first().boundingBox()).width>=200,'Freed columns should make progress longer');
    assert.equal(await page.locator('.download-route-node').count(),4);
    assert.match(await page.locator('#download-flow-status').textContent(),/verificeret.*Danmark/);
    assert.match(await page.locator('.seed-flow-metrics').textContent(),/2,86.*2,24 TiB/);
    assert(await page.locator('.packet-lane-download .traffic-packet').count()>0);
    assert(await page.locator('.packet-lane-upload .traffic-packet').count()>0);
    assert.equal(await page.locator('.packet-lane-download .traffic-packet').first().evaluate(e=>getComputedStyle(e).animationDirection),'normal');
    assert.equal(await page.locator('.packet-lane-upload .traffic-packet').first().evaluate(e=>getComputedStyle(e).animationDirection),'reverse');
    // Check geometry, not merely DOM presence: overlapping controls would pass count tests.
    if (width>760) {
      const bounds=await page.locator('#torrents .torrent').first().evaluate(row=>[...row.children].filter(e=>getComputedStyle(e).display!=='none').map(e=>({name:e.className,x:e.getBoundingClientRect().x,right:e.getBoundingClientRect().right})));
      const actions=bounds.find(e=>e.name==='actions');
      for (const cell of bounds.filter(e=>e!==actions)) assert(cell.right<=actions.x+1,'Actions overlap '+cell.name+' at '+width);
      const activity=bounds.find(e=>e.name==='seed-activity'),progress=bounds.find(e=>e.name==='seed-progress-cell');
      assert(activity.right<=progress.x+1,'Status and progress must occupy different columns');
    }
    await page.locator('#tracker-filter').selectOption('NordicBytes');
    assert.equal(await page.locator('#torrents .torrent').count(),3);
    await page.locator('#search').fill('Sintel');assert.equal(await page.locator('#torrents .torrent').count(),1);
    await page.locator('#search').fill('');await page.locator('#tracker-filter').selectOption('');
    if (width>760) {
      await page.locator('.seed-column-name').click();
      assert.equal(await page.locator('#sort').inputValue(),'name');
      const direction=await page.locator('#sort-direction').inputValue();
      await page.locator('.seed-column-name').click();
      assert.equal(await page.locator('#sort-direction').inputValue(),direction==='asc'?'desc':'asc');
      const titles=await page.locator('#torrents .torrent-name').allTextContents();
      assert.deepEqual(titles,[...titles].sort((a,b)=>a.localeCompare(b,'da')*(direction==='asc'?-1:1)));
    }
    // The Add button remains with the list after navigation and language changes.
    await page.locator('[data-view=feeds]').click();await page.locator('[data-view=downloads]').click();
    assert.equal(await page.locator('.seed-list-actions #new-torrent').isVisible(),true);
    assert.equal(await page.locator('[data-layout],.seed-layout-buttons').count(),0);
    await page.evaluate(()=>localStorage.setItem('mediaseed.torrentLayout','cards'));
    await page.reload();await page.locator('#torrents .torrent').first().waitFor();
    assert.equal(await page.locator('#workspace').getAttribute('data-torrent-layout'),'list','Old card preference migrates to list');
    assert.equal(await page.evaluate(()=>localStorage.getItem('mediaseed.torrentLayout')),null);
    if(width===1672)await page.evaluate(()=>{document.documentElement.dataset.appNavigation='dock-top';});
    // No dependency on new settings: an old browser profile defaults directly to list.
    if (width>=1440) {
      await page.locator('#torrents .seed-files-button').first().click();
      await page.locator('.seed-detail-content').getByText('Synthetic file.mkv',{exact:true}).waitFor();
      assert.match(await page.locator('.seed-detail-content').textContent(),/Synthetic file/);
      await page.locator('#close-details').click();
    }
    for (const theme of ['midnight-glass','soft-orbit','precision-terminal','floating-layers','edge-signal','classic','light','duck-pond']) {
      await page.evaluate(value=>MediaAppTheme.apply(value),theme);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,'Overflow '+theme+' '+width);
    }
    await page.evaluate(()=>MediaAppTheme.apply('midnight-glass'));
    await page.evaluate(()=>{document.querySelector('#sort').value='added_on';document.querySelector('#sort-direction').value='desc';renderTorrents();window.scrollTo(0,0);});
    if(width===1672)await page.evaluate(()=>{document.documentElement.dataset.appNavigation='dock-top';});
    if ([1672,390].includes(width)) await page.screenshot({path:`/tmp/mediaseed-seedbox-list-${width}.png`,fullPage:true});
    if(width===1672){await page.evaluate(()=>MediaAppTheme.apply('duck-pond'));await page.locator('.seed-torrent-panel').screenshot({path:'/tmp/mediaseed-roomier-torrents-preview.png'});await page.evaluate(()=>MediaAppTheme.apply('midnight-glass'));}
    idle=true;await page.evaluate(()=>refresh());
    assert.equal(await page.locator('#download-flow .traffic-packet').count(),0,'Idle traffic stops both lanes immediately');
    idle=false;await page.evaluate(()=>refresh());
    valid=false;await page.evaluate(()=>refresh());
    assert.equal(await page.locator('#download-flow').getAttribute('data-active'),'false');
    assert.equal(await page.locator('#download-flow .traffic-packet').count(),0);
    assert(!/verificeret/i.test(await page.locator('#download-flow-status').textContent()));
    valid=true;await page.evaluate(()=>refresh());
    failed=true;await page.evaluate(()=>refresh());
    assert.equal(await page.locator('#download-flow .traffic-packet').count(),0);
    assert.match(await page.locator('.seed-rate-download').textContent(),/—/);
    assert.equal(await page.locator('#torrents .torrent').count(),7,'Outages retain rows');
    failed=false;await page.evaluate(()=>refresh());
    const speed=rows[0].dlspeed; rows[0].dlspeed=null; await page.evaluate(()=>refresh());
    assert.equal(await page.locator('#download-flow .traffic-packet').count(),0,'Unknown throughput must not animate');
    rows[0].dlspeed=speed; await page.evaluate(()=>refresh());
    await page.evaluate(()=>{appRole='viewer';renderTorrents();MediaSeedWorkspace.render();});
    assert.equal(await page.locator('#torrents .torrent-toggle').count(),0);
    assert.equal(await page.locator('.seed-flow-metric').nth(2).isVisible(),false,'Viewer must not reveal tracker account totals');
    await page.evaluate(()=>{appRole='administrator';renderTorrents();MediaSeedWorkspace.render();});
    await page.emulateMedia({reducedMotion:'reduce'});
    assert.equal(await page.locator('.traffic-packet').first().evaluate(e=>getComputedStyle(e).animationName),'none');
    assert.equal(writes,0,'Presentation must not mutate torrents or VPN settings');
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({width,listAndCards:true,trackerFilter:true,sort:true,geometry:true,fourHops:true,staleAndIdleStop:true,themes:8,writes,errors}));
    await page.close();
  }
  await browser.close();
})().catch(error=>{console.error(error);process.exit(1);});
