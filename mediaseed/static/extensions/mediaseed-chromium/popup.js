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
  const controls = [$('save'), $('resume'), $('clear')]; controls.forEach(button => button.disabled = true);
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
  if (config.origin) status('Forbundet. Højreklik på et RSS- eller torrentlink → Send til MediaSeed.');
  await refresh();
}
initialize().catch(error => status(error.message, true));

// A full extension tab keeps File objects alive when MediaSeed receives focus.
// Send one file at a time: no large permanent queue or browser storage grant.
const batchMode = location.hash === '#batch';
$('batch').hidden = !batchMode;
$('batch-launch').hidden = batchMode;
$('open-batch').onclick = () => message({type: 'batch-window'}).catch(error => status(error.message, true));
let batchRows = [], batchSending = false;
function renderBatch() {
  $('batch-list').replaceChildren();
  for (const row of batchRows) {
    const li = document.createElement('li'), name = document.createElement('span'), detail = document.createElement('small');
    li.dataset.state = row.state;
    name.textContent = row.file?.name || 'Torrentlink ' + (batchRows.indexOf(row) + 1);
    detail.textContent = row.detail || ({ready: 'Klar', sending: 'Overfører…', sent: 'Klar i MediaSeed', waiting: 'Afventer MediaSeed / login', error: 'Ikke overført'})[row.state];
    li.append(name, detail); $('batch-list').append(li);
  }
}
function collectBatch() {
  const files = [...$('batch-files').files];
  const links = [...new Set($('batch-links').value.split(/\r?\n/).map(x => x.trim()).filter(Boolean))];
  if (files.length + links.length > 100 || files.reduce((n, file) => n + file.size, 0) > 64 * 1024 * 1024) throw Error('Vælg højst 100 torrents og 64 MiB i alt.');
  for (const file of files) {
    if (!file.name.toLowerCase().endsWith('.torrent') || !file.size || file.size > MS.MAX_FILE) throw Error('Vælg .torrent-filer på højst 4 MiB hver.');
    if (!batchRows.some(row => row.file === file)) batchRows.push({file, id: crypto.randomUUID(), state: 'ready'});
  }
  for (const url of links) {
    if (MS.sourceLink(url).kind !== 'link') throw Error('Indsæt torrent-downloadlinks. Send RSS og magnetlinks via højreklik.');
    if (!batchRows.some(row => row.url === url)) batchRows.push({url, id: crypto.randomUUID(), state: 'ready'});
  }
  if (batchRows.length > 100 || batchRows.reduce((n, row) => n + (row.file?.size || 0), 0) > 64 * 1024 * 1024) throw Error('Åbn en ny batch for flere end 100 torrents / 64 MiB.');
  if (!batchRows.length) throw Error('Vælg torrentfiler eller indsæt downloadlinks først.');
  renderBatch();
}
$('batch-files').onchange = () => { if (!batchSending) { batchRows = batchRows.filter(row => !row.file); renderBatch(); } };
$('batch-links').oninput = () => { if (!batchSending) { batchRows = batchRows.filter(row => !row.url); renderBatch(); } };
$('send-batch').onclick = async () => {
  if (batchSending) return;
  try {
    collectBatch();
    const origin = MS.origin($('origin').value);
    const origins = [...new Set([MS.pattern(origin), ...batchRows.filter(row => row.url).map(row => MS.pattern(new URL(row.url).origin))])];
    // Request only the selected sources, directly inside the button gesture.
    if (!await api.permissions.request({origins})) throw Error('Giv browseradgang for at sende de valgte torrents.');
    await api.storage.local.set({origin});
    batchSending = true;
    const controls = ['send-batch', 'batch-files', 'batch-links', 'save', 'resume', 'clear'];
    controls.forEach(id => $(id).disabled = true);
    try {
      for (const row of batchRows.filter(row => row.state !== 'sent')) {
        row.state = 'sending'; row.detail = ''; renderBatch();
        let result;
        try {
          if (row.file) {
            const bytes = new Uint8Array(await row.file.arrayBuffer()); MS.file(row.file.name, bytes);
            result = await message({type: 'send-file', name: row.file.name, base64: MS.encode(bytes), transferId: row.id});
          } else result = await message({type: 'send-link', url: row.url, transferId: row.id});
          row.state = result?.opened ? 'sent' : 'waiting'; renderBatch();
          if (row.state === 'waiting') {
            const current = await message({type: 'state'}); row.detail = current.status?.text || ''; renderBatch();
            $('batch-status').textContent = 'Log ind / afslut dialogen i MediaSeed, og tryk Send valgte igen her. Allerede overførte filer springes over.'; break; }
        } catch (error) { row.state = 'error'; row.detail = error.message; renderBatch(); throw error; }
      }
      if (batchRows.every(row => row.state === 'sent')) $('batch-status').textContent = batchRows.length + ' torrents er klar i MediaSeed. Vælg mappe og tryk Start eller Tilføj på pause dér.';
    } finally { batchSending = false; controls.forEach(id => $(id).disabled = false); }
  } catch (error) { $('batch-status').textContent = error.message; }
};
