const mediaSeedImports = new Set();
/* Private links are delivered in memory, never in an app URL or localStorage. */
document.addEventListener('mediaseed-import', async event => {
  if(typeof event.detail!=='string' || event.detail.length>6*1024*1024)return;
  let envelope;try{envelope=JSON.parse(event.detail);}catch{return;}
  const {id,item}=envelope;if(typeof id!=='string'||id.length>80||!item)return;
  const reply=value=>document.dispatchEvent(new CustomEvent('mediaseed-import-result',{detail:JSON.stringify({id,...value})}));
  try {
    if(document.querySelector('#workspace').hidden)throw Error(t('Sign in required'));
    if(appRole==='viewer'||(item.kind==='rss'&&appRole!=='administrator'))throw Error(t('Read-only app access'));
    if(mediaSeedImports.has(id)){reply({ok:true,opened:true});return;}
    const openDialog = document.querySelector('dialog[open]');
    const appendFile = item.kind === 'file' && openDialog === addDialog && addForm.elements.namedItem('mode').value === 'file';
    if((openDialog && !appendFile)||addingTorrent)throw Error(t('Finish the open dialog before importing another item.'));
    if(item.kind==='rss') {
      const url=new URL(item.url);
      if(url.protocol!=='https:'||url.username||url.password||url.hash||url.href.length>4096)throw Error(t('Invalid feed address'));
      feedForm.reset();feedForm.elements.namedItem('id').value='';feedForm.elements.namedItem('url').value=url.href;
      MediaSeedWorkspace.openEditor('feeds');void loadFeedLocations().catch(notify);
    } else {
      if(!['file','magnet'].includes(item.kind))throw Error(t('Unknown source.'));
      let file;
      if(item.kind==='file') {
        if(typeof item.base64!=='string'||item.base64.length>5592408)throw Error(t('Choose a torrent file'));
        const data=Uint8Array.from(atob(item.base64),value=>value.charCodeAt(0));
        if(data.length>4*1024*1024||data[0]!==100||data[data.length-1]!==101)throw Error(t('Choose a torrent file'));
        // Stable selection key, including on HTTP LAN origins without Web Crypto.
        // Backend torrent hashes remain the authority for import identity.
        let hash = 2166136261, check = 0;
        for (const byte of data) { hash = Math.imul(hash ^ byte, 16777619) >>> 0; check = (check * 31 + byte) & 65535; }
        const fingerprint = hash * 65536 + check;
        file=new File([data],String(item.name||'download.torrent').slice(0,250),{type:'application/x-bittorrent',lastModified:fingerprint});
      } else if(typeof item.magnet!=='string'||!item.magnet.startsWith('magnet:?')||item.magnet.length>8192)throw Error(t('Unknown source.'));
      // A new external source replaces the previous closed retry batch.
      // Otherwise openAdd restores old failures and resends unrelated torrents.
      if (!appendFile) { retryAddDraft = false; openAdd(); }
      if (file && (torrentFiles.length >= 100 || torrentFiles.reduce((sum, row) => sum + row.file.size, 0) + file.size > 64 * 1024 * 1024)) throw Error(t('Torrent selection is full. Start this batch before sending more files.'));
      const mode=addForm.querySelector(`[name="mode"][value="${file?'file':'magnet'}"]`);mode.checked=true;mode.dispatchEvent(new Event('change',{bubbles:true}));
      if(file)selectTorrentFiles([file]);else {document.querySelector('#add-magnet').value=item.magnet;MediaSeedAdd.changed();}
    }
    mediaSeedImports.add(id);
    if (mediaSeedImports.size > 500) mediaSeedImports.delete(mediaSeedImports.values().next().value);
    reply({ok:true,opened:true});
  } catch(error) {reply({error:error.message});}
});
