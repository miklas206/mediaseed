/* Server-only conversion workspace. Writes require explicit user actions. */
(() => {
 const page=document.querySelector('#conversions');if(!page)return;
 // Keep each section independent, including across page reloads and status polling.
 for(const panel of page.querySelectorAll('.conversion-panel')){
  const key='mediaseed.'+panel.id+'.open';
  try{const saved=localStorage.getItem(key);if(saved!==null)panel.open=saved==='true';}catch{}
  panel.addEventListener('toggle',()=>{try{localStorage.setItem(key,String(panel.open));}catch{}});
 }
 const icon=document.querySelector('[data-view=conversions] .app-nav-icon svg');
 if(icon)icon.innerHTML='<path d="M4 8h12l-3-3m7 11H8l3 3M20 8v3M4 16v-3"/>';
 const $=id=>document.getElementById(id), selected=new Map();
 let running=false,timer,selectionVersion=0,lastAdvice='';
 const phase={queued:'I kø',encoding:'Konverterer',verifying:'Kontrollerer',ready:'Publicerer',complete:'Færdig',cancelled:'Annulleret',error:'Kræver kontrol'};
 const btn=(label,action)=>{const b=element('button',label);b.type='button';b.onclick=action;return b;};
 const mutate=()=>typeof appRole!=='undefined'&&appRole!=='viewer';
 const duration=v=>v==null?'—':v>=3600?Math.floor(v/3600)+' t '+Math.round(v%3600/60)+' min':Math.max(1,Math.round(v/60))+' min';
 async function removeOriginal(job){
  const dialog=element('dialog',undefined,'media-conversion-dialog conversion-remove-dialog');
  dialog.setAttribute('aria-label','Fjern original');dialog.append(element('h2','Fjern original'));
  const status=element('p','Kontrollerer originalen og seeding af den konverterede kopi…');status.setAttribute('role','status');
  const content=element('div'),close=btn('Luk',()=>dialog.close());let sending=false;
  dialog.append(content,status,close);document.body.append(dialog);dialog.addEventListener('close',()=>dialog.remove(),{once:true});dialog.addEventListener('cancel',e=>{if(sending)e.preventDefault();});dialog.showModal();
  try{
   const plan=await api('conversions/'+job.id+'/original');
   if(plan.status!=='ready'){status.textContent=plan.message;return;}
   content.append(element('h3',plan.name),element('p','Hele originaltorrenten fjernes fra klienten, og dens filer slettes permanent.'));
   content.append(element('p',plan.sourceFolder,'conversion-path'),element('p',plan.fileCount+' filer · '+bytes(plan.deleteBytes)));
   const files=element('details');files.append(element('summary','Vis originalfilerne'));const list=element('ul');for(const name of plan.files)list.append(element('li',name));if(plan.fileCount>plan.files.length)list.append(element('li','… og '+(plan.fileCount-plan.files.length)+' flere filer'));files.append(list);content.append(files);
   content.append(element('p','Den konverterede kopi og dens seeding bevares.'),element('p',plan.convertedFolder,'conversion-path muted'));
   const form=element('form'),label=element('label',undefined,'conversion-remove-confirm'),check=element('input');check.type='checkbox';check.required=true;
   label.append(check,element('span','Jeg vil stoppe originalens seeding og slette alle dens filer. Jeg har kontrolleret trackerens seedkrav.'));
   const submit=element('button','Fjern original og filer');submit.type='submit';submit.className='danger';submit.disabled=true;check.onchange=()=>{submit.disabled=!check.checked;};
   form.append(label,submit);content.append(form);status.textContent='Sletningen kan ikke fortrydes.';
   form.onsubmit=async e=>{e.preventDefault();if(!check.checked||sending)return;sending=true;submit.disabled=true;check.disabled=true;close.disabled=true;status.textContent='Sender anmodning om at fjerne originalen…';
    try{const result=await api('conversions/'+job.id+'/original/remove',{confirmed:true,confirmation:plan.confirmation});status.textContent=result.message;form.remove();await refresh();}
    catch(error){status.textContent=error.message+' Opdater status før et nyt forsøg.';form.remove();await refresh();}
    finally{sending=false;close.disabled=false;}
   };
  }catch(error){status.textContent=error.message;}
 }
 function jobCard(job){
  const card=element('article',undefined,'conversion-job');card.dataset.job=job.id;
  card.append(element('h3',job.name));
  card.append(element('p',(job.cancelling?'Annullerer…':phase[job.status]||job.status)+' · '+job.bitrate+' Mbit/s · '+job.fileCount+' videoer'+(job.queuePosition?' · Plads '+job.queuePosition+' i køen':'')));
  if(['encoding','verifying','ready'].includes(job.status)){
   const progress=element('progress');progress.max=100;progress.value=Number(job.progress)||0;progress.setAttribute('aria-label','Konverteringsfremdrift');
   card.append(progress,element('p',Math.round(job.progress||0)+' % · '+(job.worker||'Server · NVIDIA')+(job.fps?' · '+job.fps+' fps':'')+(job.etaSeconds!=null?' · ca. '+duration(job.etaSeconds)+' tilbage på denne video':'')));
   if(job.currentFile)card.append(element('p',job.currentFile,'conversion-path muted'));
   if(job.updated&&Date.now()/1000-job.updated>30)card.append(element('p','Status er ikke opdateret for nylig. Kontrollér serverforbindelsen.','muted'));
  }
  card.append(element('p',MediaSeedConversionOptions.summary(job.options),'muted'));
  card.append(element('p',job.destination,'conversion-path muted'));
  if(job.status==='complete'&&mutate()){
   const actions=element('div',undefined,'conversion-job-actions');actions.append(btn('Klargør upload',()=>window.dispatchEvent(new CustomEvent('uploader-open',{detail:{conversion:job.id}}))));
   if(!['pending','removed'].includes(job.originalRemoval?.status)){const remove=btn('Fjern original',()=>removeOriginal(job));remove.className='danger';actions.append(remove);}card.append(actions);
  }
  if(job.originalRemoval?.message)card.append(element('p',job.originalRemoval.message,job.originalRemoval.status==='removed'?'conversion-saving':'muted'));
  if(job.status==='complete')card.append(element('p',(job.originalRemoval?.status==='removed'?'Besparelse i forhold til original: ':'Mulig besparelse efter fjernelse af original: ')+bytes(job.potentialSavingBytes),'conversion-saving'));
  if(job.error)card.append(element('p',job.error,'error'));
  if(mutate()&&!['complete','cancelled'].includes(job.status)){
   const cancel=btn('Annullér',async()=>{cancel.disabled=true;try{await api('conversions/'+job.id+'/cancel',{});await refresh();}catch(e){notify(e);cancel.disabled=false;}});
   cancel.disabled=!!job.cancelling;card.append(cancel);
  }
  return card;
 }
 function jobs(result){
  const rows=result.jobs||[];
  $('conversion-page-state').textContent=result.ready?'Server · NVIDIA HEVC er klar':result.reason||'Konverteringsarbejderen er ikke klar';
  const active=rows.filter(j=>['encoding','verifying','ready'].includes(j.status)),queued=rows.filter(j=>j.status==='queued').sort((a,b)=>a.queuePosition-b.queuePosition);
  const finished=rows.filter(j=>!active.includes(j)&&!queued.includes(j));
  let clear=$('conversion-clear-history');
  if(!clear){const tools=element('div',undefined,'conversion-history-tools'),message=element('p','Mediefilerne bevares.','muted');message.id='conversion-history-state';message.setAttribute('role','status');clear=btn('Ryd historik',async()=>{clear.disabled=true;try{const result=await api('conversions/history/clear',{});await refresh();message.textContent=result.cleared+' poster fjernet fra historikken. Mediefilerne er bevaret.';}catch(e){notify(e);clear.disabled=false;}});clear.id='conversion-clear-history';tools.append(clear,message);$('conversion-completed').before(tools);}
  clear.hidden=!mutate();clear.disabled=!finished.length;

  $('conversion-summary').replaceChildren();
  for(const [caption,value] of [['Aktive',active.length],['I kø',queued.length],['Færdige i historikken',rows.filter(j=>j.status==='complete').length],['Mulig besparelse',bytes(rows.filter(j=>j.status==='complete').reduce((n,j)=>n+(j.potentialSavingBytes||0),0))]]){
   const tile=element('div',undefined,'workspace-card');tile.append(element('span',caption,'muted'),element('strong',String(value)));$('conversion-summary').append(tile);
  }
  for(const [id,list,empty] of [['conversion-active',active,'Ingen aktiv konvertering.'],['conversion-queue',queued,'Køen er tom. Vælg en anbefaling eller Konvertér video i torrentmenuen.'],['conversion-completed',finished,'Ingen historik endnu.']])$(id).replaceChildren(...(list.length?list.map(jobCard):[element('p',empty,'muted')]));
 }
 function recommendations(result){
  const signature=JSON.stringify([result,mutate()]);if(signature===lastAdvice)return;lastAdvice=signature;
  const choices=(result.rows||[]).filter(r=>r.recommended),valid=new Set(choices.map(r=>r.hash+':'+r.index));
  for(const key of selected.keys())if(!valid.has(key))selected.delete(key);
  $('conversion-analyze').hidden=!mutate();$('conversion-queue-selected').hidden=!mutate();
  $('conversion-analysis-state').textContent=!result.scanned?'Start en analyse for at finde film med pladsbesparelse.':(result.total||0)+' videofiler · '+(result.pending||0)+' afventer metadata · '+choices.length+' anbefalet'+(result.errors?' · '+result.errors+' kunne ikke analyseres':'')+(result.truncated?' · begrænset til de største torrents':'');
  const container=$('conversion-recommendations');container.replaceChildren();
  if(!choices.length)container.append(element('p',result.pending?'Metadata analyseres på serveren…':result.scanned?'Ingen film med en estimeret besparelse på mindst 15 % og 1 GiB ved denne målbitrate.':'Ingen analyse endnu.','muted'));
  for(const row of choices){
   const card=element('article',undefined,'conversion-advice-row'),key=row.hash+':'+row.index;
   const label=element('label',undefined,'conversion-pick'),check=element('input');check.type='checkbox';check.checked=selected.has(key);check.disabled=!mutate();
   check.onchange=()=>{if(check.checked)selected.set(key,row);else selected.delete(key);selectionVersion++;selection();};label.append(check,element('strong',row.title));
   card.append(label,element('p',row.name,'conversion-path muted'));
   card.append(element('p',bytes(row.originalBytes)+' → ca. '+bytes(row.estimatedBytes)+' · '+(row.codec?.toUpperCase()||'Video')+' → H265 · '+row.currentMbps+' Mbit/s samlet → '+row.targetMbps+' Mbit/s video'));
   card.append(element('p','Mulig besparelse: '+bytes(row.savingBytes)+' ('+bytes(row.savingLowBytes)+'–'+bytes(row.savingHighBytes)+')','conversion-saving'));
   if(mutate())card.append(btn('Vælg konvertering',()=>window.dispatchEvent(new CustomEvent('conversion-open',{detail:{hash:row.hash,indexes:[row.index],bitrate:Number($('conversion-advice-rate').value)}}))));
   container.append(card);
  }
  selection();
 }
 function selection(){const rows=[...selected.values()];$('conversion-queue-selected').disabled=!mutate()||!rows.length;$('conversion-queue-selected').textContent=rows.length?'Tilføj '+rows.length+' videoer til kø · ca. '+bytes(rows.reduce((n,r)=>n+r.savingBytes,0))+' mulig besparelse':'Tilføj valgte til kø';}
 async function refresh(){
  clearTimeout(timer);if(page.hidden||running)return;running=true;
  try{
   const version=selectionVersion,rate=$('conversion-advice-rate').value;
   const [state,rows]=await Promise.allSettled([api('conversions'),api('conversions/recommendations?bitrate='+rate)]);
   if(!page.hidden){
    if(state.status==='fulfilled')jobs(state.value);
    else $('conversion-page-state').textContent='Kunne ikke opdatere konverteringer: '+state.reason.message;
    if(version===selectionVersion&&rate===$('conversion-advice-rate').value){
     if(rows.status==='fulfilled')recommendations(rows.value);
     else {lastAdvice='';$('conversion-analysis-state').textContent='Kunne ikke hente forslag: '+rows.reason.message+' · Prøv Opdater status.';}
    }
   }
  }catch(e){$('conversion-page-state').textContent='Kunne ikke opdatere konverteringer: '+e.message;}
  finally{running=false;if(!page.hidden)timer=setTimeout(refresh,3000);}
 }
 $('conversion-refresh').onclick=refresh;
 $('conversion-advice-rate').onchange=()=>{selected.clear();selectionVersion++;void refresh();};
 $('conversion-analyze').onclick=async()=>{
  const b=$('conversion-analyze');b.disabled=true;$('conversion-analysis-state').textContent='Finder færdige film…';
  try{await api('conversions/analyze',{});await refresh();}catch(e){$('conversion-analysis-state').textContent=e.message;}finally{b.disabled=false;}
 };
 $('conversion-queue-selected').onclick=async()=>{
  if(!selected.size||!mutate())return;
  try{
   const [storage,preferences,worker]=await Promise.all([api('storage'),api('conversions/preferences'),api('conversions')]);
   const dialog=element('dialog',undefined,'media-conversion-dialog');
   dialog.append(element('h2','Tilføj valgte til kø'),element('p',selected.size+' videoer · H265 · '+$('conversion-advice-rate').value+' Mbit/s. Originalerne bevares til seeding.'));
   const form=element('form'),location=element('select'),label=element('label','Gem konverterede kopier i');
   for(const row of storage.locations||[])if(row.writable)location.append(new Option(row.label,row.id));
   location.value=(storage.locations||[]).some(r=>r.id==='converted-movies'&&r.writable)?'converted-movies':storage.defaultLocation||'local';label.append(location);
   const tagLabel=element('label','Dit tag'),tag=element('input');tag.value=preferences.release_tag||'MDTech';tag.required=true;tag.maxLength=32;tag.pattern='[A-Za-z0-9][A-Za-z0-9_-]{0,31}';tagLabel.append(tag);
   const status=element('p');status.setAttribute('role','status');const submit=element('button','Tilføj til kø');submit.type='submit';submit.className='primary';
   const close=btn('Luk',()=>dialog.close());form.append(label,tagLabel);
   const options=MediaSeedConversionOptions.mount(form,preferences.options);
   if(!worker.ready||!worker.capabilities?.includes('conversion-options-v1')){submit.disabled=true;status.textContent=worker.reason||'Afventer opdatering af konverteringsworkeren. Aktuelle job bevares. Luk og åbn igen efter opdateringen.';}
   form.append(status,submit,close);dialog.append(form);document.body.append(dialog);dialog.addEventListener('close',()=>dialog.remove(),{once:true});dialog.showModal();
   let sending=false;dialog.addEventListener('cancel',event=>{if(sending)event.preventDefault();});
   form.onsubmit=async event=>{
    event.preventDefault();sending=true;submit.disabled=true;close.disabled=true;location.disabled=true;tag.disabled=true;
    const groups=new Map();for(const row of selected.values()){if(!groups.has(row.hash))groups.set(row.hash,[]);groups.get(row.hash).push(row.index);}
    const policy=options.value();form.querySelector('.conversion-options').disabled=true;
    let count=0;const failures=[];
    for(const [hash,indexes] of groups){
     try{await api('conversions',{torrent_hash:hash,indexes,bitrate:Number($('conversion-advice-rate').value),location:location.value,folder:'',release_tag:tag.value,options:policy});count+=indexes.length;for(const index of indexes)selected.delete(hash+':'+index);}
     catch(e){failures.push(e.message);}
    }
    status.textContent=count+' videoer tilføjet.'+(failures.length?' '+failures.length+' torrents kunne ikke tilføjes: '+failures.join(' · '):'');
    sending=false;close.disabled=false;location.disabled=false;tag.disabled=false;form.querySelector('.conversion-options').disabled=false;submit.textContent='Prøv resterende igen';submit.disabled=!failures.length;selection();void refresh();if(!failures.length)dialog.close();
   };
  }catch(e){notify(e);}
 };
 new MutationObserver(()=>{if(page.hidden)clearTimeout(timer);else void refresh();}).observe(page,{attributes:true,attributeFilter:['hidden']});
 window.addEventListener('conversion-refresh',()=>void refresh());
})();
