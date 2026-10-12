function detectMediaSeedBrowser(userAgent, brands = [], brave = false) {
  const combined = `${userAgent} ${brands.map(b => b.brand).join(' ')}`;
  if (/FxiOS|EdgiOS|CriOS|OPiOS/i.test(combined) || (/iPhone|iPad|iPod/i.test(combined) && !/Safari/i.test(combined))) return {package: 'safari', label: 'iOS · udvidelser kræver Safari og en pakket Safari-app'};
  if (/Firefox|FxiOS/i.test(combined)) return {package: 'firefox', label: 'Firefox'};
  if (/Edg|Microsoft Edge/i.test(combined)) return {package: 'chromium', label: 'Edge'};
  if (/OPR|Opera/i.test(combined)) return {package: 'chromium', label: 'Opera'};
  if (brave) return {package: 'chromium', label: 'Brave'};
  if (/Chrome|Chromium|CriOS/i.test(combined)) return {package: 'chromium', label: 'Chrome / Chromium'};
  if (/Safari/i.test(combined)) return {package: 'safari', label: 'Safari'};
  return {package: 'chromium', label: 'Ukendt browser · vælg manuelt'};
}
async function initializeExtensionDownload() {
  let brave = false; try { brave = await navigator.brave?.isBrave() || false; } catch {}
  const detected = detectMediaSeedBrowser(navigator.userAgent, navigator.userAgentData?.brands || [], brave);
  const select = document.getElementById('extension-browser'); select.value = detected.package;
  document.getElementById('extension-detected').textContent = `Genkendt: ${detected.label}. Du kan ændre valget ovenfor.`;
  document.getElementById('extension-origin').textContent = location.origin;
  const update = () => {
    const browser = select.value;
    for (const key of ['chromium', 'firefox', 'safari']) document.getElementById('install-' + key).hidden = browser !== key;
    const link = document.getElementById('extension-download');
    link.href = `/static/extensions/mediaseed-${browser}${browser === 'safari' ? '-source' : ''}.zip`;
    link.textContent = browser === 'safari' ? 'Hent Safari-kilde til Xcode' : browser === 'firefox' ? 'Hent til Firefox' : 'Hent til Chrome / Edge / Brave / Opera';
  };
  select.addEventListener('change', update); update();
}
void initializeExtensionDownload();
