const {chromium} = require(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({executablePath:'/usr/bin/chromium', args:['--no-sandbox']});
  const origin = process.env.MEDIASEED_TEST_ORIGIN || 'http://127.0.0.1:15386';
  for (const width of [1440, 390, 320]) {
    const page = await browser.newPage({ignoreHTTPSErrors:true,viewport:{width,height:1000}}), errors=[], saves=[];
    let policy={mode:'never',status:'disabled'}, failSave=false, failLoad=false, overviewReads=0, role='administrator';
    const hash='a'.repeat(40), now=Date.now()/1000, added=100;
    const torrent={hash,name:'Synthetic.Long.Show.S01.DANiSH.1080p.WEB-DL.H.264.DD5.1-Group',size:2**30,uploaded:0,downloaded:2**30,progress:1,ratio:0,credited_ratio:0,ratio_limit:1,state:'stalledUP',added_on:added,seeding_time:1200,tracker_name:'Synthetic tracker'};
    page.on('pageerror',error=>errors.push(error.message));
    await page.addInitScript(()=>{window.EventSource=class extends EventTarget{close(){}};});
    await page.route('**/api/**',async route=>{
      const req=route.request(), url=new URL(req.url()), path=url.pathname;let result=[];
      if(path==='/api/auth/status')result={needsSetup:false};
      else if(path==='/api/auth/me')result={username:'synthetic',role,language:'da',csrf:'synthetic'};
      else if(path==='/api/dashboard')result={runtime:{ready:true,downloadsEnabled:true,checkedAt:now},torrents:[torrent,{...torrent,hash:'b'.repeat(40),name:'Another release'}],queue:{active:0,limit:8,waiting:0}};
      else if(path.endsWith('/cleanup')){
        if(req.method()==='PUT'){
          saves.push(req.postDataJSON());
          if(failSave){await route.fulfill({status:503,json:{detail:'Synthetic save failure'}});return;}
          const body=req.postDataJSON();policy={mode:body.mode,hours:body.hours,ratio:body.ratio,delete_files:body.delete_files,status:body.mode==='never'?'disabled':'active'};result={saved:true,policy};
        }else{
          if(failLoad){await route.fulfill({status:503,json:{detail:'Synthetic load failure'}});return;}
          result={policy,added_on:added};
        }
      }
      else if(path.endsWith('/details')){overviewReads++;result={properties:{total_downloaded:2**30,total_uploaded:0,addition_date:added},credit:{},cleanup:policy,downloadFolder:{label:'Synthetic NAS',path:'/downloads/very-long-folder/'.repeat(3)},files:[],trackers:[],peers:[]};}
      else if(path==='/api/storage')result={defaultLocation:'local',locations:[{id:'local',label:'Local',available:true,writable:true,totalBytes:3e12,freeBytes:2e12}]};
      else if(path==='/api/client')result={name:'qBittorrent',version:'v5.2.4'};
      else if(path==='/api/ratio-scout')result={settings:{enabled:false,automatic:false},candidates:[],scanMinutes:10,resultLimit:10,dailyUsed:0};
      await route.fulfill({json:result});
    });
    await page.goto(origin+'/static/index.html');
    const item=page.locator('#torrents [data-hash="'+hash+'"]');
    const first=item.locator('.torrent-menu');
    await first.locator('summary').click();
    await first.getByRole('button',{name:/Rediger oprydningsregel/}).waitFor();
    const menuBox=await first.locator('.torrent-menu-content').boundingBox();
    assert(menuBox.x>=0 && menuBox.x+menuBox.width<=width, 'Menu outside viewport');
    await page.screenshot({path:`/tmp/torrent-menu-${width}.png`,fullPage:true});
    await page.keyboard.press('Escape');assert.equal(await first.evaluate(e=>e.open),false);
    await first.locator('summary').click();
    await first.getByRole('button',{name:/Rediger oprydningsregel/}).click();
    const editor=page.locator('#torrent-cleanup');
    await editor.getByText('Ændringer gælder kun denne torrent.',{exact:true}).waitFor();
    const mode=editor.locator('select').nth(0), action=editor.locator('select').nth(1);
    const hours=editor.locator('input[type=number]').nth(0), ratio=editor.locator('input[type=number]').nth(1);
    await mode.selectOption('both');await hours.fill('80');await ratio.fill('3');
    assert((await editor.locator('.cleanup-rule-summary').textContent()).includes('80'));
    assert((await editor.locator('.cleanup-rule-summary').textContent()).includes('3'));
    await page.screenshot({path:`/tmp/torrent-cleanup-${width}.png`,fullPage:true});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
    await editor.getByRole('button',{name:'Gem oprydningsregel',exact:true}).click();
    await editor.waitFor({state:'detached'});
    assert.deepEqual(saves.at(-1),{mode:'both',hours:80,ratio:3,delete_files:false,added_on:100,confirm_delete_files:false});

    // Reload the persisted rule, reject unconfirmed deletion and preserve errors/drafts.
    await first.locator('summary').click();await first.getByRole('button',{name:/Rediger oprydningsregel/}).click();
    await editor.getByText('Ændringer gælder kun denne torrent.',{exact:true}).waitFor();
    assert.equal(await hours.inputValue(),'80');assert.equal(await ratio.inputValue(),'3');
    await action.selectOption('files');const before=saves.length;
    await editor.getByRole('button',{name:'Gem oprydningsregel',exact:true}).click();assert.equal(saves.length,before);
    await editor.locator('input[type=checkbox]').check();failSave=true;
    await editor.getByRole('button',{name:'Gem oprydningsregel',exact:true}).click();
    await editor.getByRole('alert').getByText('Synthetic save failure').waitFor();
    assert.equal(await hours.inputValue(),'80');assert.equal(await editor.locator('input[type=checkbox]').isChecked(),true);
    failSave=false;await editor.getByRole('button',{name:'Gem oprydningsregel',exact:true}).click();
    await editor.waitFor({state:'detached'});assert.equal(saves.at(-1).confirm_delete_files,true);

    await item.locator('.torrent-name').click();
    const details=page.locator('#torrent-details');await details.getByText('Opdateret ',{exact:false}).waitFor();
    assert.equal(await details.locator('.detail-header-actions #detail-refresh').count(),1);
    const beforeRefresh=overviewReads;
    await details.locator('#detail-refresh').click();await page.waitForFunction(()=>!document.querySelector('#detail-refresh').disabled);
    assert(overviewReads>beforeRefresh);
    await details.getByRole('tab',{name:'Oprydningsregel',exact:true}).click();
    await details.getByRole('button',{name:'Rediger oprydningsregel',exact:true}).click();
    await editor.getByText('Ændringer gælder kun denne torrent.',{exact:true}).waitFor();
    await hours.fill('-1');await ratio.fill('');await mode.selectOption('never');await editor.getByRole('button',{name:'Behold torrent og filer',exact:true}).click();
    await editor.waitFor({state:'detached'});
    assert.equal(saves.at(-1).hours,50);assert.equal(saves.at(-1).ratio,1);
    assert.equal(await details.getByRole('tab',{name:'Oprydningsregel',exact:true}).getAttribute('aria-selected'),'true');
    await details.locator('.seed-detail-content').getByText('Seeding fortsætter, indtil du selv stopper eller fjerner torrenten. Ingen automatisk tids- eller ratiogrænse.',{exact:true}).waitFor();
    await details.getByRole('tab',{name:'Overblik',exact:true}).click();
    await page.screenshot({path:`/tmp/torrent-details-${width}.png`,fullPage:true});
    assert.equal(await details.evaluate(e=>e.scrollWidth>e.clientWidth+1),false,'Details overflow');
    await details.locator('#close-details').click();

    failLoad=true;await first.locator('summary').click();await first.getByRole('button',{name:/Rediger oprydningsregel/}).click();
    await editor.getByText('Synthetic load failure',{exact:true}).waitFor();assert.equal(await editor.locator('form').isVisible(),false);
    failLoad=false;await editor.getByRole('button',{name:'Prøv igen',exact:true}).click();
    await editor.getByText('Ændringer gælder kun denne torrent.',{exact:true}).waitFor();await editor.getByRole('button',{name:'Annuller',exact:true}).click();
    await first.locator('summary').click();await first.getByRole('button',{name:/^Kontrollér filer/}).click();
    await page.locator('#recheck-torrent').waitFor({state:'visible'});await page.locator('#cancel-recheck').click();
    await first.locator('summary').click();await first.getByRole('button',{name:/^Fjern…/}).click();
    await page.locator('#remove-torrent').waitFor({state:'visible'});assert.equal(await page.locator('#remove-files').isChecked(),false);await page.locator('#cancel-remove').click();
    role='viewer';await page.reload();await first.locator('summary').click();assert.equal(await first.getByRole('button').count(),2);assert(await first.getByRole('button',{name:/Kopiér magnetlink/}).isEnabled());assert.equal(await first.getByRole('button',{name:/Rediger oprydningsregel/}).count(),0);
    assert.deepEqual(errors,[]);console.log(JSON.stringify({width,saves:saves.length,menu:true,cleanupEditor:true,refreshInHeader:true,errors}));await page.close();
  }
  await browser.close();
})().catch(error=>{console.error(error);process.exit(1);});
