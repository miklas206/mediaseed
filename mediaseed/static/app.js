const {t, setLanguage, locale} = globalThis.MediaAppI18n;
let csrf = '';
let needsSetup = false;
let torrents = [];
let torrentPage = 1;
const listPreferenceKey = 'mediaseed.torrent-list.v1';
function saveListPreferences() {
  try { globalThis.localStorage?.setItem(listPreferenceKey, JSON.stringify({sort: document.querySelector('#sort').value, direction: document.querySelector('#sort-direction').value, pageSize: document.querySelector('#page-size').value})); } catch {}
}
try {
  const saved = JSON.parse(globalThis.localStorage?.getItem(listPreferenceKey) || '{}');
  if (['name','progress','ratio','size','added_on','completion_on','dlspeed','upspeed','eta','seeding_time','uploaded','num_seeds','state'].includes(saved.sort)) document.querySelector('#sort').value = saved.sort;
  if (['asc','desc'].includes(saved.direction)) document.querySelector('#sort-direction').value = saved.direction;
  if (['10','25','50','100'].includes(saved.pageSize)) document.querySelector('#page-size').value = saved.pageSize;
} catch {}
function sortTorrentRows(rows, key, direction) {
  const text = key === 'name' || key === 'state';
  const value = row => key === 'ratio' ? (row.credited_ratio ?? row.ratio) : row[key];
  return rows.sort((a, b) => {
    const first = value(a), second = value(b);
    const valid = v => text ? typeof v === 'string' : typeof v === 'number' && Number.isFinite(v) && v >= 0 && (key !== 'eta' || v < 8640000);
    if (valid(first) !== valid(second)) return valid(first) ? -1 : 1;
    const order = !valid(first) ? 0 : text ? first.localeCompare(second, locale(), {numeric: true, sensitivity: 'base'}) : first - second;
    return order ? (direction === 'desc' ? -order : order) : String(a.hash || '').localeCompare(String(b.hash || ''));
  });
}
let selectedHash = '';
let downloadsEnabled = false;
let refreshing = false;
let torrentListRequest = 0;
let clientLabel = 'qBittorrent';
let torrentSnapshotValid = false;
let transferHistory = [];
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
  message.textContent = t(text);
  if (text) notificationTimer = setTimeout(() => { message.textContent = ''; }, 6000);
}
message.tabIndex = 0;
message.addEventListener('mouseenter', () => clearTimeout(notificationTimer));
message.addEventListener('focus', () => clearTimeout(notificationTimer));
function resumeNotification() { if (message.textContent && !message.matches(':hover') && document.activeElement !== message) notificationTimer = setTimeout(() => { message.textContent = ''; }, 6000); }
message.addEventListener('mouseleave', resumeNotification);
message.addEventListener('blur', resumeNotification);
const errorLabels = {'Invalid credentials': t("Your username or password is incorrect."), 'Try again later': t("Too many login attempts. Try again shortly."), 'Torrent client is not configured': t("The torrent client is not connected yet."), 'Sign in required': t("Sign in to continue."), 'Tracker API key lacks access': t("The API key cannot access the tracker's torrents."), 'Tracker rate limit reached; try again later': t("The tracker limits requests. Try again shortly."), 'Tracker request failed': t("The tracker could not provide a response."), 'Tracker unavailable or invalid response': t("The tracker is unavailable or returned an unknown response."), 'Unsafe tracker download address': t("The tracker returned a download URL that is not approved."), 'Torrent client rejected the torrent file': t("qBittorrent rejected the torrent file.")};
async function api(path, body, method) {
  const response = await fetch('/api/' + path, {method: method || (body ? 'POST' : 'GET'), headers: {'Content-Type': 'application/json', 'X-MediaSeed-CSRF': csrf}, ...(body ? {body: JSON.stringify(body)} : {})});
  const result = await response.json();
  if (!response.ok) throw Object.assign(new Error(typeof result.detail === 'string' ? result.detail : t("Check the fields and try again.")), {status: response.status});
  if((method || (body ? 'POST' : 'GET'))!=='GET' && typeof seedDetailCache!=='undefined')seedDetailCache.clear();
  return result;
}
globalThis.MediaAppTheme.setOverrideHandler(themeOverride => api('auth/preferences', {themeOverride}, 'PUT'));
window.addEventListener('mediahub-theme-error', event => notify(new Error(event.detail)));
function notify(error) { showNotification(errorLabels[error.message] || error.message); }
Object.assign(errorLabels, {'Invalid torrent file': t("The file is not a valid torrent file."), 'Choose a .torrent file': t("Choose a .torrent file."), 'Torrent file exceeds 4 MiB': t("Torrent files must not exceed 4 MiB.")});
errorLabels['Starting requires a verified VPN. Choose add on pause instead.'] = t("Starting requires a verified VPN. Check the connection under VPN.");
function element(tag, text, className) { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (className) node.className = className; return node; }
function bytes(value) { const number = Number(value || 0); for (const [divisor, unit] of [[1073741824, 'GiB'], [1048576, 'MiB'], [1024, 'KiB']]) if (number >= divisor) return `${(number / divisor).toFixed(1)} ${unit}`; return `${number} B`; }
function torrentStatus(value) {
  const names = {downloading: 'Downloading', forcedDL: 'Downloading', metaDL: 'Fetching metadata', forcedMetaDL: 'Fetching metadata', stalledDL: 'Waiting for peers', queuedDL: 'Queued download', checkingDL: 'Checking files', checkingUP: 'Checking files', checkingResumeData: 'Checking files', moving: 'Moving files', uploading: 'Seeding', forcedUP: 'Seeding', stalledUP: 'Waiting to seed', queuedUP: 'Queued seeding', stoppedDL: 'Paused', stoppedUP: 'Paused', pausedDL: 'Paused', pausedUP: 'Paused', error: 'Error', missingFiles: 'Missing files', unknown: 'Unknown'};
  return t(names[value] || 'Unknown');
}
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
        showNotification(t("The torrent has been removed."), 'success');
        return;
      }
      if (Date.now() >= deadline) throw new Error(t("Removal is not confirmed yet. Refresh the list to check its status."));
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
  document.querySelector('#early-title').textContent = action === 'remove' ? t("Remove before the seeding goal is reached?") : t("Stop before the seeding goal is reached?");
  document.querySelector('#early-name').textContent = row.name;
  document.querySelector('#early-help').textContent = t("The torrent has not reached the selected {0}upload ratio of {1} or 49 hours of seeding yet. Continue anyway?", {0: 'credited_ratio' in row ? t("credited ") : '', 1: trackerNumber(Number(row.ratio_limit) >= 1 ? row.ratio_limit : 1)});
  document.querySelector('#early-consequence').textContent = action === 'remove' ? (deleteFiles ? t("The torrent and downloaded files will be deleted. This cannot be undone.") : t("The torrent will be removed. Downloaded files are preserved.")) : t("The torrent will be stopped. The torrent and downloaded files are preserved.");
  document.querySelector('#confirm-early').textContent = action === 'remove' ? t("Remove anyway") : t("Stop anyway");
  document.querySelector('#cancel-early').textContent = action === 'remove' ? t("Keep torrent") : t("Continue seeding");
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
let recheckRow;
const recheckDialog = document.querySelector('#recheck-torrent');
function chooseRecheck(row) {
  recheckRow = row;
  document.querySelector('#recheck-name').textContent = row.name;
  recheckDialog.showModal();
}
document.querySelector('#cancel-recheck').onclick = () => {recheckDialog.close(); recheckRow = null;};
document.querySelector('#confirm-recheck').onclick = async () => {
  const button = document.querySelector('#confirm-recheck');
  if (button.disabled || !recheckRow) return;
  button.disabled = true;
  try {
    await api('torrents/recheck', {torrent_hash: recheckRow.hash});
    recheckDialog.close(); recheckRow = null;
    showNotification(t('File check requested. Watch the torrent status; it remains paused after checking. Start it manually to fetch missing pieces.'), 'info');
    if (dialog.open) dialog.close();
    await refreshTorrents();
  } catch (error) { notify(error); }
  finally { button.disabled = false; }
};
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
      showNotification(t("Removal requested. Waiting for confirmation from the torrent client."), 'info');
      void confirmRemoval(row);
    }
  } catch (error) { notify(error); }
  finally { button.disabled = false; }
};
// Keep keyboard position and disclosures when a refreshed list replaces its rows.
function preserveListInteraction(target, key) {
  const controls = 'button, summary, a, input, select';
  const active = document.activeElement;
  const rows = [...target.children];
  const focusedRow = rows.find(row => row.contains(active));
  const focusedKey = focusedRow?.dataset[key];
  const focusIndex = focusedRow ? [...focusedRow.querySelectorAll(controls)].indexOf(active) : -1;
  const opened = new Set(rows.filter(row => row.querySelector('details[open]')).map(row => row.dataset[key]));
  return () => {
    for (const row of target.children) {
      if (opened.has(row.dataset[key])) { const details = row.querySelector('details'); if (details) details.open = true; }
      if (focusIndex >= 0 && row.dataset[key] === focusedKey) row.querySelectorAll(controls)[focusIndex]?.focus({preventScroll: true});
    }
  };
}

