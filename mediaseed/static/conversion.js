/* Permanent video copies; all original torrent controls and cleanup remain separate. */
(() => {
  const text=(en,da)=>locale().startsWith('da')?da:en;
  let dialog, timer;
  const button=(en,da,action)=>{const b=command(text(en,da),action);b.type='button';return b;};
  const phases={queued:'I kø',encoding:'Konverterer',verifying:'Kontrollerer',ready:'Publicerer',complete:'Færdig',error:'Kræver kontrol',cancelled:'Annulleret'};
  function shell(title) {
    clearTimeout(timer);
    if(dialog){dialog.close();dialog.remove();}
    dialog=element('dialog',undefined,'media-conversion-dialog');dialog.id='media-conversion-dialog';
    const heading=element('div',undefined,'heading');heading.append(element('h2',title),button('Close','Luk',()=>dialog.close()));
    dialog.append(heading);document.body.append(dialog);dialog.showModal();return dialog;
  }
  async function history() {
    if(dialog?.open)dialog.close();
    document.querySelector('[data-view="conversions"]')?.click();
    window.dispatchEvent(new Event('conversion-refresh'));
  }
  window.addEventListener('conversion-open',event=>void open(event.detail));
  async function open(row) {
    const panel=shell(text('Convert video','Konvertér video'));
    const status=element('p',text('Loading…','Henter…'));panel.append(status);
    try {
      const [info,worker,storage,preferences]=await Promise.all([api(`conversions/torrents/${row.hash}`),api('conversions'),api('storage'),api('conversions/preferences')]);
      if(!panel.open)return;status.textContent='';
      panel.append(element('h3',info.name));
      panel.append(element('p',text('Create a smaller HEVC/MKV copy. Originals keep seeding and are never overwritten. Existing cleanup settings are preserved.','Lav en mindre HEVC/MKV-kopi. Originalerne fortsætter med at seede og bliver ikke overskrevet. Dine oprydningsindstillinger bevares.'),'muted'));
      panel.append(element('p',text('Conversion is lossy. Choose which tracks and metadata to keep below. Copied audio is additional to the video bitrate.','Konvertering er tabsbehæftet. Vælg nedenfor, hvilke spor og metadata der skal med. Kopieret lyd kommer oven i videoens bitrate.'),'muted'));
      const form=element('form'),files=element('fieldset'),legend=element('legend',text('Choose videos','Vælg videoer'));files.append(legend);
      const chosen=[];
      for(const file of info.files) {
        const label=element('label',undefined,'conversion-file'),input=element('input');input.type='checkbox';input.value=file.index;input.checked=Array.isArray(row.indexes)&&row.indexes.includes(file.index);
        label.append(input,element('span',file.name),element('small',bytes(file.size)));files.append(label);chosen.push(input);
      }
      const rateLabel=element('label',text('Video bitrate','Videobitrate')),rate=element('select');
      for(const value of [20,30,40]){const option=element('option',value+' Mbit/s');option.value=value;rate.append(option);}rate.value=row.bitrate||preferences.bitrate||30;rateLabel.append(rate);
      const tagLabel=element('label',text('Your release tag','Dit tag')),tag=element('input');tag.value=preferences.release_tag||'MDTech';tag.maxLength=32;tag.pattern='[A-Za-z0-9][A-Za-z0-9_-]{0,31}';tag.required=true;tagLabel.append(tag);
      const locationLabel=element('label',text('Library location','Biblioteksplacering')),location=element('select');
      for(const item of storage.locations||[]){if(item.writable){const option=element('option',item.label);option.value=item.id;location.append(option);}}
      location.value=(storage.locations||[]).some(r=>r.id==='converted-movies'&&r.writable)?'converted-movies':(info.destinationStorage||info.storage).location;locationLabel.append(location);
      const folderLabel=element('label',text('Existing subfolder','Eksisterende undermappe')),folder=element('input');folder.value=info.destinationStorage?.folder||'';folder.placeholder=text('Empty = storage root','Tom = drevets rod');folderLabel.append(folder);
      location.addEventListener('change',()=>folder.value='');
      form.append(element('p',text('New copies use H265, the selected target bitrate and your tag in their names. Original names remain unchanged.','Nye kopier får H265, den valgte målbitrate og dit tag i navnet. Originalernes navne bevares.'),'muted'));
      const explanation=element('p',text('The converted copy gets its own movie folder in the selected destination, outside the torrent folder. Include it in Plex. Originals keep seeding. Both copies temporarily need disk space; outputs that do not save space are rejected.','Kopien gemmes i sin egen filmmappe på den valgte placering uden for torrentens mappe. I Konverterede Film oprettes ingen ekstra Konverteret-mappe. Placeringen skal være med i Plex. Originalerne fortsætter med at seede. Begge kopier kræver midlertidigt plads; resultater uden pladsbesparelse bliver afvist.'),'muted');
      const start=element('button',text('Start conversion','Start konvertering'));start.type='submit';start.className='primary';
      const featureReady=value=>value.ready&&value.capabilities?.includes('conversion-options-v1');
      start.disabled=!featureReady(worker)||!chosen.length;
      const workerStatus=element('p',featureReady(worker)?'':worker.reason||'Konverteringsworkeren skal opdateres, før de nye valg kan bruges. Aktuelle job bevares.','muted');
      form.append(workerStatus);
      const feedback=element('p');feedback.setAttribute('role','status');
      form.append(files,rateLabel,tagLabel,locationLabel,folderLabel);
      const options=MediaSeedConversionOptions.mount(form,preferences.options);
      const save=button('Save defaults','Gem som standard',async()=>{try{await api('conversions/preferences',{bitrate:Number(rate.value),release_tag:tag.value,options:options.value()},'PUT');feedback.textContent='Standardvalg gemt.';}catch(error){feedback.textContent=error.message;}});
      form.append(save,explanation,feedback,start);panel.append(form);
      async function readiness(){if(!panel.open)return;try{const value=await api('conversions');start.disabled=!featureReady(value)||!chosen.length;workerStatus.textContent=featureReady(value)?'':value.reason||'Afventer opdatering af konverteringsworkeren.';}catch(error){workerStatus.textContent=error.message;}if(panel.open)timer=setTimeout(readiness,5000);}
      if(!featureReady(worker))timer=setTimeout(readiness,5000);
      form.onsubmit=async event=>{
        event.preventDefault();const indexes=chosen.filter(input=>input.checked).map(input=>Number(input.value));
        if(!indexes.length){feedback.textContent=text('Choose at least one video','Vælg mindst én video');return;}
        start.disabled=true;
        try{await api('conversions',{torrent_hash:row.hash,indexes,bitrate:Number(rate.value),location:location.value,folder:folder.value,release_tag:tag.value,options:options.value()});await history();}
        catch(error){feedback.textContent=error.message;start.disabled=!featureReady(worker);}
      };
    }catch(error){status.textContent=error.message;}
  }
  function decorate() {
    if(typeof appRole==='undefined'||appRole==='viewer')return;
    const toolbar=document.querySelector('#torrent-move-toolbar');
    if(toolbar&&!toolbar.querySelector('[data-conversion-history]')){const b=button('Conversions','Konverteringer',history);b.dataset.conversionHistory='';toolbar.prepend(b);}
    for(const item of document.querySelectorAll('#torrents>.torrent')) {
      const menu=item.querySelector('.torrent-menu-content');if(!menu||menu.querySelector('[data-convert-video]'))continue;
      const row=torrents.find(row=>row.hash===item.dataset.hash);if(!row||row.progress!==1)continue;
      const b=button('Convert video…','Konvertér video…',()=>{item.querySelector('.torrent-menu').open=false;void open(row);});b.dataset.convertVideo='';b.className='torrent-menu-choice';menu.append(b);
    }
  }
  const list=document.querySelector('#torrents');if(list)new MutationObserver(decorate).observe(list,{childList:true});
  setInterval(decorate,2000);decorate();
})();
