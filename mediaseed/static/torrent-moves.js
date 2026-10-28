/* Selection and durable, verified file moves; supports list and card layouts. */
(() => {
  const selected = new Set(), notified = new Set();
  const phases = {queued:['Queued','I kø'],hashing:['Checking source files','Kontrollerer kildefiler'],moving:['Moving files','Flytter filer'],verifying:['Checking destination files','Kontrollerer filer i målmappen'],complete:['Moved and verified','Flyttet og kontrolleret'],needs_attention:['Move needs attention','Flytning kræver kontrol']};
  const text = (en, da) => locale().startsWith('da') ? da : en;
  const phase = value => text(...(phases[value] || phases.queued));
  const button = (en, da, action) => {const node=command(text(en,da), action);node.type='button';return node;};
  let toolbar, selectButton, activity, latest, dialog, polling=false, nextPoll=0;
  let panel, panelBody, panelSignature='', panelMode='move', jobView=null, removing=false;
  const downloads=document.querySelector('#downloads');
  const setText=(node,value)=>{if(node.textContent!==value)node.textContent=value;};

  function rowFor(hash) {return torrents.find(row=>row.hash===hash);}
  function eligible(row) {return row?.progress===1 && ['uploading','stalledUP','forcedUP','stoppedUP','pausedUP','queuedUP'].includes(row.state);}
  function chosen() {return [...selected].map(rowFor).filter(Boolean);}
  function clearSelection() {if(removing)return;selected.clear();jobView=null;panelSignature='';sync();}
  function choose(rows) {if(removing)return;selected.clear();for(const row of rows)selected.add(row.hash);panelMode='move';jobView=null;panelSignature='';sync();}
  function sync() {
    const heading=downloads?.querySelector('.downloads-heading .actions');
    if (!heading || typeof appRole==='undefined') return;
    if (!toolbar) {
      downloads.classList.add('torrent-actions-ready');
      if(!globalThis.MediaSeedList)downloads.classList.add('torrent-legacy-layout');
      const grid=downloads.querySelector('.seed-download-grid');
      panel=element('aside',undefined,'torrent-action-panel');panel.id='torrent-action-panel';panel.hidden=true;
      panel.setAttribute('aria-label',text('Selected torrents','Valgte torrents'));grid.append(panel);
      // Keep title/add above the panel; search/view above a separate filter row.
      const titlebar=element('div',undefined,'torrent-titlebar');
      const title=downloads.querySelector('.downloads-heading h2'),add=document.querySelector('#new-torrent');
      downloads.querySelector('.downloads-heading').before(titlebar);titlebar.append(title,add);
      new MutationObserver(()=>{if(add.parentElement!==titlebar)titlebar.append(add);}).observe(heading,{childList:true});
      const filters=downloads.querySelector('.download-filters'),filterbar=filters.querySelector('.toolbar');
      const search=document.querySelector('#search').closest('label');search.classList.add('torrent-search');
      const topbar=downloads.querySelector('.downloads-heading');topbar.prepend(search);
      if(filterbar)for(const child of [...filterbar.children])child.classList.add('torrent-filter-field');
      grid.querySelector('.seed-torrent-panel').prepend(topbar,filters);
      toolbar=element('div',undefined,'torrent-move-toolbar');toolbar.id='torrent-move-toolbar';
      selectButton=button('Select visible','Vælg viste',()=>{
        if(removing)return;
        const rows=[...document.querySelectorAll('#torrents [data-hash]')].map(node=>rowFor(node.dataset.hash)).filter(Boolean);
        const clear=rows.every(row=>selected.has(row.hash));for(const row of rows)clear?selected.delete(row.hash):selected.add(row.hash);
        jobView=null;sync();
      });
      activity=button('Move history','Flyttehistorik',()=>latest?show(latest):null);activity.className='torrent-move-history';
      activity.title=text('Move history','Flyttehistorik');activity.hidden=true;
      toolbar.append(activity,selectButton);heading.append(toolbar);
    }
    toolbar.hidden=appRole==='viewer';
    for(const hash of selected)if(!rowFor(hash) || pendingRemovals.has(hash))selected.delete(hash);
    const visible=[...document.querySelectorAll('#torrents [data-hash]')];
    const all=visible.length>0 && visible.every(item=>selected.has(item.dataset.hash));
    setText(selectButton,all?text('Clear visible','Fravælg viste'):text('Select visible','Vælg viste'));
    selectButton.disabled=removing || !visible.length;
    for(const item of visible) {
      const row=rowFor(item.dataset.hash);if(!row)continue;
      let box=item.querySelector('.torrent-move-select input');
      if(!box) {
        const label=element('label',undefined,'torrent-move-select');box=element('input');box.type='checkbox';
        box.addEventListener('change',()=>{box.checked?selected.add(row.hash):selected.delete(row.hash);jobView=null;sync();});
        label.append(box);item.append(label);
      }
      box.checked=selected.has(row.hash);box.disabled=removing;box.closest('label').hidden=appRole==='viewer';
      item.classList.toggle('torrent-is-selected',box.checked);
      box.setAttribute('aria-label',text('Select torrent: ','Vælg torrent: ')+row.name);
      const menu=item.querySelector('.torrent-menu-content');
      if(menu && !menu.querySelector('[data-move-torrent]') && appRole!=='viewer') {
        const move=button('Move files…','Flyt filer…',()=>{item.querySelector('.torrent-menu').open=false;choose([rowFor(row.hash)]);});
        move.dataset.moveTorrent=row.hash;move.disabled=!eligible(row);move.className='torrent-menu-choice';
        move.title=text('Move completed files and verify their contents','Flyt færdige filer og kontrollér indholdet');menu.prepend(move);
      }
    }
    syncPanel();
  }

  function panelShell(title) {
    if(panelBody)panelBody.open=false;
    panel.replaceChildren();panel.hidden=false;downloads.classList.add('torrent-panel-open');
    const header=element('div',undefined,'torrent-action-heading'),heading=element('h3',title);
    header.append(heading,button('Close','Luk',clearSelection));panel.append(header);
    panelBody=element('div',undefined,'torrent-action-body');panelBody.open=true;panel.append(panelBody);return panelBody;
  }
  function syncPanel() {
    if(!panel)return;
    if(appRole==='viewer' || (!selected.size && !jobView)) {
      if(panelBody)panelBody.open=false;
      panel.hidden=true;downloads.classList.remove('torrent-panel-open');panelSignature='';return;
    }
    const rows=chosen(),signature=JSON.stringify([rows.map(row=>[row.hash,row.name,eligible(row)]),panelMode,locale(),jobView?.id]);
    if(panelSignature===signature || removing)return;
    panelSignature=signature;
    if(jobView){const node=panelShell(text('File move','Filflytning'));node.dataset.job=jobView.id;paint(node,jobView);return;}
    const node=panelShell(text('Selected torrents','Valgte torrents'));
    const summary=element('p',text(`${rows.length} selected · ${bytes(rows.reduce((sum,row)=>sum+Number(row.size||0),0))}`,`${rows.length} valgte · ${bytes(rows.reduce((sum,row)=>sum+Number(row.size||0),0))}`),'torrent-selection-summary');
    const names=element('ul',undefined,'torrent-move-names');for(const row of rows){const item=element('li'),name=element('span',row.name);name.title=row.name;item.append(name);names.append(item);}
    node.append(summary,names);
    const tabs=element('div',undefined,'torrent-action-tabs');tabs.setAttribute('role','group');tabs.setAttribute('aria-label',text('Action for selected torrents','Handling for valgte torrents'));
    for(const [mode,en,da] of [['move','Move','Flyt'],['remove','Remove / delete','Fjern / slet']]) {
      const tab=button(en,da,()=>{panelMode=mode;syncPanel();});tab.setAttribute('aria-pressed',String(panelMode===mode));tabs.append(tab);
    }
    node.append(tabs);
    if(panelMode==='move') {
      if(rows.some(row=>!eligible(row)))node.append(element('p',text('Moving requires completed torrents. You can still remove or delete this selection.','Flytning kræver færdige torrents. Du kan stadig fjerne eller slette de valgte.'),'muted'));
      else void open(rows,node);
    }else removalChoices(node,rows);
  }

  function removalChoices(node,rows) {
    node.append(element('p',text('Choose whether downloaded files should be kept.','Vælg, om de downloadede filer skal bevares.'),'muted'));
    const keep=button('Remove torrents · keep files','Fjern torrents · behold filer',()=>confirmBatchRemoval(rows,false));keep.className='torrent-remove-keep';
    const files=button('Delete torrents and files…','Slet torrents og filer…',()=>confirmBatchRemoval(rows,true));files.className='danger torrent-remove-files';
    node.append(keep,element('small',text('Stops seeding and removes the torrents. Your files remain in their folders.','Stopper seeding og fjerner torrents. Filerne bliver i deres mapper.'),'muted'),files,element('small',text('Deletes downloaded files as well. This cannot be undone.','Sletter også de downloadede filer. Det kan ikke fortrydes.'),'muted'));
  }
  function confirmBatchRemoval(rows,deleteFiles) {
    if(removing || appRole==='viewer')return;
    const snapshot=rows.map(row=>({...row}));
    const node=shell(deleteFiles?text('Delete torrents and files?','Slet torrents og filer?'):text('Remove torrents?','Fjern torrents?'));
    node.append(element('p',text(`${snapshot.length} selected torrents`,`${snapshot.length} valgte torrents`)));
    const names=element('ul',undefined,'torrent-move-names');for(const row of snapshot)names.append(element('li',row.name));node.append(names);
    const warning=element('p',deleteFiles?text('All downloaded files belonging to these torrents will be deleted. This cannot be undone.','Alle downloadede filer, der tilhører disse torrents, bliver slettet. Det kan ikke fortrydes.'):text('The torrents are removed and seeding stops. Downloaded files remain in their current folders.','Torrents fjernes, og seeding stopper. Downloadede filer bliver i deres nuværende mapper.'));node.append(warning);
    const form=element('form');let consent;
    if(deleteFiles) {const label=element('label',undefined,'torrent-delete-consent');consent=element('input');consent.type='checkbox';consent.required=true;label.append(consent,element('span',text('I understand that the downloaded files will be permanently deleted.','Jeg forstår, at de downloadede filer slettes permanent.')));form.append(label);}
    const actions=element('div',undefined,'actions');
    const cancel=button('Cancel','Annuller',()=>node.close());
    const submit=element('button',deleteFiles?text('Delete torrents and files','Slet torrents og filer'):text('Remove torrents · keep files','Fjern torrents · behold filer'),deleteFiles?'danger':'primary');submit.type='submit';
    if(consent){submit.disabled=true;consent.onchange=()=>{submit.disabled=!consent.checked;};}
    actions.append(cancel,submit);form.append(actions);node.append(form);
    form.onsubmit=async event=>{
      event.preventDefault();if(removing || (deleteFiles && !consent.checked))return;
      removing=true;node.close();sync();
      const target=panelShell(text('Removing selected torrents','Fjerner valgte torrents'));
      const status=element('p',undefined,'torrent-removal-status');status.setAttribute('role','status');target.append(status);
      const results=element('ul',undefined,'torrent-removal-results');target.append(results);
      let accepted=0,processed=0;
      for(const row of snapshot) {
        status.textContent=text(`Processing ${++processed} of ${snapshot.length}…`,`Behandler ${processed} af ${snapshot.length}…`);
        try {
          // Reuse existing seeding-goal confirmation and client disappearance checks.
          if(await torrentAction(row,'remove',deleteFiles)) {accepted++;selected.delete(row.hash);void confirmRemoval(row);}
          else results.append(element('li',row.name+' · '+text('Cancelled; kept','Annulleret; bevaret')));
        }catch(error){results.append(element('li',row.name+' · '+error.message));}
      }
      removing=false;panelSignature='';sync();
      const message=text(`${accepted} of ${snapshot.length} removal requests accepted. Confirmation is checked with the torrent client.`,`${accepted} af ${snapshot.length} fjernelser accepteret. Bekræftelsen kontrolleres hos torrentklienten.`);
      if(selected.size){panelBody.append(element('p',message,'muted'),results);}
      showNotification(message,accepted===snapshot.length?'success':'info');
    };
    cancel.focus();
  }

  function shell(title) {
    if(dialog?.open)dialog.close();
    dialog=element('dialog',undefined,'torrent-move-dialog');dialog.id='torrent-move-dialog';
    const heading=element('h2',title);heading.id='torrent-move-title';dialog.setAttribute('aria-labelledby',heading.id);
    const header=element('div',undefined,'heading');header.append(heading,button('Close','Luk',()=>dialog.close()));dialog.append(header);
    const node=dialog;node.addEventListener('close',()=>{node.remove();if(dialog===node)dialog=null;},{once:true});
    document.body.append(node);node.showModal();return node;
  }

  async function open(rows, node) {
    if(appRole==='viewer' || !rows.length || rows.some(row=>!eligible(row)))return;
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
    if(job.status!=='complete')target.append(element('p',text('You can close the panel. The job continues in the background.','Du kan lukke panelet. Arbejdet fortsætter i baggrunden.'),'muted'));
  }
  function show(job) {jobView=job;panelSignature='';syncPanel();}
  async function poll() {
    if(polling || !csrf || Date.now()<nextPoll)return;
    polling=true;
    try {
      const jobs=await api('torrent-moves');if(!Array.isArray(jobs))return;
      const job=jobs[0];if(!job)return;const wasActive=latest?.id===job.id && latest.status!=='complete';latest=job;
      sync();activity.hidden=false;activity.title=text('Move history','Flyttehistorik')+': '+phase(job.status);
      if(panelBody?.dataset.job===job.id){jobView=job;paint(panelBody,job);}
      if(job.status==='complete' && wasActive && !notified.has(job.id)) {
        notified.add(job.id);for(const row of job.items)selected.delete(row.hash);sync();
        showNotification(text('Files moved and verified.','Filerne er flyttet og kontrolleret.'),'success');
        if(typeof seedDetailCache!=='undefined')seedDetailCache.clear();
      }
    } catch(error) {if(panelBody?.dataset.job)notify(error);}
    finally {polling=false;nextPoll=Date.now()+(latest && !['complete','needs_attention'].includes(latest.status)?1500:15000);}
  }
  const list=document.querySelector('#torrents');if(!list)return;
  new MutationObserver(sync).observe(list,{childList:true,subtree:true});sync();
  new MutationObserver(sync).observe(document.documentElement,{attributes:true,attributeFilter:['lang']});
  setInterval(()=>void poll(),1500);
})();
