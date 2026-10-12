/* Classic background script for Firefox/Safari; Chromium worker imports it. */
const api = globalThis.browser || chrome;
const MS = MediaSeedExtension;
let busy = false;
const temporary = api.storage.session; // Never fall back to persistent storage for private links.
const volatile = new Map();
async function pendingPut(item) {
  const entries = temporary ? Object.entries(await temporary.get(null)) : [...volatile.entries()];
  const expired = entries.filter(([, value], index) => value.expires < Date.now() || index < entries.length - 19).map(([key]) => key);
  if (temporary) await temporary.remove(expired); else expired.forEach(key => volatile.delete(key));
  const id = crypto.randomUUID(), value = {item, expires: Date.now() + 10 * 60 * 1000};
  if (temporary) await temporary.set({[id]: value}); else volatile.set(id, value);
  return id;
}
async function pendingGet(id) {
  const value = temporary ? (await temporary.get(id))[id] : volatile.get(id);
  if (!value || value.expires < Date.now()) throw Error('Tilføjelsen er udløbet. Vælg linket eller filen igen.');
  return value.item;
}
async function review(item) {
  const id = await pendingPut(item);
  await api.tabs.create({url: api.runtime.getURL('popup.html') + '?pending=' + id});
}
async function settings() { return {origin: '', sites: [], ...await api.storage.local.get(['origin', 'sites'])}; }
async function seedTab(origin) {
  const all = await api.tabs.query({});
  let tab = all.find(t => { try { return new URL(t.url).origin === origin; } catch { return false; } });
  if (!tab) tab = await api.tabs.create({url: origin + '/', active: false});
  for (let i = 0; i < 80; i++) {
    const current = await api.tabs.get(tab.id);
    if (current.status === 'complete') return current;
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  throw Error('MediaSeed svarer ikke. Åbn adressen og kontrollér forbindelsen/certifikatet.');
}
async function send(item) {
  if (busy) throw Error('En tilføjelse er allerede i gang. Vent på svaret.');
  busy = true;
  try {
    const config = await settings(), origin = MS.origin(config.origin);
    if (!await api.permissions.contains({origins: [MS.pattern(origin)]})) throw Error('Gem MediaSeed-adressen og giv adgang først.');
    if (!['check', 'magnet', 'file', 'link'].includes(item.kind)) throw Error('Ukendt kilde.');
    item = {...item, ratio: MS.ratio(item.ratio ?? 1), start: item.start === true};
    if (item.kind === 'magnet') item.magnet = MS.magnet(item.magnet);
    if (item.kind === 'link') {
      const u = MS.webURL(item.url);
      const source = item.sourceTab ? await api.tabs.get(item.sourceTab).catch(() => null) : null;
      let data;
      if (source?.url && new URL(source.url).origin === u.origin) {
        const results = await api.scripting.executeScript({target: {tabId: source.id}, func: mediaSeedReadFile, args: [u.href]});
        data = results[0]?.result;
      } else {
        if (!await api.permissions.contains({origins: [MS.pattern(u.origin)]})) throw Error('Giv adgang til torrentfilens hjemmeside via Tilføj-vinduet først.');
        data = await mediaSeedReadFile(u.href, false);
      }
      if (!data) throw Error('Torrentfilen kunne ikke hentes.');
      item = {...item, ...data, kind: 'file'};
    }
    if (item.kind === 'file') MS.file(item.name, MS.decode(item.base64));
    const tab = await seedTab(origin);
    const results = await api.scripting.executeScript({target: {tabId: tab.id}, func: mediaSeedBridge, args: [origin, item]});
    const result = results[0]?.result;
    if (!result) throw Error('MediaSeed svarede ikke. Kontrollér listen før du prøver igen.');
    if (result.login) await api.tabs.update(tab.id, {active: true});
    if (result.error) throw Error(result.error);
    return result;
  } finally { busy = false; }
}
async function inject(tab) {
  const config = await settings();
  let origin; try { origin = new URL(tab.url).origin; } catch { return; }
  if (origin === config.origin || !config.sites.includes(origin)) return;
  if (await api.permissions.contains({origins: [MS.pattern(origin)]})) await api.scripting.executeScript({target: {tabId: tab.id}, files: ['capture.js']});
}
api.tabs.onUpdated.addListener((id, info, tab) => { if (info.status === 'complete') inject(tab).catch(() => {}); });
api.runtime.onInstalled.addListener(() => {
  api.contextMenus.removeAll().then(() => api.contextMenus.create({id: 'mediaseed-add', title: 'Tilføj i MediaSeed…', contexts: ['link']}));
});
api.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId !== 'mediaseed-add') return;
  try {
    const item = info.linkUrl?.startsWith('magnet:') ? {kind: 'magnet', magnet: MS.magnet(info.linkUrl)} : {kind: 'link', url: MS.webURL(info.linkUrl).href, sourceTab: tab.id};
    review(item).catch(() => {});
  } catch { review({kind: 'invalid'}).catch(() => {}); }
});
async function handle(message, sender) {
  if (message?.type === 'capture') {
    const config = await settings();
    const source = new URL(sender.url).origin;
    if (!sender.tab || sender.frameId !== 0 || source === config.origin || !config.sites.includes(source)) throw Error('Hjemmesiden er ikke aktiveret.');
    if (!await api.permissions.contains({origins: [MS.pattern(source)]})) throw Error('Adgangen til hjemmesiden er fjernet.');
    const item = message.url.startsWith('magnet:') ? {kind: 'magnet', magnet: MS.magnet(message.url)} : {kind: 'link', url: MS.webURL(message.url).href, sourceTab: sender.tab.id};
    if (item.kind === 'link' && !new URL(item.url).pathname.toLowerCase().endsWith('.torrent')) throw Error('Kun torrentlinks kan fanges automatisk.');
    await review(item); return {ok: true};
  }
  // Only our own extension pages may configure, read imports or send API requests.
  if (sender.tab && !sender.url?.startsWith(api.runtime.getURL(''))) throw Error('Denne handling kræver udvidelsens eget vindue.');
  if (!sender.url?.startsWith(api.runtime.getURL(''))) throw Error('Ukendt afsender.');
  if (message.type === 'pending') return {item: await pendingGet(message.id)};
  if (message.type === 'inject') { const tab = await api.tabs.get(message.tabId); await inject(tab); return {ok: true}; }
  if (message.type === 'send') {
    const result = await send(message.item);
    if (message.pending && result.ok) { if (temporary) await temporary.remove(message.pending); else volatile.delete(message.pending); }
    return result;
  }
  throw Error('Ukendt handling.');
}
api.runtime.onMessage.addListener((message, sender, respond) => {
  handle(message, sender).then(respond, error => respond({error: error.message || 'Handlingen mislykkedes.'})); return true;
});
