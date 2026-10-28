/* Selection and durable, verified file moves; supports list and card layouts. */
(() => {
  const selected = new Set(), notified = new Set();
  const phases = {queued:['Queued','I kø'],hashing:['Checking source files','Kontrollerer kildefiler'],moving:['Moving files','Flytter filer'],verifying:['Checking destination files','Kontrollerer filer i målmappen'],complete:['Moved and verified','Flyttet og kontrolleret'],needs_attention:['Move needs attention','Flytning kræver kontrol']};
  const text = (en, da) => locale().startsWith('da') ? da : en;
  const phase = value => text(...(phases[value] || phases.queued));
  const button = (en, da, action) => {const node=command(text(en,da), action);node.type='button';return node;};
  let toolbar, moveButton, selectButton, activity, latest, dialog, polling=false, nextPoll=0;

  function rowFor(hash) {return torrents.find(row=>row.hash===hash);}
  function eligible(row) {return row?.progress===1 && ['uploading','stalledUP','forcedUP','stoppedUP','pausedUP','queuedUP'].includes(row.state);}
  function sync() {
    const heading=document.querySelector('#downloads .downloads-heading .actions');
    if (!heading || typeof appRole==='undefined') return;
    if (!toolbar) {
      toolbar=element('div',undefined,'torrent-move-toolbar');toolbar.id='torrent-move-toolbar';
      moveButton=button('Move selected','Flyt valgte',()=>open([...selected].map(rowFor).filter(Boolean)));
      selectButton=button('Select visible','Vælg viste',()=>{
        const rows=[...document.querySelectorAll('#torrents [data-hash]')].map(node=>rowFor(node.dataset.hash)).filter(eligible);
        const clear=rows.every(row=>selected.has(row.hash));for(const row of rows)clear?selected.delete(row.hash):selected.add(row.hash);sync();
      });
      activity=button('Moves','Flytninger',()=>latest?show(latest):null);activity.hidden=true;
      toolbar.append(selectButton,moveButton,activity);heading.append(toolbar);
    }
    toolbar.hidden=appRole==='viewer';
    for(const hash of selected)if(!rowFor(hash))selected.delete(hash);
    const label=text('Move selected','Flyt valgte')+(selected.size?` (${selected.size})`:'');
    if(moveButton.textContent!==label)moveButton.textContent=label;
    moveButton.disabled=!selected.size;
    for(const item of document.querySelectorAll('#torrents [data-hash]')) {
      const row=rowFor(item.dataset.hash);if(!row)continue;
      let box=item.querySelector('.torrent-move-select input');
      if(!box) {
        const label=element('label',undefined,'torrent-move-select');box=element('input');box.type='checkbox';
        box.addEventListener('change',()=>{box.checked?selected.add(row.hash):selected.delete(row.hash);sync();});
        label.append(box);(item.querySelector('.seed-identity') || item).prepend(label);
      }
      box.checked=selected.has(row.hash);box.disabled=!eligible(row);box.closest('label').hidden=appRole==='viewer';
      box.setAttribute('aria-label',text('Select for moving: ','Vælg til flytning: ')+row.name);
      const menu=item.querySelector('.torrent-menu-content');
      if(menu && !menu.querySelector('[data-move-torrent]') && appRole!=='viewer') {
        const move=button('Move files…','Flyt filer…',()=>{item.querySelector('.torrent-menu').open=false;open([rowFor(row.hash)]);});
        move.dataset.moveTorrent=row.hash;move.disabled=!eligible(row);move.className='torrent-menu-choice';
        move.title=text('Move completed files and verify their contents','Flyt færdige filer og kontrollér indholdet');menu.prepend(move);
      }
    }
  }

  function shell(title) {
    if(dialog?.open)dialog.close();
    dialog=element('dialog',undefined,'torrent-move-dialog');dialog.id='torrent-move-dialog';
    const heading=element('h2',title);heading.id='torrent-move-title';dialog.setAttribute('aria-labelledby',heading.id);
    const header=element('div',undefined,'heading');header.append(heading,button('Close','Luk',()=>dialog.close()));dialog.append(header);
    const node=dialog;node.addEventListener('close',()=>{node.remove();if(dialog===node)dialog=null;},{once:true});
    document.body.append(node);node.showModal();return node;
  }

  async function open(rows) {
    if(appRole==='viewer' || !rows.length || rows.some(row=>!eligible(row)))return;
    const node=shell(text('Move files','Flyt filer'));
    const names=element('ul',undefined,'torrent-move-names');for(const row of rows)names.append(element('li',row.name));node.append(names);
    node.append(element('p',text('Files are moved and their contents verified. Seeding and pause status are preserved. Existing destination files are never overwritten.','Filerne flyttes, og indholdet kontrolleres bagefter. Seeding og pausestatus bevares. Eksisterende filer i målmappen overskrives aldrig.'),'muted'));
    const status=element('p',text('Loading folders…','Henter mapper…'),'muted');status.setAttribute('role','status');node.append(status);
    try {
      const storage=await api('storage');if(!node.open)return;
      const form=element('form'),label=element('label',text('Destination','Målmappe')),location=element('select');
      for(const row of storage.locations || []) {const option=new Option(row.label,row.id);option.disabled=!row.available || !row.writable;location.append(option);}
      location.value=(storage.locations || []).find(row=>row.available && row.writable && row.id==='movies')?.id || (storage.locations || []).find(row=>row.available && row.writable)?.id || '';
      location.setAttribute('aria-label',text('Storage location','Lagerplacering'));label.append(location);form.append(label);
      const path=element('p',undefined,'torrent-move-path'),folders=element('select'),up=button('Parent folder','Overordnet mappe',()=>void browse(folder.split('/').slice(0,-1).join('/')).catch(notify));
      folders.setAttribute('aria-label',text('Subfolder','Undermappe'));form.append(path,folders,up);
      const submit=element('button',text('Move and verify','Flyt og kontrollér'),'primary');submit.type='submit';form.append(submit);node.append(form);
      let folder='',ready=false,generation=0;
      async function browse(value) {
        ready=false;submit.disabled=true;folders.disabled=true;const request=++generation;
        const result=await api(`storage/folders?location=${encodeURIComponent(location.value)}&folder=${encodeURIComponent(value)}`);
        if(request!==generation || !node.open)return;
        folder=value;path.textContent=location.selectedOptions[0].textContent+' /'+value;up.disabled=!value;
        folders.replaceChildren(new Option(text('Use this folder','Brug denne mappe'),''),...result.folders.map(row=>new Option(row.name,row.name)));
        ready=result.writable;submit.disabled=!ready;folders.disabled=false;status.textContent='';
      }
      location.onchange=()=>void browse('').catch(notify);
      folders.onchange=()=>{if(folders.value)void browse([folder,folders.value].filter(Boolean).join('/')).catch(notify);};
      form.onsubmit=async event=>{
        event.preventDefault();if(!ready)return;submit.disabled=true;
        try {const job=await api('torrent-moves',{hashes:rows.map(row=>row.hash),location:location.value,folder},'POST');latest=job;nextPoll=0;show(job);void poll();}
        catch(error){notify(error);submit.disabled=false;}
      };
      await browse('');
    } catch(error) {status.textContent=text('Folders could not be loaded. Close this window and try again.','Mapperne kunne ikke hentes. Luk vinduet, og prøv igen.');notify(error);}
  }

  function paint(node, job) {
    let target=node.querySelector('.torrent-move-progress');
    if(!target){target=element('div',undefined,'torrent-move-progress');target.setAttribute('role','status');target.setAttribute('aria-live','polite');node.append(target);}
    target.replaceChildren(element('strong',phase(job.status)));
    for(const row of job.items) {
      const block=element('section'),name=element('p',row.name),label=element('small',phase(row.phase));
      block.append(name,label);
      if(['hashing','verifying','complete'].includes(row.phase)) {
        const progress=element('progress');progress.max=row.bytes || 1;progress.value=row.phase==='complete'?(row.bytes || 1):Math.min(row.bytes,row.checkedBytes || 0);
        progress.setAttribute('aria-label',phase(row.phase)+': '+row.name);block.append(progress);
      }
      if(row.phase==='complete')block.append(element('small',text(`${row.verifiedFiles} files verified`,`${row.verifiedFiles} filer kontrolleret`)));
      target.append(block);
    }
    if(job.error){target.append(element('p',text('The move needs review. Files have been preserved.','Flytningen kræver kontrol. Filerne er bevaret.'),'muted'),element('p',job.error));target.append(button('Try again','Prøv igen',async()=>{try{latest=await api(`torrent-moves/${job.id}/retry`,{},'POST');nextPoll=0;paint(node,latest);void poll();}catch(error){notify(error);}}));}
    if(job.status!=='complete')target.append(element('p',text('You can close this window. The job continues in the background.','Du kan lukke vinduet. Arbejdet fortsætter i baggrunden.'),'muted'));
  }
  function show(job) {const node=shell(text('Move files','Flyt filer'));node.dataset.job=job.id;paint(node,job);}
  async function poll() {
    if(polling || !csrf || Date.now()<nextPoll)return;
    polling=true;
    try {
      const jobs=await api('torrent-moves');if(!Array.isArray(jobs))return;
      const job=jobs[0];if(!job)return;const wasActive=latest?.id===job.id && latest.status!=='complete';latest=job;
      sync();activity.hidden=false;const label=text('Moves','Flytninger')+': '+phase(job.status);if(activity.textContent!==label)activity.textContent=label;
      if(dialog?.dataset.job===job.id)paint(dialog,job);
      if(job.status==='complete' && wasActive && !notified.has(job.id)) {
        notified.add(job.id);for(const row of job.items)selected.delete(row.hash);sync();
        showNotification(text('Files moved and verified.','Filerne er flyttet og kontrolleret.'),'success');
        if(typeof seedDetailCache!=='undefined')seedDetailCache.clear();
      }
    } catch(error) {if(dialog?.dataset.job)notify(error);}
    finally {polling=false;nextPoll=Date.now()+(latest && !['complete','needs_attention'].includes(latest.status)?1500:15000);}
  }
  const list=document.querySelector('#torrents');if(!list)return;
  new MutationObserver(sync).observe(list,{childList:true,subtree:true});sync();
  setInterval(()=>void poll(),1500);
})();