function renderTorrents() {
  const measured = torrentSnapshotValid ? torrentTraffic() : null;
  for (const [id, value] of [['seed-download-speed', measured ? `${bytes(measured.dl_info_speed)}/s` : '\u2014'], ['seed-upload-speed', measured ? `${bytes(measured.up_info_speed)}/s` : '\u2014'], ['seed-torrent-count', torrentSnapshotValid ? String(torrents.length) : '\u2014']]) {
    const node = document.querySelector('#' + id); if (node) node.textContent = value;
  }
  const search = document.querySelector('#search').value.toLocaleLowerCase(); const filter = document.querySelector('#filter').value; const sort = document.querySelector('#sort').value;
  const visible = torrents.filter(row => !pendingRemovals.has(row.hash));
  const rows = visible.filter(row => String(row.name).toLocaleLowerCase().includes(search) && (filter === 'all' || (filter === 'checking' ? String(row.state).startsWith('checking') : state(row) === filter)));
  const source = document.querySelector('#source-filter').value; const filtered = rows.filter(row => source === 'all' || (row.category === 'MediaSeed-Ratio' ? 'ratio' : String(row.category || '').startsWith('MediaSeed-RSS-') ? 'rss' : 'manual') === source); rows.splice(0, rows.length, ...filtered);
  sortTorrentRows(rows, sort, document.querySelector('#sort-direction').value || (sort === 'name' ? 'asc' : 'desc'));
  const pending = [...pendingRemovals.values()].filter(row => !row.confirmed).length;
  document.querySelector('#count').textContent = t("{0} of {1} torrents{2}", {0: rows.length, 1: visible.length, 2: pending ? ' · ' + pending + ' fjernes' : ''});
  const target = document.querySelector('#torrents'); const restoreInteraction = preserveListInteraction(target, 'hash'); target.replaceChildren(); if (!rows.length) target.append(element('p', torrents.length ? t("No matching torrents") : torrentSnapshotValid ? t("No torrents") : t("Torrent client unavailable"), 'muted'));
  const pageSize = [10,25,50,100].includes(Number(document.querySelector('#page-size').value)) ? Number(document.querySelector('#page-size').value) : 25;
  const pages = Math.max(1, Math.ceil(rows.length / pageSize));
  torrentPage = Math.max(1, Math.min(torrentPage, pages));
  const start = (torrentPage - 1) * pageSize;
  document.querySelector('#previous-page').disabled = torrentPage <= 1;
  document.querySelector('#next-page').disabled = torrentPage >= pages;
  document.querySelector('#page-status').textContent = t('Showing {0}–{1} of {2} · Page {3} of {4}', {0: rows.length ? start + 1 : 0, 1: Math.min(start + pageSize, rows.length), 2: rows.length, 3: torrentPage, 4: pages});
  for (const [index, row] of rows.slice(start, start + pageSize).entries()) target.append(seedTorrent(row, start + index));
  restoreInteraction();

}
function torrentTraffic() {
  if (!torrents.every(row => ['dlspeed', 'upspeed'].every(key => typeof row[key] === 'number' && Number.isFinite(row[key]) && row[key] >= 0))) return;
  return torrents.reduce((total, row) => ({dl_info_speed: total.dl_info_speed + row.dlspeed, up_info_speed: total.up_info_speed + row.upspeed}), {dl_info_speed: 0, up_info_speed: 0});
}
function renderTransferSummary() {
  const traffic = torrentTraffic();
  document.querySelector('#transfer').textContent = traffic ? `${clientLabel} · Download ${bytes(traffic.dl_info_speed)}/s · Upload ${bytes(traffic.up_info_speed)}/s · ${downloadsEnabled ? t("VPN verified") : t("Downloads blocked until VPN is verified")}${torrents.length === 500 ? t(" · Traffic for the 500 displayed torrents") : ''}` : t("{0} · waiting for traffic measurement", {0: clientLabel});
}
function applyTorrentSnapshot(rows, request) {
  if (request !== torrentListRequest) return;
  torrentSnapshotValid = Array.isArray(rows);
  if (torrentSnapshotValid) {
    torrents = rows;
    const sample = torrentTraffic();
    if (sample) transferHistory.push(sample);
    transferHistory = transferHistory.slice(-24);
    for (const [id, key] of [['seed-download-chart', 'dl_info_speed'], ['seed-upload-chart', 'up_info_speed']]) {
      const path = document.querySelector('#' + id);
      if (path) { const maximum = Math.max(1, ...transferHistory.map(point => point[key])); path.setAttribute('d', transferHistory.length < 2 ? '' : transferHistory.map((point,index) => `${index ? 'L' : 'M'}${(index/(transferHistory.length-1)*90).toFixed(1)},${(28-point[key]/maximum*24).toFixed(1)}`).join(' ')); }
    }
    for (const [hash, removal] of pendingRemovals) if (removal.confirmed && !torrents.some(row => row.hash === hash)) pendingRemovals.delete(hash);
    renderTorrents();
    renderTraffic(torrentTraffic()); renderTransferSummary();
  } else {
    transferHistory = [];
    for (const id of ["seed-download-chart", "seed-upload-chart"]) document.querySelector("#"+id)?.setAttribute("d", "");
    torrents = []; renderTorrents(); document.querySelector('#count').textContent = t("Torrent client unavailable");
    renderTraffic(); document.querySelector('#transfer').textContent = t("Torrent client unavailable · waiting for traffic measurement");
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
      try {
        const capacity = await api('storage');
        const groups = new Map();
        for (const row of capacity.locations || [capacity]) {
          const key = row.available !== false && row.filesystemId ? row.filesystemId : row.id || 'local';
          if (!groups.has(key)) groups.set(key, []);
          groups.get(key).push(row);
        }
        storage.replaceChildren(element('h2', t('Storage')));
        for (const rows of groups.values()) {
          const row = rows[0], card = element('article', undefined, 'storage-capacity');
          card.append(element('strong', rows.map(r => t(r.label)).join(' · ')), element('p', row.available === false ? t('Storage unavailable') : t('{0} used · {1} free · {2} total', {0: bytes(row.usedBytes), 1: bytes(row.freeBytes), 2: bytes(row.totalBytes)})));
          if (rows.some(r => r.id === 'local')) card.append(element('p', t('Local downloads share the system disk. Capacity includes the operating system, Docker images and app data, not just downloaded files.'), 'muted'));
          if (rows.length > 1) card.append(element('p', t('These folders share the same storage capacity.'), 'muted'));
          if (row.lowSpace) card.append(element('p', t('Low disk space'), 'cleanup-warning'));
          storage.append(card);
        }
      }
      catch { storage.textContent = t("Local storage unavailable"); storageRefreshAt = Date.now() + 30000; }
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
    globalThis.MediaSeedQueue = queue;
    globalThis.MediaSeedWorkspace?.render();
    if (request === torrentListRequest) document.querySelector('#queue-status').textContent = queue ? t("Queue: {0}/{1} active · {2} waiting · RSS {3} · Manual {4}{5}", {0: queue.active, 1: queue.limit === -1 ? t('Unlimited') : queue.limit ?? t('Unknown'), 2: queue.waiting, 3: queue.rssActive, 4: queue.manualActive, 5: (queue.ratioActive ? t(" · Build ratio: {0}", {0: queue.ratioActive}) : '') + (queue.boundedView ? t(" · Limited to 500 torrents") : '')}) : t("Queue status unavailable");
  } catch {
    downloadsEnabled = false; renderConnection({unavailable: true}); applyTorrentSnapshot(null, request);
    globalThis.MediaSeedQueue = null; globalThis.MediaSeedWorkspace?.render();
    document.querySelector('#queue-status').textContent = t("Queue status unavailable");
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
  if (!country) return t("Country unknown");
  const code = countryCode(country);
  try { if (code) return new Intl.DisplayNames([locale()], {type: 'region'}).of(code); } catch {}
  return country;
}
function renderConnection(state) {
  const checked = Number(state.checkedAt);
  const fresh = Number.isFinite(checked) && Math.abs(Date.now() - checked * 1000) < 90000;
  const active = state.provider === 'mediavpn' && state.ready === true && state.downloadsEnabled === true && fresh;
  const unavailable = state.unavailable || (state.ready && !fresh);
  const status = active ? 'active' : unavailable ? 'unknown' : state.provider || state.disconnectPending ? 'blocked' : 'disconnected';
  document.querySelector('#connection-flow').dataset.state = status;
  document.querySelector('#connection-status').textContent = active ? t("Connected · VPN verified") : unavailable ? t("Status cannot be verified") : state.disconnectPending ? t("Disconnection pending") : state.provider ? t("Connection blocked") : t("No VPN selected");
  document.querySelector('#connection-country').textContent = active ? countryLabel(state.country) : t("Waiting for connection");
  const flag = active ? countryFlag(state.country) : null;
  if (flag) document.querySelector('#connection-country').append(flag);
  document.querySelector('#connection-ip').textContent = active ? state.publicIp || t("IP unknown") : t("Downloads blocked");
  document.querySelector('#connection-port').textContent = active ? t("VPN port {port}", {port: state.forwardedPort || t("Unknown")}) : state.provider ? t("Check the connection under VPN") : t("Select a VPN under the VPN tab");
  document.querySelector('#connection-checked').textContent = fresh ? t("Checked {0}", {0: new Date(checked * 1000).toLocaleTimeString(locale())}) : t("Waiting for status check");
  vpnFreshUntil = active ? Date.now() + 30000 : 0;
  if (!active) renderTraffic();
  globalThis.MediaSeedWorkspace?.render();
}
function renderTraffic(transfer) {
  const flow = document.querySelector('#connection-flow');
  const active = flow.dataset.state === 'active';
  const down = Number(transfer?.dl_info_speed);
  const up = Number(transfer?.up_info_speed);
  const measured = active && typeof transfer?.dl_info_speed === 'number' && typeof transfer?.up_info_speed === 'number' && Number.isFinite(down) && Number.isFinite(up) && down >= 0 && up >= 0;
  flow.dataset.upload = String(measured && up > 0);
  flow.dataset.download = String(measured && down > 0);
  document.querySelector('#connection-download').textContent = measured ? `↓ Download · ${bytes(down)}/s` : t("↓ Download · waiting for traffic measurement");
  document.querySelector('#connection-upload').textContent = measured ? `↑ Upload · ${bytes(up)}/s` : t("↑ Upload · waiting for traffic measurement");
  document.querySelector('#connection-activity').textContent = !active ? t("Traffic requires a verified VPN connection.") : !measured ? t("Waiting for a fresh traffic measurement.") : down > 0 && up > 0 ? t("Downloading and uploading · both directions are active.") : down > 0 ? 'Henter · Internet → MediaVPN → MediaSeed' : up > 0 ? 'Seeder · MediaSeed → MediaVPN → Internet' : t("VPN ready · no active transfer.");
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
  select.replaceChildren(element('option', t("Choose profile"))); select.firstChild.value = '';
  for (const profile of state.profiles || []) { const option = element('option', `${profile.name} \u00b7 ${profile.forwardedPort ? 'port ' + profile.forwardedPort : t("port not verified")}`); option.value = profile.id; select.append(option); }
  select.value = vpnDraft ? previous : state.profileId || '';
  document.querySelector('#vpn-status').textContent = state.ready ? t("Verified") : state.provider ? t("Unverified") : t("Not attached");
  document.querySelector('#vpn-downloads').textContent = state.downloadsEnabled ? t("Allowed through VPN") : t("Blocked");
  document.querySelector('#vpn-ip').textContent = state.publicIp || t("Unverified"); document.querySelector('#vpn-port').textContent = state.forwardedPort || t("Unverified"); document.querySelector('#vpn-reason').textContent = t(state.reason || '');
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
    showNotification(provider ? t("MediaVPN selected. The torrent client starts when the profile is verified.") : t("MediaVPN disconnected. Downloads are blocked. Data is preserved."), 'success');
  } catch (error) { notify(error); } finally { updateVpnChoice(); }
};
document.querySelector('#vpn-disconnect').onclick = async () => {
  const button = document.querySelector('#vpn-disconnect'); button.disabled = true;
  try { await api('vpn', {enabled: false, provider: null}, 'PUT'); vpnDraft = false; await refreshVpn(); await refresh(); showNotification(t("MediaVPN disconnected. Downloads are blocked. Data is preserved."), 'success'); }
  catch (error) { notify(error); button.disabled = false; }
};
document.querySelector('#vpn-refresh').onclick = () => refreshVpn().catch(error => { renderConnection({unavailable: true}); notify(error); });
function trackerCreditView(row) {
  const panel = element('section', undefined, 'tracker-credit');
  const title = row.credit_observation_stale ? t("Waiting for traffic measurement · ratio goal protected") : row.green_pending ? t("Waiting for tracker rules · provisional 0.5× credit") : row.green_active ? t("Green period · 0.5× upload") : row.green_uploaded > 0 ? t("Green period ended · new uploads count 1×") : t("Tracker rules · uploads count 1×");
  panel.append(element('strong', title));
  const accountUpload=row.account_credited_uploaded??row.credited_uploaded;panel.append(element('p',t('Estimated credited upload')+': '+(typeof accountUpload==='number'?'≈ '+trackerBytes(accountUpload):t('Unknown'))));
  panel.append(element('p', t("Raw upload {0} · Conservative seeding credit {1} · Goal ratio {2} / {3}", {0: trackerBytes(row.uploaded), 1: trackerBytes(row.credited_uploaded), 2: trackerNumber(row.credited_ratio), 3: trackerNumber(row.ratio_limit)})));
  if (row.green_pending) panel.append(element('p', row.credit_observation_stale ? t("Waiting for a stable traffic measurement. An old measurement or reset counter must not trigger a ratio goal.") : t("Waiting for the tracker to confirm the creation date. Ratio and automatic cleanup provisionally use half upload credit."), 'muted'));
  if (row.green_rule_until) panel.append(element('p', t("The first 24 hours end at {0}. MediaSeed uses a 30-minute safety margin until {1}.", {0: new Date(row.green_rule_until * 1000).toLocaleString(locale()), 1: new Date(row.green_until * 1000).toLocaleTimeString(locale())}), 'muted'));
  if (row.green_uploaded > 0) panel.append(element('p', t("{0} of uploads were recorded at 0.5×. That portion retains half credit after the period ends.", {0: trackerBytes(row.green_uploaded)}), 'muted'));
  if (row.green_history_estimated) panel.append(element('p', t("Historical uploads cannot be split precisely by time and have been estimated conservatively."), 'muted'));
  const benefits = row.tracker_benefits;
  if (benefits) {
    const flags = [benefits.freeleech === null ? t("Freeleech unknown") : `Freeleech ${benefits.freeleech} %`, benefits.doubleUpload ? t("Double upload") : null, benefits.featured ? 'Featured' : null, benefits.internal ? 'Internal' : null, benefits.refundable ? t("Refundable") : null, benefits.ownUpload ? t("Own release · exempt from Green") : null].filter(Boolean);
    panel.append(element('p', t("{0} · Confirmed info hash · {1}", {0: benefits.tracker, 1: flags.join(' · ')}), 'tracker-benefit-flags'));
    if (benefits.doubleUpload) panel.append(element('p', t("Tracker bonuses are shown for information. The local seeding goal conservatively uses measured uploads with the Green deduction; the account's actual credit is shown on the tracker card."), 'muted'));
  } else panel.append(element('p', t("Current tracker benefits are awaiting a new API lookup."), 'muted'));
  return panel;
}
function detailTable(title, columns, rows) {
  const section = element('section'); section.append(element('h3', title)); if (!rows.length) { section.append(element('p', t("No data"), 'muted')); return section; }
  const table = element('table'); const heading = element('tr'); for (const [, label] of columns) heading.append(element('th', label)); const head = element('thead'); head.append(heading); table.append(head);
  const body = element('tbody'); for (const row of rows) { const line = element('tr'); for (const [key] of columns) line.append(element('td', String(row[key] ?? '—'))); body.append(line); } table.append(body); const wrap = element('div', undefined, 'table-wrap'); wrap.append(table); section.append(wrap); return section;
}
async function showDetails(row) { return richDetails(row); }

