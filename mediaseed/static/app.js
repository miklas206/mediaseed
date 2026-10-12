let csrf = '';
let needsSetup = false;
let torrents = [];
let selectedHash = '';
let downloadsEnabled = false;
let refreshing = false;
let torrentListRequest = 0;
let clientLabel = 'qBittorrent';
let torrentSnapshotValid = false;
let clientRefreshAt = 0;
let storageRefreshAt = 0;
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
errorLabels['Starting requires a verified VPN. Choose add on pause instead.'] = 'Start kræver en verificeret VPN. Kontrollér forbindelsen under VPN.';
function element(tag, text, className) { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (className) node.className = className; return node; }
function bytes(value) { const number = Number(value || 0); for (const [divisor, unit] of [[1073741824, 'GiB'], [1048576, 'MiB'], [1024, 'KiB']]) if (number >= divisor) return `${(number / divisor).toFixed(1)} ${unit}`; return `${number} B`; }
function state(row) { if (/error|missing/i.test(row.state)) return 'error'; if (/stopped|paused/i.test(row.state)) return 'stopped'; if (Number(row.progress) >= 1) return 'seeding'; return 'downloading'; }
function command(label, handler) { const button = element('button', label); button.type = 'button'; button.onclick = async () => { button.disabled = true; try { await handler(); } catch (error) { notify(error); } finally { button.disabled = button.dataset.added === 'true'; } }; return button; }
async function torrentAction(row, action, deleteFiles = false) {
  try { await api('torrents/' + action, {torrent_hash: row.hash, delete_files: deleteFiles}); }
  catch (error) { if (error.status !== 409 || action === 'start') throw error; if (!await confirmEarlyAction(row, action, deleteFiles)) return; await api('torrents/' + action, {torrent_hash: row.hash, delete_files: deleteFiles, confirm_early: true}); }
  if (action === 'remove') {
    pendingRemovals.set(row.hash, {confirmed: false});
    renderTorrents();
  } else await refreshTorrents();
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
const earlyDialog = document.querySelector('#early-seeding');
let earlyConfirmation;
function confirmEarlyAction(row, action, deleteFiles) {
  if (earlyConfirmation) return Promise.resolve(false);
  document.querySelector('#early-title').textContent = action === 'remove' ? 'Fjern før seedingmålet er nået?' : 'Stop før seedingmålet er nået?';
  document.querySelector('#early-name').textContent = row.name;
  document.querySelector('#early-consequence').textContent = action === 'remove' ? (deleteFiles ? 'Torrenten og de downloadede filer slettes. Det kan ikke fortrydes.' : 'Torrenten fjernes. De downloadede filer bliver bevaret.') : 'Torrenten stoppes. Torrenten og de downloadede filer bliver bevaret.';
  document.querySelector('#confirm-early').textContent = action === 'remove' ? 'Fjern alligevel' : 'Stop alligevel';
  document.querySelector('#cancel-early').textContent = action === 'remove' ? 'Behold torrent' : 'Fortsæt seeding';
  return new Promise(resolve => { earlyConfirmation = resolve; earlyDialog.showModal(); document.querySelector('#cancel-early').focus(); });
}
function finishEarlyAction(accepted) {
  const resolve = earlyConfirmation;
  earlyConfirmation = undefined;
  earlyDialog.close();
  if (resolve) resolve(accepted);
}
document.querySelector('#confirm-early').onclick = () => finishEarlyAction(true);
document.querySelector('#cancel-early').onclick = () => finishEarlyAction(false);
earlyDialog.addEventListener('cancel', event => { event.preventDefault(); finishEarlyAction(false); });
earlyDialog.addEventListener('close', () => { if (earlyConfirmation) finishEarlyAction(false); });
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
function torrentTraffic() {
  if (!torrents.every(row => ['dlspeed', 'upspeed'].every(key => typeof row[key] === 'number' && Number.isFinite(row[key]) && row[key] >= 0))) return;
  return torrents.reduce((total, row) => ({dl_info_speed: total.dl_info_speed + row.dlspeed, up_info_speed: total.up_info_speed + row.upspeed}), {dl_info_speed: 0, up_info_speed: 0});
}
function renderTransferSummary() {
  const traffic = torrentTraffic();
  document.querySelector('#transfer').textContent = traffic ? `${clientLabel} · Download ${bytes(traffic.dl_info_speed)}/s · Upload ${bytes(traffic.up_info_speed)}/s · ${downloadsEnabled ? 'VPN verificeret' : 'Downloads blokeret indtil VPN er verificeret'}${torrents.length === 500 ? ' · Trafik for de 500 viste torrents' : ''}` : `${clientLabel} · afventer trafikmåling`;
}
function applyTorrentSnapshot(rows, request) {
  if (request !== torrentListRequest) return;
  torrentSnapshotValid = Array.isArray(rows);
  if (torrentSnapshotValid) {
    torrents = rows;
    for (const [hash, removal] of pendingRemovals) if (removal.confirmed && !torrents.some(row => row.hash === hash)) pendingRemovals.delete(hash);
    renderTorrents();
    renderTraffic(torrentTraffic()); renderTransferSummary();
  } else {
    torrents = []; renderTorrents(); document.querySelector('#count').textContent = 'Torrentklient utilgængelig';
    renderTraffic(); document.querySelector('#transfer').textContent = 'Torrentklient utilgængelig · afventer trafikmåling';
  }
}
async function refreshTorrents() {
  const request = ++torrentListRequest;
  try { applyTorrentSnapshot(await api('torrents'), request); }
  catch { applyTorrentSnapshot(null, request); }
}
async function refreshAuxiliary() {
  const tasks = [];
  if (Date.now() >= clientRefreshAt) {
    clientRefreshAt = Date.now() + 300000;
    tasks.push((async () => {
      try { const client = await api('client'); clientLabel = `${client.name} ${client.version}`; }
      catch { clientLabel = 'qBittorrent'; clientRefreshAt = Date.now() + 30000; }
      if (torrentSnapshotValid) renderTransferSummary();
    })());
  }
  if (Date.now() >= storageRefreshAt) {
    storageRefreshAt = Date.now() + 60000;
    tasks.push((async () => {
      const storage = document.querySelector('#storage'); storage.hidden = false;
      try { const capacity = await api('storage'); storage.textContent = `Lokal storage: ${bytes(capacity.freeBytes)} ledig af ${bytes(capacity.totalBytes)}${capacity.lowSpace ? ' · Lav diskplads' : ''}${capacity.writable ? '' : ' · Ikke skrivbar'}`; }
      catch { storage.textContent = 'Lokal storage utilgængelig'; storageRefreshAt = Date.now() + 30000; }
    })());
  }
  await Promise.allSettled(tasks);
}
async function refresh() {
  if (refreshing) return; refreshing = true;
  const request = ++torrentListRequest;
  try {
    const snapshot = await api('dashboard');
    downloadsEnabled = snapshot.runtime.downloadsEnabled === true; renderConnection(snapshot.runtime);
    applyTorrentSnapshot(snapshot.torrents, request);
    const queue = snapshot.queue;
    if (request === torrentListRequest) document.querySelector('#queue-status').textContent = queue ? `Kø: ${queue.active}/${queue.limit} aktive · ${queue.waiting} ventende · RSS ${queue.rssActive} · Manuelle ${queue.manualActive}${queue.boundedView ? ' · Begrænset til 500 torrents' : ''}` : 'Køstatus utilgængelig';
  } catch {
    downloadsEnabled = false; renderConnection({unavailable: true}); applyTorrentSnapshot(null, request);
    document.querySelector('#queue-status').textContent = 'Køstatus utilgængelig';
  } finally { updateAddStart(); refreshing = false; void refreshAuxiliary(); }
}

function countryCode(country) {
  if (typeof country !== 'string' || !country.trim()) return null;
  const name = country.trim();
  if (/^[A-Za-z]{2}$/.test(name)) {
    try { return new Intl.Locale('und-' + name.toUpperCase()).region; } catch { return name.toUpperCase(); }
  }
  const aliases = {
    'bosnia and herzegovina': 'BA', 'brunei darussalam': 'BN', "cote d'ivoire": 'CI',
    'czech republic': 'CZ', 'democratic republic of the congo': 'CD', 'hong kong': 'HK',
    'korea': 'KR', "lao people's democratic republic": 'LA', 'macao': 'MO',
    'palestine, state of': 'PS', 'russian federation': 'RU', 'syrian arab republic': 'SY', 'turkey': 'TR',
  };
  const normalized = name.toLocaleLowerCase('en');
  if (aliases[normalized]) return aliases[normalized];
  try {
    if (!countryCode.names) {
      const names = new Map();
      const english = new Intl.DisplayNames(['en'], {type: 'region'});
      const danish = new Intl.DisplayNames(['da'], {type: 'region'});
      for (let first = 65; first <= 90; first++) for (let second = 65; second <= 90; second++) {
        const code = String.fromCharCode(first, second);
        // Historical aliases (DD, CS, VD, YD, RH) can share modern names.
        // Only map canonical codes to the actual bundled flag filenames.
        if (new Intl.Locale('und-' + code).region !== code) continue;
        for (const display of [english.of(code), danish.of(code)]) {
          if (display !== code) names.set(display.toLocaleLowerCase('en'), code);
        }
      }
      countryCode.names = names;
    }
    return countryCode.names.get(normalized) || null;
  } catch { return null; }
}
function countryFlag(country) {
  const code = countryCode(country);
  if (!code) return null;
  const flag = element('img', undefined, 'country-flag');
  flag.src = `/static/flags/${code.toLowerCase()}.svg`;
  flag.alt = ''; flag.width = 20; flag.height = 15;
  flag.onerror = () => { flag.hidden = true; };
  return flag;
}
function countryLabel(country) {
  if (!country) return 'Land ukendt';
  const code = countryCode(country);
  try { if (code) return new Intl.DisplayNames(['da'], {type: 'region'}).of(code); } catch {}
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
function randomBits(count) { return Array.from({length: count}, () => Math.random() < .5 ? '0' : '1').join(''); }
function advanceStream(stream, download) {
  // Replace only the 12 characters that moved outside the clipped lane.
  // The remaining text stays aligned when the CSS animation loops.
  stream.textContent = download ? stream.textContent.slice(12) + randomBits(12) : randomBits(12) + stream.textContent.slice(0, -12);
}
function initializeStreams() {
  for (const lane of document.querySelectorAll('.binary-lane')) {
    lane.replaceChildren();
    const stream = element('b', randomBits(96));
    stream.addEventListener('animationiteration', () => advanceStream(stream, lane.classList.contains('binary-download')));
    lane.append(stream);
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
  try { const details = await api(`torrents/${row.hash}/details`); target.replaceChildren(element('p', `Downloadet ${bytes(details.properties.total_downloaded)} · Uploadet ${bytes(details.properties.total_uploaded)} · Seeding ${(Number(details.properties.seeding_time || 0) / 3600).toFixed(1)} timer`)); target.append(detailTable('Filer', [['name', 'Navn'], ['size', 'Bytes'], ['progress', 'Fremdrift']], details.files), detailTable('Trackere', [['host', 'Tracker'], ['status', 'Status'], ['num_peers', 'Peers']], details.trackers), detailTable('Peers', [['ip', 'IP'], ['client', 'Klient'], ['progress', 'Fremdrift']], details.peers)); renderCleanupDetails(details.cleanup, row, target); document.querySelector('#policy-form').hidden = appRole === 'viewer' || (details.cleanup?.mode && details.cleanup.mode !== 'never'); try { const benefits = await api(`torrents/${row.hash}/benefits`); if (benefits.status === 'matched') { const flags = [benefits.freeleech === null ? null : `Freeleech ${benefits.freeleech} %`, benefits.doubleUpload ? 'Dobbelt upload' : null, benefits.featured ? 'Featured' : null, benefits.internal ? 'Internal' : null, benefits.refundable ? 'Refunderbar' : null].filter(Boolean); target.append(element('h3', 'Trackerfordele'), element('p', `${benefits.tracker} · Præcist hash-match · ${flags.join(' · ') || 'Ingen bekræftede fordele'}`, 'muted')); } } catch {} } catch (error) { target.replaceChildren(element('p', error.message)); }
}
function renderCleanupDetails(policy = {mode: 'never'}, row, target) {
  target.append(element('h3', 'Automatisk oprydning'));
  if (policy.mode === 'never') { target.append(element('p', 'Slået fra · torrent og filer bevares.', 'muted')); return; }
  const goals = {time: `${policy.hours} timers aktiv seeding`, ratio: `uploadratio ${policy.ratio}`, both: `${policy.hours} timers aktiv seeding OG ratio ${policy.ratio}`, either: `${policy.hours} timers aktiv seeding ELLER ratio ${policy.ratio}`};
  const state = {waiting: 'Afventer klargøring; torrenten holdes på pause', preparing: 'Klargøring blev afbrudt; kontrollér torrenten eller slå reglen fra', active: 'Overvåges · venter på målet og på tilsluttede downloadere', identity_mismatch: 'Oprydning blokeret: torrentens identitet kunne ikke bekræftes'};
  target.append(element('p', `${policy.delete_files ? 'Fjern torrent og filer' : 'Fjern kun torrent · behold filer'} efter ${goals[policy.mode]}.`), element('p', policy.observationFailed ? 'Statuskontrol fejlede · oprydning afventer en ny, gyldig måling.' : state[policy.status] || 'Afventer kontrol', 'muted'));
  if (appRole !== 'viewer') {
    const cancel = element('button', 'Slå automatisk oprydning fra');
    cancel.onclick = async () => { cancel.disabled = true; try { const result = await api(`torrents/${row.hash}/cleanup/cancel`, {}); showNotification(result.stopLimitsRestored === false ? 'Oprydning slået fra. Stopmålet kunne ikke gendannes; indstil det under Detaljer, når klienten er klar.' : 'Automatisk oprydning slået fra. Torrent og filer bevares.', 'success'); await showDetails(row); } catch (error) {notify(error); cancel.disabled = false;} };
    target.append(cancel);
  }
}
function trackerNumber(value) {
  return typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('da-DK', {maximumFractionDigits: 2}) : '—';
}
function trackerBytes(value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  let amount = Math.abs(value), index = 0;
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB'];
  while (amount >= 1024 && index < units.length - 1) { amount /= 1024; index++; }
  return `${value < 0 ? '−' : ''}${amount.toLocaleString('da-DK', {maximumFractionDigits: index ? 2 : 0})} ${units[index]}`;
}
function trackerCard(row) {
  const card = element('article', undefined, 'source-row tracker-card');
  const head = element('div', undefined, 'tracker-card-heading');
  const identity = element('div');
  identity.append(element('h3', row.name), element('p', `NordicBytes${row.username ? ` · ${row.username}` : ''}`, 'muted'));
  const available = row.status === 'ok' && row.stats && typeof row.stats === 'object';
  head.append(identity, element('span', available ? 'Forbundet' : 'Kontodata utilgængelige', `tracker-state${available ? ' ready' : ''}`));
  card.append(head);
  if (available) {
    const stats = element('dl', undefined, 'tracker-stats');
    for (const [label, field, format] of [
      ['Ratio', 'ratio', trackerNumber], ['Upload', 'uploaded', trackerBytes], ['Download', 'downloaded', trackerBytes],
      ['Buffer', 'buffer', trackerBytes], ['Seeder', 'seeding', trackerNumber], ['Downloader', 'leeching', trackerNumber],
      ['Shards / bonus', 'seedbonus', trackerNumber], ['Seeding-størrelse', 'seeding_size', trackerBytes],
      ['Udgivelser', 'total_uploads', trackerNumber], ['Hit-and-runs', 'hit_and_runs', trackerNumber], ['Advarsler', 'warnings', trackerNumber],
    ]) {
      const metric = element('div', undefined, 'tracker-metric');
      metric.append(element('dt', label), element('dd', format(row.stats[field])));
      if (['hit_and_runs', 'warnings'].includes(field) && Number.isFinite(row.stats[field]) && row.stats[field] > 0) metric.classList.add('attention');
      stats.append(metric);
    }
    card.append(stats);
    const checked = new Date(row.checkedAt * 1000);
    card.append(element('p', Number.isFinite(checked.getTime()) ? `Hentet ${checked.toLocaleString('da-DK', {dateStyle: 'short', timeStyle: 'short'})} · Kontrolleres automatisk hvert 5. minut` : 'Tidspunkt for kontotal er ukendt', 'tracker-updated muted'));
  } else {
    card.append(element('p', 'Kontotallene kunne ikke hentes. Kontrollér API-nøglens adgang under Trackere. MediaSeed prøver igen automatisk.', 'muted'));
  }
  return card;
}
function renderTrackerOverview(rows) {
  document.querySelector('#tracker-overview').hidden = !rows.length;
  document.querySelector('#tracker-account-list').replaceChildren(...rows.map(trackerCard));
}
let trackerOverviewLoading = false;
async function refreshTrackerOverview() {
  if (appRole === 'viewer' || trackerOverviewLoading) return;
  trackerOverviewLoading = true;
  try { renderTrackerOverview(await api('trackers')); }
  catch { document.querySelector('#tracker-account-list').replaceChildren(element('p', 'Trackerstatistik kunne ikke hentes. Prøver igen automatisk.', 'muted')); }
  finally { trackerOverviewLoading = false; }
}
async function loadSources(kind) {
  const rows = await api(kind); const target = document.querySelector(kind === 'feeds' ? '#feed-list' : '#tracker-list'); target.replaceChildren(); if (!rows.length) target.append(element('p', kind === 'feeds' ? 'Ingen feeds' : 'Ingen konti', 'muted'));
  if (kind === 'trackers') {
    renderTrackerOverview(rows);
    const select = document.querySelector('#tracker-account'); const previous = select.value; select.replaceChildren(element('option', 'Vælg konto')); select.firstChild.value = '';
    for (const row of rows) { const option = element('option', row.name); option.value = row.id; select.append(option); }
    select.value = rows.some(row => row.id === previous) ? previous : rows.length === 1 ? rows[0].id : '';
    document.querySelector('#tracker-search-submit').disabled = !rows.length;
  }
  for (const row of rows) {
    const item = kind === 'trackers' ? trackerCard(row) : element('article', undefined, 'source-row');
    if (kind === 'feeds') item.append(element('h2', row.name), element('p', `${row.folder || '/downloads'} · Ratio ${row.ratio} · ${row.paused ? 'Pauset' : 'Aktiv'} · ${row.status || ''}`, 'muted'));
    const actions = element('div', undefined, 'actions');
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
document.querySelector('#manage-trackers').onclick = () => document.querySelector('[data-view="trackers"]').click();
function showWorkspace() { void refreshTrackerOverview(); document.body.classList.add('signed-in'); document.querySelector('#identity').hidden = true; document.querySelector('#workspace').hidden = false; document.querySelector('#logout').hidden = false; document.querySelector('#new-torrent').hidden = appRole === 'viewer'; document.querySelector('#policy-form').hidden = appRole === 'viewer'; for (const selector of ['#feed-form', '#tracker-form', '#vpn-form']) document.querySelector(selector).hidden = appRole !== 'administrator'; for (const selector of ['[data-view="feeds"]', '[data-view="trackers"]']) document.querySelector(selector).hidden = appRole === 'viewer'; refresh(); }
document.querySelector('#login').onsubmit = async event => { event.preventDefault(); const body = Object.fromEntries(new FormData(event.target)); try { if (needsSetup) { await api('auth/setup', body); needsSetup = false; } csrf = (await api('auth/login', body)).csrf; event.target.reset(); showWorkspace(); } catch (error) { notify(error); } };
document.querySelector('#refresh').onclick = refresh;
const addDialog = document.querySelector('#add-torrent');
const addForm = document.querySelector('#add-torrent-form');
const fileInput = document.querySelector('#add-file');
const fileField = document.querySelector('#file-field');
let droppedTorrent = null;
let addingTorrent = false;
function addCleanupPolicy() {
  return {mode: document.querySelector('#add-cleanup-mode').value || 'never', hours: Number(document.querySelector('#add-cleanup-hours').value || 49), ratio: Number(document.querySelector('#add-cleanup-ratio').value || 1), delete_files: document.querySelector('#add-cleanup-action').value === 'files'};
}
function updateCleanupFields() {
  const {mode, delete_files: files} = addCleanupPolicy(), active = mode !== 'never';
  document.querySelector('#add-cleanup-options').hidden = !active;
  for (const [key, visible] of [['hours', ['time', 'both', 'either'].includes(mode)], ['ratio', ['ratio', 'both', 'either'].includes(mode)]]) {
    document.querySelector(`#add-cleanup-${key}-field`).hidden = !visible;
    document.querySelector(`#add-cleanup-${key}`).disabled = !visible;
  }
  document.querySelector('#add-cleanup-action').disabled = !active;
  document.querySelector('#add-cleanup-warning').hidden = !active || !files;
  document.querySelector('#add-seeding-help').textContent = active ? 'Den valgte oprydningsregel styrer seedingmålet. Vælg start nu eller tilføj på pause. Start kræver verificeret VPN.' : 'Vælg start nu eller tilføj på pause. Start kræver verificeret VPN. Stopmål: valgt ratio eller 49 timers seeding; torrent og filer bevares.';
  const stop = addForm.elements.namedItem('ratio'); stop.disabled = active;
  const label = stop.closest?.('label'); if (label) label.hidden = active;
}
document.querySelector('#add-cleanup-mode').onchange = updateCleanupFields;
document.querySelector('#add-cleanup-action').onchange = updateCleanupFields;
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
  updateCleanupFields();
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
  const cleanup = addCleanupPolicy();
  const cleanupQuery = cleanup.mode === 'never' ? '' : `&cleanup_mode=${cleanup.mode}&cleanup_hours=${cleanup.hours}&cleanup_ratio=${cleanup.ratio}&cleanup_delete_files=${cleanup.delete_files}`;
  const file = droppedTorrent || fileInput.files[0];
  if (fileMode && (!file || !file.name.toLowerCase().endsWith('.torrent'))) { showNotification('Vælg en .torrent-fil.'); return; }
  if (fileMode && file.size > 4 * 1024 * 1024) { showNotification('Torrentfilen må højst være 4 MiB.'); return; }
  addingTorrent = true; const controls = [...addForm.elements, document.querySelector('#close-add')]; const disabledStates = controls.map(control => control.disabled); controls.forEach(control => { control.disabled = true; });
  try {
    let result;
    if (fileMode) {
      const response = await fetch(`/api/torrents/upload?filename=${encodeURIComponent(file.name)}&ratio=${ratio}${cleanupQuery}&start=${start}`, {method: 'POST', headers: {'Content-Type': 'application/x-bittorrent', 'X-MediaSeed-CSRF': csrf}, body: file});
      result = await response.json(); if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Torrentfilen kunne ikke tilføjes.');
    } else { result = await api('torrents', {url: addForm.elements.namedItem('url').value.trim(), ratio, start, ...(cleanup.mode === 'never' ? {} : {cleanup})}); }
    addDialog.close(); showNotification((result.cleanupPending ? 'Torrent tilføjet på pause. Oprydningsreglen afventer klargøring; valgt start udføres derefter.' : result.startRequested ? (result.pending ? 'Magnetlink modtaget. Start er valgt; afventer metadata fra torrentnetværket.' : 'Torrent tilføjet. Start er anmodet gennem VPN.') : (result.pending ? 'Magnetlink modtaget på pause. Metadata afventer VPN.' : 'Torrent tilføjet på pause. Tryk Start, når du vil hente.')) + (result.webSourcesIgnored ? ' Ekstra webkilder i linket er udeladt; hash og trackere bruges.' : ''), 'success');
    await refreshTorrents();
  } catch (error) { notify(error); }
  finally { addingTorrent = false; controls.forEach((control, index) => { control.disabled = disabledStates[index]; }); updateAddStart(); }
};
document.querySelector('#logout').onclick = async () => { try { await api('auth/logout', {}); location.reload(); } catch (error) { notify(error); } };
setInterval(() => { if (!document.hidden && document.body.classList.contains('signed-in')) { if (!document.querySelector('#vpn').hidden) refreshVpn().catch(() => {}); if (!document.querySelector('#feeds').hidden) loadSources('feeds').catch(() => {}); refresh(); } }, 5000);
setInterval(() => { if (!document.hidden && document.body.classList.contains('signed-in')) { if (!document.querySelector('#trackers').hidden) loadSources('trackers').catch(() => {}); else if (!document.querySelector('#downloads').hidden) void refreshTrackerOverview(); } }, 60000);
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
