/* Private links are delivered in memory, never in an app URL or localStorage. */
document.addEventListener('mediaseed-import', async event => {
  if(typeof event.detail!=='string' || event.detail.length>6*1024*1024)return;
  let envelope;try{envelope=JSON.parse(event.detail);}catch{return;}
  const {id,item}=envelope;if(typeof id!=='string'||id.length>80||!item)return;
  const reply=value=>document.dispatchEvent(new CustomEvent('mediaseed-import-result',{detail:JSON.stringify({id,...value})}));
  try {
    if(document.querySelector('#workspace').hidden)throw Error(t('Sign in required'));
    if(appRole==='viewer'||(item.kind==='rss'&&appRole!=='administrator'))throw Error(t('Read-only app access'));
    if(document.querySelector('dialog[open]')||addingTorrent)throw Error(t('Finish the open dialog before importing another item.'));
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
        file=new File([data],String(item.name||'download.torrent').slice(0,250),{type:'application/x-bittorrent'});
      } else if(typeof item.magnet!=='string'||!item.magnet.startsWith('magnet:?')||item.magnet.length>8192)throw Error(t('Unknown source.'));
      openAdd();
      const mode=addForm.querySelector(`[name="mode"][value="${file?'file':'magnet'}"]`);mode.checked=true;mode.dispatchEvent(new Event('change',{bubbles:true}));
      if(file)selectTorrentFiles([file]);else {document.querySelector('#add-magnet').value=item.magnet;MediaSeedAdd.changed();}
    }
    reply({ok:true,opened:true});
  } catch(error) {reply({error:error.message});}
});