function renderCleanupDetails(policy = {mode: 'never'}, row, target) {
  target.append(element('h3', t("Automatic cleanup")));
  if (policy.mode === 'never') { target.append(element('p', t("Disabled · torrent and files are preserved."), 'muted')); return; }
  const goals = {time: t("{0} hours of active seeding", {0: policy.hours}), ratio: t("credited upload ratio {0}", {0: policy.ratio}), both: t("{0} hours of active seeding AND credited ratio {1}", {0: policy.hours, 1: policy.ratio}), either: t("{0} hours of active seeding OR credited ratio {1}", {0: policy.hours, 1: policy.ratio})};
  const state = {waiting: t("Waiting for preparation; torrent is kept paused"), preparing: t("Preparation was interrupted; check the torrent or disable the rule"), active: t("Monitoring · waiting for the goal and connected downloaders"), identity_mismatch: t("Cleanup blocked: torrent identity could not be confirmed")};
  target.append(element('p', t("{0} after {1}.", {0: policy.delete_files ? t("Remove torrent and files") : t("Remove torrent only · keep files"), 1: goals[policy.mode]})), element('p', policy.observationFailed ? t("Status check failed · cleanup is waiting for a new valid measurement.") : state[policy.status] || t("Waiting for check"), 'muted'));
  if (appRole !== 'viewer') {
    const cancel = element('button', t("Disable automatic cleanup"));
    cancel.onclick = async () => { cancel.disabled = true; try { const result = await api(`torrents/${row.hash}/cleanup/cancel`, {}); showNotification(result.stopLimitsRestored === false ? t("Cleanup disabled. The stop goal could not be restored; set it under Details when the client is ready.") : t("Automatic cleanup disabled. Torrent and files are preserved."), 'success'); await showDetails(row); } catch (error) {notify(error); cancel.disabled = false;} };
    target.append(cancel);
  }
}
function trackerNumber(value) {
  return typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString(locale(), {maximumFractionDigits: 2}) : '—';
}
function trackerBytes(value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  let amount = Math.abs(value), index = 0;
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB'];
  while (amount >= 1024 && index < units.length - 1) { amount /= 1024; index++; }
  return `${value < 0 ? '−' : ''}${amount.toLocaleString(locale(), {maximumFractionDigits: index ? 2 : 0})} ${units[index]}`;
}
function rankGoal(item) {
  return item.unit==='bytes'?trackerBytes(item.goal):item.unit==='days'?t('{0} days',{0:trackerNumber(item.goal)}):trackerNumber(item.goal);
}
function rankRequirements(entry,compact=false) {
  const section=element('section',undefined,compact?'rank-next-summary':'rank-requirements');section.append(element('strong',entry.title));
  const labels={trueUpload:'True upload',membership:'Membership',comments:'Comments',posts:'Posts',averageSeeding:'Average seeding time',seedingSize:'Seeding size'};
  const list=element('dl');
  for(const item of entry.requirements){
    const line=element('div');line.append(element('dt',t(labels[item.metric])),element('dd',rankGoal(item)));
    if(!compact && item.missing!==null){const missing={...item,goal:item.missing};line.append(element('small',item.missing===0?t('Requirement met'):t('Missing: {0}',{0:rankGoal(missing)}),'muted'));}
    list.append(line);
  }
  section.append(list);return section;
}
function showRankDialog(row) {
  let dialog=document.querySelector('#rank-requirements-dialog');
  if(!dialog){dialog=element('dialog');dialog.id='rank-requirements-dialog';dialog.setAttribute('aria-labelledby','rank-dialog-title');document.body.append(dialog);}
  const heading=element('div',undefined,'heading'),title=element('h2',t('Rank requirements'));title.id='rank-dialog-title';
  const close=command(t('Close'),()=>dialog.close());close.type='button';heading.append(title,close);
  const select=document.createElement('select');select.id='rank-choice';const label=element('label',t('Choose rank'));label.append(select);
  const entries=row.rankProgress?.table||[];
  for(const entry of entries){const option=element('option',entry.title);option.value=entry.title;select.append(option);}
  select.value=row.rankProgress?.target?.title||row.rankProgress?.firstListed?.title||entries[0]?.title||'';
  const content=element('div');
  const note=element('p',t('Progress is only shown for values supplied by the tracker. True upload and credited upload are different.'),'muted');
  const source=element('a',t('Open NordicBytes rank table'));source.href='https://nordicbytes.org/pages/groups';source.target='_blank';source.rel='noopener noreferrer';
  dialog.replaceChildren(heading,element('p',t('Rank: {0}',{0:row.ranks?.automatic||t('Unknown')}),'muted'),label,content,note,source);
  function paint(){const entry=entries.find(item=>item.title===select.value);content.replaceChildren();if(entry){content.append(rankRequirements(entry));if(entry.requirements.some(item=>item.missing===null))content.append(element('p',t('Some account values are unavailable; the remaining requirements cannot yet be calculated.'),'muted'));}}
  select.onchange=paint;paint();
  // This dialog lives outside the periodically replaced tracker cards.
  // A background account refresh must not close it or reset the selected rank.
  if(!dialog.open)dialog.showModal();
}
function trackerCard(row) {
  const card = element('article', undefined, 'source-row tracker-card');
  const head = element('div', undefined, 'tracker-card-heading');
  const identity = element('div');
  identity.append(element('h3', row.name), element('p', `NordicBytes${row.username ? ` · ${row.username}` : ''}`, 'muted'));
  const available = row.status === 'ok' && row.stats && typeof row.stats === 'object';
  head.append(identity, element('span', available ? t("Connected") : t("Account data unavailable"), `tracker-state${available ? ' ready' : ''}`));
  card.append(head);
  if (available) {
    const stats = element('dl', undefined, 'tracker-stats');
    for (const [label, field, format] of [
      ['Ratio', 'ratio', trackerNumber], ['Upload', 'uploaded', trackerBytes], ['Download', 'downloaded', trackerBytes],
      ['Buffer', 'buffer', trackerBytes], [t("Seeding"), 'seeding', trackerNumber], [t("Downloading"), 'leeching', trackerNumber],
      ['Shards / bonus', 'seedbonus', trackerNumber], [t("Seeding size"), 'seeding_size', trackerBytes],
      [t("Releases"), 'total_uploads', trackerNumber], ['Hit-and-runs', 'hit_and_runs', trackerNumber], [t("Warnings"), 'warnings', trackerNumber],
    ]) {
      const metric = element('div', undefined, 'tracker-metric');
      metric.append(element('dt', label), element('dd', format(row.stats[field])));
      if (['hit_and_runs', 'warnings'].includes(field) && Number.isFinite(row.stats[field]) && row.stats[field] > 0) metric.classList.add('attention');
      stats.append(metric);
    }
    card.append(stats);
    const ranks=element('div',undefined,'tracker-ranks');
    for(const [key,label] of [['automatic','Rank'],['upload','Uploader rank'],['internal','Staff rank']]){
      if(row.ranks?.[key])ranks.append(element('p',t(label)+': '+row.ranks[key]));
    }
    if(!row.ranks?.automatic)ranks.append(element('p',t('Rank unavailable from tracker'),'muted'));
    const progress=row.rankProgress,goal=progress?.target||progress?.firstListed;
    if(goal){ranks.append(element('small',t(progress?.target?'Next rank':'First rank in this table'),'muted'),rankRequirements(goal,true));}
    const view=command(t('View rank requirements'),()=>showRankDialog(row));view.type='button';ranks.append(view);
    card.append(ranks);

    const checked = new Date(row.checkedAt * 1000);
    card.append(element('p', Number.isFinite(checked.getTime()) ? t("Retrieved {0} · Checked automatically every 5 minutes", {0: checked.toLocaleString(locale(), {dateStyle: 'short', timeStyle: 'short'})}) : t("Account statistics time is unknown"), 'tracker-updated muted'));
  } else {
    card.append(element('p', t("Account statistics could not be retrieved. Check the API key's access under Trackers. MediaSeed retries automatically."), 'muted'));
  }
  return card;
}
function renderTrackerOverview(rows) {
  globalThis.MediaSeedTrackerRows = rows; globalThis.MediaSeedWorkspace?.render();
  richTrackerSummary(rows);
  document.querySelector('#tracker-overview').hidden = !rows.length;

}
let trackerOverviewLoading = false;
async function refreshTrackerOverview() {
  if (appRole === 'viewer' || trackerOverviewLoading) return;
  trackerOverviewLoading = true;
  try { renderTrackerOverview(await api('trackers')); }
  catch { document.querySelector('#tracker-account-list').replaceChildren(element('p', t("Tracker statistics could not be retrieved. Retrying automatically."), 'muted')); }
  finally { trackerOverviewLoading = false; }
}
const feedForm = document.querySelector('#feed-form');
const feedLocation = document.querySelector('#feed-location');
const feedFolder = document.querySelector('#feed-folder');
let feedLocations = [], feedFolderPath = '', feedStorageReady = false, feedStorageRequest = 0, feedEditing = false;
function feedStoragePending() {
  feedStorageReady = false;
  document.querySelector('#feed-save').disabled = true;
  feedFolder.disabled = true;
  document.querySelector('#feed-folder-up').disabled = true;
}
async function loadFeedLocations(location, folder = '') {
  feedStoragePending(); const request = ++feedStorageRequest;
  feedLocation.disabled = true;
  const catalog = await api('storage');
  if (request !== feedStorageRequest) return;
  feedLocations = catalog.locations || [{...catalog, id: 'local', label: 'Local downloads'}];
  feedLocation.replaceChildren(...feedLocations.map(row => {
    const option = new Option(t(row.label) + (!row.available ? ' - ' + t('Storage unavailable') : !row.writable ? ' - ' + t('Read-only') : ''), row.id);
    option.disabled = !row.available; return option;
  }));
  const choice = location ?? catalog.defaultLocation ?? 'local';
  if (!feedLocations.some(row => row.id === choice)) {
    const missing = new Option(choice + ' - ' + t('Storage unavailable'), choice); missing.disabled = true; feedLocation.append(missing);
  }
  feedLocation.value = choice; feedLocation.disabled = false;
  await loadFeedFolders(folder);
}
async function loadFeedFolders(path) {
  feedStoragePending(); const request = ++feedStorageRequest, location = feedLocation.value;
  feedFolderPath = path;
  document.querySelector('#feed-folder-path').textContent = '/' + path;
  document.querySelector('#feed-folder-up').disabled = !path;
  const rows = await api(`storage/folders?location=${encodeURIComponent(location)}&folder=${encodeURIComponent(path)}`);
  if (request !== feedStorageRequest) return;
  feedFolderPath = path; feedStorageReady = rows.writable;
  feedForm.elements.namedItem('folder').value = path;
  document.querySelector('#feed-folder-path').textContent = '/' + path;
  document.querySelector('#feed-folder-up').disabled = !path;
  feedFolder.replaceChildren(new Option(t('Use this folder'), ''), ...rows.folders.map(row => new Option(row.name + (row.writable ? '' : ' - ' + t('Read-only')), row.name)));
  feedFolder.disabled = false;
  const capacity = feedLocations.find(row => row.id === location);
  document.querySelector('#feed-storage-capacity').textContent = (capacity ? t('{0} free of {1}', {0: bytes(capacity.freeBytes), 1: bytes(capacity.totalBytes)}) : '') + (rows.writable ? '' : ' - ' + t('Read-only')) + (rows.truncated ? ' - ' + t('Only the first 200 folders are shown') : '');
  document.querySelector('#feed-save').disabled = !feedStorageReady;
}
feedLocation.onchange = () => { void loadFeedFolders('').catch(notify); };
feedFolder.onchange = () => { if (feedFolder.value) void loadFeedFolders([feedFolderPath, feedFolder.value].filter(Boolean).join('/')).catch(notify); };
document.querySelector('#feed-folder-up').onclick = () => { void loadFeedFolders(feedFolderPath.split('/').slice(0, -1).join('/')).catch(notify); };
feedForm.addEventListener('reset', () => { if (!feedEditing) queueMicrotask(() => { void loadFeedLocations().catch(notify); }); });

