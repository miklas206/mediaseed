const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'@playwright/test');
const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
try{for(const width of [1440,390]){
 const page=await browser.newPage({viewport:{width,height:900}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.setContent('<select id="sort"><option>name</option></select><main></main><dialog id="details"><h2 id="detail-name"></h2><p id="detail-message"></p><form id="policy-form"><input></form><button id="detail-refresh"></button><div id="detail-data"></div></dialog>');
 await page.evaluate(()=>{
  Object.assign(window,{t:v=>v==='Tracker unknown'?'Tracker ukendt':v,element:(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;},
   state:()=> 'seeding',trackerBytes:v=>String(v||0),bytes:v=>String(v||0),trackerNumber:v=>String(v),locale:()=> 'en',torrentStatus:v=>v,appRole:'viewer',downloadsEnabled:true,csrf:'synthetic',dialog:document.querySelector('dialog'),
   command:(text,callback)=>{const e=document.createElement('button');e.textContent=text;e.onclick=callback;return e;},torrentActionMenu:()=>document.createElement('span'),api:async()=>({})});
 });
 await page.addScriptTag({content:fs.readFileSync(path.resolve(__dirname,'../mediaseed/static/rich-ui.js'),'utf8')});
 for(const [fields,label] of [[{tracker_provider:'nordicbytes'},'NordicBytes'],[{tracker_provider:null},'Tracker ukendt'],[{tracker_provider:'nordicbytes',tracker_name:'My tracker account'},'My tracker account'],[{tracker_benefits:{tracker:'Other tracker'}},'Other tracker']]){
  const row={hash:'a'.repeat(40),name:'Synthetic.Upload',size:123,state:'stalledUP',progress:1,ratio_limit:1,...fields};
  await page.evaluate(async row=>{document.querySelector('main').replaceChildren(seedTorrent(row,0));await richDetails(row,'activity');},row);
  assert((await page.locator('.seed-subline').first().textContent()).includes(label));
  assert((await page.locator('.seed-detail-hero').textContent()).includes(label));
  assert.equal(await page.locator('main .seed-badge.free').count(),0);
  assert.equal(await page.locator('main .seed-badge.double').count(),0);
  await page.evaluate(()=>dialog.close());
 }
 assert.deepEqual(errors,[]);console.log(`Tracker labels ${width}px: known announce provider works without API metadata; saved names and benefits labels preserved; unknown remains unknown; no benefits invented.`);await page.close();
}}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
