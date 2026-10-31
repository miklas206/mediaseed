const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'@playwright/test');
(async()=>{
 const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
 const origin=process.env.MEDIASEED_TEST_ORIGIN||'http://127.0.0.1:15384';
 for(const width of [390,1440]){
  const page=await browser.newPage({viewport:{width,height:400},ignoreHTTPSErrors:true});
  await page.goto(origin+'/static/packet-test-document');
  await page.setContent('<style>*{box-sizing:border-box}body{margin:20px;background:#10212a;--surface:#10212a;--line:#487080;--accent:#00cdef;--healthy:#67ddb4;color:white}</style><section class="download-flow" data-active="true"><div class="download-route">'+['Internet','VPN','MediaSeed','Storage'].map((name,i)=>(i?'<span class="download-route-link"></span>':'')+'<span class="download-route-node"><span class="route-icon">•</span><strong>'+name+'</strong></span>').join('')+'</div></section>');
  await page.addStyleTag({url:origin+'/static/download-workspace.css?v=packet-lines-2'});
  await page.addScriptTag({url:origin+'/static/traffic-packets.js'});
  await page.evaluate(()=>{window.flow=MediaSeedPackets.mount(document.querySelector('.download-route'));flow.update(1048576,1048576,true);});
  for(const reduced of [false,true]){
   await page.evaluate(value=>{document.documentElement.dataset.reducedMotion=String(value);flow.update(1048576,1048576,true);},reduced);
   await page.waitForTimeout(100);
   const result=await page.evaluate(()=>{
    const overlay=document.querySelector('.route-packets'),bounds=overlay.getBoundingClientRect();
    return [...document.querySelectorAll('.traffic-packet')].map(packet=>{const rect=packet.getBoundingClientRect();const upload=packet.parentElement.classList.contains('packet-lane-upload');return {offset:Math.abs(rect.top+rect.height/2-(upload?bounds.bottom:bounds.top)),size:rect.height};});
   });
   if(result.length<6||result.some(row=>row.offset>.1))throw Error('Packet off line: '+JSON.stringify({width,reduced,result}));
  }
  const layers=await page.evaluate(()=>{
   const overlay=document.querySelector('.route-packets'),lane=document.querySelector('.packet-lane'),node=document.querySelector('.download-route-node');
   return {line:Number(getComputedStyle(overlay,'::after').zIndex),packets:Number(getComputedStyle(lane).zIndex),overlay:Number(getComputedStyle(overlay).zIndex),icons:Number(getComputedStyle(node).zIndex)};
  });
  if(layers.packets<=layers.line||layers.icons<=layers.overlay)throw Error('Wrong paint order: '+JSON.stringify(layers));
  console.log(JSON.stringify({width,packetSizes:3,directions:2,reducedMotion:true,aligned:true,layers}));await page.close();
 }
 await browser.close();
})().catch(error=>{console.error(error);process.exit(1)});
