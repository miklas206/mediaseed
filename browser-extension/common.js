// The helpers are also injected into tracker pages. Do not load the document
// translator there or translate a third-party page's content. Use English
// validation messages in that isolated context; the background localizes them.
var {t, setLanguage, locale} = globalThis.MediaAppI18n || {t: text => text, setLanguage() {}, locale: () => 'en-GB'};
globalThis.MediaSeedExtension = (() => {
  const MAX_FILE = 4 * 1024 * 1024;
  function origin(value) {
    const u = new URL(String(value).trim());
    if (!['http:', 'https:'].includes(u.protocol) || u.username || u.password || u.search || u.hash || u.pathname !== '/') throw Error(t("Use only the MediaSeed address, e.g. https://seed.example.dk"));
    return u.origin;
  }
  function pattern(value) { const u = new URL(origin(value)); return `${u.protocol}//${u.hostname}/*`; }
  function magnet(value) {
    const text = String(value).trim();
    if (text.length > 8192 || /[\x00-\x20\x7f]/.test(text)) throw Error(t("The magnet link is invalid."));
    const u = new URL(text);
    if (u.protocol !== 'magnet:' || u.hash || !u.searchParams.getAll('xt').some(x => /^urn:btih:(?:[a-f\d]{40}|[a-z2-7]{32})$/i.test(x) || /^urn:btmh:1220[a-f\d]{64}$/i.test(x))) throw Error(t("The magnet link is missing a valid torrent ID."));
    return text;
  }
  function file(name, bytes) {
    if (typeof name !== 'string' || name.length > 255 || !name.toLowerCase().endsWith('.torrent') || /[\x00-\x1f/\\]/.test(name)) throw Error(t("Choose a .torrent file."));
    if (!bytes.length || bytes.length > MAX_FILE || bytes[0] !== 100 || bytes[bytes.length - 1] !== 101) throw Error(t("The torrent file is invalid or exceeds 4 MiB."));
  }
  function webURL(value) { const u = new URL(value); if (!['https:', 'http:'].includes(u.protocol) || u.username || u.password || value.length > 8192) throw Error(t("Invalid torrent address.")); return u; }
  function sourceLink(value) {
    if (String(value).startsWith('magnet:')) return {kind: 'magnet', magnet: magnet(value)};
    const u = webURL(value);
    if (/\/(?:create|edit|delete|manage)(?:\/|$)/i.test(u.pathname)) throw Error('Vælg et downloadlink eller et RSS-feed.');
    if (/\/(?:torrents\/)?(?:rss|feed|feeds)(?:\.(?:xml|php|atom))?(?:\/|$)/i.test(u.pathname) || /\.(?:rss|xml|atom)$/i.test(u.pathname)) {
      if (u.protocol !== 'https:' || u.href.length > 4096) throw Error('RSS-linket skal bruge HTTPS.');
      u.hash = ''; return {kind: 'rss', url: u.href};
    }
    u.hash = ''; return {kind: 'link', url: u.href};
  }
  function selectSeedTab(tabs, address, preferredId) {
    return tabs.filter(tab => {
      try { const u = new URL(tab.pendingUrl || tab.url); return u.origin === address && ['/', '/login', '/static/index.html'].includes(u.pathname); } catch { return false; }
    }).sort((a,b) => Number(b.id === preferredId) - Number(a.id === preferredId) || Number(!!b.active) - Number(!!a.active) || (b.lastAccessed || 0) - (a.lastAccessed || 0))[0];
  }
  function encode(bytes) { let text = ''; for (let i = 0; i < bytes.length; i += 8192) text += String.fromCharCode(...bytes.subarray(i, i + 8192)); return btoa(text); }
  function decode(text) { if (typeof text !== 'string' || text.length > Math.ceil(MAX_FILE / 3) * 4) throw Error(t("The torrent file is too large.")); return Uint8Array.from(atob(text), x => x.charCodeAt(0)); }
  return {MAX_FILE, origin, pattern, magnet, file, webURL, sourceLink, selectSeedTab, encode, decode};
})();
