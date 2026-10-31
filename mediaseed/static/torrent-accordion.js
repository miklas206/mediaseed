/* Inline torrent details share the existing authenticated, lazy detail cache. */
(() => {
  const list = globalThis.MediaSeedList;
  const workspace = document.querySelector('#workspace');
  if (!list || !workspace) return;
  workspace.classList.add('torrent-accordion-ready');
  const expanded = new Map();
  const text = (en, da) => locale().startsWith('da') ? da : en;
  const header = document.querySelector('.torrent-table-header');
  header.replaceChildren();
  for (const [key, label] of [['', ''], ['name', 'Name'], ['state', 'Status'], ['progress', 'Progress'], ['dlspeed', 'Speed'], ['ratio', 'Ratio'], ['', 'Actions']]) {
    const cell = key ? command('', () => {
      const sort = document.querySelector('#sort'), direction = document.querySelector('#sort-direction');
      direction.value = sort.value === key && direction.value === 'asc' ? 'desc' : 'asc';
      sort.value = key; sort.dispatchEvent(new Event('input'));
    }) : element('span');
    cell.className = 'seed-column seed-column-' + (key || (label ? 'actions' : 'selection'));
    cell.dataset.label = label; cell.dataset.sortKey = key; header.append(cell);
  }

  const decorate = list.decorate;
  list.decorate = (item, row) => {
    decorate(item, row);
    const meter = item.querySelector('progress');
    if (meter && !meter.dataset.follows) item.querySelector('.seed-list-percent').textContent = meter.getAttribute('aria-valuetext');
    const speeds = element('div', undefined, 'seed-list-speeds');
    for (const [key, arrow] of [['download', '↓'], ['upload', '↑']]) {
      const metric = item.querySelector('.seed-list-' + key);
      const symbol = element('span', arrow, 'seed-rate-arrow'); symbol.setAttribute('aria-hidden', 'true');
      metric.prepend(symbol); speeds.append(metric);
    }
    item.append(speeds);
    item.querySelector('.seed-list-time').remove();
    item.querySelector('.seed-list-added').remove();

    const toggle = command('', () => {
      if (expanded.has(row.hash)) expanded.delete(row.hash);
      else {
        if (expanded.size >= 100) expanded.delete(expanded.keys().next().value);
        expanded.set(row.hash, {tab: 'overview'});
      }
      update(); if (!panel.hidden) void load();
    });
    toggle.className = 'seed-expand-toggle';
    toggle.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 5 7 7-7 7"/></svg>';
    const panel = element('section', undefined, 'seed-inline-details');
    panel.id = 'seed-inline-' + row.hash;
    panel.setAttribute('aria-label', t('Torrent details') + ': ' + row.name);
    toggle.setAttribute('aria-controls', panel.id);
    const tabs = element('div', undefined, 'seed-inline-tabs');
    tabs.setAttribute('role', 'tablist'); tabs.setAttribute('aria-label', t('Torrent details'));
    const status = element('small', '', 'seed-inline-status'); status.setAttribute('role', 'status');
    const content = element('div', undefined, 'seed-inline-content');
    content.id = panel.id + '-content'; content.setAttribute('role', 'tabpanel');
    const names = [['overview', t('Overview')], ['files', t('Files')], ['connections', t('Trackers')]];
    let cycle = 0, details = {}, loaded = false, overviewLoaded = false;
    const currentTab = () => expanded.get(row.hash)?.tab || 'overview';
    for (const [index, [key, label]] of names.entries()) {
      const button = command(label, () => select(key));
      button.id = panel.id + '-' + key; button.setAttribute('role', 'tab');
      button.setAttribute('aria-controls', content.id);
      button.onkeydown = event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? names.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : names.length - 1)) % names.length;
        select(names[next][0]); tabs.children[next].focus();
      };
      tabs.append(button);
    }
    const footer = element('div', undefined, 'seed-inline-footer');
    const folder = element('span', undefined, 'seed-inline-folder');
    const full = command(text('All details', 'Alle detaljer'), () => richDetails(row, currentTab()));
    const reload = command(t('Refresh'), () => load(true));
    footer.append(folder, reload, full);
    panel.append(tabs, status, content, footer); item.prepend(toggle); item.append(panel);
    panel.addEventListener('keydown', event => {
      if (event.key === 'Escape') {event.preventDefault(); expanded.delete(row.hash); update(); toggle.focus();}
    });
    function update() {
      panel.hidden = !expanded.has(row.hash);
      item.classList.toggle('seed-is-expanded', !panel.hidden);
      toggle.setAttribute('aria-expanded', String(!panel.hidden));
      toggle.setAttribute('aria-label', text(panel.hidden ? 'Show details: ' : 'Hide details: ', panel.hidden ? 'Vis detaljer: ' : 'Skjul detaljer: ') + row.name);
      toggle.title = toggle.getAttribute('aria-label');
      for (const [i, [key]] of names.entries()) {
        tabs.children[i].setAttribute('aria-selected', String(currentTab() === key));
        tabs.children[i].tabIndex = currentTab() === key ? 0 : -1;
      }
      content.setAttribute('aria-labelledby', panel.id + '-' + currentTab());
    }
    function select(key) {
      if (!expanded.has(row.hash)) return;
      expanded.get(row.hash).tab = key; loaded = false;
      update(); paint(); void load();
    }
    function group(title, entries) {
      const section = element('section'), facts = element('dl');
      section.append(element('h4', title), facts);
      for (const [label, value] of entries) {const pair = element('div'); pair.append(element('dt', label), element('dd', value)); facts.append(pair);}
      return section;
    }
    function paint() {
      content.dataset.tab = currentTab();
      content.replaceChildren();
      const props = details.properties || {}, unknown = '—';
      folder.textContent = t('Download folder') + ': ' + (details.downloadFolder ? details.downloadFolder.path : overviewLoaded ? t('Download folder is outside approved storage or unavailable') : unknown);
      if (currentTab() === 'overview') {
        const facts = element('div', undefined, 'seed-inline-facts');
        const goal = seedCleanupGoal(details.cleanup || row.cleanup || {mode: 'never'});
        const added = props.addition_date ?? row.added_on;
        const date = added > 0 ? new Date(added * 1000).toLocaleString(locale(), {dateStyle: 'short', timeStyle: 'short'}) : unknown;
        const seconds = row.seeding_time;
        const time = typeof seconds === 'number' && Number.isFinite(seconds) && seconds >= 0 ? `${Math.floor(seconds / 3600)} h ${Math.floor(seconds % 3600 / 60)} min` : unknown;
        facts.append(
          group(text('Transferred', 'Overført'), [[t('Downloaded'), trackerBytes(props.total_downloaded ?? row.downloaded ?? row.completed)], [t('Uploaded'), trackerBytes(props.total_uploaded ?? row.uploaded)]]),
          group(t('Connections'), [[t('Seeders'), seedPeerCount(row.num_seeds)], [t('Leechers'), seedPeerCount(row.num_leechs)]]),
          group(t('Source'), [[t('Tracker'), seedTracker(row)], [t('Added'), date], [t('Source'), seedSource(row)]]),
          group(t('Seeding'), [[t('Active seeding'), time], [t('Ratio'), trackerNumber(seedCreditRatio({...row, ...details.credit}))], [text('Seeding goal', 'Seedmål'), goal || t('No cleanup goal')]])
        );
        content.append(facts, seedBadges(row));
      } else if (currentTab() === 'files') {
        if (loaded && appRole !== 'viewer') content.append(command(t('Check files'), () => chooseRecheck(row)));
        if (loaded) content.append(detailTable(t('Files'), [['name', t('Name')], ['displaySize', t('Size')], ['displayProgress', t('Progress')]], (details.files || []).map(file => ({...file, displaySize: trackerBytes(file.size), displayProgress: typeof file.progress === 'number' ? `${Math.round(file.progress * 100)}%` : unknown}))));
      } else if (loaded) {
        content.append(detailTable(t('Trackers'), [['host', t('Tracker')], ['status', t('Status')], ['num_peers', t('Peers')]], details.trackers || []), detailTable(t('Peers'), [['ip', 'IP'], ['client', t('Client')], ['progress', t('Progress')]], details.peers || []));
      }
    }
    async function load(force = false) {
      const tab = currentTab(), request = ++cycle;
      status.textContent = t('Fetching details…'); reload.disabled = true;
      try {
        const data = await cachedDetail(row.hash, tab, force);
        if (request !== cycle || panel.hidden || !panel.isConnected || tab !== currentTab()) return;
        details = {...details, ...data}; loaded = true; if (tab === 'overview') overviewLoaded = true; paint();
        status.textContent = '';
      } catch (error) {
        if (request === cycle && panel.isConnected && !panel.hidden) {
          status.replaceChildren(element('span', t(error.message)), command(text('Retry', 'Prøv igen'), () => load(true)));
        }
      } finally {if (request === cycle) reload.disabled = false;}
    }
    update(); paint();
    // Rows are connected by renderTorrents immediately after decoration.
    if (!panel.hidden) queueMicrotask(() => {if (panel.isConnected) void load();});
  };
  const existing = document.querySelectorAll('#torrents>.torrent');
  for (const item of existing) delete item.seedSignature;
  if (existing.length) renderTorrents();
  list.renderSummary();
})();
