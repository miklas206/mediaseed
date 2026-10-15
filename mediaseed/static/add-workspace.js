/* Aligned source/estimate and destination/seeding rows; existing handlers remain. */
(() => {
  const form=document.querySelector('#add-torrent-form'),modal=document.querySelector('#add-torrent');if(!form||!modal)return;
  const source=document.createElement('section');source.className='add-source-column';
  const settings=document.createElement('section');settings.className='add-settings-column';
  const preview=document.querySelector('#seed-add-preview'),previewButton=document.querySelector('#seed-preview-button');
  for(const selector of ['.input-mode','#magnet-field','#file-field','#torrent-file-selection'])source.append(form.querySelector(selector));
  const location=form.querySelector('.download-location'),cleanup=form.querySelector('.cleanup-settings'),ratio=form.querySelector('[name="ratio"]').closest('label');
  const summary=document.createElement('aside');summary.className='add-ratio-summary';const caption=document.createElement('h3');caption.textContent=t('Account ratio after adding');summary.append(caption,preview,previewButton);summary.append(ratio);
  settings.append(cleanup);
  const help=document.createElement('details'),helpTitle=document.createElement('summary');helpTitle.textContent=t('Folder help');help.className='add-folder-help';for(const p of [...location.querySelectorAll('p')].slice(1))help.append(p);help.prepend(helpTitle);location.append(help);
  const actions=form.querySelector(':scope > .actions');actions.classList.add('add-submit-actions');
  const hints=document.createElement('details'),hintsTitle=document.createElement('summary');hints.className='add-seeding-notes';hintsTitle.textContent=t('Seeding and VPN');hints.append(hintsTitle,document.querySelector('#add-seeding-help'),document.querySelector('#add-start-help'));settings.append(hints);
  form.prepend(source,summary,location,settings);form.append(actions);
  const duplicateName=document.querySelector('#file-name');duplicateName.hidden=true;
  for(const p of document.querySelectorAll('#torrent-file-selection > p.muted'))p.hidden=true;
  previewButton.textContent=t('Refresh estimate');
  let timer,last='';
  function changed(){clearTimeout(timer);timer=setTimeout(()=>{if(!modal.open)return;const fileMode=form.elements.namedItem('mode').value==='file',files=torrentFiles.filter(item=>item.state!=='added');const signature=fileMode?files.map(item=>torrentFileKey(item.file)).join('|'):document.querySelector('#add-magnet').value;
    if(!signature){last='';preview.replaceChildren(element('p',t('Choose files or paste a magnet to see the estimated account ratio.'),'muted'));return;}
    if(signature===last)return;last=signature;void richPreview();
  },250);}
  function labels(){caption.textContent=t('Account ratio after adding');previewButton.textContent=t('Refresh estimate');helpTitle.textContent=t('Folder help');hintsTitle.textContent=t('Seeding and VPN');}
  new MutationObserver(labels).observe(document.documentElement,{attributes:true,attributeFilter:['lang']});labels();
  modal.addEventListener('close',()=>{last='';clearTimeout(timer);});
  new MutationObserver(changed).observe(document.querySelector('#torrent-file-list'),{childList:true,subtree:true});
  globalThis.MediaSeedAdd={changed};
})();