async function loadSources(kind) {
  const requests = loadSources.requests ||= {}; const generation = requests[kind] = (requests[kind] || 0) + 1;
  const rows = await api(kind); if (generation !== requests[kind]) return;
  const target = document.querySelector(kind === 'feeds' ? '#feed-list' : '#tracker-list'); const restoreInteraction = preserveListInteraction(target, 'sourceId'); target.replaceChildren(); if (!rows.length) target.append(element('p', kind === 'feeds' ? t("No feeds") : t("No accounts"), 'muted'));
  if (kind === 'feeds') {
    const preview = document.querySelector('#feed-preview');
    if (preview) {
      preview.replaceChildren();
      if (!rows.length) preview.append(element('p', t("No feeds"), 'muted'));
      for (const row of rows.slice(0, 6)) {
        const item = element('div', undefined, 'seed-feed-row');
        item.append(element('strong', row.name), element('small', row.paused ? t("Paused") : t("Active")));
        preview.append(item);
      }
    }
  }
  if (kind === 'trackers') {
    renderTrackerOverview(rows);
    const select = document.querySelector('#tracker-account'); const previous = select.value; select.replaceChildren(element('option', t("Choose account"))); select.firstChild.value = '';
    for (const row of rows) { const option = element('option', row.name); option.value = row.id; select.append(option); }
    select.value = rows.some(row => row.id === previous) ? previous : rows.length === 1 ? rows[0].id : '';
    document.querySelector('#tracker-search-submit').disabled = !rows.length;
  }
  for (const row of rows) {
    const item = kind === 'trackers' ? trackerCard(row) : element('article', undefined, 'source-row'); item.dataset.sourceId = row.id;
    if (kind === 'feeds') item.append(element('h2', row.name), element('p', `${row.location ? t(feedLocations.find(place => place.id === row.location)?.label || (row.location === 'local' ? 'Local downloads' : row.location)) + ' /' + (row.folder || '') : row.folder || '/downloads'} · Ratio ${row.ratio} · ${row.paused ? t("Paused") : t("Active")} · ${t(row.status || '')}`, 'muted'));
    if (kind === 'feeds') item.append(element('p', row.start_at ? t('Only fetch RSS entries from: {0}', {0: new Date(row.start_at).toLocaleString()}) : t('No start date selected'), 'muted'));
    const actions = element('div', undefined, 'actions');
    if (kind === 'feeds') { item.append(element('p',t('Estimated credited upload')+': '+(typeof row.upload?.creditedUploadedBytes==='number'?'≈ '+trackerBytes(row.upload.creditedUploadedBytes):t('Unknown'))+(row.upload?.unknownDownloads?' · '+t('{0} downloads without measurements',{0:row.upload.unknownDownloads}):''),'feed-upload-total'));item.append(element('p', [row.include?.join(', '), row.exclude?.length ? t('Avoid') + ': ' + row.exclude.join(', ') : '', row.max_bytes ? t('Maximum size') + ': ' + trackerBytes(row.max_bytes) : '', row.badges?.join(' · ')].filter(Boolean).join(' · ') || t('No additional filters'), 'seed-feed-rules')); actions.append(command(t('History'), () => richFeedHistory(row.id, row.name))); }
    if (appRole === 'administrator') {
      if (kind === 'feeds') actions.append(command(row.paused ? 'Aktivér' : 'Pause', async () => { await api(`feeds/${row.id}/state`, {enabled: row.paused}, 'PUT'); await loadSources('feeds'); showNotification(row.paused ? t("Feed enabled through VPN.") : t("Feed paused. Existing torrents are preserved."), 'success'); }));
      actions.append(command(t("Edit"), () => { globalThis.MediaSeedWorkspace?.openEditor(kind);  const form = document.querySelector(kind === 'feeds' ? '#feed-form' : '#tracker-form'); feedEditing = kind === 'feeds'; form.reset(); feedEditing = false; for (const [key, value] of Object.entries(row)) { const input = form.elements.namedItem(key); if (input) input.value = key === 'start_at' && value ? new Date(new Date(value).getTime() - new Date(value).getTimezoneOffset() * 60000).toISOString().slice(0, 16) : value ?? ''; } if (kind === 'feeds') { for (const key of ['include','exclude']) form.elements.namedItem(key).value = (row[key] || []).join(', '); for (const option of form.elements.namedItem('badges').options) option.selected = (row.badges || []).includes(option.value); globalThis.MediaSeedWorkspace?.syncBadges(); form.elements.namedItem('max_gib').value = row.max_bytes ? row.max_bytes / (1024 ** 3) : ''; form.elements.namedItem('url').placeholder = t('Saved feed address is kept. Fill in only to change it.'); void loadFeedLocations(row.location || 'local', row.folder || '').catch(notify); } form.querySelector('input[name="name"]').focus(); }), command(t("Remove"), async () => { if (!confirm(t("Remove {0}? Files and torrents are preserved.", {0: row.name}))) return; await api(`settings/${kind}/${row.id}`, undefined, 'DELETE'); await loadSources(kind); }));
    }
    item.append(actions); target.append(item);
  }
  restoreInteraction();
}
async function pollSources(kind) {
  const pending = pollSources.pending ||= new Set();
  if (pending.has(kind)) return;
  pending.add(kind);
  try { await loadSources(kind); } finally { pending.delete(kind); }
}
let trackerSearchGeneration = 0;
document.querySelector('#tracker-account').onchange = () => { trackerSearchGeneration++; document.querySelector('#tracker-results').replaceChildren(); document.querySelector('#tracker-search-status').textContent = ''; };
document.querySelector('#tracker-search').onsubmit = async event => {
  event.preventDefault(); const form = event.target; const account = form.elements.namedItem('account').value; if (!account) return;
  const generation = ++trackerSearchGeneration; const button = document.querySelector('#tracker-search-submit'); const target = document.querySelector('#tracker-results'); const status = document.querySelector('#tracker-search-status'); button.disabled = true; target.replaceChildren(); status.textContent = t("Searching…");
  try {
    const response = await api(`trackers/${encodeURIComponent(account)}/search?query=${encodeURIComponent(form.elements.namedItem('query').value)}`);
    if (generation !== trackerSearchGeneration) return;
    status.textContent = response.results.length ? t("{0} results · Torrents are added paused. Starting requires a verified VPN.", {0: response.results.length}) : t("No results");
    for (const row of response.results) {
      const item = element('article', undefined, 'source-row'); item.append(element('h2', row.name), element('p', t("{0} · Seeders {1} · Leechers {2}", {0: row.size === null ? t("Unknown size") : bytes(row.size), 1: row.seeders ?? '?', 2: row.leechers ?? '?'}), 'muted'));
      const actions = element('div', undefined, 'actions'); const add = command(t("Add paused"), async () => {
        if (!form.reportValidity()) return;
        await api(`trackers/${encodeURIComponent(account)}/torrents/${encodeURIComponent(row.id)}`, {ratio: Number(form.elements.namedItem('ratio').value)});
        add.textContent = t("Added paused"); add.dataset.added = 'true'; showNotification(t("Torrent added paused. Downloading requires a verified VPN."), 'success'); await refresh();
      });
      actions.append(add); item.append(actions); target.append(item);
    }
  } catch (error) { if (generation === trackerSearchGeneration) { status.textContent = ''; notify(error); } }
  finally { button.disabled = document.querySelector('#tracker-account').options.length === 1; }
};
for (const [selector, kind] of [['#feed-form', 'feeds'], ['#tracker-form', 'trackers']]) {
  const form = document.querySelector(selector); form.onsubmit = async event => { event.preventDefault(); const body = Object.fromEntries(new FormData(form)); const identifier = body.id; delete body.id; if (kind === 'feeds') { if (!feedStorageReady) { showNotification(t('Choose an available writable folder')); return; } body.location = feedLocation.value; body.folder = feedFolderPath; body.ratio = Number(body.ratio); for (const key of ['include','exclude']) body[key] = String(body[key] || '').split(',').map(x => x.trim()).filter(Boolean); body.badges = [...form.elements.namedItem('badges').selectedOptions].map(option => option.value); body.max_bytes = body.max_gib ? Math.round(Number(body.max_gib) * (1024 ** 3)) : null; delete body.max_gib; body.start_at = body.start_at ? new Date(body.start_at).toISOString() : null; } try { const saved = await api(kind + (identifier ? '/' + identifier : ''), body, identifier ? 'PUT' : 'POST'); if (kind === 'feeds' && (saved.start_at ? new Date(saved.start_at).getTime() : null) !== (body.start_at ? new Date(body.start_at).getTime() : null)) throw new Error(t('The saved start date could not be verified. Your form is preserved.')); form.reset(); form.elements.namedItem('id').value = ''; globalThis.MediaSeedWorkspace?.closeEditor(kind); await loadSources(kind); showNotification(t("Saved palette"), 'success'); } catch (error) { notify(error); } };
  form.addEventListener('reset', () => { form.elements.namedItem('id').setAttribute('value', ''); });
}
for (const button of document.querySelectorAll('[data-view]')) button.onclick = async () => { message.textContent = ''; for (const other of document.querySelectorAll('[data-view]')) other.setAttribute('aria-pressed', String(other === button)); for (const section of document.querySelectorAll('.view')) section.hidden = section.id !== button.dataset.view; document.querySelector('#connection-flow').hidden = button.dataset.view !== 'vpn'; try { if (button.dataset.view === 'feeds' || button.dataset.view === 'trackers') { await loadSources(button.dataset.view); if (button.dataset.view === 'feeds' && appRole === 'administrator') await loadFeedLocations(feedLocations.length ? feedLocation.value : undefined, feedLocations.length ? feedFolderPath : ''); } else if (button.dataset.view === 'vpn') await refreshVpn(); } catch (error) { notify(error); } };
document.querySelector('#close-details').onclick = () => dialog.close();
document.querySelector('#policy-form').onsubmit = async event => { event.preventDefault(); try { await api(`torrents/${selectedHash}/policy`, {ratio: Number(event.target.elements.namedItem('ratio').value)}); document.querySelector('#detail-message').textContent = t("Seeding goal saved"); } catch (error) { document.querySelector('#detail-message').textContent = t(error.message); } };
for (const selector of ['#search', '#filter', '#sort', '#source-filter', '#sort-direction', '#page-size']) document.querySelector(selector).addEventListener('input', () => {torrentPage = 1; saveListPreferences(); renderTorrents();});
document.querySelector('#previous-page').onclick = () => {torrentPage--; renderTorrents();};
document.querySelector('#next-page').onclick = () => {torrentPage++; renderTorrents();};
document.querySelector('#open-rss')?.addEventListener('click', () => document.querySelector('[data-view="feeds"]').click());
document.querySelector('#manage-trackers').onclick = () => document.querySelector('[data-view="trackers"]').click();
function showWorkspace() { if (appRole !== 'viewer') void loadSources('feeds').catch(() => { const preview = document.querySelector('#feed-preview'); if (preview) preview.textContent = t('Feed list unavailable'); }); const rssPanel = document.querySelector('.seed-rss'); if (rssPanel) rssPanel.hidden = appRole === 'viewer'; void refreshTrackerOverview(); document.body.classList.add('signed-in'); document.querySelector('#identity').hidden = true; document.querySelector('#workspace').hidden = false; document.querySelector('#logout').hidden = false; document.querySelector('#new-torrent').hidden = appRole === 'viewer'; document.querySelector('#policy-form').hidden = appRole === 'viewer'; for (const selector of ['#feed-form', '#tracker-form', '#vpn-form']) document.querySelector(selector).hidden = appRole !== 'administrator'; for (const selector of ['[data-view="feeds"]', '[data-view="trackers"]', '[data-view="ratio-scout"]']) document.querySelector(selector).hidden = appRole === 'viewer'; refresh(); }
document.querySelector('#login').onsubmit = async event => { event.preventDefault(); const body = Object.fromEntries(new FormData(event.target)); try { if (needsSetup) { await api('auth/setup', body); needsSetup = false; } csrf = (await api('auth/login', body)).csrf; const identity = await api('auth/me'); setLanguage(identity.language || 'en'); globalThis.MediaAppTheme.applyIdentity(identity); event.target.reset(); showWorkspace(); } catch (error) { notify(error); } };
document.querySelector('#refresh').onclick = refresh;
const addDialog = document.querySelector('#add-torrent');
const addForm = document.querySelector('#add-torrent-form');
document.querySelector('#seed-preview-button').onclick = richPreview;
const fileInput = document.querySelector('#add-file');
const fileField = document.querySelector('#file-field');
let torrentFiles = [];
const torrentFileKey = file => JSON.stringify([file.name, file.size, file.lastModified || 0]);
function renderTorrentFiles() {
  const list = document.querySelector('#torrent-file-list'); list.replaceChildren();
  const statuses = {pending: 'Ready to add', uploading: 'Adding…', added: 'Added', error: 'Failed — check the torrent list before retrying'};
  for (const item of torrentFiles) {
    const row = element('li', undefined, 'torrent-file-row'); row.dataset.state = item.state;
    const info = element('div'); info.append(element('strong', item.file.name), element('small', `${bytes(item.file.size)} · ${t(statuses[item.state])}${item.message ? ' · ' + t(item.message) : ''}`, 'muted'));
    row.append(info);
    if (item.state !== 'added') {
      const remove = element('button', t('Remove from selection')); remove.type = 'button'; remove.disabled = addingTorrent;
      remove.setAttribute('aria-label', t('Remove {0} from selection', {0: item.file.name}));
      remove.onclick = () => { torrentFiles = torrentFiles.filter(entry => entry !== item); renderTorrentFiles(); setAddMode(); };
      row.append(remove);
    }
    list.append(row);
  }
  document.querySelector('#file-name').textContent = torrentFiles.length === 1 ? torrentFiles[0].file.name : t('{0} files selected', {0: torrentFiles.length});
  document.querySelector('#torrent-batch-status').textContent = t('{0} of {1} files added', {0: torrentFiles.filter(item => item.state === 'added').length, 1: torrentFiles.length});
  document.querySelector('#seed-preview-button').disabled = addingTorrent;
  updateAddStart();
}
function selectTorrentFiles(files) {
  const rejected = [];
  for (const file of files) {
    if (!file.name.toLowerCase().endsWith('.torrent') || file.size > 4 * 1024 * 1024 || file.size === 0) { rejected.push(file.name); continue; }
    if (torrentFiles.some(item => torrentFileKey(item.file) === torrentFileKey(file))) continue;
    if (torrentFiles.length >= 100 || torrentFiles.reduce((sum, item) => sum + item.file.size, 0) + file.size > 64 * 1024 * 1024) { rejected.push(file.name); continue; }
    torrentFiles.push({file, state: 'pending', message: ''});
  }
  fileInput.value = ''; renderTorrentFiles(); setAddMode();
  if (rejected.length) showNotification(t('{0} files were skipped. Choose .torrent files up to 4 MiB each; at most 100 files and 64 MiB per selection.', {0: rejected.length}));
}
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
  document.querySelector('#add-seeding-help').textContent = active ? t("The selected cleanup rule controls the seeding goal. Choose start now or add paused. Starting requires a verified VPN.") : t("Choose start now or add paused. Starting requires a verified VPN. Stop goal: selected credited ratio or 49 hours of seeding; torrent and files are preserved.");
  const stop = addForm.elements.namedItem('ratio'); stop.disabled = active;
  const label = stop.closest?.('label'); if (label) label.hidden = active;
}
document.querySelector('#add-cleanup-mode').onchange = updateCleanupFields;
document.querySelector('#add-cleanup-action').onchange = updateCleanupFields;
function setAddMode() {
  richPreview.request++; document.querySelector('#seed-add-preview').replaceChildren();
  const fileMode = addForm.elements.namedItem('mode').value === 'file';
  document.querySelector('#magnet-field').hidden = fileMode; fileField.hidden = !fileMode; document.querySelector('#torrent-file-selection').hidden = !fileMode;
  document.querySelector('#add-magnet').disabled = fileMode; document.querySelector('#add-magnet').required = !fileMode;
  fileInput.disabled = !fileMode; fileInput.required = fileMode && !torrentFiles.length;
  document.querySelector('#seed-preview-button').disabled = addingTorrent;
  updateAddStart();globalThis.MediaSeedAdd?.changed();
}
function updateAddStart() {
  const complete = addForm.elements.namedItem('mode').value === 'file' && torrentFiles.length > 0 && torrentFiles.every(item => item.state === 'added');
  if (!addingTorrent) { document.querySelector('#add-start').disabled = !downloadsEnabled || complete; document.querySelector('#add-submit').disabled = complete; }
  document.querySelector('#add-start-help').hidden = downloadsEnabled;
}
for (const radio of addForm.querySelectorAll('[name="mode"]')) radio.onchange = setAddMode;
function openAdd() {
  if (addDialog.open) return;
  showNotification(''); addForm.reset(); torrentFiles = []; renderTorrentFiles(); richPreview.request++; document.querySelector('#seed-add-preview').replaceChildren();
  document.querySelector('#file-name').textContent = ''; setAddMode();
  updateAddStart();
  updateCleanupFields();
  addDialog.append(document.querySelector('.notifications')); addDialog.showModal();
  void loadAddStorage().catch(notify);
}
const addLocation = document.querySelector('#add-location');
const addFolder = document.querySelector('#add-folder');
let addFolderPath = '', addStorageReady = false, storageRequest = 0, addLocations = [];
async function loadAddStorage() {
  addStorageReady = false; const request = ++storageRequest;
  addLocation.replaceChildren(new Option(t('Loading storage'), '')); addLocation.disabled = true;
  const catalog = await api('storage'); if (request !== storageRequest || !addDialog.open) return;
  addLocations = catalog.locations;
  addLocation.replaceChildren(...addLocations.map(row => { const option = new Option(t(row.label) + (row.writable ? '' : ' - ' + t('Read-only')), row.id); option.disabled = !row.available; return option; }));
  addLocation.value = catalog.defaultLocation; addLocation.disabled = false;
  await loadAddFolders('');
}
async function loadAddFolders(path) {
  addStorageReady = false; const request = ++storageRequest, location = addLocation.value;
  addFolder.disabled = true;
  const rows = await api(`storage/folders?location=${encodeURIComponent(location)}&folder=${encodeURIComponent(path)}`);
  if (request !== storageRequest || !addDialog.open) return;
  addFolderPath = path; addStorageReady = rows.writable;
  document.querySelector('#add-folder-path').textContent = '/' + path;
  document.querySelector('#add-folder-up').disabled = !path;
  addFolder.replaceChildren(new Option(t('Use this folder'), ''), ...rows.folders.map(row => new Option(row.name + (row.writable ? '' : ' - ' + t('Read-only')), row.name)));
  addFolder.disabled = false;
  const capacity = addLocations.find(row => row.id === location);
  document.querySelector('#add-storage-capacity').textContent = t('{0} free of {1}', {0: bytes(capacity.freeBytes), 1: bytes(capacity.totalBytes)}) + (rows.writable ? '' : ' - ' + t('Read-only')) + (rows.truncated ? ' - ' + t('Only the first 200 folders are shown') : '');
}
addLocation.onchange = () => { void loadAddFolders('').catch(notify); };
addFolder.onchange = () => { if (addFolder.value) void loadAddFolders([addFolderPath, addFolder.value].filter(Boolean).join('/')).catch(notify); };
document.querySelector('#add-folder-up').onclick = () => { void loadAddFolders(addFolderPath.split('/').slice(0, -1).join('/')).catch(notify); };
document.querySelector('#new-torrent').onclick = openAdd;
addDialog.addEventListener('close', () => { document.body.append(document.querySelector('.notifications')); });
function closeAdd() { if (!addingTorrent) addDialog.close(); }
document.querySelector('#close-add').onclick = closeAdd; document.querySelector('#cancel-add').onclick = closeAdd;
addDialog.addEventListener('cancel', event => { if (addingTorrent) event.preventDefault(); });
document.querySelector('#add-magnet').addEventListener('input', () => {richPreview.request++; document.querySelector('#seed-add-preview').replaceChildren();globalThis.MediaSeedAdd?.changed();});
fileInput.onchange = () => { if (!addingTorrent) selectTorrentFiles(Array.from(fileInput.files)); };
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
  const files = Array.from(event.dataTransfer.files);
  if (!files.some(file => file.name.toLowerCase().endsWith('.torrent') && file.size > 0 && file.size <= 4 * 1024 * 1024)) { showNotification(t("Choose .torrent files up to 4 MiB each.")); return; }
  openAdd();
  addForm.querySelector('[name="mode"][value="file"]').checked = true;
  selectTorrentFiles(files);
});
addForm.onsubmit = async event => {
  event.preventDefault(); if (addingTorrent) return;
  const fileMode = addForm.elements.namedItem('mode').value === 'file'; const ratio = Number(addForm.elements.namedItem('ratio').value);
  const start = event.submitter?.value === 'start';
  if (!addStorageReady) { showNotification(t('Choose an available writable folder')); return; }
  const location = addLocation.value, folder = addFolderPath;
  const cleanup = addCleanupPolicy();
  const cleanupQuery = cleanup.mode === 'never' ? '' : `&cleanup_mode=${cleanup.mode}&cleanup_hours=${cleanup.hours}&cleanup_ratio=${cleanup.ratio}&cleanup_delete_files=${cleanup.delete_files}`;
  const pending = torrentFiles.filter(item => item.state !== 'added');
  if (fileMode && !pending.length) { showNotification(t("Choose torrent files that have not been added yet.")); return; }
  addingTorrent = true; const controls = [...addForm.elements, document.querySelector('#close-add')]; const disabledStates = controls.map(control => control.disabled); controls.forEach(control => { control.disabled = true; });
  try {
    let result;
    if (fileMode) {
      let added = 0;
      for (const item of pending) {
        item.state = 'uploading'; item.message = ''; renderTorrentFiles();
        try {
          const response = await fetch(`/api/torrents/upload?filename=${encodeURIComponent(item.file.name)}&ratio=${ratio}${cleanupQuery}&start=${start}&location=${encodeURIComponent(location)}&folder=${encodeURIComponent(folder)}`, {method: 'POST', headers: {'Content-Type': 'application/x-bittorrent', 'X-MediaSeed-CSRF': csrf}, body: item.file});
          const payload = await response.json(); if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : t("The torrent file could not be added."));
          result = payload; item.state = 'added'; added++;
          item.message = payload.cleanupPending ? 'Cleanup preparation pending' : payload.startRequested ? 'Start requested through VPN' : 'Added paused';
        } catch (error) { item.state = 'error'; item.message = errorLabels[error.message] || t('The addition could not be confirmed. Check the torrent list before retrying.'); }
        renderTorrentFiles();
      }
      const failed = pending.filter(item => item.state === 'error').length;
      if (torrentFiles.length > 1 || failed) {
        showNotification(t('{0} files added; {1} failed. Successful files will not be sent again.', {0: added, 1: failed}), failed ? 'danger' : 'success');
        await refreshTorrents(); return;
      }
    } else { result = await api('torrents', {url: addForm.elements.namedItem('url').value.trim(), ratio, start, location, folder, ...(cleanup.mode === 'never' ? {} : {cleanup})}); }
    addDialog.close(); showNotification((result.cleanupPending ? t("Torrent added paused. The cleanup rule is awaiting preparation; your selected start will follow.") : result.startRequested ? (result.pending ? t("Magnet link received. Start selected; waiting for metadata from the torrent network.") : t("Torrent added. Start requested through VPN.")) : (result.pending ? t("Magnet link received paused. Metadata is waiting for VPN.") : t("Torrent added paused. Press Start when you want to download."))) + (result.webSourcesIgnored ? t(" Extra web sources in the link were omitted; the hash and trackers are used.") : ''), 'success');
    await refreshTorrents();
  } catch (error) { notify(error); }
  finally { addingTorrent = false; controls.forEach((control, index) => { control.disabled = disabledStates[index]; }); updateAddStart(); if (fileMode) renderTorrentFiles(); }
};
document.querySelector('#logout').onclick = async () => { try { await api('auth/logout', {}); location.reload(); } catch (error) { notify(error); } };
setInterval(() => { if (!document.hidden && document.body.classList.contains('signed-in')) { if (!document.querySelector('#vpn').hidden) refreshVpn().catch(() => {}); if (!document.querySelector('#feeds').hidden) pollSources('feeds').catch(() => {}); refresh(); } }, 5000);
setInterval(() => { if (!document.hidden && document.body.classList.contains('signed-in')) { if (!document.querySelector('#trackers').hidden) pollSources('trackers').catch(() => {}); else if (!document.querySelector('#downloads').hidden) void refreshTrackerOverview(); } }, 60000);
async function initializeLogin() {
  try {
    const status = await api('auth/status'); needsSetup = status.needsSetup;
    if (status.managed) {
      document.querySelector('#login').hidden = true;
      try { const identity = await api('auth/me'); setLanguage(identity.language || 'en'); globalThis.MediaAppTheme.applyIdentity(identity); csrf = identity.csrf; appRole = identity.role || 'administrator'; showWorkspace(); }
      catch (error) { if (error.status === 401) location.replace(status.loginUrl); else notify(error); }
      return;
    }
    document.querySelector('#identity-title').textContent = needsSetup ? t("A fresh start.") : t("Welcome back.");
    document.querySelector('#identity-subtitle').textContent = needsSetup ? t("Create your local MediaSeed administrator.") : t("Sign in to MediaSeed.");
    document.querySelector('#login-submit').textContent = needsSetup ? t("Create administrator") : t("Sign in");
    if (!needsSetup) { try { const identity = await api('auth/me'); setLanguage(identity.language || 'en'); globalThis.MediaAppTheme.applyIdentity(identity); csrf = identity.csrf; appRole = identity.role || 'administrator'; showWorkspace(); } catch {} }
  } catch (error) { notify(error); }
}
void initializeLogin();

