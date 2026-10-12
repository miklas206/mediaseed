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
  if (!value || value.expires < Date.now()) throw Error(t("The addition expired. Choose the link or file again."));
  return value.item;
}
async function review(item) {
  const id = await pendingPut(item);
  await api.tabs.create({url: api.runtime.getURL('popup.html') + '?pending=' + id});
}
async function settings() { return {origin: '', sites: [], language: 'da', ...await api.storage.local.get(['origin', 'sites', 'defaults', 'language'])}; }
async function seedTab(origin) {
  const all = await api.tabs.query({});
  let tab = all.find(t => { try { return new URL(t.url).origin === origin; } catch { return false; } });
  if (!tab) tab = await api.tabs.create({url: origin + '/', active: false});
  for (let i = 0; i < 80; i++) {
    const current = await api.tabs.get(tab.id);
    if (current.status === 'complete') return current;
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  throw Error(t("MediaSeed is not responding. Open the address and check the connection/certificate."));
}
async function send(item) {
  if (busy) throw Error(t("An addition is already in progress. Wait for the response."));
  busy = true;
  try {
    const config = await settings(), origin = MS.origin(config.origin); setLanguage(config.language);
    if (!await api.permissions.contains({origins: [MS.pattern(origin)]})) throw Error(t("Save the MediaSeed address and grant access first."));
    if (!['check', 'magnet', 'file', 'link'].includes(item.kind)) throw Error(t("Unknown source."));
    const options = MS.defaults({...config.defaults, ...Object.fromEntries(['ratio', 'start', 'cleanup'].filter(k => item[k] !== undefined).map(k => [k, item[k]]))});
    item = {...item, ...options};
    if (item.kind === 'magnet') item.magnet = MS.magnet(item.magnet);
    if (item.kind === 'link') {
      const u = MS.webURL(item.url);
      const source = item.sourceTab ? await api.tabs.get(item.sourceTab).catch(() => null) : null;
      let data;
      if (source?.url && new URL(source.url).origin === u.origin) {
        const results = await api.scripting.executeScript({target: {tabId: source.id}, func: mediaSeedReadFile, args: [u.href]});
        data = results[0]?.result;
      } else {
        if (!await api.permissions.contains({origins: [MS.pattern(u.origin)]})) throw Error(t("Grant access to the torrent file's website in the Add window first."));
        data = await mediaSeedReadFile(u.href, false);
      }
      if (!data) throw Error(t("The torrent file could not be downloaded."));
      item = {...item, ...data, kind: 'file'};
    }
    if (item.kind === 'file') MS.file(item.name, MS.decode(item.base64));
    const tab = await seedTab(origin);
    const results = await api.scripting.executeScript({target: {tabId: tab.id}, func: mediaSeedBridge, args: [origin, item]});
    const result = results[0]?.result;
    if (!result) throw Error(t("MediaSeed did not respond. Check the list before trying again."));
    if (result.login) await api.tabs.update(tab.id, {active: true});
    if (result.language) { setLanguage(result.language); await api.storage.local.set({language: result.language}); await api.contextMenus.update?.('mediaseed-add', {title: t('Add to MediaSeed')}); }
    if (result.error) throw Error(t(result.error));
    return result;
  } finally { busy = false; }
}
function resultText(result) {
  return result.cleanupPending ? t("Added paused · preparing cleanup before the selected start.") : result.pending ? t("Added · waiting for metadata in MediaSeed.") : result.stopped ? t("Added paused.") : t("Added · start requested in MediaSeed.");
}
async function report(text, error = false) {
  text = t(String(text)).replace(/(?:https?:\/\/|magnet:\?)[^\s"'<>]+/gi, '[adresse]');
  await api.storage.local.set({lastResult: {text, error, at: Date.now()}});
  await api.action?.setBadgeBackgroundColor({color: error ? '#b83e58' : '#147d65'});
  await api.action?.setBadgeText({text: error ? '!' : 'OK'});
  await api.action?.setTitle({title: 'MediaSeed · ' + text});
}
async function direct(item, tab) {
  let result;
  try {
    if (!(await settings()).origin) { await review(item); return {ok: true, setup: true}; }
    await api.action?.setBadgeText({text: '…'});
    result = await send(item);
  } catch (error) {
    await report(error.message || t("The addition failed."), true).catch(() => {});
    // Preserve the draft for a deliberate retry after setup/login; never retry POST.
    await review(item);
    return {error: error.message, reviewed: true};
  }
  // Feedback failure after POST must never turn a successful import into a retry.
  await report(resultText(result)).catch(() => {});
  if (tab?.id) await api.scripting.executeScript({target: {tabId: tab.id}, func: mediaSeedToast, args: [resultText(result)]}).catch(() => {});
  return result;
}
async function inject(tab) {
  const config = await settings();
  let origin; try { origin = new URL(tab.url).origin; } catch { return; }
  if (origin === config.origin || !config.sites.includes(origin)) return;
  if (await api.permissions.contains({origins: [MS.pattern(origin)]})) await api.scripting.executeScript({target: {tabId: tab.id}, files: ['common.js', 'capture.js']});
}
api.tabs.onUpdated.addListener((id, info, tab) => { if (info.status === 'complete') inject(tab).catch(() => {}); });
api.runtime.onInstalled.addListener(async () => {
  const config = await settings(); setLanguage(config.language || 'da');
  await api.contextMenus.removeAll();
  api.contextMenus.create({id: 'mediaseed-add', title: t("Add to MediaSeed"), contexts: ['link']});
});
api.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId !== 'mediaseed-add') return;
  try {
    const item = info.linkUrl?.startsWith('magnet:') ? {kind: 'magnet', magnet: MS.magnet(info.linkUrl)} : {kind: 'link', url: MS.webURL(info.linkUrl).href, sourceTab: tab?.id};
    direct(item, tab).catch(() => {});
  } catch { review({kind: 'invalid'}).catch(() => {}); }
});
async function handle(message, sender) {
  if (message?.type === 'capture') {
    const config = await settings();
    const source = new URL(sender.url).origin;
    if (!sender.tab || sender.frameId !== 0 || source === config.origin || !config.sites.includes(source)) throw Error(t("This website is not enabled."));
    if (!await api.permissions.contains({origins: [MS.pattern(source)]})) throw Error(t("Website access removed."));
    const item = message.url.startsWith('magnet:') ? {kind: 'magnet', magnet: MS.magnet(message.url)} : {kind: 'link', url: MS.webURL(message.url).href, sourceTab: sender.tab.id};
    if (!MS.capturable(message.url, source)) throw Error(t("Only torrent links can be captured automatically."));
    return await direct(item, sender.tab);
  }
  // Only our own extension pages may configure, read imports or send API requests.
  if (sender.tab && !sender.url?.startsWith(api.runtime.getURL(''))) throw Error(t("This action requires the extension's own window."));
  if (!sender.url?.startsWith(api.runtime.getURL(''))) throw Error(t("Unknown sender."));
  if (message.type === 'pending') return {item: await pendingGet(message.id)};
  if (message.type === 'inject') { const tab = await api.tabs.get(message.tabId); await inject(tab); return {ok: true}; }
  if (message.type === 'send') {
    const result = await send(message.item);
    if (message.pending && result.ok) { if (temporary) await temporary.remove(message.pending).catch(() => {}); else volatile.delete(message.pending); }
    if (result.ok) await report(resultText(result)).catch(() => {});
    return result;
  }
  throw Error(t("Unknown action."));
}
api.runtime.onMessage.addListener((message, sender, respond) => {
  handle(message, sender).then(respond, error => respond({error: error.message || t("The action failed.")})); return true;
});
