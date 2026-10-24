/* Group comparisons use the durable ledger; expansion fetches one bounded page. */
(() => {
  let generation = 0;
  function money(value) { return typeof value === 'number' && Number.isFinite(value) ? '≈ ' + trackerBytes(value) : t('Unknown'); }
  function pager(page, total, size, load) {
    const nav = element('nav', undefined, 'rss-history-pages'); nav.setAttribute('aria-label', t('History pages'));
    const previous = command(t('Previous page'), () => load(page - 1)), next = command(t('Next page'), () => load(page + 1));
    previous.disabled = page <= 1; next.disabled = page * size >= total;
    nav.append(previous, element('span', `${page} / ${Math.max(1, Math.ceil(total / size))}`), next); return nav;
  }
  async function open(id, name, page = 1) {
    const mine = ++generation;
    let popup = document.querySelector('#feed-history-dialog');
    if (!popup) { popup = element('dialog'); popup.id = 'feed-history-dialog'; popup.setAttribute('aria-labelledby', 'feed-history-title'); document.body.append(popup); }
    const heading = element('div', undefined, 'heading'), title = element('h2', name); title.id = 'feed-history-title';
    const actions = element('div', undefined, 'rss-history-header-actions');
    const refresh = command(t('Refresh'), () => void load(currentPage)), close = command(t('Close'), () => popup.close()); actions.append(refresh, close); heading.append(title, actions);
    const totals = element('p', undefined, 'rss-history-total'); totals.setAttribute('role', 'status');
    const tools = element('div', undefined, 'rss-history-tools');
    function select(label, options) {
      const field = element('label', t(label)), node = element('select');
      for (const [value, text] of options) { const option = element('option', t(text)); option.value = value; node.append(option); }
      field.append(node); tools.append(field); return node;
    }
    const view = select('History view', [['series', 'Grouped by series'], ['downloads', 'All downloads']]);
    const sort = select('Sort by', [['upload', 'Most credited upload'], ['average', 'Upload per measured download'], ['recent', 'Most recent download']]);
    const searchField = element('label', t('Find a series')), search = element('input'); search.type = 'search'; search.maxLength = 120; search.placeholder = t('Series title'); searchField.append(search); tools.append(searchField);
    const help = element('p', t('Averages use measured downloads only. A season pack counts as one download.'), 'rss-history-help');
    const list = element('div', undefined, 'rss-history-list');
    popup.replaceChildren(heading, totals, tools, help, list);
    if (!popup.open) popup.showModal();
    let currentPage = page, request = 0, searchTimer;
    const active = () => mine === generation && popup.open;
    function download(row) {
      const item = element('article', undefined, 'rss-history-entry'), identity = element('div', undefined, 'rss-history-entry-title');
      if (row.episode) identity.append(element('small', row.episode, 'rss-history-episode'));
      identity.append(element('strong', row.title || t('RSS entry')), element('small', new Date(row.created * 1000).toLocaleString(locale()), 'muted'));
      const credit = element('div', undefined, 'rss-history-entry-credit'); credit.append(element('strong', money(row.creditedUploadedBytes)));
      const torrent = torrents.find(item => String(item.hash || '').toLowerCase() === String(row.hash || '').toLowerCase());
      if (torrent) credit.append(command(t('Show torrent'), () => { popup.close(); void showDetails(torrent); }));
      item.append(identity, credit); return item;
    }
    function group(row, reopen) {
      const card = element('details', undefined, 'rss-series'); card.dataset.series = row.key;
      const summary = element('summary'), identity = element('div', undefined, 'rss-series-identity');
      identity.append(element('strong', row.name), element('small', `${t(row.kind === 'series' ? 'Series group' : 'Standalone download')} · ${t('{0} downloads', {0: row.downloads})}`, 'muted'));
      const metrics = element('div', undefined, 'rss-series-metrics');
      for (const [label, value] of [['Credited upload', row.creditedUploadedBytes], ['Average per measured download', row.averageUploadedBytes]]) {
        const fact = element('span'); fact.append(element('small', t(label)), element('strong', money(value))); metrics.append(fact);
      }
      summary.append(identity, metrics); card.append(summary);
      if (row.unknownDownloads) identity.append(element('small', t('{0} of {1} downloads have no upload measurement', {0: row.unknownDownloads, 1: row.downloads}), 'rss-series-unknown'));
      const body = element('div', undefined, 'rss-series-downloads'); card.append(body);
      let loaded = false, loading = false, cycle = 0;
      async function members(memberPage = 1) {
        loading = true; const token = ++cycle; body.replaceChildren(element('p', t('Loading…'), 'muted')); body.setAttribute('aria-busy', 'true');
        try {
          const result = await api(`feeds/${encodeURIComponent(id)}/history?series=${row.key}&page=${memberPage}&pageSize=25`);
          if (!active() || !card.isConnected || token !== cycle) return;
          loaded = true; body.replaceChildren();
          if (!result.entries.length) body.append(element('p', t('No downloaded torrents in this feed yet.'), 'muted'));
          for (const entry of result.entries) body.append(download(entry));
          if (result.total > 25) body.append(pager(memberPage, result.total, 25, next => void members(next)));
        } catch (error) {
          if (!active() || !card.isConnected || token !== cycle) return;
          body.replaceChildren(element('p', t(error.message), 'muted'), command(t('Try again'), () => void members(memberPage)));
        } finally { if (token === cycle) { loading = false; body.setAttribute('aria-busy', 'false'); } }
      }
      card.ontoggle = () => { if (card.open && !loaded && !loading) void members(); };
      if (reopen) card.open = true;
      return card;
    }
    async function load(next = 1) {
      const token = ++request;
      const expanded = new Set([...list.querySelectorAll('.rss-series[open]')].map(node => node.dataset.series));
      currentPage = next; refresh.disabled = true; list.setAttribute('aria-busy', 'true');
      list.replaceChildren(element('p', t('Loading…'), 'muted'));
      const grouped = view.value === 'series'; sort.disabled = !grouped; search.disabled = !grouped; help.hidden = !grouped;
      try {
        const result = await api(`feeds/${encodeURIComponent(id)}/history?view=${view.value}&sort=${sort.value}&search=${encodeURIComponent(grouped ? search.value : '')}&page=${next}&pageSize=25`);
        if (!active() || token !== request) return;
        const stats = result.upload;
        totals.textContent = `${t('Estimated credited upload')}: ${money(stats?.creditedUploadedBytes)} · ${t('{0} downloads', {0: stats?.downloads || 0})}`;
        if (stats?.unknownDownloads) totals.textContent += ' · ' + t('{0} downloads without measurements', {0: stats.unknownDownloads});
        list.replaceChildren();
        const rows = grouped ? result.groups : result.entries;
        if (!rows.length) list.append(element('p', t(search.value && grouped ? 'No matching series' : 'No downloaded torrents in this feed yet.'), 'muted'));
        for (const row of rows) list.append(grouped ? group(row, expanded.has(row.key)) : download(row));
        list.append(pager(next, result.total, 25, selected => void load(selected)));
      } catch (error) {
        if (active() && token === request) list.replaceChildren(element('p', t(error.message), 'muted'), command(t('Try again'), () => void load(next)));
      } finally {
        if (active() && token === request) { refresh.disabled = false; list.setAttribute('aria-busy', 'false'); }
      }
    }
    view.onchange = sort.onchange = () => { clearTimeout(searchTimer); void load(1); };
    search.oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { if (active()) void load(1); }, 250); };
    await load(page);
  }
  globalThis.MediaSeedFeedHistory = {open};
})();
