/* Explicit, identity-bound editing of one torrent's cleanup rule. */
function cleanupGoal(policy) {
  const goals = {
    time: t('{0} hours of active seeding', {0: policy.hours}),
    ratio: t('credited upload ratio {0}', {0: policy.ratio}),
    both: t('{0} hours of active seeding AND credited ratio {1}', {0: policy.hours, 1: policy.ratio}),
    either: t('{0} hours of active seeding OR credited ratio {1}', {0: policy.hours, 1: policy.ratio})
  };
  return goals[policy.mode] || '';
}

async function openTorrentCleanup(row, onSaved) {
  if (appRole === 'viewer' || document.querySelector('#torrent-cleanup')) return;
  const editor = element('dialog', undefined, 'torrent-cleanup-dialog');
  editor.id = 'torrent-cleanup'; editor.setAttribute('aria-labelledby', 'torrent-cleanup-title');
  const header = element('div', undefined, 'heading');
  const title = element('h2', t('Cleanup rule')); title.id = 'torrent-cleanup-title';
  const close = command(t('Close'), () => editor.close());
  header.append(title, close);
  const name = element('p', row.name, 'cleanup-torrent-name');
  const status = element('p', t('Loading cleanup rule…'), 'muted'); status.setAttribute('role', 'status');
  const form = element('form'); form.hidden = true;
  function selectField(label, options) {
    const field = element('label', t(label)), select = element('select');
    for (const [value, text] of options) {const option = element('option', t(text)); option.value = value; select.append(option);}
    field.append(select); form.append(field); return [field, select];
  }
  const [, mode] = selectField('When should cleanup happen?', [
    ['never', 'Never · keep torrent and files'], ['time', 'After active seeding time'],
    ['ratio', 'After credited upload ratio'], ['both', 'When BOTH time and ratio are reached'],
    ['either', 'When EITHER time or ratio is reached']
  ]);
  function numberField(label, min, max, value) {
    const field = element('label', t(label)), input = element('input');
    input.type = 'number'; input.min = min; input.max = max; input.step = 'any'; input.value = value; input.required = true;
    field.append(input); form.append(field); return [field, input];
  }
  const [hoursField, hours] = numberField('Active seeding time (hours)', 1 / 60, 87600, 50);
  const [ratioField, ratio] = numberField('Credited upload ratio', 1, 1000, 1);
  const [actionField, action] = selectField('What should be removed?', [
    ['torrent', 'Remove torrent only · keep files'], ['files', 'Remove torrent and files']
  ]);
  const explanation = element('p', t('Only active seeding counts. Cleanup waits for connected downloaders. MediaSeed must be running.'), 'muted');
  const summary = element('p', undefined, 'cleanup-rule-summary'); summary.setAttribute('aria-live', 'polite');
  const immediate = element('p', t('If the goal is already reached, cleanup can happen at the next check after saving.'), 'muted');
  const confirmation = element('label', undefined, 'cleanup-delete-confirmation');
  const consent = element('input'); consent.type = 'checkbox';
  confirmation.append(consent, element('span', t('I understand that downloaded files will be deleted automatically. This cannot be undone.')));
  const error = element('p', undefined, 'cleanup-warning'); error.setAttribute('role', 'alert');
  const footer = element('div', undefined, 'cleanup-editor-footer');
  const cancel = command(t('Cancel'), () => editor.close()), save = element('button', t('Save cleanup rule'), 'primary'); save.type = 'submit';
  footer.append(cancel, save); form.append(explanation, summary, immediate, confirmation, error, footer);
  const retry = command(t('Try again'), () => load()); retry.hidden = true;
  editor.append(header, name, status, retry, form); document.body.append(editor);
  let busy = false, identity;
  editor.addEventListener('cancel', event => {if (busy) event.preventDefault();});
  editor.addEventListener('close', () => editor.remove(), {once: true});
  function policy() {
    return {mode: mode.value,
      hours: ['ratio', 'never'].includes(mode.value) ? 50 : Number(hours.value),
      ratio: ['time', 'never'].includes(mode.value) ? 1 : Number(ratio.value),
      delete_files: mode.value !== 'never' && action.value === 'files'};
  }
  function paint() {
    const value = policy(), active = value.mode !== 'never';
    for (const [field, input, visible] of [
      [hoursField, hours, active && value.mode !== 'ratio'], [ratioField, ratio, active && value.mode !== 'time'],
      [actionField, action, active]
    ]) {field.hidden = !visible; input.disabled = !visible;}
    explanation.hidden = immediate.hidden = !active;
    confirmation.hidden = !value.delete_files; consent.required = value.delete_files;
    summary.textContent = active ? t('{0} after {1}.', {0: value.delete_files ? t('Remove torrent and files') : t('Remove torrent only · keep files'), 1: cleanupGoal(value)}) : t('Seeding continues until you stop or remove the torrent. No automatic time or ratio limit.');
    save.textContent = t(value.mode === 'never' ? 'Keep torrent and files' : 'Save cleanup rule');
    save.classList.toggle('danger', value.delete_files);
  }
  mode.onchange = action.onchange = () => {consent.checked = false; paint();};
  hours.oninput = ratio.oninput = paint;
  form.onsubmit = async event => {
    event.preventDefault(); if (busy || !identity || !form.reportValidity()) return;
    busy = true; error.textContent = ''; const value = policy();
    const controls = [...editor.querySelectorAll('button,input,select')];
    const disabled = controls.map(control => control.disabled); controls.forEach(control => control.disabled = true);
    save.textContent = t('Saving…');
    try {
      const result = await api(`torrents/${row.hash}/cleanup`, {...value, added_on: identity, confirm_delete_files: value.delete_files && consent.checked}, 'PUT');
      row.cleanup = result.policy || {mode: 'never', status: 'disabled'};
      const listed = torrents.find(item => item.hash === row.hash);
      if (listed) listed.cleanup = row.cleanup;
      renderTorrents();
      seedDetailCache.delete(csrf + ':' + row.hash + ':overview');
      editor.close();
      showNotification(result.stopLimitsRestored === false ? t('Cleanup disabled. Removing old stop limits is waiting for the client; starting the torrent clears them.') : t('Cleanup rule saved.'), 'success');
      if (onSaved) await onSaved();
    } catch (failure) {error.textContent = t(failure.message);}
    finally {busy = false; controls.forEach((control, index) => control.disabled = disabled[index]); paint();}
  };
  async function load() {
    retry.hidden = true; status.textContent = t('Loading cleanup rule…');
    try {
      const data = await api(`torrents/${row.hash}/cleanup`);
      if (!editor.open) return;
      if (!(data.added_on > 0) || (row.added_on && data.added_on !== row.added_on)) throw new Error('Torrent changed; reload its cleanup settings');
      identity = data.added_on;
      const saved = data.policy;
      mode.value = saved.mode; hours.value = saved.hours ?? 50; ratio.value = saved.ratio ?? 1;
      action.value = saved.delete_files ? 'files' : 'torrent';
      form.hidden = false; status.textContent = t('Changes apply only to this torrent.'); paint(); mode.focus();
    } catch (failure) {if (editor.open) {status.textContent = t(failure.message); retry.hidden = false;}}
  }
  editor.showModal(); await load();
}

