const api = globalThis.browser || chrome, MS = MediaSeedExtension;
const $ = id => document.getElementById(id);
let currentPending = null, busy = false;
function status(text, error = false) { $('status').textContent = text; $('status').dataset.error = String(error); }
async function message(value) {
  const result = await api.runtime.sendMessage(value);
  if (result?.error) throw Error(result.error);
  return result;
}
async function refresh() {
  const result = await message({type: 'state'});
  currentPending = result.pending;
  $('pending').hidden = !currentPending;
  $('pending-name').textContent = currentPending?.name || (currentPending?.kind === 'rss' ? 'RSS-feed · indstillinger åbnes i MediaSeed' : currentPending ? 'Torrentlink · klar til MediaSeed' : '');
  if (result.status?.text) status(result.status.text, result.status.error);
}
async function run(action) {
  if (busy) return;
  busy = true;
  const controls = [...document.querySelectorAll('button,input[type=file]')]; controls.forEach(button => button.disabled = true);
  try { await action(); await refresh(); }
  catch (error) { status(error.message, true); }
  finally { busy = false; controls.forEach(button => button.disabled = false); }
}
$('connect').onsubmit = event => {
  event.preventDefault();
  return run(async () => {
    const origin = MS.origin($('origin').value);
    const origins = [MS.pattern(origin)];
    if (currentPending?.sourceOrigin) origins.push(MS.pattern(currentPending.sourceOrigin));
    // Browser grants happen directly in this explicit submit gesture.
    if (!await api.permissions.request({origins: [...new Set(origins)]})) throw Error('Browseradgang blev ikke givet. Prøv igen for at forbinde.');
    await api.storage.local.set({origin}); $('origin').value = origin;
    const result = await message({type: 'state'});
    await message({type: result.pending ? 'resume' : 'open'});
    status('MediaSeed er åbnet. Alle downloadindstillinger vælges dér.');
  });
};
$('resume').onclick = () => run(async () => {
  if (currentPending?.sourceOrigin && !await api.permissions.request({origins: [MS.pattern(currentPending.sourceOrigin)]})) throw Error('Giv adgang til trackerens hjemmeside for at hente torrentfilen.');
  await message({type: 'resume'});
});
$('clear').onclick = () => run(() => message({type: 'clear'}));
async function initialize() {
  const config = await api.storage.local.get('origin');
  $('origin').value = config.origin || '';
  if (config.origin) status('Forbundet. Vælg et link eller en torrentfil, og send til MediaSeed.');
  await refresh();
}
initialize().catch(error => status(error.message, true));

$('open-app').onclick = () => run(() => message({type: 'open'}));

$('scan-links').onclick=()=>run(async()=>{
  const result=await message({type:'page-links'});$('page-links').replaceChildren();
  for(const item of result.links){
    const button=document.createElement('button');button.type='button';button.textContent=item.label+' · '+item.host;
    button.onclick=()=>run(()=>message({type:'send-source',url:item.url,tabId:result.tabId}));$('page-links').append(button);
  }
  status(result.links.length?'Vælg det link, du vil åbne i MediaSeed.':'Ingen downloadlinks fundet. Indsæt linket, eller vælg en torrentfil nedenfor.');
});
$('send-source').onsubmit=event=>{event.preventDefault();return run(async()=>{
  const value=$('source-link').value.trim(),item=MS.sourceLink(value);
  if(item.kind==='link'&&!await api.permissions.request({origins:[MS.pattern(new URL(item.url).origin)]}))throw Error('Giv adgang til trackerens hjemmeside for at hente torrentfilen.');
  await message({type:'send-source',url:value});$('source-link').value='';
});};
$('source-file').onchange=()=>run(async()=>{
  const file=$('source-file').files[0];if(!file)return;
  if(file.size>MS.MAX_FILE)throw Error('Torrentfilen må højst fylde 4 MiB.');
  const bytes=new Uint8Array(await file.arrayBuffer());MS.file(file.name,bytes);
  await message({type:'send-file',name:file.name,base64:MS.encode(bytes),transferId:crypto.randomUUID()});$('source-file').value='';
});
