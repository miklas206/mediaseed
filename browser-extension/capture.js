(() => {
  if (globalThis.mediaSeedCaptureInstalled) return;
  globalThis.mediaSeedCaptureInstalled = true;
  const api = globalThis.browser || chrome;
  document.addEventListener('click', event => {
    if (!event.isTrusted || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const a = event.target.closest?.('a[href]'); if (!a || !enabled) return;
    if (!MediaSeedExtension.capturable(a.href, location.origin)) return;
    event.preventDefault(); event.stopImmediatePropagation();
    api.runtime.sendMessage({type: 'capture', url: a.href}).then(result => { if (result?.error && !result.reviewed) alert('MediaSeed: ' + result.error); }, () => alert(failureMessage));
  }, true);
  let enabled = false;
  let failureMessage = 'MediaSeed could not be opened. Try right-click → Add to MediaSeed.';
  const refresh = () => api.storage.local.get(['sites', 'origin', 'language']).then(c => { enabled = (c.sites || []).includes(location.origin) && c.origin !== location.origin; failureMessage = c.language === 'da' ? 'MediaSeed kunne ikke åbnes. Prøv højreklik → Tilføj i MediaSeed.' : 'MediaSeed could not be opened. Try right-click → Add to MediaSeed.'; });
  api.storage.onChanged.addListener(refresh); void refresh();
})();