function torrentMagnet(row) {
  const valid = (value, length) => typeof value === 'string' && new RegExp('^[a-f0-9]{' + length + '}$', 'i').test(value) ? value.toLowerCase() : '';
  let v1 = valid(row.infohash_v1, 40), v2 = valid(row.infohash_v2, 64);
  if (!v1 && !v2) {v1 = valid(row.hash, 40); v2 = valid(row.hash, 64);}
  if (!v1 && !v2) return '';
  const params = new URLSearchParams();
  if (v1) params.append('xt', 'urn:btih:' + v1);
  if (v2) params.append('xt', 'urn:btmh:1220' + v2);
  if (typeof row.name === 'string' && row.name) params.set('dn', row.name);
  return 'magnet:?' + params.toString();
}

async function copyTorrentMagnet(row, returnFocus) {
  const magnet = torrentMagnet(row);
  if (!magnet) {notify(new Error('Torrent identity is not yet available.')); return;}
  try {
    await navigator.clipboard.writeText(magnet);
    showNotification(t('Magnet link copied.'), 'success');
  } catch {
    const editor = element('dialog', undefined, 'torrent-cleanup-dialog');
    editor.id = 'torrent-magnet'; editor.setAttribute('aria-labelledby', 'torrent-magnet-title');
    const title = element('h2', t('Magnet link')); title.id = 'torrent-magnet-title';
    const text = element('textarea'); text.readOnly = true; text.rows = 5; text.value = magnet;
    text.setAttribute('aria-label', t('Magnet link'));
    const close = command(t('Close'), () => editor.close()); close.type = 'button';
    editor.append(title, element('p', t('Select and copy the link manually.'), 'muted'), text, close);
    editor.addEventListener('close', () => {editor.remove(); returnFocus?.focus();}, {once:true});
    document.body.append(editor); editor.showModal(); text.focus(); text.select();
  }
}

