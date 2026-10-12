const api = globalThis.browser || chrome, MS = MediaSeedExtension;
const $ = id => document.getElementById(id);
let selected = null, pending = new URL(location.href).searchParams.get('pending'), currentTab = null, adding = false, savedDefaults = MS.defaults();
function renderDefaults() {
  const {start, ratio, cleanup: c} = savedDefaults;
  const goals = {never: t("Keep files · stop at ratio {0} / 49 hours", {0: ratio}), time: t("{0} hours of seeding", {0: c.hours}), ratio: `ratio ${c.ratio}`, both: t("{0} hours AND ratio {1}", {0: c.hours, 1: c.ratio}), either: t("{0} hours OR ratio {1}", {0: c.hours, 1: c.ratio})};
  $('settings-summary').textContent = `${start ? t("Start immediately") : t("Add on pause")} · ${goals[c.mode]}${c.mode === 'never' ? '' : ' · ' + (c.delete_files ? t("remove torrent and files") : t("remove torrent only"))}`;
  $('add-default').textContent = start ? t("Add and start") : t("Add on pause");
  $('add-other').textContent = start ? t("Add on pause") : t("Add and start"); $('add-other').value = start ? 'pause' : 'start';
}
function cleanupPolicy() {
  return {mode: $('cleanup-mode').value || 'never', hours: Number($('cleanup-hours').value || 49), ratio: Number($('cleanup-ratio').value || 1), delete_files: $('cleanup-action').value === 'files'};
}
function updateCleanup() {
  const {mode, delete_files: files} = cleanupPolicy(), active = mode !== 'never';
  $('cleanup-options').hidden = !active;
  for (const [key, show] of [['hours', ['time', 'both', 'either'].includes(mode)], ['ratio', ['ratio', 'both', 'either'].includes(mode)]]) {
    $('cleanup-' + key + '-field').hidden = !show; $('cleanup-' + key).disabled = !show;
  }
  $('cleanup-action').disabled = !active; $('cleanup-warning').hidden = !active || !files;
  $('ratio').disabled = active; const label = $('ratio').closest?.('label'); if (label) label.hidden = active;
}
$('cleanup-mode').onchange = updateCleanup; $('cleanup-action').onchange = updateCleanup; updateCleanup();
function markDirty() { $('settings-state').textContent = t("There are changes – save to use them for new additions."); $('settings-state').dataset.dirty = 'true'; }
$('settings').oninput = markDirty;
$('settings').onchange = markDirty;
$('settings').onsubmit = async e => {
  e.preventDefault();
  try {
    const defaults = MS.defaults({start: $('default-start').value === 'start', ratio: $('ratio').value, cleanup: cleanupPolicy()});
    await api.storage.local.set({defaults}); savedDefaults = defaults; renderDefaults();
    $('settings-state').textContent = t("Saved · used automatically for new additions."); $('settings-state').dataset.dirty = 'false';
    status(t("Defaults saved. Right-click now sends directly with these settings."));
  } catch (err) { status(err.message, true); }
};
function status(text, error = false) { $('status').textContent = t(text); $('status').dataset.error = String(error); }
async function message(value) { const result = await api.runtime.sendMessage(value); if (result?.error) throw Error(result.error); return result; }
async function permission(origin) { if (!await api.permissions.request({origins: [MS.pattern(origin)]})) throw Error(t("Browser access was not granted.")); }
function showItem(item) {
  selected = item;
  $('magnet').disabled = !!item && item.kind !== 'magnet';
  $('magnet').value = item?.magnet || '';
  $('filename').textContent = item?.kind === 'file' ? item.name : item?.kind === 'link' ? t("Torrent link ready · file is downloaded when adding") : t("Max. 4 MiB");
  $('clear').hidden = !item || item.kind === 'magnet';
}
async function pick(file) {
  if (!file || file.size > MS.MAX_FILE) throw Error(t("Choose a torrent file no larger than 4 MiB."));
  const bytes = new Uint8Array(await file.arrayBuffer()); MS.file(file.name, bytes);
  showItem({kind: 'file', name: file.name, base64: MS.encode(bytes)});
}
$('file').onchange = () => pick($('file').files[0]).catch(e => status(e.message, true));
$('clear').onclick = () => { showItem(null); $('file').value = ''; pending = null; };
document.addEventListener('dragover', e => { e.preventDefault(); $('drop').classList.add('over'); });
document.addEventListener('dragleave', () => $('drop').classList.remove('over'));
document.addEventListener('drop', e => { e.preventDefault(); $('drop').classList.remove('over'); if (e.dataTransfer.files.length !== 1) {status(t("Choose one torrent file at a time."), true); return;} pick(e.dataTransfer.files[0]).catch(err => status(err.message, true)); });
$('connect').onsubmit = async e => {
  e.preventDefault();
  try {
    const origin = MS.origin($('origin').value);
    await permission(origin); await api.storage.local.set({origin}); $('origin').value = origin;
    $('connection-name').textContent = new URL(origin).host;
    status(t("Checking your MediaSeed login…"));
    const result = await message({type: 'send', item: {kind: 'check'}}); setLanguage(result.language || 'en'); renderDefaults();
    status(result.role === 'viewer' ? t("Connected · your account has read-only access.") : t("Connected · ready to add torrents."));
    $('connection').open = result.role === 'viewer';
  } catch (err) { status(err.message, true); }
};
$('open').onclick = async () => { try { await api.tabs.create({url: MS.origin($('origin').value) + '/'}); } catch (err) {status(err.message, true);} };
$('add').onsubmit = async e => {
  e.preventDefault(); if (adding) return;
  let controls;
  try {
    let item = selected?.kind === 'file' || selected?.kind === 'link' ? {...selected} : {kind: 'magnet', magnet: MS.magnet($('magnet').value)};
    item = {...item, ...savedDefaults, start: ['start', 'pause'].includes(e.submitter?.value) ? e.submitter.value === 'start' : savedDefaults.start};
    adding = true; controls = [...document.querySelectorAll('button')]; controls.forEach(b => {b.disabled = true;});
    // Request only the specific source host, during this explicit user gesture.
    if (item.kind === 'link') await permission(MS.webURL(item.url).origin);
    status(t("Sending to MediaSeed…"));
    const result = await message({type: 'send', item, pending});
    status(result.cleanupPending ? t("Added paused · cleanup rule is awaiting preparation. Your selected start will follow.") : result.pending ? t("Added · waiting for torrent metadata in MediaSeed.") : result.stopped ? t("Added paused.") : t("Added · start requested. Follow progress in MediaSeed."));
    showItem(null); $('file').value = ''; pending = null;
  } catch (err) { status(err.message, true); }
  finally { adding = false; controls?.forEach(b => {b.disabled = false;}); $('enable').disabled = !currentTab; }
};
async function renderSites() {
  const {sites = []} = await api.storage.local.get('sites'); $('sites').replaceChildren();
  for (const site of sites) {
    const li = document.createElement('li'), text = document.createElement('span'), remove = document.createElement('button');
    text.textContent = site; remove.textContent = t("Disable"); remove.type = 'button';
    remove.onclick = async () => { await api.storage.local.set({sites: (await api.storage.local.get('sites')).sites.filter(x => x !== site)}); await renderSites(); };
    li.append(text, remove); $('sites').append(li);
  }
}
$('enable').onclick = async () => {
  try {
    const origin = MS.webURL(currentTab.url).origin;
    await permission(origin);
    const {sites = [], origin: seedOrigin} = await api.storage.local.get(['sites', 'origin']);
    if (origin === seedOrigin) throw Error(t("Link capture is not needed on MediaSeed itself."));
    await api.storage.local.set({sites: [...new Set([...sites, origin])]});
    await message({type: 'inject', tabId: currentTab.id}); await renderSites(); status(t("Link capture enabled on ") + origin);
  } catch (err) { status(err.message, true); }
};
async function initialize() {
  const config = await api.storage.local.get(['origin', 'defaults', 'lastResult', 'language']); $('origin').value = config.origin || '';
  setLanguage(config.language || 'da'); savedDefaults = MS.defaults(config.defaults);
  $('default-start').value = savedDefaults.start ? 'start' : 'pause'; $('ratio').value = String(savedDefaults.ratio);
  $('cleanup-mode').value = savedDefaults.cleanup.mode; $('cleanup-hours').value = String(savedDefaults.cleanup.hours);
  $('cleanup-ratio').value = String(savedDefaults.cleanup.ratio); $('cleanup-action').value = savedDefaults.cleanup.delete_files ? 'files' : 'torrent';
  updateCleanup(); renderDefaults(); $('connection').open = !config.origin;
  if (config.origin) { $('connection-name').textContent = new URL(config.origin).host; status(t("Ready · sends directly with your saved settings.")); }
  if (config.lastResult && Date.now() - config.lastResult.at < 10 * 60 * 1000) status(config.lastResult.text, config.lastResult.error);
  const [tab] = await api.tabs.query({active: true, currentWindow: true});
  try { if (tab?.url && MS.webURL(tab.url)) {currentTab = tab; $('site').textContent = new URL(tab.url).origin; $('enable').disabled = false;} } catch {}
  if (!config.origin && currentTab && api.scripting) {
    try {
      const result = await api.scripting.executeScript({target: {tabId: currentTab.id}, func: mediaSeedDiscover});
      if (result[0]?.result) { $('origin').value = MS.origin(result[0].result); status(t("MediaSeed found. Press Save and connect – the address is filled in.")); }
    } catch { /* An ordinary website need not permit discovery. */ }
  }
  if (pending) { const result = await message({type: 'pending', id: pending}); if (result.item.kind === 'invalid') throw Error(t("The link is not a valid magnet or torrent link.")); showItem(result.item); }
  if (config.origin) {
    try { const identity = await message({type: 'send', item: {kind: 'check'}}); setLanguage(identity.language || 'en'); updateCleanup(); renderDefaults(); }
    catch (error) { status(error.message, true); }
  }
  await renderSites();
}
initialize().catch(e => status(e.message, true));
