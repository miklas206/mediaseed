/* Measured cross-release suggestions; explicit cut/sync confirmation before a write. */
(() => {
 const page=document.getElementById('conversions');if(!page)return;
 const $=id=>document.getElementById(id),container=$('conversion-mux-recommendations'),state=$('conversion-mux-state');
 let timer,running=false,signature='';
 const writable=()=>typeof appRole!=='undefined'&&appRole!=='viewer';
 const button=(text,action)=>{const b=element('button',text);b.type='button';b.onclick=action;return b;};
 async function choose(row){
  try{
   const storage=await api('storage'),dialog=element('dialog',undefined,'media-conversion-dialog conversion-mux-dialog');
   dialog.setAttribute('aria-label','Kombinér billede og lyd');
   dialog.append(element('h2','Kombinér billede og lyd'),element('p','Billede: '+row.video.name,'conversion-path'),element('p','Lyd: '+row.audio+' fra '+row.donor.name,'conversion-path'));
   dialog.append(element('p','Billedet, HDR/Dolby Vision og lydsporet kopieres uden genkodning. Eksisterende lydspor, undertekster og kapitler fra billedkilden beholdes. Det nye lydspor bliver standard. Begge originaler bevares.'));
   dialog.append(element('p','Varighed og billedhastighed passer, men det beviser ikke samme klip eller korrekt lydsynkronisering. Kontrollér begyndelsen, midten og slutningen af begge udgaver før start.','muted'));
   const form=element('form'),label=element('label','Gem den nye kopi i'),location=element('select');location.required=true;
   for(const item of storage.locations||[])if(item.writable)location.append(new Option(item.label,item.id));
   location.value=(storage.locations||[]).some(r=>r.id==='converted-movies'&&r.writable)?'converted-movies':[...location.options].some(o=>o.value===storage.defaultLocation)?storage.defaultLocation:location.options[0]?.value||'';label.append(location);
   const delayLabel=element('label','Lydforsinkelse (millisekunder)'),delay=element('input');delay.type='number';delay.min='-5000';delay.max='5000';delay.step='1';delay.value='0';delay.required=true;delayLabel.append(delay);
   const help=element('p','0 bevarer lydens timing. Et positivt tal afspiller lyden senere; et negativt tal tidligere. Én forsinkelse kan ikke rette forskellige klip eller lyd, der gradvist kommer ud af takt.','muted');
   const confirmLabel=element('label',undefined,'conversion-remove-confirm'),confirm=element('input');confirm.type='checkbox';confirm.required=true;
   confirmLabel.append(confirm,element('span','Jeg har kontrolleret samme filmudgave/afsnit og lydsynkronisering.'));
   const status=element('p');status.setAttribute('role','status');const submit=element('button','Opret mux-kopi');submit.type='submit';submit.className='primary';
   const close=button('Luk',()=>dialog.close());let sending=false;
   form.append(label,delayLabel,help,confirmLabel,status,submit,close);dialog.append(form);document.body.append(dialog);
   dialog.addEventListener('close',()=>dialog.remove(),{once:true});dialog.addEventListener('cancel',e=>{if(sending)e.preventDefault();});dialog.showModal();
   if(!location.options.length){submit.disabled=true;status.textContent='Klargør en skrivbar lagerplacering i Indstillinger før muxing.';}
   form.onsubmit=async e=>{
    e.preventDefault();if(!confirm.checked||sending)return;sending=true;submit.disabled=true;close.disabled=true;status.textContent='Kontrollerer originaler og tilføjer til kø…';
    try{
     await api('conversions/mux',{proposal:row.id,location:location.value,folder:'',delay_ms:Number(delay.value),confirmed_same_cut_and_sync:true});
     dialog.close();window.dispatchEvent(new Event('conversion-refresh'));void refresh();
    }catch(error){status.textContent=error.message;submit.disabled=false;}
    finally{sending=false;close.disabled=false;}
   };
  }catch(error){notify(error);}
 }
 function render(result){
  $('conversion-mux-analyze').hidden=!writable();
  state.textContent=!result.ready?result.reason:!result.scanned?'Start en analyse for at finde mulige kombinationer.':result.total+' videofiler · '+result.pending+' afventer metadata · '+result.rows.length+' mulige kombinationer'+(result.errors?' · '+result.errors+' kunne ikke kontrolleres':'')+(result.truncated?' · gennemgangen er begrænset til 80 torrents og 240 videofiler':'');
  const rows=result.ready?(result.rows||[]):[];
  const next=JSON.stringify([rows,writable()]);if(next===signature)return;signature=next;container.replaceChildren();
  for(const row of rows){
   const card=element('article',undefined,'conversion-advice-row');
   card.append(element('h3',row.video.name),element('p','Billede: '+row.video.resolution+' frem for '+row.donor.resolution),element('p','Bedre lyd: '+row.audio+' frem for '+row.replaces.join(' / '),'conversion-saving'),element('p','Lydkilde: '+row.donor.name,'conversion-path muted'));
   if(writable())card.append(button('Vælg kombination',()=>choose(row)));container.append(card);
  }
 }
 async function refresh(){
  clearTimeout(timer);if(page.hidden||running)return;running=true;
  try{const result=await api('conversions/mux/recommendations');if(!page.hidden)render(result);}
  catch(error){signature='';container.replaceChildren();state.textContent='Kunne ikke hente mux-forslag: '+error.message;}
  finally{running=false;if(!page.hidden)timer=setTimeout(refresh,4000);}
 }
 $('conversion-mux-analyze').onclick=async()=>{
  const b=$('conversion-mux-analyze');b.disabled=true;state.textContent='Finder komplette film og serieafsnit…';
  try{await api('conversions/mux/analyze',{});void refresh();}catch(error){state.textContent=error.message;}finally{b.disabled=false;}
 };
 new MutationObserver(()=>{if(page.hidden)clearTimeout(timer);else void refresh();}).observe(page,{attributes:true,attributeFilter:['hidden']});
 window.addEventListener('conversion-refresh',()=>void refresh());
})();
