/* Shared, dependency-free validation. Never store account credentials. */
globalThis.MediaSeedExtension = (() => {
  const MAX_FILE = 4 * 1024 * 1024;
  function origin(value) {
    const u = new URL(String(value).trim());
    if (!['http:', 'https:'].includes(u.protocol) || u.username || u.password || u.search || u.hash || u.pathname !== '/') throw Error('Brug kun MediaSeed-adressen, fx https://seed.example.dk');
    return u.origin;
  }
  function pattern(value) { const u = new URL(origin(value)); return `${u.protocol}//${u.hostname}/*`; }
  function magnet(value) {
    const text = String(value).trim();
    if (text.length > 8192 || /[\x00-\x20\x7f]/.test(text)) throw Error('Magnetlinket er ugyldigt.');
    const u = new URL(text);
    if (u.protocol !== 'magnet:' || u.hash || !u.searchParams.getAll('xt').some(x => /^urn:btih:(?:[a-f\d]{40}|[a-z2-7]{32})$/i.test(x) || /^urn:btmh:1220[a-f\d]{64}$/i.test(x))) throw Error('Magnetlinket mangler et gyldigt torrent-id.');
    return text;
  }
  function file(name, bytes) {
    if (typeof name !== 'string' || name.length > 255 || !name.toLowerCase().endsWith('.torrent') || /[\x00-\x1f/\\]/.test(name)) throw Error('Vælg en .torrent-fil.');
    if (!bytes.length || bytes.length > MAX_FILE || bytes[0] !== 100 || bytes[bytes.length - 1] !== 101) throw Error('Torrentfilen er ugyldig eller større end 4 MiB.');
  }
  function ratio(value) { const n = Number(value); if (!Number.isFinite(n) || n < 1 || n > 1000) throw Error('Ratio skal være mellem 1 og 1000.'); return n; }
  function webURL(value) { const u = new URL(value); if (!['https:', 'http:'].includes(u.protocol) || u.username || u.password || value.length > 8192) throw Error('Ugyldig torrentadresse.'); return u; }
  function encode(bytes) { let text = ''; for (let i = 0; i < bytes.length; i += 8192) text += String.fromCharCode(...bytes.subarray(i, i + 8192)); return btoa(text); }
  function decode(text) { if (typeof text !== 'string' || text.length > Math.ceil(MAX_FILE / 3) * 4) throw Error('Torrentfilen er for stor.'); return Uint8Array.from(atob(text), x => x.charCodeAt(0)); }
  return {MAX_FILE, origin, pattern, magnet, file, ratio, webURL, encode, decode};
})();