const accountLanguage = document.querySelector('#account-language');
if (accountLanguage) { accountLanguage.onchange = () => setLanguage(accountLanguage.value); setLanguage(accountLanguage.value || 'en'); }

let refreshingPreferences = false;
async function refreshAccountPreferences() {
  if (refreshingPreferences || document.hidden || document.querySelector('#workspace').hidden) return;
  refreshingPreferences = true;
  try { const identity = await api('auth/me'); setLanguage(identity.language || 'en'); globalThis.MediaAppTheme.applyIdentity(identity); } catch {} finally { refreshingPreferences = false; }
}
document.addEventListener('visibilitychange', () => { if (!document.hidden) void refreshAccountPreferences(); });
window.addEventListener('focus', () => { void refreshAccountPreferences(); });
setInterval(() => { void refreshAccountPreferences(); }, 60000);


window.addEventListener('mediaseed-settings-open', async () => {
  const form = document.querySelector('#download-settings-form'); if (!form) return;
  const input = document.querySelector('#max-active-downloads'), button = document.querySelector('#save-download-settings'), status = document.querySelector('#download-settings-status');
  button.disabled = true; input.disabled = true; status.textContent = t('Loading download settings…');
  try {
    const settings = await api('download-settings');
    input.value = settings.maxActiveDownloads > 0 ? settings.maxActiveDownloads : '';
    input.disabled = appRole !== 'administrator'; button.disabled = appRole !== 'administrator';
    status.textContent = appRole !== 'administrator' ? t('Only an administrator can change the shared download limit.') : settings.maxActiveDownloads === -1 ? t('The client currently has no download limit. Choose a value to enable its queue.') : t('Current limit: {0} simultaneous downloads', {0: settings.maxActiveDownloads});
  } catch (error) {status.textContent = errorLabels[error.message] || t(error.message);}
  form.onsubmit = async event => {
    event.preventDefault(); if (button.disabled) return;
    button.disabled = true;
    try { const saved = await api('download-settings', {maxActiveDownloads: Number(input.value)}, 'PUT'); input.value = saved.maxActiveDownloads; status.textContent = t('Current limit: {0} simultaneous downloads', {0: saved.maxActiveDownloads}); showNotification(t('Download limit saved.'), 'success'); await refresh(); }
    catch (error) {notify(error);}
    finally {button.disabled = appRole !== 'administrator';}
  };
});