const trackerRefreshPending = new Set();
async function refreshTrackerMetadata(row) {
  if (appRole === 'viewer' || trackerRefreshPending.has(row.hash)) return;
  trackerRefreshPending.add(row.hash);
  try {
    await api(`torrents/${row.hash}/metadata/refresh`, {}, 'POST');
    for (const key of seedDetailCache.keys()) if (key.startsWith(csrf + ':' + row.hash + ':')) seedDetailCache.delete(key);
    showNotification(t('Tracker refresh queued. Images and metadata will appear when available.'), 'success');
  } catch (error) {notify(error);}
  finally {trackerRefreshPending.delete(row.hash);}
}

function torrentActionMenu(row) {
  const menu = element('details', undefined, 'torrent-menu');
  const trigger = element('summary', '⋯'); trigger.setAttribute('aria-label', t('Torrent actions') + ': ' + row.name); trigger.title = t('Torrent actions');
  const panel = element('div', undefined, 'torrent-menu-content');
  panel.append(element('small', t('Torrent actions'), 'torrent-menu-label'));
  function choice(label, help, handler, danger = false) {
    const button = command('', () => {menu.open = false; return handler();});
    button.className = 'torrent-menu-choice' + (danger ? ' torrent-menu-danger' : '');
    button.append(element('strong', t(label)), element('small', t(help))); panel.append(button); return button;
  }
  choice('View details', 'Status, files and tracker information', () => showDetails(row));
  const copyMagnet = choice('Copy magnet link', 'Info hash and name · no private tracker addresses', () => copyTorrentMagnet(row, trigger));
  copyMagnet.disabled = !torrentMagnet(row);
  if (appRole !== 'viewer') {
    const paused = /^(paused|stopped)/.test(row.state || '');
    const download = Number(row.progress) < 1;
    const toggle = choice(paused ? 'Start' : download ? 'Pause download' : 'Pause seeding',
      paused ? 'Resume this torrent through the verified VPN' : 'Pause this torrent · keep files and cleanup settings',
      () => torrentAction(row, paused ? 'start' : 'stop'));
    toggle.disabled = paused && !downloadsEnabled;
    choice('Refresh data from tracker', 'Fetch images and metadata again', () => refreshTrackerMetadata(row));
    choice('Edit cleanup rule', 'Choose when to remove this torrent', () => openTorrentCleanup(row));
    choice('Check files', 'Verify existing files · leaves torrent paused', () => chooseRecheck(row));
    const divider = element('hr'); panel.append(divider);
    choice('Remove…', 'Choose whether to keep downloaded files', () => chooseRemoval(row), true);
  }
  menu.append(trigger, panel);
  menu.addEventListener('toggle', () => {
    if (!menu.open) return;
    for (const other of document.querySelectorAll('.torrent-menu[open]')) if (other !== menu) other.open = false;
    const box = trigger.getBoundingClientRect();
    menu.dataset.upward = String(innerHeight - box.bottom < panel.offsetHeight + 12 && box.top > panel.offsetHeight + 12);
  });
  return menu;
}
document.addEventListener('click', event => {
  for (const menu of document.querySelectorAll('.torrent-menu[open]')) if (!menu.contains(event.target)) menu.open = false;
});
document.addEventListener('keydown', event => {
  const menu = document.querySelector('.torrent-menu[open]');
  if (event.key === 'Escape' && menu) {event.preventDefault(); menu.open = false; menu.querySelector('summary').focus();}
});
