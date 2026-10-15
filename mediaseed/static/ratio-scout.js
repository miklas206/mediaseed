/* Uses the app's authorized API client; no tracker keys or private URLs enter DOM. */
(() => {
  const form = document.querySelector('#ratio-form');
  if (!form) return;
  let loading = false, busy = false;
  const field = name => form.elements.namedItem(name);
  const numeric = ['minSeeders','maxSeeders','minLeechers','maxGiB','reserveGiB','dailyLimit','scanMinutes'];
  const status = document.querySelector('#ratio-status');
  const formStatus = document.querySelector('#ratio-form-status');
  const admin = () => appRole === 'administrator';
  function render(data) {
    document.querySelector('#ratio-scan-info').textContent=t('One tracker search every {0} minutes, up to {1} results. Refresh uses saved results; approval performs fresh safety checks.',{0:data.scanMinutes,1:data.resultLimit});
    if(data.settings.enabled && data.nextScanAt>0) document.querySelector('#ratio-scan-info').textContent+=' '+t('Next search: {0}',{0:new Date(data.nextScanAt*1000).toLocaleString(locale())});
    status.textContent = t('Discovery status: {0} · Used in the last 24 hours: {1}/{2}', {0: t(data.status), 1: data.dailyUsed, 2: data.settings.dailyLimit});
    const cards = (data.candidates || []).map(row => {
      const card = element('article', undefined, 'source-row ratio-candidate');
      card.append(element('h3', row.name));
      const metrics = element('div', undefined, 'ratio-candidate-metrics');
      for (const [label, value] of [['Seeders',row.seeders],['Leechers',row.leechers],['Size',bytes(row.size)]]) {
        const metric = element('div'); metric.append(element('small', t(label)), element('strong', String(value))); metrics.append(metric);
      }
      card.append(metrics, element('p', row.doubleUpload ? t('100% freeleech · Double upload') : t('100% freeleech · Normal upload credit'), 'muted'));
      card.append(element('p', t('Checked {0}', {0: new Date(row.checkedAt * 1000).toLocaleString(locale())}), 'muted'));
      const button = element('button', t(row.importStatus ? 'Already handled' : row.stale ? 'Waiting for a fresh scan' : 'Approve download'));
      button.type = 'button'; button.disabled = busy || !admin() || !!row.importStatus || row.stale || !data.settings.enabled;
      button.onclick = async () => {
        if (busy || button.disabled) return;
        busy = true; button.disabled = true; formStatus.textContent = t('Checking candidate before download...');
        try { await api(`ratio-scout/${encodeURIComponent(row.candidateId)}/add`, {}, 'POST'); showNotification(t('Torrent added to the download queue. Files will be kept.'), 'success'); }
        catch (error) { formStatus.textContent = t(error.message); notify(error); }
        finally { busy = false; await load(false); }
      };
      card.append(button);
      if (row.blockedReason) card.append(element('p', t(row.blockedReason), 'muted'));
      if (row.importStatus === 'intent') card.append(element('p', t('Import outcome is uncertain. Check the torrent list; automatic retry is blocked.'), 'muted'));
      return card;
    });
    document.querySelector('#ratio-candidates').replaceChildren(...(cards.length ? cards : [element('p', t('No matching candidates in the latest scan. Broaden the filters or wait for new releases.'), 'muted')]));
  }
  async function load(fill = true) {
    if (loading) return;
    loading = true;
    try {
      const data = await api('ratio-scout');
      if (fill) {
        const [trackers, storage] = await Promise.all([api('trackers'), api('storage')]);
        field('tracker').replaceChildren(new Option(t('Choose tracker'), ''), ...trackers.map(r => new Option(r.name, r.id)));
        field('location').replaceChildren(...(storage.locations || []).map(r => {const option = new Option(t(r.label), r.id); option.disabled = !r.available || !r.writable; return option;}));
        for (const name of ['enabled','requireDouble']) field(name).checked = data.settings[name];
        for (const name of [...numeric,'tracker','query','location','folder']) field(name).value = data.settings[name];
        field('automatic').value = String(data.settings.automatic);
      }
      for (const control of form.elements) control.disabled = !admin() || busy;
      if (!admin()) formStatus.textContent = t('Only administrators can change discovery or approve downloads.');
      render(data);
    } catch (error) {status.textContent = t(error.message); document.querySelector('#ratio-save').disabled = true;}
    finally {loading = false;}
  }
  form.onsubmit = async event => {
    event.preventDefault(); if (busy || !admin()) return;
    const settings = {enabled:field('enabled').checked, automatic:field('automatic').value === 'true', requireDouble:field('requireDouble').checked};
    for (const name of numeric) settings[name] = Number(field(name).value);
    for (const name of ['tracker','query','location','folder']) settings[name] = field(name).value;
    busy = true; for (const control of form.elements) control.disabled = true;
    try {await api('ratio-scout/settings', settings, 'PUT'); formStatus.textContent = t('Discovery rules saved. The next background scan will use them.');}
    catch (error) {formStatus.textContent = t(error.message); notify(error);}
    finally {busy = false; for (const control of form.elements) control.disabled = !admin(); await load(false);}
  };
  document.querySelector('[data-view="ratio-scout"]').addEventListener('click', () => {void load();});
  document.querySelector('#ratio-reload').onclick = () => {void load(false);};
  setInterval(() => {if (!document.hidden && !document.querySelector('#ratio-scout').hidden && !busy) void load(false);}, 15000);
})();
