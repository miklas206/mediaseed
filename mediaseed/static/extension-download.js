const {t, setLanguage, locale} = globalThis.MediaAppI18n;
function detectMediaSeedBrowser(userAgent, brands = [], brave = false) {
  const combined = `${userAgent} ${brands.map(b => b.brand).join(' ')}`;
  if (/FxiOS|EdgiOS|CriOS|OPiOS/i.test(combined) || (/iPhone|iPad|iPod/i.test(combined) && !/Safari/i.test(combined))) return {package: 'safari', label: t("iOS · extensions require Safari and a packaged Safari app")};
  if (/Firefox|FxiOS/i.test(combined)) return {package: 'firefox', label: 'Firefox'};
  if (/Edg|Microsoft Edge/i.test(combined)) return {package: 'chromium', label: 'Edge'};
  if (/OPR|Opera/i.test(combined)) return {package: 'chromium', label: 'Opera'};
  if (brave) return {package: 'chromium', label: 'Brave'};
  if (/Chrome|Chromium|CriOS/i.test(combined)) return {package: 'chromium', label: 'Chrome / Chromium'};
  if (/Safari/i.test(combined)) return {package: 'safari', label: 'Safari'};
  return {package: 'chromium', label: t("Unknown browser · choose manually")};
}
async function readExtensionPackage(response) {
  const max = 2 * 1024 * 1024, header = response.headers.get('content-length'), expected = header === null ? null : Number(header);
  if (!response.ok || !['application/zip', 'application/x-zip-compressed'].includes((response.headers.get('content-type') || '').split(';')[0])) throw Error(t("The server did not provide a ZIP package. Reload the page and try again."));
  if (expected !== null && (!Number.isInteger(expected) || expected < 22 || expected > max)) throw Error(t("The download package size is invalid."));
  const reader = response.body.getReader(), chunks = []; let size = 0;
  try {
    while (true) {
      const {done, value} = await reader.read(); if (done) break;
      size += value.length; if (size > max) throw Error(t("The download package is too large.")); chunks.push(value);
    }
  } finally { await reader.cancel().catch(() => {}); }
  if (size < 22 || expected !== null && expected !== size) throw Error(t("The download was interrupted. The complete package was not received."));
  const bytes = new Uint8Array(size); let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  if (bytes[0] !== 80 || bytes[1] !== 75 || bytes[2] !== 3 || bytes[3] !== 4 || bytes[size - 22] !== 80 || bytes[size - 21] !== 75 || bytes[size - 20] !== 5 || bytes[size - 19] !== 6) throw Error(t("The download package is incomplete or invalid."));
  return new Blob([bytes], {type: 'application/zip'});
}
async function saveExtensionPackage(path) {
  const name = path.split('/').at(-1);
  if (!/^mediaseed-(chromium|firefox|safari-source)\.zip$/.test(name) || path !== '/static/extensions/' + name) throw Error(t("Unknown download package."));
  // Fetch through the page's accepted HTTPS connection, then save local bytes.
  // Native downloads start a separate certificate check and may reject a LAN CA.
  const response = await fetch(path, {credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: AbortSignal.timeout(30000)});
  const blob = await readExtensionPackage(response), url = URL.createObjectURL(blob), anchor = document.createElement('a');
  try {
    anchor.href = url; anchor.download = name; document.body.append(anchor); anchor.click();
  } finally { anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 60000); }
}
async function initializeExtensionDownload() {
  try { const response = await fetch('/api/auth/me', {credentials: 'same-origin'}); if (response.ok) setLanguage((await response.json()).language || 'en'); } catch {}
  let brave = false; try { brave = await navigator.brave?.isBrave() || false; } catch {}
  const detected = detectMediaSeedBrowser(navigator.userAgent, navigator.userAgentData?.brands || [], brave);
  const select = document.getElementById('extension-browser'); select.value = detected.package;
  document.getElementById('extension-detected').textContent = t("Detected: {0}. You can change the selection above.", {0: detected.label});
  document.getElementById('extension-origin').textContent = location.origin;
  const update = () => {
    const browser = select.value;
    for (const key of ['chromium', 'firefox', 'safari']) document.getElementById('install-' + key).hidden = browser !== key;
    const link = document.getElementById('extension-download');
    link.href = `/static/extensions/mediaseed-${browser}${browser === 'safari' ? '-source' : ''}.zip`;
    link.textContent = browser === 'safari' ? t("Download Safari source for Xcode") : browser === 'firefox' ? t("Download for Firefox") : t("Download for Chrome / Edge / Brave / Opera");
  };
  select.addEventListener('change', update); update();
  const link = document.getElementById('extension-download'), progress = document.getElementById('extension-progress'), notice = document.getElementById('message');
  let busy = false, noticeTimer;
  const clearNotice = () => { notice.textContent = ''; };
  const resumeNotice = () => { clearTimeout(noticeTimer); noticeTimer = setTimeout(clearNotice, 8000); };
  notice.addEventListener('mouseenter', () => clearTimeout(noticeTimer)); notice.addEventListener('focus', () => clearTimeout(noticeTimer));
  notice.addEventListener('mouseleave', resumeNotice); notice.addEventListener('blur', resumeNotice);
  link.addEventListener('click', async event => {
    event.preventDefault(); if (busy) return;
    busy = true; link.setAttribute('aria-disabled', 'true'); select.disabled = true; notice.textContent = ''; progress.textContent = t("Downloading the complete package…");
    try { await saveExtensionPackage(link.getAttribute('href')); progress.textContent = t("The package was downloaded and sent to your browser's downloads."); }
    catch (error) { progress.textContent = ''; notice.textContent = error instanceof TypeError || error.name === 'TimeoutError' ? t("The package could not be downloaded. Check the MediaSeed connection and HTTPS certificate, then try again.") : error.message; resumeNotice(); }
    finally { busy = false; link.removeAttribute('aria-disabled'); select.disabled = false; }
  });
}
void initializeExtensionDownload();
