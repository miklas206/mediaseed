let csrf = '';
let needsSetup = false;
let torrents = [];
let selectedHash = '';
let downloadsEnabled = false;
let refreshing = false;
let torrentListRequest = 0;
const pendingRemovals = new Map();
let appRole = 'administrator';
let vpnDraft = false;
let vpnFreshUntil = 0;
let trafficFreshUntil = 0;
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
errorLabels['Starting requires a verified VPN. Choose add on pause instead.'] = 'Start kræver en verificeret VPN. Du kan vælge Tilføj på pause.';
function element(tag, text, className) { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (className) node.className = className; return node; }
function bytes(value) { const number = Number(value || 0); for (const [divisor, unit] of [[1073741824, 'GiB'], [1048576, 'MiB'], [1024, 'KiB']]) if (number >= divisor) return `${(number / divisor).toFixed(1)} ${unit}`; return `${number} B`; }
function state(row) { if (/error|missing/i.test(row.state)) return 'error'; if (/stopped|paused/i.test(row.state)) return 'stopped'; if (Number(row.progress) >= 1) return 'seeding'; return 'downloading'; }
function command(label, handler) { const button = element('button', label); button.type = 'button'; button.onclick = async () => { button.disabled = true; try { await handler(); } catch (error) { notify(error); } finally { button.disabled = button.dataset.added === 'true'; } }; return button; }
async function torrentAction(row, action, deleteFiles = false) {
  try { await api('torrents/' + action, {torrent_hash: row.hash, delete_files: deleteFiles}); }
  catch (error) { if (error.status !== 409 || action === 'start') throw error; if (!confirm('Seedingmålet er ikke nået: ratio under 1 og mindre end 49 timer. Vil du alligevel fortsætte?')) return; await api('torrents/' + action, {torrent_hash: row.hash, delete_files: deleteFiles, confirm_early: true}); }
  if (action === 'remove') {
    pendingRemovals.set(row.hash, {confirmed: false});
    renderTorrents();
  } else await refresh();
  return true;
}
async function confirmRemoval(row) {
  const deadline = Date.now() + 30000;
  try {
    while (true) {
      const result = await api(`torrents/${row.hash}/presence`);
      if (result.present === false) {
        pendingRemovals.set(row.hash, {confirmed: true});
        renderTorrents();
        showNotification('Torrenten er fjernet.', 'success');
        return;
      }
      if (Date.now() >= deadline) throw new Error('Fjernelsen er endnu ikke bekræftet. Opdater listen for at kontrollere status.');
      await new Promise(resolve => setTimeout(resolve, 500));
    }
  } catch (error) {
    pendingRemovals.delete(row.hash);
    renderTorrents(); notify(error);
  }
}
let removalRow;
const removalDialog = document.querySelector('#remove-torrent');
function chooseRemoval(row) {
  removalRow = row;
  document.querySelector('#remove-name').textContent = row.name;
  document.querySelector('#remove-form').reset();
  removalDialog.showModal();
}
document.querySelector('#cancel-remove').onclick = () => removalDialog.close();
document.querySelector('#remove-form').onsubmit = async event => {
  event.preventDefault();
  const button = document.querySelector('#confirm-remove');
  if (button.disabled || !removalRow) return;
  button.disabled = true;
  try {
    const row = removalRow;
    if (await torrentAction(row, 'remove', document.querySelector('#remove-files').checked)) {
      removalDialog.close(); removalRow = null;
      showNotification('Fjernelse anmodet. Afventer bekræftelse fra torrentklienten.', 'info');
      void confirmRemoval(row);
    }
  } catch (error) { notify(error); }
  finally { button.disabled = false; }
};
function renderTorrents() {
  const search = document.querySelector('#search').value.toLocaleLowerCase(); const filter = document.querySelector('#filter').value; const sort = document.querySelector('#sort').value;
  const visible = torrents.filter(row => !pendingRemovals.has(row.hash));
  const rows = visible.filter(row => String(row.name).toLocaleLowerCase().includes(search) && (filter === 'all' || state(row) === filter));
  const source = document.querySelector('#source-filter').value; const filtered = rows.filter(row => source === 'all' || String(row.category || '').startsWith('MediaSeed-RSS-') === (source === 'rss')); rows.splice(0, rows.length, ...filtered);
  rows.sort((first, second) => sort === 'name' ? String(first.name).localeCompare(String(second.name)) : Number(second[sort]) - Number(first[sort]));
  const pending = [...pendingRemovals.values()].filter(row => !row.confirmed).length;
  document.querySelector('#count').textContent = `${rows.length} af ${visible.length} torrents${pending ? ' · ' + pending + ' fjernes' : ''}`;
  const target = document.querySelector('#torrents'); target.replaceChildren(); if (!rows.length) target.append(element('p', torrents.length ? 'Ingen matchende torrents' : 'Ingen torrents', 'muted'));
  for (const row of rows) {
    const item = element('article', undefined, 'torrent'); const progress = element('progress'); progress.max = 1; progress.value = Math.max(0, Math.min(1, Number(row.progress || 0))); progress.setAttribute('aria-label', 'Fremdrift');
    if (typeof row.cover === 'string' && /^\/api\/covers\/(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(row.cover)) { const cover = element('img', undefined, 'torrent-cover'); cover.src = row.cover; cover.alt = ''; cover.width = 64; cover.height = 96; cover.loading = 'lazy'; cover.onerror = () => cover.remove(); item.append(cover); }
    const status = element('p', `${Math.round(Number(row.progress || 0) * 100)} % · ${row.state || ''} · ${bytes(row.size)} · Ratio ${Number(row.ratio || 0).toFixed(2)}`);
    const speeds = element('p', `Download ${bytes(row.dlspeed)}/s · Upload ${bytes(row.upspeed)}/s · Seeding ${(Number(row.seeding_time || 0) / 3600).toFixed(1)} timer`);
    const actions = element('div', undefined, 'actions'); actions.append(command('Detaljer', () => showDetails(row))); if (appRole !== 'viewer') { const start = command('Start', () => torrentAction(row, 'start')); start.disabled = !downloadsEnabled; actions.append(start, command('Stop', () => torrentAction(row, 'stop')), command('Fjern', () => chooseRemoval(row))); }
    item.append(element('h2', row.name), progress, status, speeds, actions); target.append(item);
  }
}
async function refreshTorrents() {
  const request = ++torrentListRequest;
  try {
    const rows = await api('torrents');
    if (request !== torrentListRequest) return;
    torrents = rows;
    for (const [hash, removal] of pendingRemovals) if (removal.confirmed && !torrents.some(row => row.hash === hash)) pendingRemovals.delete(hash);
    renderTorrents();
  } catch (error) {
    if (request !== torrentListRequest) return;
    torrents = []; renderTorrents(); document.querySelector('#count').textContent = 'Torrentklient utilgængelig';
  }
}
async function refresh() {
  if (refreshing) return; refreshing = true;
  try { const runtime = await api('runtime'); downloadsEnabled = runtime.downloadsEnabled === true; renderConnection(runtime); } catch { downloadsEnabled = false; renderConnection({unavailable: true}); }
  updateAddStart();
  await refreshTorrents();
  try { const client = await api('client'); const transfer = await api('transfer'); renderTraffic(transfer); document.querySelector('#transfer').textContent = `${client.name} ${client.version} · Download ${bytes(transfer.dl_info_speed)}/s · Upload ${bytes(transfer.up_info_speed)}/s · ${downloadsEnabled ? 'VPN verificeret' : 'Downloads blokeret indtil VPN er verificeret'}`; } catch { renderTraffic(); document.querySelector('#transfer').textContent = 'qBittorrent ikke tilsluttet · downloads blokeret'; }
  try { const queue = await api('queue'); document.querySelector('#queue-status').textContent = `Kø: ${queue.active}/${queue.limit} aktive · ${queue.waiting} ventende · RSS ${queue.rssActive} · Manuelle ${queue.manualActive}${queue.boundedView ? ' · Begrænset til 500 torrents' : ''}`; } catch { document.querySelector('#queue-status').textContent = 'Køstatus utilgængelig'; }
  const storage = document.querySelector('#storage'); storage.hidden = false;
  try { const capacity = await api('storage'); storage.textContent = `Lokal storage: ${bytes(capacity.freeBytes)} ledig af ${bytes(capacity.totalBytes)}${capacity.lowSpace ? ' · Lav diskplads' : ''}${capacity.writable ? '' : ' · Ikke skrivbar'}`; } catch { storage.textContent = 'Lokal storage utilgængelig'; }
  refreshing = false;
}

function countryFlag(country) {
  if (!country) return null;
  let code = /^[A-Z]{2}$/.test(country) ? country : null;
  try {
    const english = new Intl.DisplayNames(['en'], {type: 'region'});
    for (let first = 65; !code && first <= 90; first++) for (let second = 65; second <= 90; second++) {
      const candidate = String.fromCharCode(first, second);
      if (english.of(candidate) === country) { code = candidate; break; }
    }
  } catch {}
  if (!code) return null;
  const flag = element('img', undefined, 'country-flag');
  flag.src = `/static/flags/${code.toLowerCase()}.svg`;
  flag.alt = ''; flag.width = 20; flag.height = 15;
  flag.onerror = () => { flag.hidden = true; };
  return flag;
}
function countryLabel(country) {
  if (!country) return 'Land ukendt';
  try {
    const english = new Intl.DisplayNames(['en'], {type: 'region'});
    const danish = new Intl.DisplayNames(['da'], {type: 'region'});
    if (/^[A-Z]{2}$/.test(country)) return danish.of(country);
    for (let first = 65; first <= 90; first++) for (let second = 65; second <= 90; second++) {
      const code = String.fromCharCode(first, second);
      if (english.of(code) === country) return danish.of(code);
    }
  } catch {}
  return country;
}
function renderConnection(state) {
  const checked = Number(state.checkedAt);
  const fresh = Number.isFinite(checked) && Math.abs(Date.now() - checked * 1000) < 90000;
  const active = state.provider === 'mediavpn' && state.ready === true && state.downloadsEnabled === true && fresh;
  const unavailable = state.unavailable || (state.ready && !fresh);
  const status = active ? 'active' : unavailable ? 'unknown' : state.provider || state.disconnectPending ? 'blocked' : 'disconnected';
  document.querySelector('#connection-flow').dataset.state = status;
  document.querySelector('#connection-status').textContent = active ? 'Forbundet · VPN verificeret' : unavailable ? 'Status kan ikke verificeres' : state.disconnectPending ? 'Frakobling afventer' : state.provider ? 'Forbindelsen er blokeret' : 'Ingen VPN valgt';
  document.querySelector('#connection-country').textContent = active ? countryLabel(state.country) : 'Afventer forbindelse';
  const flag = active ? countryFlag(state.country) : null;
  if (flag) document.querySelector('#connection-country').append(flag);
  document.querySelector('#connection-ip').textContent = active ? state.publicIp || 'IP ukendt' : 'Downloads blokeret';
  document.querySelector('#connection-port').textContent = active ? `VPN-port ${state.forwardedPort || 'ukendt'}` : state.provider ? 'Kontrollér forbindelsen under VPN' : 'Vælg VPN under VPN-fanen';
  document.querySelector('#connection-checked').textContent = fresh ? `Kontrolleret ${new Date(checked * 1000).toLocaleTimeString('da-DK')}` : 'Afventer statuskontrol';
  vpnFreshUntil = active ? Date.now() + 30000 : 0;
  if (!active) renderTraffic();
}
function renderTraffic(transfer) {
  const flow = document.querySelector('#connection-flow');
  const active = flow.dataset.state === 'active';
  const down = Number(transfer?.dl_info_speed);
  const up = Number(transfer?.up_info_speed);
  const measured = active && typeof transfer?.dl_info_speed === 'number' && typeof transfer?.up_info_speed === 'number' && Number.isFinite(down) && Number.isFinite(up) && down >= 0 && up >= 0;
  flow.dataset.upload = String(measured && up > 0);
  flow.dataset.download = String(measured && down > 0);
  document.querySelector('#connection-download').textContent = measured ? `↓ Download · ${bytes(down)}/s` : '↓ Download · afventer trafikmåling';
  document.querySelector('#connection-upload').textContent = measured ? `↑ Upload · ${bytes(up)}/s` : '↑ Upload · afventer trafikmåling';
  document.querySelector('#connection-activity').textContent = !active ? 'Trafik kræver en verificeret VPN-forbindelse.' : !measured ? 'Afventer en frisk trafikmåling.' : down > 0 && up > 0 ? 'Henter og sender · begge retninger er aktive.' : down > 0 ? 'Henter · Internet → MediaVPN → MediaSeed' : up > 0 ? 'Seeder · MediaSeed → MediaVPN → Internet' : 'VPN klar · ingen aktiv overførsel.';
  trafficFreshUntil = measured ? Date.now() + 30000 : 0;
}
function expireConnection() {
  if (vpnFreshUntil && Date.now() >= vpnFreshUntil) { downloadsEnabled = false; renderConnection({unavailable: true}); }
  if (trafficFreshUntil && Date.now() >= trafficFreshUntil) renderTraffic();
}
// Decorative bits follow measured activity; they never represent torrent content.
function randomizeStream(packet) {
  const count = 8 + Math.floor(Math.random() * 5);
  packet.textContent = Array.from({length: count}, () => Math.random() < .5 ? '0' : '1').join('');
}
function initializeStreams() {
  for (const lane of document.querySelectorAll('.binary-lane')) {
    lane.replaceChildren();
    const flight = 2.35 + Math.random() * .3;
    const phase = Math.random() * flight;
    for (let index = 0; index < 2; index++) {
      const packet = element('b');
      randomizeStream(packet);
      packet.style.setProperty('--flight', `${flight.toFixed(2)}s`);
      packet.style.setProperty('--phase', `${(-index * flight / 2 - phase).toFixed(2)}s`);
      packet.addEventListener('animationiteration', () => randomizeStream(packet));
      lane.append(packet);
    }
  }
}
initializeStreams();
setInterval(expireConnection, 1000);
document.addEventListener('visibilitychange', () => { expireConnection(); if (!document.hidden && document.body.classList.contains('signed-in')) void refresh(); });

function updateVpnChoice() {
  const selected = document.querySelector('#vpn-provider').value === 'mediavpn';
  const profile = document.querySelector('#vpn-profile');
  document.querySelector('#vpn-profile-field').hidden = !selected;
  profile.disabled = !selected;
  profile.required = selected;
  document.querySelector('#vpn-connect').disabled = selected && !profile.value;
}
async function refreshVpn() {
  const state = await api('vpn');
  renderConnection(state);
  const provider = document.querySelector('#vpn-provider');
  if (!vpnDraft) provider.value = state.provider || '';
  provider.querySelector('option[value="mediavpn"]').disabled = !state.integrationAvailable;
  const select = document.querySelector('#vpn-profile');
  const previous = select.value;
  select.replaceChildren(element('option', 'V\u00e6lg profil')); select.firstChild.value = '';
  for (const profile of state.profiles || []) { const option = element('option', `${profile.name} \u00b7 ${profile.forwardedPort ? 'port ' + profile.forwardedPort : 'port ikke verificeret'}`); option.value = profile.id; select.append(option); }
  select.value = vpnDraft ? previous : state.profileId || '';
  document.querySelector('#vpn-status').textContent = state.ready ? 'Verificeret' : state.provider ? 'Ikke verificeret' : 'Ikke tilkoblet';
  document.querySelector('#vpn-downloads').textContent = state.downloadsEnabled ? 'Tilladt gennem VPN' : 'Blokeret';
  document.querySelector('#vpn-ip').textContent = state.publicIp || 'Ikke verificeret'; document.querySelector('#vpn-port').textContent = state.forwardedPort || 'Ikke verificeret'; document.querySelector('#vpn-reason').textContent = state.reason || '';
  document.querySelector('#vpn-disconnect').disabled = !state.provider && !state.disconnectPending;
  updateVpnChoice();
}
document.querySelector('#vpn-provider').onchange = () => { vpnDraft = true; updateVpnChoice(); };
document.querySelector('#vpn-profile').onchange = () => { vpnDraft = true; updateVpnChoice(); };
document.querySelector('#vpn-form').onsubmit = async event => {
  event.preventDefault(); const button = document.querySelector('#vpn-connect'); button.disabled = true;
  const provider = document.querySelector('#vpn-provider').value || null;
  try {
    await api('vpn', {enabled: provider === 'mediavpn', provider, profileId: document.querySelector('#vpn-profile').value || null}, 'PUT');
    vpnDraft = false;
    await refreshVpn(); await refresh();
    showNotification(provider ? 'MediaVPN valgt. Torrentklienten starter, n\u00e5r profilen er verificeret.' : 'MediaVPN frakoblet. Downloads er blokeret. Data bevares.', 'success');
  } catch (error) { notify(error); } finally { updateVpnChoice(); }
};
document.querySelector('#vpn-disconnect').onclick = async () => {
  const button = document.querySelector('#vpn-disconnect'); button.disabled = true;
  try { await api('vpn', {enabled: false, provider: null}, 'PUT'); vpnDraft = false; await refreshVpn(); await refresh(); showNotification('MediaVPN frakoblet. Downloads er blokeret. Data bevares.', 'success'); }
  catch (error) { notify(error); button.disabled = false; }
};
document.querySelector('#vpn-refresh').onclick = () => refreshVpn().catch(error => { renderConnection({unavailable: true}); notify(error); });
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
function updateAddStart() {
  if (!addingTorrent) document.querySelector('#add-start').disabled = !downloadsEnabled;
  document.querySelector('#add-start-help').hidden = downloadsEnabled;
}
for (const radio of addForm.querySelectorAll('[name="mode"]')) radio.onchange = setAddMode;
function openAdd() {
  if (addDialog.open) return;
  showNotification(''); addForm.reset(); droppedTorrent = null;
  document.querySelector('#file-name').textContent = ''; setAddMode();
  updateAddStart();
  addDialog.append(document.querySelector('.notifications')); addDialog.showModal();
}
document.querySelector('#new-torrent').onclick = openAdd;
addDialog.addEventListener('close', () => { document.body.append(document.querySelector('.notifications')); });
function closeAdd() { if (!addingTorrent) addDialog.close(); }
document.querySelector('#close-add').onclick = closeAdd; document.querySelector('#cancel-add').onclick = closeAdd;
addDialog.addEventListener('cancel', event => { if (addingTorrent) event.preventDefault(); });
fileInput.onchange = () => { droppedTorrent = null; document.querySelector('#file-name').textContent = fileInput.files[0]?.name || ''; setAddMode(); };
fileField.ondragover = event => { event.preventDefault(); if (!addingTorrent) fileField.classList.add('dragging'); };
fileField.ondragleave = () => fileField.classList.remove('dragging');
function canDropTorrent() { return document.body.classList.contains('signed-in') && appRole !== 'viewer' && !addingTorrent; }
function isFileDrag(event) { return Array.from(event.dataTransfer?.types || []).includes('Files') || event.dataTransfer?.files?.length > 0; }
let fileDragDepth = 0;
function clearFileDrag() {
  fileDragDepth = 0; document.body.classList.remove('torrent-dragging'); fileField.classList.remove('dragging');
}
document.addEventListener('dragenter', event => {
  if (!isFileDrag(event)) return;
  event.preventDefault(); fileDragDepth++;
  if (canDropTorrent()) document.body.classList.add('torrent-dragging');
});
document.addEventListener('dragover', event => {
  if (!isFileDrag(event)) return;
  event.preventDefault(); event.dataTransfer.dropEffect = canDropTorrent() ? 'copy' : 'none';
});
document.addEventListener('dragleave', event => {
  if (!isFileDrag(event)) return;
  if (--fileDragDepth <= 0) clearFileDrag();
});
document.addEventListener('dragend', clearFileDrag);
document.addEventListener('visibilitychange', () => { if (document.hidden) clearFileDrag(); });
document.addEventListener('drop', event => {
  if (!isFileDrag(event)) return;
  event.preventDefault(); clearFileDrag();
  if (!canDropTorrent()) return;
  const files = event.dataTransfer.files;
  if (files.length !== 1) { showNotification('Vælg én torrentfil.'); return; }
  const file = files[0];
  if (!file.name.toLowerCase().endsWith('.torrent')) { showNotification('Vælg en .torrent-fil.'); return; }
  if (file.size > 4 * 1024 * 1024) { showNotification('Torrentfilen må højst være 4 MiB.'); return; }
  openAdd();
  addForm.querySelector('[name="mode"][value="file"]').checked = true;
  droppedTorrent = file; fileInput.value = '';
  document.querySelector('#file-name').textContent = file.name; setAddMode();
});
addForm.onsubmit = async event => {
  event.preventDefault(); if (addingTorrent) return;
  const fileMode = addForm.elements.namedItem('mode').value === 'file'; const ratio = Number(addForm.elements.namedItem('ratio').value);
  const start = event.submitter?.value === 'start';
  const file = droppedTorrent || fileInput.files[0];
  if (fileMode && (!file || !file.name.toLowerCase().endsWith('.torrent'))) { showNotification('Vælg en .torrent-fil.'); return; }
  if (fileMode && file.size > 4 * 1024 * 1024) { showNotification('Torrentfilen må højst være 4 MiB.'); return; }
  addingTorrent = true; const controls = [...addForm.elements, document.querySelector('#close-add')]; const disabledStates = controls.map(control => control.disabled); controls.forEach(control => { control.disabled = true; });
  try {
    let result;
    if (fileMode) {
      const response = await fetch(`/api/torrents/upload?filename=${encodeURIComponent(file.name)}&ratio=${ratio}&start=${start}`, {method: 'POST', headers: {'Content-Type': 'application/x-bittorrent', 'X-MediaSeed-CSRF': csrf}, body: file});
      result = await response.json(); if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Torrentfilen kunne ikke tilføjes.');
    } else { result = await api('torrents', {url: addForm.elements.namedItem('url').value.trim(), ratio, start}); }
    addDialog.close(); showNotification((result.startRequested ? (result.pending ? 'Magnetlink modtaget. Start er valgt; afventer metadata fra torrentnetværket.' : 'Torrent tilføjet. Start er anmodet gennem VPN.') : (result.pending ? 'Magnetlink modtaget på pause. Metadata afventer VPN.' : 'Torrent tilføjet på pause. Tryk Start, når du vil hente.')) + (result.webSourcesIgnored ? ' Ekstra webkilder i linket er udeladt; hash og trackere bruges.' : ''), 'success');
    await refreshTorrents();
  } catch (error) { notify(error); }
  finally { addingTorrent = false; controls.forEach((control, index) => { control.disabled = disabledStates[index]; }); updateAddStart(); }
};
document.querySelector('#logout').onclick = async () => { try { await api('auth/logout', {}); location.reload(); } catch (error) { notify(error); } };
setInterval(() => { if (!document.hidden && document.body.classList.contains('signed-in')) { if (!document.querySelector('#vpn').hidden) refreshVpn().catch(() => {}); if (!document.querySelector('#feeds').hidden) loadSources('feeds').catch(() => {}); refresh(); } }, 10000);
async function initializeLogin() {
  try {
    const status = await api('auth/status'); needsSetup = status.needsSetup;
    if (status.managed) {
      document.querySelector('#login').hidden = true;
      try { const identity = await api('auth/me'); csrf = identity.csrf; appRole = identity.role || 'administrator'; showWorkspace(); }
      catch (error) { if (error.status === 401) location.replace(status.loginUrl); else notify(error); }
      return;
    }
    document.querySelector('#identity-title').textContent = needsSetup ? 'En frisk begyndelse.' : 'Velkommen tilbage.';
    document.querySelector('#identity-subtitle').textContent = needsSetup ? 'Opret din lokale MediaSeed-administrator.' : 'Log ind p\u00e5 MediaSeed.';
    document.querySelector('#login-submit').textContent = needsSetup ? 'Opret administrator' : 'Log ind';
    if (!needsSetup) { try { const identity = await api('auth/me'); csrf = identity.csrf; appRole = identity.role || 'administrator'; showWorkspace(); } catch {} }
  } catch (error) { notify(error); }
}
void initializeLogin();
