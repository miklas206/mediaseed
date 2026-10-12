const api = globalThis.browser || chrome, MS = MediaSeedExtension;
const $ = id => document.getElementById(id);
let selected = null, pending = new URL(location.href).searchParams.get('pending'), currentTab = null, adding = false;
function status(text, error = false) { $('status').textContent = text; $('status').dataset.error = String(error); }
async function message(value) { const result = await api.runtime.sendMessage(value); if (result?.error) throw Error(result.error); return result; }
async function permission(origin) { if (!await api.permissions.request({origins: [MS.pattern(origin)]})) throw Error('Browseradgang blev ikke givet.'); }
function showItem(item) {
  selected = item;
  $('magnet').disabled = !!item && item.kind !== 'magnet';
  $('magnet').value = item?.magnet || '';
  $('filename').textContent = item?.kind === 'file' ? item.name : item?.kind === 'link' ? 'Torrentlink klar · filen hentes ved tilføjelse' : 'Maks. 4 MiB';
  $('clear').hidden = !item || item.kind === 'magnet';
}
async function pick(file) {
  if (!file || file.size > MS.MAX_FILE) throw Error('Vælg en torrentfil på højst 4 MiB.');
  const bytes = new Uint8Array(await file.arrayBuffer()); MS.file(file.name, bytes);
  showItem({kind: 'file', name: file.name, base64: MS.encode(bytes)});
}
$('file').onchange = () => pick($('file').files[0]).catch(e => status(e.message, true));
$('clear').onclick = () => { showItem(null); $('file').value = ''; pending = null; };
document.addEventListener('dragover', e => { e.preventDefault(); $('drop').classList.add('over'); });
document.addEventListener('dragleave', () => $('drop').classList.remove('over'));
document.addEventListener('drop', e => { e.preventDefault(); $('drop').classList.remove('over'); if (e.dataTransfer.files.length !== 1) {status('Vælg én torrentfil ad gangen.', true); return;} pick(e.dataTransfer.files[0]).catch(err => status(err.message, true)); });
$('connect').onsubmit = async e => {
  e.preventDefault();
  try {
    const origin = MS.origin($('origin').value);
    await permission(origin); await api.storage.local.set({origin}); $('origin').value = origin;
    status('Kontrollerer dit MediaSeed-login…');
    const result = await message({type: 'send', item: {kind: 'check'}});
    status(result.role === 'viewer' ? 'Forbundet · din bruger har kun læseadgang.' : 'Forbundet · klar til at tilføje torrents.');
  } catch (err) { status(err.message, true); }
};
$('open').onclick = async () => { try { await api.tabs.create({url: MS.origin($('origin').value) + '/'}); } catch (err) {status(err.message, true);} };
$('add').onsubmit = async e => {
  e.preventDefault(); if (adding) return;
  let controls;
  try {
    let item = selected?.kind === 'file' || selected?.kind === 'link' ? {...selected} : {kind: 'magnet', magnet: MS.magnet($('magnet').value)};
    item.ratio = MS.ratio($('ratio').value); item.start = e.submitter?.value === 'start';
    adding = true; controls = [...document.querySelectorAll('button')]; controls.forEach(b => {b.disabled = true;});
    // Request only the specific source host, during this explicit user gesture.
    if (item.kind === 'link') await permission(MS.webURL(item.url).origin);
    status('Sender til MediaSeed…');
    const result = await message({type: 'send', item, pending});
    status(result.pending ? 'Tilføjet · afventer torrentmetadata i MediaSeed.' : result.stopped ? 'Tilføjet på pause.' : 'Tilføjet · start anmodet. Følg fremdriften i MediaSeed.');
    showItem(null); $('file').value = ''; pending = null;
  } catch (err) { status(err.message, true); }
  finally { adding = false; controls?.forEach(b => {b.disabled = false;}); $('enable').disabled = !currentTab; }
};
async function renderSites() {
  const {sites = []} = await api.storage.local.get('sites'); $('sites').replaceChildren();
  for (const site of sites) {
    const li = document.createElement('li'), text = document.createElement('span'), remove = document.createElement('button');
    text.textContent = site; remove.textContent = 'Slå fra'; remove.type = 'button';
    remove.onclick = async () => { await api.storage.local.set({sites: (await api.storage.local.get('sites')).sites.filter(x => x !== site)}); await renderSites(); };
    li.append(text, remove); $('sites').append(li);
  }
}
$('enable').onclick = async () => {
  try {
    const origin = MS.webURL(currentTab.url).origin;
    await permission(origin);
    const {sites = [], origin: seedOrigin} = await api.storage.local.get(['sites', 'origin']);
    if (origin === seedOrigin) throw Error('Linkfangst er ikke nødvendig på MediaSeed selv.');
    await api.storage.local.set({sites: [...new Set([...sites, origin])]});
    await message({type: 'inject', tabId: currentTab.id}); await renderSites(); status('Linkfangst aktiveret på ' + origin);
  } catch (err) { status(err.message, true); }
};
async function initialize() {
  const config = await api.storage.local.get('origin'); $('origin').value = config.origin || '';
  if (config.origin) status('Klar · bruger din session i MediaSeed.');
  const [tab] = await api.tabs.query({active: true, currentWindow: true});
  try { if (tab?.url && MS.webURL(tab.url)) {currentTab = tab; $('site').textContent = new URL(tab.url).origin; $('enable').disabled = false;} } catch {}
  if (pending) { const result = await message({type: 'pending', id: pending}); if (result.item.kind === 'invalid') throw Error('Linket er ikke et gyldigt magnet- eller torrentlink.'); showItem(result.item); }
  await renderSites();
}
initialize().catch(e => status(e.message, true));
