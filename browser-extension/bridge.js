/* Executed in MediaSeed's isolated world: normal session + fresh CSRF,
   same-origin writes. No auth material crosses back to the extension. */
async function mediaSeedBridge(expectedOrigin, item) {
  if (location.origin !== expectedOrigin) return {error: 'MediaSeed-adressen ændrede sig. Gem den aktuelle adresse i udvidelsen.'};
  try {
    const me = await fetch('/api/auth/me', {credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(15000)});
    if (me.status === 401) return {login: true, error: 'Log ind i MediaSeed-fanen, og prøv derefter igen.'};
    if (!me.ok) return {error: 'MediaSeed kunne ikke kontrollere din adgang.'};
    const user = await me.json();
    if (item.kind === 'check') return {connected: true, role: user.role};
    if (user.role === 'viewer' || !user.csrf) return {error: 'Din bruger har ikke adgang til at tilføje torrents.'};
    let url = '/api/torrents', body = JSON.stringify({url: item.magnet, ratio: item.ratio, start: item.start});
    let contentType = 'application/json';
    if (item.cleanup?.mode && item.cleanup.mode !== 'never') body = JSON.stringify({url: item.magnet, ratio: item.ratio, start: item.start, cleanup: item.cleanup});
    if (item.kind === 'file') {
      url += '/upload?' + new URLSearchParams({filename: item.name, ratio: item.ratio, start: item.start});
      if (item.cleanup?.mode && item.cleanup.mode !== 'never') url += '&' + new URLSearchParams({cleanup_mode: item.cleanup.mode, cleanup_hours: item.cleanup.hours, cleanup_ratio: item.cleanup.ratio, cleanup_delete_files: item.cleanup.delete_files});
      body = Uint8Array.from(atob(item.base64), x => x.charCodeAt(0)); contentType = 'application/x-bittorrent';
    }
    const response = await fetch(url, {method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: {'Content-Type': contentType, 'X-MediaSeed-CSRF': user.csrf}, body, signal: AbortSignal.timeout(25000)});
    if (!response.ok) {
      if (response.status === 401) return {login: true, error: 'Din session er udløbet. Log ind og prøv igen.'};
      if (response.status === 409) return {error: 'MediaSeed afviste start. Kontrollér VPN, eller tilføj på pause.'};
      return {error: `MediaSeed afviste tilføjelsen (HTTP ${response.status}). Kontrollér fil/link og din adgang.`};
    }
    const result = await response.json();
    return {ok: true, pending: result.pending === true, stopped: result.stopped === true, cleanupPending: result.cleanupPending === true};
  } catch { return {error: 'Forbindelsen blev afbrudt. Kontrollér listen i MediaSeed før du prøver igen; torrenten kan være tilføjet.'}; }
}

/* Same-origin source-tab download preserves the site's normal login. */
async function mediaSeedReadFile(value, requireSameOrigin = true) {
  const MAX = 4 * 1024 * 1024;
  const u = new URL(value);
  if (!['http:', 'https:'].includes(u.protocol) || u.username || u.password || (requireSameOrigin && u.origin !== location.origin)) throw Error('Torrentlinket skal hentes fra sin egen hjemmeside.');
  const r = await fetch(u.href, {credentials: 'include', redirect: 'error', signal: AbortSignal.timeout(15000)});
  if (!r.ok || Number(r.headers.get('content-length')) > MAX) throw Error('Torrentfilen kunne ikke hentes eller er større end 4 MiB.');
  const reader = r.body.getReader(), chunks = []; let length = 0;
  try {
    while (true) { const {done, value: chunk} = await reader.read(); if (done) break; length += chunk.length; if (length > MAX) throw Error('Torrentfilen er større end 4 MiB.'); chunks.push(chunk); }
  } finally { await reader.cancel(); }
  const bytes = new Uint8Array(length); let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  if (!length || bytes[0] !== 100 || bytes[length - 1] !== 101) throw Error('Linket gav ikke en torrentfil. Hent filen normalt og vælg den i udvidelsen.');
  let text = ''; for (let i = 0; i < length; i += 8192) text += String.fromCharCode(...bytes.subarray(i, i + 8192));
  return {base64: btoa(text), name: 'download.torrent'};
}