window.addEventListener('mediaseed-settings-open', async () => {
  const form = document.querySelector('#rss-settings-form'); if (!form) return;
  const input = document.querySelector('#rss-refresh-minutes'), button = document.querySelector('#save-rss-settings'), status = document.querySelector('#rss-settings-status');
  button.disabled = true; input.disabled = true; status.textContent = t('Loading RSS settings...');
  try {
    const settings = await api('rss-settings'); input.value = settings.rssRefreshMinutes;
    input.disabled = appRole !== 'administrator'; button.disabled = appRole !== 'administrator';
    status.textContent = appRole !== 'administrator' ? t('Only an administrator can change the RSS interval.') : t('Current interval: {0} minutes', {0: settings.rssRefreshMinutes});
  } catch (error) {status.textContent = errorLabels[error.message] || t(error.message);}
  form.onsubmit = async event => {
    event.preventDefault(); if (button.disabled) return;
    button.disabled = true; input.disabled = true;
    try { const saved = await api('rss-settings', {rssRefreshMinutes: Number(input.value)}, 'PUT'); input.value = saved.rssRefreshMinutes; status.textContent = t('Current interval: {0} minutes', {0: saved.rssRefreshMinutes}); showNotification(t('RSS interval saved.'), 'success'); }
    catch (error) {notify(error);}
    finally {button.disabled = appRole !== 'administrator'; input.disabled = appRole !== 'administrator';}
  };
});
