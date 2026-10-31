/* Injected on its own into MediaSeed. All choices and writes stay in the app. */
async function mediaSeedBridge(expectedOrigin, item) {
  if (location.origin !== expectedOrigin) return {error: 'MediaSeed-adressen ændrede sig.'};
  try {
    const me = await fetch('/api/auth/me', {credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(15000)});
    if (me.status === 401) return {login: true, error: 'Log ind i MediaSeed. Linket venter og åbnes efter login.'};
    if (!me.ok) return {error: 'MediaSeed kunne ikke kontrollere din adgang.'};
    const user = await me.json();
    if (user.role === 'viewer' || (item.kind === 'rss' && user.role !== 'administrator')) return {error: 'Din bruger har ikke adgang til denne tilføjelse.'};
    if (!['file', 'magnet', 'rss'].includes(item.kind) || typeof item.transferId !== 'string') return {error: 'Ukendt overførsel.'};
    for (let i = 0; document.querySelector('#workspace')?.hidden && i < 20; i++) await new Promise(resolve => setTimeout(resolve, 250));
    return await new Promise(resolve => {
      const id = item.transferId;
      const timeout = setTimeout(() => { document.removeEventListener('mediaseed-import-result', done); resolve({error: 'MediaSeed er ikke klar. Tryk Fortsæt i udvidelsen.'}); }, 10000);
      function done(event) {
        let result; try { result = JSON.parse(event.detail); } catch { return; }
        if (result.id !== id) return;
        clearTimeout(timeout); document.removeEventListener('mediaseed-import-result', done);
        resolve({...result, language: user.language || 'da'});
      }
      document.addEventListener('mediaseed-import-result', done);
      document.dispatchEvent(new CustomEvent('mediaseed-import', {detail: JSON.stringify({id, item})}));
    });
  } catch { return {error: 'Forbindelsen til MediaSeed blev afbrudt. Linket er bevaret; tryk Fortsæt.'}; }
}

/* Same-origin source-tab download preserves the site's normal login. */
async function mediaSeedReadFile(value, requireSameOrigin = true) {
  const MAX = 4 * 1024 * 1024;
  const u = new URL(value);
  if (!['http:', 'https:'].includes(u.protocol) || u.username || u.password || (requireSameOrigin && u.origin !== location.origin)) throw Error('Torrentlinket skal hentes fra sin egen hjemmeside.');
  const r = await fetch(u.href, {credentials: 'include', redirect: 'error', cache: 'no-store', signal: AbortSignal.timeout(15000)});
  if (!r.ok || Number(r.headers.get('content-length')) > MAX) throw Error('Torrentfilen kunne ikke hentes eller er større end 4 MiB.');
  const reader = r.body.getReader(), chunks = []; let length = 0;
  try {
    while (true) { const {done, value: chunk} = await reader.read(); if (done) break; length += chunk.length; if (length > MAX) throw Error('Torrentfilen er større end 4 MiB.'); chunks.push(chunk); }
  } finally { await reader.cancel(); }
  const bytes = new Uint8Array(length); let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  if (!length || bytes[0] !== 100 || bytes[length - 1] !== 101) throw Error('Linket gav ikke en torrentfil. Hent filen normalt og vælg den i udvidelsen.');
  let text = ''; for (let i = 0; i < length; i += 8192) text += String.fromCharCode(...bytes.subarray(i, i + 8192));
  // Read the original info name without re-encoding or altering torrent bytes.
  // This function is injected on its own, so helpers must remain local.
  let title = '', cursor = 0, tokens = 0;
  const decoder = new TextDecoder('utf-8', {fatal: true});
  function read(depth = 0, scope = '') {
    if (depth > 32 || ++tokens > 100000 || cursor >= length) throw Error('Invalid metadata');
    const marker = bytes[cursor];
    if (marker >= 48 && marker <= 57) {
      let end = cursor;
      while (end < length && bytes[end] >= 48 && bytes[end] <= 57 && end - cursor < 8) end++;
      if (bytes[end] !== 58) throw Error('Invalid string');
      const size = Number(String.fromCharCode(...bytes.subarray(cursor, end)));
      cursor = end + 1;
      if (cursor + size > length) throw Error('Truncated string');
      const value = bytes.subarray(cursor, cursor + size); cursor += size; return value;
    }
    cursor++;
    if (marker === 105) {
      while (cursor < length && bytes[cursor] !== 101) cursor++;
      if (cursor++ >= length) throw Error('Invalid integer');
    } else if (marker === 108 || marker === 100) {
      while (cursor < length && bytes[cursor] !== 101) {
        if (marker === 108) { read(depth + 1); continue; }
        const keyBytes = read(depth + 1);
        if (!(keyBytes instanceof Uint8Array)) throw Error('Invalid key');
        const key = keyBytes.length < 64 ? decoder.decode(keyBytes) : '';
        const value = read(depth + 1, depth === 0 && key === 'info' ? 'info' : '');
        if (scope === 'info' && (key === 'name.utf-8' || (key === 'name' && !title)) && value instanceof Uint8Array && value.length <= 4096) title = decoder.decode(value);
      }
      if (cursor++ >= length) throw Error('Invalid collection');
    } else throw Error('Invalid metadata');
  }
  try { read(); if (cursor !== length) title = ''; } catch { title = ''; }
  if (!title) {
    const disposition = r.headers.get('content-disposition') || '';
    const encoded = disposition.match(/filename\*\s*=\s*UTF-8''([^;]+)/i);
    const plain = disposition.match(/filename\s*=\s*(?:"([^"\r\n]*)"|([^;\r\n]*))/i);
    try { title = encoded ? decodeURIComponent(encoded[1].trim()) : (plain?.[1] || plain?.[2] || '').trim(); } catch { title = ''; }
  }
  title = title.replace(/[\x00-\x1f\x7f/\\:*?"<>|]/g, '_').replace(/^[. ]+|[. ]+$/g, '').replace(/\.torrent$/i, '');
  title = [...title].slice(0, 100).join('') || 'download';
  return {base64: btoa(text), name: title + '.torrent'};
}
