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
  function ratio(value) { const n = Number(value); if (!Number.isFinite(n) || n < 1 || n > 1000) throw Error(t("Ratio must be between 1 and 1000.")); return n; }
  function defaults(value = {}) {
    const c = value.cleanup || {}, mode = c.mode ?? 'never', hours = Number(c.hours ?? 49);
    if (!['never', 'time', 'ratio', 'both', 'either'].includes(mode) || !Number.isFinite(hours) || hours < 1 / 60 || hours > 87600) throw Error(t("Choose valid cleanup and seeding time between one minute and 87600 hours."));
    if (value.start !== undefined && typeof value.start !== 'boolean' || c.delete_files !== undefined && typeof c.delete_files !== 'boolean') throw Error(t("Choose valid start and deletion settings."));
    return {start: value.start ?? true, ratio: ratio(value.ratio ?? 1), cleanup: {mode, hours, ratio: ratio(c.ratio ?? 1), delete_files: c.delete_files ?? false}};
  }
  function webURL(value) { const u = new URL(value); if (!['https:', 'http:'].includes(u.protocol) || u.username || u.password || value.length > 8192) throw Error(t("Invalid torrent address.")); return u; }
  function capturable(value, sourceOrigin) {
    try {
      if (value.startsWith('magnet:')) { magnet(value); return true; }
      const u = webURL(value);
      if (u.pathname.toLowerCase().endsWith('.torrent')) return true;
      // UNIT3D download links have no .torrent suffix. Restrict this adapter to
      // a user-enabled NordicBytes origin and its actual torrent download route.
      return u.origin === sourceOrigin && ['nordicbytes.org', 'www.nordicbytes.org'].includes(u.hostname) && /^\/torrents\/download\/[^/]+\/?$/.test(u.pathname);
    } catch { return false; }
  }
  function encode(bytes) { let text = ''; for (let i = 0; i < bytes.length; i += 8192) text += String.fromCharCode(...bytes.subarray(i, i + 8192)); return btoa(text); }
  function decode(text) { if (typeof text !== 'string' || text.length > Math.ceil(MAX_FILE / 3) * 4) throw Error(t("The torrent file is too large.")); return Uint8Array.from(atob(text), x => x.charCodeAt(0)); }
  return {MAX_FILE, origin, pattern, magnet, file, ratio, defaults, webURL, capturable, encode, decode};
})();
