/* Compact seedbox view. All commands reuse the existing authenticated flows. */
(() => {
  const downloads = document.querySelector('#downloads');
  const flow = document.querySelector('#download-flow');
  if (!downloads || !flow) return;
  const workspace = document.querySelector('#workspace');
  workspace.dataset.torrentLayout = 'list';
  try { localStorage.removeItem('mediaseed.torrentLayout'); } catch {}

  const icon = path => `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${path}</svg>`;
  const heading = downloads.querySelector('.downloads-heading');
  const title = heading.querySelector('h2');
  const total = element('span', undefined, 'seed-list-total');
  const actions = heading.querySelector('.actions'); actions.classList.add('seed-list-actions');
  actions.append(document.querySelector('#new-torrent'));
  title.append(total);

  // Keep every sort, source and paging option; place infrequent choices together.
  const filters = downloads.querySelector('.download-filters');
  const toolbar = filters.querySelector('.toolbar');
  const trackerLabel = element('label'); const trackerSelect = document.createElement('select');
  trackerSelect.id = 'tracker-filter'; trackerLabel.append(trackerSelect); toolbar.append(trackerLabel);
  const more = element('details', undefined, 'seed-list-options');
  const moreTitle = element('summary'); const moreFields = element('div', undefined, 'seed-list-option-fields');
  for (const id of ['sort-direction', 'page-size']) moreFields.append(document.querySelector('#' + id).closest('label'));
  moreFields.append(document.querySelector('#refresh'));
  more.append(moreTitle, moreFields); filters.append(more);
  for (const id of ['search', 'filter', 'tracker-filter', 'source-filter', 'sort']) toolbar.append(document.querySelector('#' + id).closest('label'));
  trackerSelect.onchange = () => {torrentPage = 1; renderTorrents();};
  heading.append(filters, actions);

  const columns = [
    ['name', 'Name'], ['state', 'Status'], ['progress', 'Progress'], ['dlspeed', 'Download'],
    ['upspeed', 'Upload'], ['ratio', 'Ratio'], ['seeding_time', 'Seeding time'],
    ['added_on', 'Added'], ['', 'Actions'],
  ];
  const tableHeader = downloads.querySelector('.torrent-table-header');
  tableHeader.removeAttribute('aria-hidden'); tableHeader.replaceChildren();
  for (const [key, label] of columns) {
    const cell = key ? command('', () => {
      const sort = document.querySelector('#sort'), direction = document.querySelector('#sort-direction');
      direction.value = sort.value === key && direction.value === 'asc' ? 'desc' : 'asc';
      sort.value = key; sort.dispatchEvent(new Event('input'));
    }) : element('span');
    cell.className = 'seed-column seed-column-' + (key || label.toLowerCase());
    cell.dataset.label = label; cell.dataset.sortKey = key;
    if (key) cell.type = 'button'; tableHeader.append(cell);
  }

  // Four reference hops; motion represents measured client traffic, not disk I/O.
  const route = flow.querySelector('.download-route');
  const nodes = route.querySelectorAll('.download-route-node');
  nodes[2].querySelector('.route-icon').innerHTML = icon('<rect x="3" y="3" width="18" height="5" rx="2"/><rect x="3" y="10" width="18" height="5" rx="2"/><rect x="3" y="17" width="18" height="5" rx="2"/>');
  nodes[3].querySelector('.route-icon').innerHTML = nodes[2].querySelector('.route-icon').innerHTML;
  const metrics = element('div', undefined, 'seed-flow-metrics');
  const down = element('div', undefined, 'seed-flow-metric seed-rate-download');
  const up = element('div', undefined, 'seed-flow-metric seed-rate-upload');
  const ratio = command('', () => document.querySelector('[data-view=trackers]').click());
  const storage = command('', () => document.querySelector('#storage').scrollIntoView({block:'center', behavior:'instant'}));
  ratio.className = 'seed-flow-metric'; storage.className = 'seed-flow-metric';
  metrics.append(down, up, ratio, storage); flow.append(metrics);
  // Retain detailed account, queue and client information without repeating the banner.
  const overview = element('details', undefined, 'seed-overview-details');
  const overviewTitle = element('summary'); overview.append(overviewTitle);
  const statusCards = downloads.querySelector('.download-status-cards'); downloads.append(overview); overview.append(statusCards, downloads.querySelector('.automatic-activity'), downloads.querySelector('.client-details'));

  function syncTrackers() {
    const current = trackerSelect.value;
    const names = [...new Set(torrents.map(seedTracker))].sort((a,b) => a.localeCompare(b));
    // A temporarily missing selected tracker must not reset a user's filter.
    if (current && !names.includes(current)) names.push(current);
    const signature = JSON.stringify([names, locale()]);
    if (trackerSelect.dataset.signature === signature) return;
    trackerSelect.replaceChildren(new Option(t('All trackers'), ''), ...names.map(name => new Option(name, name)));
    trackerSelect.value = current; trackerSelect.dataset.signature = signature;
    trackerSelect.setAttribute('aria-label', t('Tracker'));
  }

  function metric(item, key, label, value, extra = '') {
    const cell = element('span', undefined, 'seed-list-metric seed-list-' + key + (extra ? ' ' + extra : ''));
    cell.append(element('small', t(label), 'seed-metric-label'), element('span', value, 'seed-metric-value'));
    cell.title = t(label) + ': ' + value; item.append(cell); return cell;
  }
  function decorate(item, row) {
    const validRate = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? trackerBytes(value) + '/s' : '—';
    metric(item, 'download', 'Download', validRate(row.dlspeed), 'seed-live-metric');
    metric(item, 'upload', 'Upload', validRate(row.upspeed), 'seed-live-metric');
    const ratioValue = seedCreditRatio(row);
    metric(item, 'ratio', 'Credited upload ratio', trackerNumber(ratioValue), ratioValue >= 1 ? 'seed-ratio-positive' : '');
    const seconds = row.seeding_time;
    const time = typeof seconds === 'number' && Number.isFinite(seconds) && seconds >= 0
      ? seconds >= 86400 ? `${Math.floor(seconds/86400)} d ${Math.floor(seconds%86400/3600)} h` : seconds >= 3600 ? `${Math.floor(seconds/3600)} h ${Math.floor(seconds%3600/60)} min` : `${Math.floor(seconds/60)} min` : '—';
    metric(item, 'time', 'Active seeding', time);
    metric(item, 'added', 'Added', row.added_on > 0 ? new Date(row.added_on*1000).toLocaleString(locale(), {dateStyle:'short',timeStyle:'short'}) : '—');
    const progress = item.querySelector('progress');
    item.querySelector('.seed-progress-cell').append(element('span', progress?.dataset.follows && progress.dataset.follows !== 'download' ? t('Goal {0}%', {0:Math.round(progress.value*100)}) : `${Math.round((progress?.value || 0)*100)}%`, 'seed-list-percent'));
    const fileButton = command('', () => richDetails(row, 'files')); fileButton.type = 'button'; fileButton.className = 'seed-files-button';
    fileButton.innerHTML = icon('<path d="M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>');
    fileButton.title = t('Files'); fileButton.setAttribute('aria-label', t('Files') + ': ' + row.name);
    item.querySelector('.actions').prepend(fileButton);
  }

  function quantity(text) {
    const value = element('strong');
    const split = text.lastIndexOf(' ');
    if (split < 0) value.textContent = text;
    else value.append(document.createTextNode(text.slice(0, split) + ' '), element('span', text.slice(split + 1), 'seed-metric-unit'));
    return value;
  }

  function renderSummary() {
    const active = flow.dataset.active === 'true';
    const age = Date.now() - lastTorrentSample;
    const traffic = active && torrentSnapshotValid && age >= 0 && age < 30000 ? torrentTraffic() : null;
    const format = value => typeof value === 'number' && Number.isFinite(value) ? trackerBytes(value) + '/s' : '—';
    down.replaceChildren(element('small', t('Download')), quantity(format(traffic?.dl_info_speed)));
    up.replaceChildren(element('small', t('Upload')), quantity(format(traffic?.up_info_speed)));
    flow.querySelector('.download-flow-eyebrow').textContent = t('Protected traffic');
    if (active) document.querySelector('#download-flow-status').textContent = t('VPN verified') + ' · ' + document.querySelector('#connection-country').textContent;
    const account = appRole === 'viewer' ? null : (globalThis.MediaSeedTrackerRows || []).find(row => row.status === 'ok' && row.stats);
    ratio.replaceChildren(element('small', t('Ratio')), element('strong', trackerNumber(account?.stats?.ratio)));
    ratio.hidden = appRole === 'viewer'; ratio.title = account ? account.name + ' · ' + t('Tracker account totals') : t('Account data unavailable');
    const capacity = globalThis.MediaSeedStorage;
    const locations = capacity?.locations || (capacity ? [capacity] : []);
    const location = locations.find(row => row.id === capacity?.defaultLocation) || locations.find(row => row.id === 'local') || locations[0];
    const free = location?.available !== false ? location?.freeBytes : undefined;
    storage.replaceChildren(element('small', t('Free space')), quantity(trackerBytes(free)));
    storage.title = location ? t(location.label || 'Storage') + ' · ' + t('Free space') : t('Storage unavailable');
    storage.disabled = document.querySelector('#storage').hidden;
    title.replaceChildren(document.createTextNode(t('Torrents') + ' '), total);
    total.textContent = '(' + torrents.filter(row => !pendingRemovals.has(row.hash)).length + ')';
    moreTitle.textContent = t('Display options'); overviewTitle.textContent = t('Account, queue and activity');
    document.querySelector('#search').placeholder = t('Search torrents…');
    document.querySelector('#filter option[value=all]').textContent = t('All statuses');
    document.querySelector('#source-filter option[value=all]').textContent = t('All sources');
    const sort = document.querySelector('#sort').value, dir = document.querySelector('#sort-direction').value;
    for (const cell of tableHeader.children) {
      const selected = !!cell.dataset.sortKey && cell.dataset.sortKey === sort;
      cell.textContent = t(cell.dataset.label) + (selected ? dir === 'asc' ? ' ↑' : ' ↓' : '');
      if (cell.dataset.sortKey) cell.setAttribute('aria-label', t('Sort by {0}', {0:t(cell.dataset.label)}) + (selected ? ' · ' + t(dir === 'asc' ? 'Ascending' : 'Descending') : ''));
    }
    syncTrackers();
  }
  globalThis.MediaSeedList = {decorate, syncTrackers, renderSummary};
  for (const control of [document.querySelector('#sort'), document.querySelector('#sort-direction')]) control.addEventListener('change', renderSummary);
  new MutationObserver(renderSummary).observe(document.documentElement, {attributes:true, attributeFilter:['lang']});
  renderSummary();
})();
