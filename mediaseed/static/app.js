let csrf = '';
let needsSetup = false;
let torrents = [];
let selectedHash = '';
let downloadsEnabled = false;
let refreshing = false;
let appRole = 'administrator';
const message = document.querySelector('#message');
const dialog = document.querySelector('#torrent-details');
let notificationTimer;
function showNotification(text, tone = 'danger') {
  clearTimeout(notificationTimer);
  message.dataset.tone = tone;
  message.textContent = text;
  if (text) notificationTimer = setTimeout(() => { message.textContent = ''; }, 6000);
}
message.tabIndex = 0;
message.addEventListener('mouseenter', () => clearTimeout(notificationTimer));
message.addEventListener('focus', () => clearTimeout(notificationTimer));
function resumeNotification() { if (message.textContent && !message.matches(':hover') && document.activeElement !== message) notificationTimer = setTimeout(() => { message.textContent = ''; }, 6000); }
message.addEventListener('mouseleave', resumeNotification);
message.addEventListener('blur', resumeNotification);
const errorLabels = {'Invalid credentials': 'Brugernavn eller adgangskode er forkert.', 'Try again later': 'For mange loginforsøg. Prøv igen om lidt.', 'Torrent client is not configured': 'Torrentklienten er endnu ikke tilsluttet.', 'Sign in required': 'Log ind for at fortsætte.', 'Tracker API key lacks access': 'API-nøglen har ikke adgang til trackerens torrents.', 'Tracker rate limit reached; try again later': 'Trackeren begrænser antal opslag. Prøv igen om lidt.', 'Tracker request failed': 'Trackeren kunne ikke levere svaret.', 'Tracker unavailable or invalid response': 'Trackeren er utilgængelig eller returnerede et ukendt svar.', 'Unsafe tracker download address': 'Trackeren returnerede et downloadlink, som ikke er godkendt.', 'Torrent client rejected the torrent file': 'qBittorrent afviste torrentfilen.'};
async function api(path, body, method) {
  const response = await fetch('/api/' + path, {method: method || (body ? 'POST' : 'GET'), headers: {'Content-Type': 'application/json', 'X-MediaSeed-CSRF': csrf}, ...(body ? {body: JSON.stringify(body)} : {})});
  const result = await response.json();
  if (!response.ok) throw Object.assign(new Error(typeof result.detail === 'string' ? result.detail : 'Kontrollér felterne og prøv igen.'), {status: response.status});
  return result;
}
function notify(error) { showNotification(errorLabels[error.message] || error.message); }
Object.assign(errorLabels, {'Invalid torrent file': 'Filen er ikke en gyldig torrentfil.', 'Choose a .torrent file': 'Vælg en .torrent-fil.', 'Torrent file exceeds 4 MiB': 'Torrentfilen må højst være 4 MiB.'});
function element(tag, text, className) { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (className) node.className = className; return node; }
function bytes(value) { const number = Number(value || 0); for (const [divisor, unit] of [[1073741824, 'GiB'], [1048576, 'MiB'], [1024, 'KiB']]) if (number >= divisor) return `${(number / divisor).toFixed(1)} ${unit}`; return `${number} B`; }
function state(row) { if (/error|missing/i.test(row.state)) return 'error'; if (/stopped|paused/i.test(row.state)) return 'stopped'; if (Number(row.progress) >= 1) return 'seeding'; return 'downloading'; }
function command(label, handler) { const button = element('button', label); button.type = 'button'; button.onclick = async () => { button.disabled = true; try { await handler(); } catch (error) { notify(error); } finally { button.disabled = button.dataset.added === 'true'; } }; return button; }
async function torrentAction(row, action) {
  if (action === 'remove' && !confirm(`Fjern ${row.name}? Downloadede filer bevares.`)) return;
  try { await api('torrents/' + action, {torrent_hash: row.hash}); }
  catch (error) { if (error.status !== 409 || action === 'start') throw error; if (!confirm('Seedingmålet er ikke nået: ratio under 1 og mindre end 49 timer. Vil du alligevel fortsætte?')) return; await api('torrents/' + action, {torrent_hash: row.hash, confirm_early: true}); }
  await refresh();
}
function renderTorrents() {
  const search = document.querySelector('#search').value.toLocaleLowerCase(); const filter = document.querySelector('#filter').value; const sort = document.querySelector('#sort').value;
  const rows = torrents.filter(row => String(row.name).toLocaleLowerCase().includes(search) && (filter === 'all' || state(row) === filter));
  const source = document.querySelector('#source-filter').value; const filtered = rows.filter(row => source === 'all' || String(row.category || '').startsWith('MediaSeed-RSS-') === (source === 'rss')); rows.splice(0, rows.length, ...filtered);
  rows.sort((first, second) => sort === 'name' ? String(first.name).localeCompare(String(second.name)) : Number(second[sort]) - Number(first[sort]));
  document.querySelector('#count').textContent = `${rows.length} af ${torrents.length} torrents`;
  const target = document.querySelector('#torrents'); target.replaceChildren(); if (!rows.length) target.append(element('p', torrents.length ? 'Ingen matchende torrents' : 'Ingen torrents', 'muted'));
  for (const row of rows) {
    const item = element('article', undefined, 'torrent'); const progress = element('progress'); progress.max = 1; progress.value = Math.max(0, Math.min(1, Number(row.progress || 0))); progress.setAttribute('aria-label', 'Fremdrift');
    if (typeof row.cover === 'string' && /^\/api\/covers\/(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(row.cover)) { const cover = element('img', undefined, 'torrent-cover'); cover.src = row.cover; cover.alt = ''; cover.width = 64; cover.height = 96; cover.loading = 'lazy'; cover.onerror = () => cover.remove(); item.append(cover); }
    const status = element('p', `${Math.round(Number(row.progress || 0) * 100)} % · ${row.state || ''} · ${bytes(row.size)} · Ratio ${Number(row.ratio || 0).toFixed(2)}`);
    const speeds = element('p', `Download ${bytes(row.dlspeed)}/s · Upload ${bytes(row.upspeed)}/s · Seeding ${(Number(row.seeding_time || 0) / 3600).toFixed(1)} timer`);
    const actions = element('div', undefined, 'actions'); actions.append(command('Detaljer', () => showDetails(row))); if (appRole !== 'viewer') { const start = command('Start', () => torrentAction(row, 'start')); start.disabled = !downloadsEnabled; actions.append(start, command('Stop', () => torrentAction(row, 'stop')), command('Fjern torrent', () => torrentAction(row, 'remove'))); }
    item.append(element('h2', row.name), progress, status, speeds, actions); target.append(item);
  }
}
async function refresh() {
  if (refreshing) return; refreshing = true;
  try { const runtime = await api('runtime'); downloadsEnabled = runtime.downloadsEnabled === true; } catch { downloadsEnabled = false; }
  try { torrents = await api('torrents'); renderTorrents(); } catch (error) { torrents = []; renderTorrents(); document.querySelector('#count').textContent = 'Torrentklient utilgængelig'; }
  try { const client = await api('client'); const transfer = await api('transfer'); document.querySelector('#transfer').textContent = `${client.name} ${client.version} · Download ${bytes(transfer.dl_info_speed)}/s · Upload ${bytes(transfer.up_info_speed)}/s · ${downloadsEnabled ? 'VPN verificeret' : 'Downloads blokeret indtil VPN er verificeret'}`; } catch { document.querySelector('#transfer').textContent = 'qBittorrent ikke tilsluttet · downloads blokeret'; }
  try { const queue = await api('queue'); document.querySelector('#queue-status').textContent = `Kø: ${queue.active}/${queue.limit} aktive · ${queue.waiting} ventende · RSS ${queue.rssActive} · Manuelle ${queue.manualActive}${queue.boundedView ? ' · Begrænset til 500 torrents' : ''}`; } catch { document.querySelector('#queue-status').textContent = 'Køstatus utilgængelig'; }
  const storage = document.querySelector('#storage'); storage.hidden = false;
  try { const capacity = await api('storage'); storage.textContent = `Lokal storage: ${bytes(capacity.freeBytes)} ledig af ${bytes(capacity.totalBytes)}${capacity.lowSpace ? ' · Lav diskplads' : ''}${capacity.writable ? '' : ' · Ikke skrivbar'}`; } catch { storage.textContent = 'Lokal storage utilgængelig'; }
  refreshing = false;
}

async function refreshVpn() {
  const state = await api('vpn'); const select = document.querySelector('#vpn-profile'); const previous = select.value; select.replaceChildren(element('option', 'Vælg profil')); select.firstChild.value = '';
  for (const profile of state.profiles || []) { const option = element('option', `${profile.name} · ${profile.forwardedPort ? 'port ' + profile.forwardedPort : 'port ikke verificeret'}`); option.value = profile.id; select.append(option); }
  select.value = state.profileId || previous; if (!select.value && select.options.length === 2) select.selectedIndex = 1;
  document.querySelector('#vpn-status').textContent = state.ready ? 'Verificeret' : 'Ikke verificeret';
  document.querySelector('#vpn-downloads').textContent = state.downloadsEnabled ? 'Tilladt gennem VPN' : 'Blokeret';
  document.querySelector('#vpn-ip').textContent = state.publicIp || 'Ikke verificeret'; document.querySelector('#vpn-port').textContent = state.forwardedPort || 'Ikke verificeret'; document.querySelector('#vpn-reason').textContent = state.reason || '';
  document.querySelector('#vpn-connect').disabled = select.options.length < 2;
}
document.querySelector('#vpn-form').onsubmit = async event => { event.preventDefault(); const button = document.querySelector('#vpn-connect'); button.disabled = true; try { await api('vpn', {enabled: true, profileId: document.querySelector('#vpn-profile').value}, 'PUT'); await refreshVpn(); await refresh(); showNotification('Seedbox-tilkobling gemt. Status verificeres.', 'success'); } catch (error) { notify(error); } finally { button.disabled = false; } };
document.querySelector('#vpn-disconnect').onclick = async () => { try { await api('vpn', {enabled: false, profileId: document.querySelector('#vpn-profile').value || null}, 'PUT'); await refreshVpn(); await refresh(); showNotification('Seedbox stoppet.', 'success'); } catch (error) { notify(error); } };
document.querySelector('#vpn-refresh').onclick = () => refreshVpn().catch(notify);
function detailTable(title, columns, rows) {
  const section = element('section'); section.append(element('h3', title)); if (!rows.length) { section.append(element('p', 'Ingen data', 'muted')); return section; }
  const table = element('table'); const heading = element('tr'); for (const [, label] of columns) heading.append(element('th', label)); const head = element('thead'); head.append(heading); table.append(head);
  const body = element('tbody'); for (const row of rows) { const line = element('tr'); for (const [key] of columns) line.append(element('td', String(row[key] ?? '—'))); body.append(line); } table.append(body); const wrap = element('div', undefined, 'table-wrap'); wrap.append(table); section.append(wrap); return section;
}
async function showDetails(row) {
  selectedHash = row.hash; document.querySelector('#detail-name').textContent = row.name; document.querySelector('#detail-message').textContent = ''; const target = document.querySelector('#detail-data'); target.replaceChildren(element('p', 'Henter detaljer…'));
  document.querySelector('#policy-form input').value = Number(row.ratio_limit) >= 1 ? row.ratio_limit : 1; dialog.showModal();
  try { const details = await api(`torrents/${row.hash}/details`); target.replaceChildren(element('p', `Downloadet ${bytes(details.properties.total_downloaded)} · Uploadet ${bytes(details.properties.total_uploaded)} · Seeding ${(Number(details.properties.seeding_time || 0) / 3600).toFixed(1)} timer`)); target.append(detailTable('Filer', [['name', 'Navn'], ['size', 'Bytes'], ['progress', 'Fremdrift']], details.files), detailTable('Trackere', [['host', 'Tracker'], ['status', 'Status'], ['num_peers', 'Peers']], details.trackers), detailTable('Peers', [['ip', 'IP'], ['client', 'Klient'], ['progress', 'Fremdrift']], details.peers)); try { const benefits = await api(`torrents/${row.hash}/benefits`); if (benefits.status === 'matched') { const flags = [benefits.freeleech === null ? null : `Freeleech ${benefits.freeleech} %`, benefits.doubleUpload ? 'Dobbelt upload' : null, benefits.featured ? 'Featured' : null, benefits.internal ? 'Internal' : null, benefits.refundable ? 'Refunderbar' : null].filter(Boolean); target.append(element('h3', 'Trackerfordele'), element('p', `${benefits.tracker} · Præcist hash-match · ${flags.join(' · ') || 'Ingen bekræftede fordele'}`, 'muted')); } } catch {} } catch (error) { target.replaceChildren(element('p', error.message)); }
}
async function loadSources(kind) {
  const rows = await api(kind); const target = document.querySelector(kind === 'feeds' ? '#feed-list' : '#tracker-list'); target.replaceChildren(); if (!rows.length) target.append(element('p', kind === 'feeds' ? 'Ingen feeds' : 'Ingen konti', 'muted'));
  if (kind === 'trackers') {
    const select = document.querySelector('#tracker-account'); const previous = select.value; select.replaceChildren(element('option', 'Vælg konto')); select.firstChild.value = '';
    for (const row of rows) { const option = element('option', row.name); option.value = row.id; select.append(option); }
    select.value = rows.some(row => row.id === previous) ? previous : rows.length === 1 ? rows[0].id : '';
    document.querySelector('#tracker-search-submit').disabled = !rows.length;
  }
  for (const row of rows) {
    const item = element('article', undefined, 'source-row'); item.append(element('h2', row.name), element('p', kind === 'feeds' ? `${row.folder || '/downloads'} · Ratio ${row.ratio} · ${row.paused ? 'Pauset' : 'Aktiv'} · ${row.status || ''}` : row.status === 'ok' ? `NordicBytes · ${row.username || ''} · Ratio ${row.stats.ratio ?? '?'} · Upload ${bytes(row.stats.uploaded)} · Download ${bytes(row.stats.downloaded)} · Bonus ${row.stats.seedbonus ?? '?'} · Advarsler ${row.stats.warnings ?? '?'}` : 'NordicBytes · Kontodata utilgængelige', 'muted')); const actions = element('div', undefined, 'actions');
    if (appRole === 'administrator') {
      if (kind === 'feeds') actions.append(command(row.paused ? 'Aktivér' : 'Pause', async () => { await api(`feeds/${row.id}/state`, {enabled: row.paused}, 'PUT'); await loadSources('feeds'); showNotification(row.paused ? 'Feed aktiveret gennem VPN.' : 'Feed sat på pause. Eksisterende torrents bevares.', 'success'); }));
      actions.append(command('Rediger', () => { const form = document.querySelector(kind === 'feeds' ? '#feed-form' : '#tracker-form'); form.reset(); for (const [key, value] of Object.entries(row)) { const input = form.elements.namedItem(key); if (input) input.value = key === 'start_at' && value ? new Date(new Date(value).getTime() - new Date(value).getTimezoneOffset() * 60000).toISOString().slice(0, 16) : value ?? ''; } form.querySelector('input[name="name"]').focus(); }), command('Fjern', async () => { if (!confirm(`Fjern ${row.name}? Filer og torrents bevares.`)) return; await api(`settings/${kind}/${row.id}`, undefined, 'DELETE'); await loadSources(kind); }));
    }
    item.append(actions); target.append(item);
  }
}
let trackerSearchGeneration = 0;
document.querySelector('#tracker-account').onchange = () => { trackerSearchGeneration++; document.querySelector('#tracker-results').replaceChildren(); document.querySelector('#tracker-search-status').textContent = ''; };
document.querySelector('#tracker-search').onsubmit = async event => {
  event.preventDefault(); const form = event.target; const account = form.elements.namedItem('account').value; if (!account) return;
  const generation = ++trackerSearchGeneration; const button = document.querySelector('#tracker-search-submit'); const target = document.querySelector('#tracker-results'); const status = document.querySelector('#tracker-search-status'); button.disabled = true; target.replaceChildren(); status.textContent = 'Søger…';
  try {
    const response = await api(`trackers/${encodeURIComponent(account)}/search?query=${encodeURIComponent(form.elements.namedItem('query').value)}`);
    if (generation !== trackerSearchGeneration) return;
    status.textContent = response.results.length ? `${response.results.length} resultater · Torrents tilføjes stoppet. Start kræver verificeret VPN.` : 'Ingen resultater';
    for (const row of response.results) {
      const item = element('article', undefined, 'source-row'); item.append(element('h2', row.name), element('p', `${row.size === null ? 'Ukendt størrelse' : bytes(row.size)} · Seeders ${row.seeders ?? '?'} · Leechers ${row.leechers ?? '?'}`, 'muted'));
      const actions = element('div', undefined, 'actions'); const add = command('Tilføj stoppet', async () => {
        if (!form.reportValidity()) return;
        await api(`trackers/${encodeURIComponent(account)}/torrents/${encodeURIComponent(row.id)}`, {ratio: Number(form.elements.namedItem('ratio').value)});
        add.textContent = 'Tilføjet stoppet'; add.dataset.added = 'true'; showNotification('Torrent tilføjet stoppet. Download kræver verificeret VPN.', 'success'); await refresh();
      });
      actions.append(add); item.append(actions); target.append(item);
    }
  } catch (error) { if (generation === trackerSearchGeneration) { status.textContent = ''; notify(error); } }
  finally { button.disabled = document.querySelector('#tracker-account').options.length === 1; }
};
for (const [selector, kind] of [['#feed-form', 'feeds'], ['#tracker-form', 'trackers']]) {
  const form = document.querySelector(selector); form.onsubmit = async event => { event.preventDefault(); const body = Object.fromEntries(new FormData(form)); const identifier = body.id; delete body.id; if (kind === 'feeds') { body.ratio = Number(body.ratio); body.start_at = body.start_at ? new Date(body.start_at).toISOString() : null; } try { await api(kind + (identifier ? '/' + identifier : ''), body, identifier ? 'PUT' : 'POST'); form.reset(); form.elements.namedItem('id').value = ''; await loadSources(kind); showNotification('Gemt', 'success'); } catch (error) { notify(error); } };
  form.addEventListener('reset', () => { form.elements.namedItem('id').setAttribute('value', ''); });
}
for (const button of document.querySelectorAll('[data-view]')) button.onclick = async () => { message.textContent = ''; for (const other of document.querySelectorAll('[data-view]')) other.setAttribute('aria-pressed', String(other === button)); for (const section of document.querySelectorAll('.view')) section.hidden = section.id !== button.dataset.view; try { if (button.dataset.view === 'feeds' || button.dataset.view === 'trackers') await loadSources(button.dataset.view); else if (button.dataset.view === 'vpn') await refreshVpn(); } catch (error) { notify(error); } };
document.querySelector('#close-details').onclick = () => dialog.close();
document.querySelector('#policy-form').onsubmit = async event => { event.preventDefault(); try { await api(`torrents/${selectedHash}/policy`, {ratio: Number(event.target.elements.namedItem('ratio').value)}); document.querySelector('#detail-message').textContent = 'Seedingmål gemt'; } catch (error) { document.querySelector('#detail-message').textContent = error.message; } };
for (const selector of ['#search', '#filter', '#sort', '#source-filter']) document.querySelector(selector).addEventListener('input', renderTorrents);
function showWorkspace() { document.body.classList.add('signed-in'); document.querySelector('#identity').hidden = true; document.querySelector('#workspace').hidden = false; document.querySelector('#logout').hidden = false; document.querySelector('#new-torrent').hidden = appRole === 'viewer'; document.querySelector('#policy-form').hidden = appRole === 'viewer'; for (const selector of ['#feed-form', '#tracker-form', '#vpn-form']) document.querySelector(selector).hidden = appRole !== 'administrator'; for (const selector of ['[data-view="feeds"]', '[data-view="trackers"]']) document.querySelector(selector).hidden = appRole === 'viewer'; refresh(); }
document.querySelector('#login').onsubmit = async event => { event.preventDefault(); const body = Object.fromEntries(new FormData(event.target)); try { if (needsSetup) { await api('auth/setup', body); needsSetup = false; } csrf = (await api('auth/login', body)).csrf; event.target.reset(); showWorkspace(); } catch (error) { notify(error); } };
document.querySelector('#refresh').onclick = refresh;
const addDialog = document.querySelector('#add-torrent');
const addForm = document.querySelector('#add-torrent-form');
const fileInput = document.querySelector('#add-file');
const fileField = document.querySelector('#file-field');
let droppedTorrent = null;
let addingTorrent = false;
function setAddMode() {
  const fileMode = addForm.elements.namedItem('mode').value === 'file';
  document.querySelector('#magnet-field').hidden = fileMode; fileField.hidden = !fileMode;
  document.querySelector('#add-magnet').disabled = fileMode; document.querySelector('#add-magnet').required = !fileMode;
  fileInput.disabled = !fileMode; fileInput.required = fileMode && !droppedTorrent;
}
for (const radio of addForm.querySelectorAll('[name="mode"]')) radio.onchange = setAddMode;
document.querySelector('#new-torrent').onclick = () => { showNotification(''); addForm.reset(); droppedTorrent = null; document.querySelector('#file-name').textContent = ''; setAddMode(); addDialog.append(document.querySelector('.notifications')); addDialog.showModal(); };
addDialog.addEventListener('close', () => { document.body.append(document.querySelector('.notifications')); });
function closeAdd() { if (!addingTorrent) addDialog.close(); }
document.querySelector('#close-add').onclick = closeAdd; document.querySelector('#cancel-add').onclick = closeAdd;
addDialog.addEventListener('cancel', event => { if (addingTorrent) event.preventDefault(); });
fileInput.onchange = () => { droppedTorrent = null; document.querySelector('#file-name').textContent = fileInput.files[0]?.name || ''; setAddMode(); };
fileField.ondragover = event => { event.preventDefault(); if (!addingTorrent) fileField.classList.add('dragging'); };
fileField.ondragleave = () => fileField.classList.remove('dragging');
fileField.ondrop = event => { event.preventDefault(); fileField.classList.remove('dragging'); if (addingTorrent) return; if (event.dataTransfer.files.length !== 1) { showNotification('Vælg én torrentfil.'); return; } droppedTorrent = event.dataTransfer.files[0]; fileInput.value = ''; document.querySelector('#file-name').textContent = droppedTorrent.name; setAddMode(); };
addForm.onsubmit = async event => {
  event.preventDefault(); if (addingTorrent) return;
  const fileMode = addForm.elements.namedItem('mode').value === 'file'; const ratio = Number(addForm.elements.namedItem('ratio').value);
  const file = droppedTorrent || fileInput.files[0];
  if (fileMode && (!file || !file.name.toLowerCase().endsWith('.torrent'))) { showNotification('Vælg en .torrent-fil.'); return; }
  if (fileMode && file.size > 4 * 1024 * 1024) { showNotification('Torrentfilen må højst være 4 MiB.'); return; }
  addingTorrent = true; const controls = [...addForm.elements, document.querySelector('#close-add')]; const disabledStates = controls.map(control => control.disabled); controls.forEach(control => { control.disabled = true; });
  try {
    let result;
    if (fileMode) {
      const response = await fetch(`/api/torrents/upload?filename=${encodeURIComponent(file.name)}&ratio=${ratio}`, {method: 'POST', headers: {'Content-Type': 'application/x-bittorrent', 'X-MediaSeed-CSRF': csrf}, body: file});
      result = await response.json(); if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Torrentfilen kunne ikke tilføjes.');
    } else { result = await api('torrents', {url: addForm.elements.namedItem('url').value.trim(), ratio}); }
    addDialog.close(); await refresh(); showNotification(result.pending ? 'Magnetlink modtaget stoppet. Metadata afventer VPN.' : 'Torrent tilføjet stoppet. Start kræver verificeret VPN.', 'success');
  } catch (error) { notify(error); }
  finally { addingTorrent = false; controls.forEach((control, index) => { control.disabled = disabledStates[index]; }); }
};
document.querySelector('#logout').onclick = async () => { try { await api('auth/logout', {}); location.reload(); } catch (error) { notify(error); } };
setInterval(() => { if (!document.hidden && document.body.classList.contains('signed-in')) { if (!document.querySelector('#vpn').hidden) refreshVpn().catch(() => {}); if (!document.querySelector('#feeds').hidden) loadSources('feeds').catch(() => {}); refresh(); } }, 10000);
(async () => { try { const status = await api('auth/status'); needsSetup = status.needsSetup; document.querySelector('#identity-title').textContent = needsSetup ? 'En frisk begyndelse.' : 'Velkommen tilbage.'; document.querySelector('#identity-subtitle').textContent = needsSetup ? 'Opret din lokale MediaSeed-administrator.' : 'Log ind p\u00e5 MediaSeed.'; document.querySelector('#login-submit').textContent = needsSetup ? 'Opret administrator' : 'Log ind'; if (status.managed) { document.querySelector('#login').hidden = true; const link = element('a', 'Log ind med MediaHub', 'primary'); link.href = '/auth/hub/start'; document.querySelector('#login').after(link); } if (!needsSetup) { try { const identity = await api('auth/me'); csrf = identity.csrf; appRole = identity.role || 'administrator'; showWorkspace(); } catch {} } } catch (error) { notify(error); } })();