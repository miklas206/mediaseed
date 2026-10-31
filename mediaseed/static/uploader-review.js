/* Review and explicit publication in the native upload workspace. */
(() => {
 const node=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;};
 let settings;
 async function responseJson(response){
  const text=await response.text();let data;
  try{data=JSON.parse(text);}catch{throw Error(response.status===401?'Din session er udløbet. Log ind igen.':'Serveren svarede ikke med et gyldigt svar (HTTP '+response.status+'). Tjek uploadstatus, før du prøver at sende igen.');}
  if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'Handlingen kunne ikke gennemføres (HTTP '+response.status+').');
  return data;
 }
 const config=()=>settings??=fetch('/api/uploader/status').then(responseJson).catch(e=>{settings=undefined;throw e;});
 async function request(path,body){const me=await fetch('/api/auth/me').then(responseJson);return fetch('/api/uploader/'+path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json','X-MediaSeed-CSRF':me.csrf},...(body?{body:JSON.stringify(body)}:{})}).then(responseJson);}
 async function accountPicker(parent){const label=node('label','Trackerkonto'),select=node('select');label.append(select);parent.append(label);const data=await config();for(const a of data.trackers||[])select.append(new Option(a.name,a.id));if(!select.options.length)parent.append(node('p','Tilføj NordicBytes under Trackere i MediaSeed for at bruge dubletkontrol og API-upload.'));return select;}
 const labels={size:'Størrelse',videoCodec:'Videocodec',width:'Bredde',height:'Højde',bitDepth:'Bitdybde',frameRate:'Billeder/s',videoBitrate:'Videobitrate',totalBitrate:'Samlet bitrate',audioBitrate:'Lydens bitrate',duration:'Varighed',hdr:'HDR',audio:'Lydspor',subtitles:'Undertekster'};
 const num=(n,d=2)=>Number(n).toLocaleString('da-DK',{maximumFractionDigits:d});
 const rate=n=>n==null?'Ikke oplyst':num(n/1e6)+' Mbit/s';
 function audioText(tracks){if(tracks==null)return 'Ikke oplyst';if(!tracks.length)return 'Ingen lydspor';return tracks.map(t=>{const channels=t.channels==null?'ukendt kanalantal':/\bLFE\b/.test(t.channelLayout||'')&&t.channels===8?'7.1':/\bLFE\b/.test(t.channelLayout||'')&&t.channels===6?'5.1':num(t.channels)+' kanaler';return [(t.language||'ukendt sprog'),(t.codec||'ukendt codec')+(t.atmos?' Atmos':''),channels,rate(t.bitrate)].join(' · ');}).join('\n');}
 function subtitleText(tracks){if(tracks==null)return 'Ikke oplyst';if(!tracks.length)return 'Ingen indlejrede tekster';const groups=new Map();for(const t of tracks){const lang=t.language||'ukendt sprog';if(!groups.has(lang))groups.set(lang,new Set());groups.get(lang).add((t.format||'ukendt format')+(t.forced?' forced':'')+(t.sdh?' SDH':''));}return [...groups].map(([lang,formats])=>lang+' ('+[...formats].join(', ')+')').join(' · ');}
 function value(f,key){const x=f?.[key];if(key==='audio')return audioText(x);if(key==='subtitles')return subtitleText(x);if(x==null)return 'Ikke oplyst';if(key==='size')return num(x/1e9)+' GB ('+num(x/1024**3)+' GiB)';if(/Bitrate$/.test(key))return rate(x);if(key==='hdr')return x.join(' + ');if(key==='duration')return num(x/60)+' min';return String(x);}
 function results(parent,data){
  parent.classList.add('upload-comparison-list');parent.replaceChildren();
  const local=node('section');local.className='upload-own-facts';local.append(node('h4','Din fil'),node('p',value(data.local,'size')+' · video '+value(data.local,'videoBitrate')+' · samlet '+value(data.local,'totalBitrate')),node('p',value(data.local,'audio')));parent.append(local);
  parent.append(node('p','Størrelse og bitrate fortæller ikke alene billedkvaliteten. Data på trackeren kan være ufuldstændige. Forskelle er ikke automatisk tilladelse til upload.'));
  for(const row of [...data.matches,...(data.different||[]),...data.uncertain]){
   const card=node('section');card.className='upload-comparison-card';const heading=node('h4'),link=node('a',row.name);link.href=row.url;link.target='_blank';link.rel='noopener noreferrer';heading.append(link);card.append(heading);
   const comparison=row.comparison;card.append(node('p',comparison?.status==='equivalent'?'Teknisk tilsvarende · upload blokeret':comparison?.status==='different'?'Tekniske forskelle · kræver gennemgang':'Manglende oplysninger · kræver gennemgang'));
   const table=node('table'),head=node('thead'),hr=node('tr');for(const text of ['Egenskab','Din fil','Trackerudgaven'])hr.append(node('th',text));head.append(hr);table.append(head);const body=node('tbody');
   for(const key of ['size','videoCodec','width','height','videoBitrate','totalBitrate','bitDepth','frameRate','duration','hdr','audio','subtitles']){const tr=node('tr'),field=comparison?.fields.find(f=>f.field===key);tr.dataset.comparison=field?.status||'unknown';tr.append(node('th',labels[key]),node('td',value(data.local,key)),node('td',value(comparison?.facts,key)));body.append(tr);}table.append(body);card.append(table);
   const differences=comparison?.fields.filter(f=>f.status==='different').map(f=>labels[f.field])||[],missing=comparison?.fields.filter(f=>f.status==='unknown').map(f=>labels[f.field])||[];
   if(differences.length)card.append(node('p','Forskelle: '+differences.join(', ')+'.'));
   if(missing.length)card.append(node('p','Ikke tilstrækkeligt oplyst: '+missing.join(', ')+'.'));
   parent.append(card);
  }
  parent.append(node('p','Tilsvarende intervaller: størrelse ±5 %, bitrate ±10 %, varighed ±1 % og billedfrekvens ±0,5 %. Lydspor, undertekster og HDR sammenlignes særskilt.'));
  if(data.apiUploadRoute)parent.append(node('p','Upload-API findes. Uploadrettigheder kontrolleres af trackeren ved afsendelse.'));
 }
 function mount(parent,job){
  const box=node('section');box.className='uploader-review upload-manual-seeding';box.append(node('h3','Manuel upload og seeding'),node('p','Upload torrent, NFO og beskrivelse på trackeren. Vælg derefter trackerens færdige torrentfil eller indsæt linket til uploaden. Medierne bliver i deres nuværende mappe.'));parent.append(box);
  const account=node('div'),open=node('a','Åbn trackerens uploadside');open.target='_blank';open.rel='noopener noreferrer';open.hidden=true;box.append(account,open);
  const controls=node('div');controls.className='upload-seeding-inputs';
  const urlLabel=node('label','Link til den færdige upload'),url=node('input');url.type='url';url.placeholder='https://nordicbytes.org/torrents/12345';urlLabel.append(url);
  const fileLabel=node('label','Eller vælg torrentfilen fra trackeren'),file=node('input');file.type='file';file.accept='.torrent,application/x-bittorrent';fileLabel.append(file);controls.append(urlLabel,fileLabel);box.append(controls);
  const confirmLabel=node('label'),confirm=node('input');confirm.type='checkbox';confirmLabel.className='check';confirmLabel.append(confirm,node('span','Jeg har uploadet denne pakke manuelt på trackeren og vil starte seeding.'));box.append(confirmLabel);
  const actions=node('div'),start=node('button','Start seeding'),refresh=node('button','Opdater seedingstatus');start.type=refresh.type='button';actions.append(start,refresh);box.append(actions);
  const state=node('p','Efter manuel upload: vælg trackerens torrentfil eller indsæt uploadlinket.');state.className='upload-publication-status';state.setAttribute('role','status');state.setAttribute('aria-live','polite');box.append(state);
  if(!job.hasTorrent)state.textContent='Klargør en lokal torrentfil til pakken først, så MediaSeed kan kontrollere trackerens fil mod din pakke.';
  const details=node('details'),summary=node('summary','Dubletkontrol og tidligere API-forsøg'),check=node('button','Tjek dubletter'),output=node('div');check.type='button';details.append(summary,check,output);box.append(details);
  let chosen,busy=false,phase='not_started',timer;
  const active=()=>['queued','awaiting_client','stopping','checking','waiting_vpn','starting','seeding'].includes(phase);
  const sync=()=>{start.disabled=busy||active()||!job.hasTorrent||!chosen?.value||!confirm.checked||(!file.files.length&&!url.value.trim());start.textContent=busy?'Klargør seeding…':phase==='checking'?'Kontrollerer filer…':phase==='waiting_vpn'?'Afventer VPN…':phase==='seeding'?'Seeder':'Start seeding';};
  const show=value=>{phase=value.status;state.textContent=value.message+(value.status==='checking'&&Number.isFinite(value.progress)?' '+Math.round(value.progress*100)+' %':'');sync();};
  async function status(){clearTimeout(timer);refresh.disabled=true;try{show(await request('jobs/'+job.id+'/seeding'));if(active()&&phase!=='seeding')timer=setTimeout(()=>{if(box.isConnected)void status();},3000);}catch(e){state.textContent=e.message;}finally{refresh.disabled=false;}}
  refresh.onclick=()=>void status();
  for(const input of [confirm,url,file])input.onchange=sync;url.oninput=sync;
  void accountPicker(account).then(async select=>{chosen=select;const data=await config();const update=()=>{const value=data.trackers.find(a=>a.id===select.value);open.href=value?.uploadUrl||'#';open.hidden=!value?.uploadUrl;sync();};chosen.onchange=update;update();}).catch(e=>state.textContent=e.message);
  start.onclick=async()=>{busy=true;sync();state.textContent='Henter og kontrollerer torrentfilen. Ingen medier flyttes.';try{const me=await fetch('/api/auth/me').then(responseJson);const selected=file.files[0],query=new URLSearchParams({tracker:chosen.value,confirmed:String(confirm.checked)});const response=await fetch('/api/uploader/jobs/'+job.id+(selected?'/seed-file?'+query:'/seed'),{method:'POST',headers:{'X-MediaSeed-CSRF':me.csrf,'Content-Type':selected?'application/x-bittorrent':'application/json'},body:selected||JSON.stringify({tracker:chosen.value,confirmed:confirm.checked,url:url.value.trim()})});show(await responseJson(response));confirm.checked=false;void status();}catch(e){state.textContent=e.message;}finally{busy=false;sync();}};
  check.onclick=async()=>{check.disabled=true;details.open=true;try{const data=await request('jobs/'+job.id+'/check',{tracker:chosen?.value||''});results(output,data);output.prepend(node('p',data.message));}catch(e){state.textContent=e.message;}finally{check.disabled=false;}};
  void request('jobs/'+job.id+'/publication').then(value=>{if(value.state!=='not_published'){const old=node('details'),title=node('summary','Tidligere API-status · '+value.state),log=node('pre',JSON.stringify({time:value.at?new Date(value.at*1000).toISOString():null,state:value.state,...value.diagnostics,history:value.history},null,2));log.className='upload-publication-log';old.append(title,log);details.append(old);}}).catch(()=>{});
  sync();void status();
 }
 async function conversion(hint,id){
  const root=document.querySelector('#uploader-workspace'),box=node('section');box.className='uploader-review conversion-review';
  box.append(node('h2','Konverteret film · dubletkontrol'));
  const state=node('p','Den konverterede fil er valgt. Originalkilden er krediteret. Gennemgå oplysningerne før klargøring.');box.append(state);
  const details=node('details'),summary=node('summary','Sammenlign eksisterende udgaver');details.open=true;details.className='conversion-review-details';details.append(summary);box.append(details);
  root.querySelector('.conversion-review')?.remove();root.querySelector('#upload-tools-panel').before(box);
  const select=await accountPicker(details),check=node('button','Tjek om udgaven allerede findes');check.type='button';details.append(check);
  const output=node('div');details.append(output);
  const resume=node('button','Fortsæt med upload ↑');resume.type='button';resume.className='conversion-review-resume';details.append(resume);
  resume.onclick=()=>{details.open=false;const panel=root.querySelector('.wizard-panel:not([hidden])');panel?.querySelector('h2')?.focus({preventScroll:true});panel?.scrollIntoView({block:'start',behavior:'instant'});};
  check.onclick=async()=>{check.disabled=true;details.open=true;try{const data=await request('conversions/'+id+'/check?tracker='+encodeURIComponent(select.value));results(output,data);state.textContent=data.message;}catch(e){state.textContent=e.message;}finally{check.disabled=false;}};
  if(hint.tmdb&&select.value)check.click();
 }

 window.MediaSeedUploaderReview={mount,conversion};
})();
