/* MediaSeed workspace. Data stays on the existing authenticated APIs. */
function seedPoster(row, large = false) {
  const holder = element('span', undefined, 'seed-poster' + (large ? ' large' : ''));
  holder.setAttribute('aria-hidden', 'true'); holder.textContent = '↓';
  if (row.cover && /^\/api\/covers\/[a-f0-9]{40,64}$/.test(row.cover)) {
    const image = document.createElement('img'); image.src = row.cover; image.alt = ''; image.loading = 'lazy';
    image.onerror = () => image.remove(); holder.append(image);
  }
  return holder;
}
function seedBadges(row) {
  const wrap = element('span', undefined, 'seed-badges'), benefits = row.tracker_benefits;
  if (benefits) {
    for (const [visible, text, tone] of [[benefits.freeleech > 0, `${benefits.freeleech}% Freeleech`, 'free'],
      [benefits.doubleUpload, t('Double upload'), 'double'], [benefits.featured, 'Featured', ''], [benefits.internal, 'Internal', '']]) {
      if (visible) wrap.append(element('span', text, 'seed-badge ' + tone));
    }
  }
  if (row.green_active) wrap.append(element('span', 'Green · 0.5×', 'seed-badge green'));
  return wrap;
}
function seedSource(row) {
  return String(row.category || '').startsWith('MediaSeed-RSS-') ? t('RSS') : t('Manual torrent');
}
function seedEta(value) {
  const seconds = Number(value); if (!Number.isFinite(seconds) || seconds < 0 || seconds >= 8640000) return t('Waiting for estimate');
  return seconds < 60 ? t('Less than a minute') : seconds < 3600 ? `${Math.ceil(seconds / 60)} min` : `${Math.floor(seconds / 3600)} h ${Math.ceil(seconds % 3600 / 60)} min`;
}
function seedSeeding(row) {
  const ratio = Number(row.credited_ratio ?? row.ratio), goal = Number(row.ratio_limit);
  return Number.isFinite(ratio) ? `${t('Credited ratio')} ${trackerNumber(ratio)}${goal >= 1 ? ' / ' + trackerNumber(goal) : ''}` : t('Ratio unknown');
}
function seedTorrent(row, index) {
  const item = element('article', undefined, 'torrent seed-rich-row'); item.dataset.state = state(row); item.dataset.hash = row.hash;
  const identity = element('div', undefined, 'seed-identity');
  const heading = command(row.name, () => showDetails(row)); heading.className = 'torrent-name'; heading.title = row.name;
  const info = element('small', `${trackerBytes(row.size)} · ${row.tracker_benefits?.tracker || t('Tracker unknown')} · ${seedSource(row)}`, 'seed-subline');
  identity.append(heading, info, seedBadges(row));
  const sortKey = document.querySelector('#sort').value;
  const hints = {
    added_on: ['Added', row.added_on > 0 ? new Date(row.added_on * 1000).toLocaleString(locale()) : '—'],
    completion_on: ['Completed', row.completion_on > 0 ? new Date(row.completion_on * 1000).toLocaleString(locale()) : '—'],
    uploaded: ['Uploaded', trackerBytes(row.uploaded)],
    num_seeds: ['Seeders', row.num_seeds ?? '—'],
    ratio: ['Credited ratio', trackerNumber(row.credited_ratio ?? row.ratio)],
  };
  if (hints[sortKey]) identity.append(element('small', `${t(hints[sortKey][0])}: ${hints[sortKey][1]}`, 'seed-subline'));
  const transfer = element('div', undefined, 'seed-progress-cell');
  const progress = element('progress'); progress.max = 1; progress.value = Math.max(0, Math.min(1, Number(row.progress || 0))); progress.setAttribute('aria-label', t('Download progress'));
  if (state(row) === 'seeding' && Number(row.ratio_limit) >= 1) { progress.value = Math.max(0, Math.min(1, Number(row.credited_ratio ?? row.ratio) / Number(row.ratio_limit) || 0)); progress.setAttribute('aria-label', t('Seeding progress')); }
  transfer.append(element('strong', state(row) === 'seeding' ? seedSeeding(row) : `${Math.round(progress.value * 100)}% · ${seedEta(row.eta)}`), progress,
    element('small', state(row) === 'seeding' ? `${t('Active seeding')} ${(Number(row.seeding_time || 0) / 3600).toFixed(1)} h` : `${trackerBytes(row.completed ?? Number(row.size || 0) * progress.value)} / ${trackerBytes(row.size)}`));
  const activity = element('div', undefined, 'seed-activity');
  activity.append(element('span', torrentStatus(row.state), 'torrent-state'), element('small', `↓ ${bytes(row.dlspeed || 0)}/s · ↑ ${bytes(row.upspeed || 0)}/s`));
  const actions = element('div', undefined, 'actions');
  if (appRole !== 'viewer') { const stopped = state(row) === 'stopped'; const toggle = command(stopped ? '▶' : 'Ⅱ', () => torrentAction(row, stopped ? 'start' : 'stop')); toggle.title = stopped ? t('Start') : t('Stop'); toggle.setAttribute('aria-label', toggle.title + ': ' + row.name); toggle.disabled = stopped && !downloadsEnabled; actions.append(toggle); }
  const menu = element('details', undefined, 'torrent-menu'), trigger = element('summary', '⋯'); trigger.setAttribute('aria-label', t('Actions'));
  const commands = element('div', undefined, 'torrent-menu-content'); commands.append(command(t('Details'), () => showDetails(row)));
  if (appRole !== 'viewer') commands.append(command(t('Check files'), () => chooseRecheck(row)), command(t('Remove'), () => chooseRemoval(row)));
  menu.append(trigger, commands); actions.append(menu);
  item.append(seedPoster(row), identity, transfer, activity, actions); return item;
}
let seedDetailRequest = 0;
async function richDetails(row) {
  const request = ++seedDetailRequest; selectedHash = row.hash;
  document.querySelector('#detail-name').textContent = row.name;
  document.querySelector('#detail-message').textContent = '';
  const target = document.querySelector('#detail-data'); target.replaceChildren(element('p', t('Fetching details…'), 'muted'));
  document.querySelector('#policy-form input').value = Number(row.ratio_limit) >= 1 ? row.ratio_limit : 1;
  document.querySelector('#policy-form').hidden = true;
  if (!dialog.open) dialog.showModal();
  try {
    const details = await api(`torrents/${row.hash}/details`); if (request !== seedDetailRequest || !dialog.open) return;
    const credited = {...row, ...details.credit}, hero = element('div', undefined, 'seed-detail-hero');
    const summary = element('div'); summary.append(element('span', torrentStatus(row.state), 'seed-badge'), seedBadges(credited), element('p', `${trackerBytes(row.size)} · ${seedSource(row)}`, 'muted'));
    hero.append(seedPoster(row, true), summary);
    const tabs = element('div', undefined, 'seed-detail-tabs'); tabs.setAttribute('role', 'tablist'); tabs.setAttribute('aria-label', t('Torrent details'));
    const content = element('div', undefined, 'seed-detail-content'); content.id = 'seed-detail-tabpanel'; content.setAttribute('role','tabpanel'); target.replaceChildren(hero, tabs, content);
    const overview = () => {
      const list = element('dl', undefined, 'seed-detail-facts');
      for (const [label, value] of [[t('Downloaded'), trackerBytes(details.properties.total_downloaded)], [t('Uploaded'), trackerBytes(details.properties.total_uploaded)], [t('Source'), seedSource(row)], [t('Added'), details.properties.addition_date ? new Date(details.properties.addition_date * 1000).toLocaleString(locale()) : '—'], [t('Download folder'), details.downloadFolder ? `${t(details.downloadFolder.label)} · ${details.downloadFolder.path}` : t('Download folder is outside approved storage or unavailable')]]) list.append(element('dt', label), element('dd', value));
      content.append(list);
    };
    const panels = [ ['Overview', overview], ['Seeding', () => { content.append(trackerCreditView(credited)); renderCleanupDetails(details.cleanup, row, content); }],
      ['Files', () => {
        if (appRole !== 'viewer') content.append(command(t('Check files'), () => chooseRecheck(row)));
        content.append(element('p', t('File progress is the last client observation. Check files to verify existing data and detect missing or damaged pieces.'), 'muted'), detailTable(t('Files'), [['name', t('Name')], ['displaySize', t('Size')], ['displayProgress', t('Progress')]], details.files.map(file => ({...file, displaySize: trackerBytes(file.size), displayProgress: typeof file.progress === 'number' ? `${Math.round(file.progress * 100)}%` : '—'})) ));
      }],
      ['Connections', () => content.append(detailTable(t('Trackers'), [['host', 'Tracker'], ['status', t('Status')], ['num_peers', 'Peers']], details.trackers), detailTable('Peers', [['ip', 'IP'], ['client', t('Client')], ['progress', t('Progress')]], details.peers))],
      ['Activity', () => { content.append(element('p', torrentStatus(row.state)), element('p', t('VPN availability is checked before starting. Historical controller events are available in MediaHub Logs.'), 'muted')); renderCleanupDetails(details.cleanup, row, content); }]];
    function select(index) {
      content.replaceChildren(); panels[index][1]();
      content.setAttribute('aria-labelledby', 'seed-detail-tab-' + index);
      [...tabs.children].forEach((button, i) => {button.setAttribute('aria-selected', String(i === index)); button.tabIndex = i === index ? 0 : -1;});
      document.querySelector('#policy-form').hidden = index !== 1 || appRole === 'viewer' || (details.cleanup?.mode && details.cleanup.mode !== 'never');
    }
    panels.forEach(([name], index) => { const button = command(t(name), () => select(index)); button.id = 'seed-detail-tab-' + index; button.setAttribute('aria-controls','seed-detail-tabpanel'); button.setAttribute('role', 'tab'); button.onkeydown = event => { if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return; event.preventDefault(); const next = (index + (event.key === 'ArrowRight' ? 1 : -1) + panels.length) % panels.length; select(next); tabs.children[next].focus(); }; tabs.append(button); }); select(0);
  } catch (error) {if (request === seedDetailRequest && dialog.open) target.replaceChildren(element('p', t(error.message), 'muted'));}
}
function richTrackerSummary(rows) {
  const target = document.querySelector('#seed-accounts'); if (!target) return;
  target.replaceChildren();
  for (const row of rows) { const card = element('div', undefined, 'seed-account'); card.append(element('strong', row.name));
    card.append(element('span', row.stats ? `${t('Ratio')} ${trackerNumber(row.stats.ratio)} · ${t('Buffer')} ${trackerBytes(row.stats.buffer)} · ${t('Warnings')} ${trackerNumber(row.stats.warnings)}` : t('Account data unavailable')));
    if (row.checkedAt) card.append(element('small', new Date(row.checkedAt * 1000).toLocaleTimeString(locale()), 'muted')); target.append(card); }
}
async function richPreview() {
  const target = document.querySelector('#seed-add-preview'); target.replaceChildren(element('p', t('Reading torrent information…'), 'muted'));
  const generation = ++richPreview.request;
  try {
    const file = torrentFiles[0]?.file, mode = addForm.elements.namedItem('mode').value;
    let response;
    if (mode === 'file') {if (!file || file.size > 4 * 1024 * 1024) throw new Error(t('Choose a torrent file up to 4 MiB')); response = await fetch('/api/torrent-preview', {method: 'POST', headers: {'Content-Type':'application/x-bittorrent', 'X-MediaSeed-CSRF':csrf}, body:file});}
    else response = await fetch('/api/torrent-preview', {method:'POST', headers:{'Content-Type':'application/json','X-MediaSeed-CSRF':csrf}, body:JSON.stringify({magnet:document.querySelector('#add-magnet').value})});
    const result = await response.json(); if (generation !== richPreview.request || !addDialog.open) return;
    if (!response.ok) throw new Error(t('Torrent information unavailable'));
    target.replaceChildren(element('strong', result.name || t('Torrent information')), element('p', `${t('Size')}: ${result.size === null ? t('Unknown') : trackerBytes(result.size)}`, 'muted'));
    for (const estimate of result.estimates) target.append(element('p', `${estimate.name} · ${t('Estimated account ratio')}: ${trackerNumber(estimate.current)} → ${estimate.projected === null ? '∞' : trackerNumber(estimate.projected)}`, estimate.projected !== null && estimate.projected <= .5 ? 'seed-ratio-warning' : ''));
    target.append(element('small', t('Estimate excludes future uploads and other downloads. Unknown freeleech counts as a normal download.'), 'muted'));
    if (!result.estimates.length) target.append(element('p', t('Ratio estimate needs a known torrent size and fresh tracker account data.'), 'muted'));
  } catch (error) {if (generation === richPreview.request) target.replaceChildren(element('p', t(error.message), 'muted'));}
}
richPreview.request = 0;
async function richFeedHistory(id, name) {
  const request = ++richFeedHistory.request;
  const box = document.querySelector('#seed-feed-history'); box.replaceChildren(element('h2', name), element('p', t('Loading…'), 'muted'));
  try { const result = await api(`feeds/${id}/history`); if (request !== richFeedHistory.request) return; box.replaceChildren(element('h2', name));
    if (!result.entries.length) box.append(element('p', t('No feed observations yet. Existing downloads are preserved.'), 'muted'));
    for (const row of result.entries) { const line = element('div', undefined, 'seed-history-row'); line.append(element('strong', row.title || t('RSS entry')), element('span', t('RSS status: ' + row.status)), element('small', new Date(row.created * 1000).toLocaleString(locale()), 'muted')); box.append(line); }
  } catch (error) {if (request === richFeedHistory.request) box.append(element('p', t(error.message), 'muted'));}
}
richFeedHistory.request = 0;
