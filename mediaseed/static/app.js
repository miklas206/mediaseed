let csrf = '';
let needsSetup = false;
let torrents = [];
let selectedHash = '';
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
const errorLabels = {'Invalid credentials': 'Brugernavn eller adgangskode er forkert.', 'Try again later': 'For mange loginforsøg. Prøv igen om lidt.', 'Torrent client is not configured': 'Torrentklienten er endnu ikke tilsluttet.', 'Sign in required': 'Log ind for at fortsætte.'};
async function api(path, body, method) {
  const response = await fetch('/api/' + path, {method: method || (body ? 'POST' : 'GET'), headers: {'Content-Type': 'application/json', 'X-MediaSeed-CSRF': csrf}, ...(body ? {body: JSON.stringify(body)} : {})});
  const result = await response.json();
  if (!response.ok) throw Object.assign(new Error(typeof result.detail === 'string' ? result.detail : 'Kontrollér felterne og prøv igen.'), {status: response.status});
  return result;
}
function notify(error) { showNotification(errorLabels[error.message] || error.message); }
function element(tag, text, className) { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (className) node.className = className; return node; }
function bytes(value) { const number = Number(value || 0); for (const [divisor, unit] of [[1073741824, 'GiB'], [1048576, 'MiB'], [1024, 'KiB']]) if (number >= divisor) return `${(number / divisor).toFixed(1)} ${unit}`; return `${number} B`; }
function state(row) { if (/error|missing/i.test(row.state)) return 'error'; if (/stopped|paused/i.test(row.state)) return 'stopped'; if (Number(row.progress) >= 1) return 'seeding'; return 'downloading'; }
function command(label, handler) { const button = element('button', label); button.type = 'button'; button.onclick = async () => { button.disabled = true; try { await handler(); } catch (error) { notify(error); } finally { button.disabled = false; } }; return button; }
async function torrentAction(row, action) {
  if (action === 'remove' && !confirm(`Fjern ${row.name}? Downloadede filer bevares.`)) return;
  try { await api('torrents/' + action, {torrent_hash: row.hash}); }
  catch (error) { if (error.status !== 409) throw error; if (!confirm('Seedingmålet er ikke nået: ratio under 1 og mindre end 49 timer. Vil du alligevel fortsætte?')) return; await api('torrents/' + action, {torrent_hash: row.hash, confirm_early: true}); }
  await refresh();
}
function renderTorrents() {
  const search = document.querySelector('#search').value.toLocaleLowerCase(); const filter = document.querySelector('#filter').value; const sort = document.querySelector('#sort').value;
  const rows = torrents.filter(row => String(row.name).toLocaleLowerCase().includes(search) && (filter === 'all' || state(row) === filter));
  rows.sort((first, second) => sort === 'name' ? String(first.name).localeCompare(String(second.name)) : Number(second[sort]) - Number(first[sort]));
  document.querySelector('#count').textContent = `${rows.length} af ${torrents.length} torrents`;
  const target = document.querySelector('#torrents'); target.replaceChildren(); if (!rows.length) target.append(element('p', torrents.length ? 'Ingen matchende torrents' : 'Ingen torrents', 'muted'));
  for (const row of rows) {
    const item = element('article', undefined, 'torrent'); const progress = element('progress'); progress.max = 1; progress.value = Math.max(0, Math.min(1, Number(row.progress || 0))); progress.setAttribute('aria-label', 'Fremdrift');
    const status = element('p', `${Math.round(Number(row.progress || 0) * 100)} % · ${row.state || ''} · ${bytes(row.size)} · Ratio ${Number(row.ratio || 0).toFixed(2)}`);
    const speeds = element('p', `Download ${bytes(row.dlspeed)}/s · Upload ${bytes(row.upspeed)}/s · Seeding ${(Number(row.seeding_time || 0) / 3600).toFixed(1)} timer`);
    const actions = element('div', undefined, 'actions'); actions.append(command('Detaljer', () => showDetails(row)), command('Stop', () => torrentAction(row, 'stop')), command('Fjern torrent', () => torrentAction(row, 'remove')));
    item.append(element('h2', row.name), progress, status, speeds, actions); target.append(item);
  }
}
async function refresh() {
  showNotification(''); try { torrents = await api('torrents'); renderTorrents(); } catch (error) { notify(error); }
  try { const transfer = await api('transfer'); document.querySelector('#transfer').textContent = `Download ${bytes(transfer.dl_info_speed)}/s · Upload ${bytes(transfer.up_info_speed)}/s · Klient ${transfer.connection_status || 'ukendt'}`; } catch { document.querySelector('#transfer').textContent = 'Klient ikke tilsluttet'; }
  const storage = document.querySelector('#storage'); storage.hidden = false;
  try { const capacity = await api('storage'); storage.textContent = `Lokal storage: ${bytes(capacity.freeBytes)} ledig af ${bytes(capacity.totalBytes)}${capacity.lowSpace ? ' · Lav diskplads' : ''}${capacity.writable ? '' : ' · Ikke skrivbar'}`; } catch { storage.textContent = 'Lokal storage utilgængelig'; }
}
function detailTable(title, columns, rows) {
  const section = element('section'); section.append(element('h3', title)); if (!rows.length) { section.append(element('p', 'Ingen data', 'muted')); return section; }
  const table = element('table'); const heading = element('tr'); for (const [, label] of columns) heading.append(element('th', label)); const head = element('thead'); head.append(heading); table.append(head);
  const body = element('tbody'); for (const row of rows) { const line = element('tr'); for (const [key] of columns) line.append(element('td', String(row[key] ?? '—'))); body.append(line); } table.append(body); const wrap = element('div', undefined, 'table-wrap'); wrap.append(table); section.append(wrap); return section;
}
async function showDetails(row) {
  selectedHash = row.hash; document.querySelector('#detail-name').textContent = row.name; document.querySelector('#detail-message').textContent = ''; const target = document.querySelector('#detail-data'); target.replaceChildren(element('p', 'Henter detaljer…'));
  document.querySelector('#policy-form input').value = Number(row.ratio_limit) >= 1 ? row.ratio_limit : 1; dialog.showModal();
  try { const details = await api(`torrents/${row.hash}/details`); target.replaceChildren(element('p', `Downloadet ${bytes(details.properties.total_downloaded)} · Uploadet ${bytes(details.properties.total_uploaded)} · Seeding ${(Number(details.properties.seeding_time || 0) / 3600).toFixed(1)} timer`)); target.append(detailTable('Filer', [['name', 'Navn'], ['size', 'Bytes'], ['progress', 'Fremdrift']], details.files), detailTable('Trackere', [['host', 'Tracker'], ['status', 'Status'], ['num_peers', 'Peers']], details.trackers), detailTable('Peers', [['ip', 'IP'], ['client', 'Klient'], ['progress', 'Fremdrift']], details.peers)); } catch (error) { target.replaceChildren(element('p', error.message)); }
}
async function loadSources(kind) {
  const rows = await api(kind); const target = document.querySelector(kind === 'feeds' ? '#feed-list' : '#tracker-list'); target.replaceChildren(); if (!rows.length) target.append(element('p', kind === 'feeds' ? 'Ingen feeds' : 'Ingen konti', 'muted'));
  for (const row of rows) {
    const item = element('article', undefined, 'source-row'); item.append(element('h2', row.name), element('p', kind === 'feeds' ? `${row.folder || '/downloads'} · Ratio ${row.ratio} · Pauset` : 'NordicBytes · Ikke synkroniseret', 'muted')); const actions = element('div', undefined, 'actions');
    actions.append(command('Rediger', () => { const form = document.querySelector(kind === 'feeds' ? '#feed-form' : '#tracker-form'); form.reset(); for (const [key, value] of Object.entries(row)) { const input = form.elements.namedItem(key); if (input) input.value = key === 'start_at' && value ? new Date(new Date(value).getTime() - new Date(value).getTimezoneOffset() * 60000).toISOString().slice(0, 16) : value ?? ''; } form.querySelector('input[name="name"]').focus(); }), command('Fjern', async () => { if (!confirm(`Fjern ${row.name}? Filer og torrents bevares.`)) return; await api(`settings/${kind}/${row.id}`, undefined, 'DELETE'); await loadSources(kind); })); item.append(actions); target.append(item);
  }
}
for (const [selector, kind] of [['#feed-form', 'feeds'], ['#tracker-form', 'trackers']]) {
  const form = document.querySelector(selector); form.onsubmit = async event => { event.preventDefault(); const body = Object.fromEntries(new FormData(form)); const identifier = body.id; delete body.id; if (kind === 'feeds') { body.ratio = Number(body.ratio); body.start_at = body.start_at ? new Date(body.start_at).toISOString() : null; } try { await api(kind + (identifier ? '/' + identifier : ''), body, identifier ? 'PUT' : 'POST'); form.reset(); form.elements.namedItem('id').value = ''; await loadSources(kind); showNotification('Gemt', 'success'); } catch (error) { notify(error); } };
  form.addEventListener('reset', () => { form.elements.namedItem('id').setAttribute('value', ''); });
}
for (const button of document.querySelectorAll('[data-view]')) button.onclick = async () => { message.textContent = ''; for (const other of document.querySelectorAll('[data-view]')) other.setAttribute('aria-pressed', String(other === button)); for (const section of document.querySelectorAll('.view')) section.hidden = section.id !== button.dataset.view; try { if (button.dataset.view === 'feeds' || button.dataset.view === 'trackers') await loadSources(button.dataset.view); else if (button.dataset.view === 'vpn') { const runtime = await api('runtime'); document.querySelector('#vpn-status').textContent = runtime.vpn === 'unverified' ? 'Ikke verificeret' : 'Ukendt'; } } catch (error) { notify(error); } };
document.querySelector('#close-details').onclick = () => dialog.close();
document.querySelector('#policy-form').onsubmit = async event => { event.preventDefault(); try { await api(`torrents/${selectedHash}/policy`, {ratio: Number(event.target.elements.namedItem('ratio').value)}); document.querySelector('#detail-message').textContent = 'Seedingmål gemt'; } catch (error) { document.querySelector('#detail-message').textContent = error.message; } };
for (const selector of ['#search', '#filter', '#sort']) document.querySelector(selector).addEventListener('input', renderTorrents);
function showWorkspace() { document.body.classList.add('signed-in'); document.querySelector('#identity').hidden = true; document.querySelector('#workspace').hidden = false; document.querySelector('#logout').hidden = false; refresh(); }
document.querySelector('#login').onsubmit = async event => { event.preventDefault(); const body = Object.fromEntries(new FormData(event.target)); try { if (needsSetup) { await api('auth/setup', body); needsSetup = false; } csrf = (await api('auth/login', body)).csrf; event.target.reset(); showWorkspace(); } catch (error) { notify(error); } };
document.querySelector('#refresh').onclick = refresh;
document.querySelector('#logout').onclick = async () => { try { await api('auth/logout', {}); location.reload(); } catch (error) { notify(error); } };
(async () => { try { needsSetup = (await api('auth/status')).needsSetup; document.querySelector('#identity-title').textContent = needsSetup ? 'En frisk begyndelse.' : 'Velkommen tilbage.'; document.querySelector('#identity-subtitle').textContent = needsSetup ? 'Opret din lokale MediaSeed-administrator.' : 'Log ind på MediaSeed.'; document.querySelector('#login-submit').textContent = needsSetup ? 'Opret administrator' : 'Log ind'; if (!needsSetup) { try { csrf = (await api('auth/me')).csrf; showWorkspace(); } catch {} } } catch (error) { notify(error); } })();