const api = globalThis.browser || chrome, MS = MediaSeedExtension;
const MAX_AGE = 15 * 60 * 1000;
let working = false, delivering = false;
// Private source links and bytes exist only in browser-session memory.
let volatilePending = null, volatileStatus = null;
async function state() { return api.storage.session ? api.storage.session.get(['pending', 'status']) : {pending: volatilePending, status: volatileStatus}; }
async function savePending(pending) {
  if (api.storage.session) { if (pending) await api.storage.session.set({pending}); else await api.storage.session.remove('pending'); }
  else volatilePending = pending;
}
async function report(text, error = false) {
  text = String(text).replace(/(?:https?:\/\/|magnet:\?)[^\s"'<>]+/gi, '[adresse]');
  const status = {text, error};
  if (api.storage.session) await api.storage.session.set({status}); else volatileStatus = status;
  await api.action?.setBadgeText({text: error ? '!' : ''});
  await api.action?.setBadgeBackgroundColor({color: '#b83e58'});
  await api.action?.setTitle({title: 'MediaSeed · ' + text});
}
async function setup() {
  const url = api.runtime.getURL('popup.html');
  const tabs = await api.tabs.query({});
  const existing = tabs.find(tab => tab.url === url);
  if (existing) await api.tabs.update(existing.id, {active: true});
  else await api.tabs.create({url, active: true});
}
async function destination() {
  const config = await api.storage.local.get('origin');
  if (!config.origin) throw Error('Gem din MediaSeed-adresse i udvidelsen først.');
  const origin = MS.origin(config.origin);
  if (!await api.permissions.contains({origins: [MS.pattern(origin)]})) throw Error('Gem adressen igen for at give adgang til MediaSeed.');
  return origin;
}
async function openDestination(origin, preferredId) {
  const existing = MS.selectSeedTab(await api.tabs.query({}), origin, preferredId);
  const tab = existing ? await api.tabs.update(existing.id, {active: true}) : await api.tabs.create({url: origin + '/', active: true});
  if (tab.windowId !== undefined) await api.windows?.update(tab.windowId, {focused: true});
  return tab;
}
async function deliver(tabId) {
  if (delivering) return;
  delivering = true;
  try {
    const {pending} = await state();
    if (!pending || pending.tabId !== tabId) return;
    if (Date.now() - pending.created >= MAX_AGE) { await savePending(null); await report('Linket udløb efter 15 minutter. Send det igen.', true); return; }
    // Re-check the configured origin and its current grant after login/navigation.
    const origin = await destination();
    if (pending.origin !== origin) throw Error('MediaSeed-adressen er ændret. Ryd det ventende link og send igen.');
    const tab = await api.tabs.get(tabId);
    if (tab.status !== 'complete' || new URL(tab.url).origin !== origin) return;
    const results = await api.scripting.executeScript({target: {tabId}, func: mediaSeedBridge, args: [origin, pending]});
    const result = results[0]?.result;
    if (result?.opened) {
      // Never repeat delivery because a badge/status update failed afterwards.
      await savePending(null);
      await report(pending.kind === 'rss' ? 'RSS-linket er klar i MediaSeed. Vælg indstillinger og gem feedet.' : 'Torrenten er klar i MediaSeed. Vælg mappe og indstillinger, og tryk Start eller Tilføj på pause.').catch(() => {});
      return {ok: true, opened: true};
    }
    await report(result?.error || 'Åbn MediaSeed og tryk Fortsæt i udvidelsen.', !result?.login);
    return {waiting: true, login: !!result?.login};
  } finally { delivering = false; }
}
async function begin(value, sourceTab) {
  if (working || delivering) throw Error('En overførsel er allerede i gang.');
  working = true;
  try {
    const previous = (await state()).pending;
    if (previous && Date.now() - previous.created < MAX_AGE) {
      if (previous.transferId === value?.transferId) {
        const origin = await destination();
        if (previous.origin && previous.origin !== origin) throw Error('Adressen er ændret. Ryd linket og send det igen.');
        return await prepareAndOpen(previous, origin, previous.sourceTab);
      }
      throw Error('Et link venter allerede. Tryk Fortsæt eller Ryd i udvidelsen.');
    }
    await savePending(null);
    let item = typeof value === 'string' ? MS.sourceLink(value) : value;
    if (item.kind === 'file') MS.file(item.name, MS.decode(item.base64));
    else if (!['link', 'magnet', 'rss'].includes(item.kind)) throw Error('Ukendt overførsel.');
    item = {...item, transferId: /^[a-f0-9-]{36}$/i.test(item.transferId || '') ? item.transferId : crypto.randomUUID(), created: Date.now(), sourceTab: sourceTab?.id};
    // Keep the selected source while the user completes first-time setup.
    await savePending(item);
    let origin;
    try { origin = await destination(); }
    catch { await report('Gem din MediaSeed-adresse for at fortsætte.'); await setup(); return {setup: true}; }
    return await prepareAndOpen(item, origin, sourceTab?.id);
  } finally { working = false; }
}
async function prepareAndOpen(item, origin, sourceId) {
  if (item.kind === 'link') {
    const url = MS.webURL(item.url);
    const source = sourceId ? await api.tabs.get(sourceId).catch(() => null) : null;
    await report('Henter torrentfilen…');
    let file;
    if (source?.url && new URL(source.url).origin === url.origin) {
      file = (await api.scripting.executeScript({target: {tabId: source.id}, func: mediaSeedReadFile, args: [url.href]}))[0]?.result;
    } else {
      if (!await api.permissions.contains({origins: [MS.pattern(url.origin)]})) throw Error('Giv adgang til trackerens hjemmeside i udvidelsen, og tryk Fortsæt.');
      file = await mediaSeedReadFile(url.href, false);
    }
    if (!file) throw Error('Torrentfilen kunne ikke hentes. Kontrollér dit tracker-login.');
    MS.file(file.name, MS.decode(file.base64));
    item = {kind: 'file', transferId: item.transferId, created: item.created, ...file};
  }
  const tab = await openDestination(origin, item.tabId);
  item = {...item, origin, tabId: tab.id};
  await savePending(item);
  await report('Åbner MediaSeed. Log ind dér, hvis du bliver bedt om det.');
  return await deliver(tab.id) || {waiting: true};
}
async function resume() {
  if (working || delivering) throw Error('Vent, til overførslen er færdig.');
  working = true;
  try {
    const {pending} = await state();
    if (!pending) return {ok: true};
    if (Date.now() - pending.created >= MAX_AGE) { await savePending(null); throw Error('Linket er udløbet. Send det igen.'); }
    const origin = await destination();
    if (pending.origin && pending.origin !== origin) throw Error('Adressen er ændret. Ryd linket og send det igen.');
    return await prepareAndOpen(pending, origin, pending.sourceTab);
  } finally { working = false; }
}
api.runtime.onInstalled.addListener(async () => {
  await api.contextMenus.removeAll();
  api.contextMenus.create({id: 'mediaseed-add', title: 'Send til MediaSeed', contexts: ['link']});
});
api.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === 'mediaseed-add') begin(info.linkUrl, tab).catch(error => report(error.message, true).catch(() => {}));
});
api.tabs.onUpdated.addListener((id, change) => {
  if (change.status === 'complete') deliver(id).catch(() => report('Linket venter. Åbn udvidelsen og tryk Fortsæt.', true).catch(() => {}));
});
async function handle(message, sender) {
  if (!sender.url || ![api.runtime.getURL('popup.html'), api.runtime.getURL('popup.html') + '#batch'].includes(sender.url) || (sender.id && sender.id !== api.runtime.id)) throw Error('Åbn udvidelsens eget vindue.');
  if (message.type === 'state') {
    const current = await state();
    const pending = current.pending;
    return {status: current.status, pending: pending ? {kind: pending.kind, name: pending.name, created: pending.created, sourceOrigin: pending.kind === 'link' ? new URL(pending.url).origin : null} : null};
  }
  if (message.type === 'batch-window') {
    await api.tabs.create({url: api.runtime.getURL('popup.html') + '#batch', active: true}); return {ok: true};
  }
  if (message.type === 'send-file') return begin({kind: 'file', name: message.name, base64: message.base64, transferId: message.transferId});
  if (message.type === 'send-link') {
    const item = MS.sourceLink(message.url);
    if (item.kind !== 'link') throw Error('Brug torrent-downloadlinks her. Send RSS og magnetlinks via højreklik.');
    return begin({...item, transferId: message.transferId});
  }
  if (message.type === 'resume') return resume();
  if (message.type === 'clear') {
    if (working || delivering) throw Error('Vent, til overførslen er færdig.');
    await savePending(null); await report('Ventende link ryddet. Ingen download er startet.'); return {ok: true};
  }
  if (message.type === 'open') { await openDestination(await destination()); return {ok: true}; }
  throw Error('Ukendt handling.');
}
api.runtime.onMessage.addListener((message, sender, reply) => {
  handle(message, sender).then(reply).catch(async error => { await report(error.message, true).catch(() => {}); reply({error: error.message}); });
  return true;
});
