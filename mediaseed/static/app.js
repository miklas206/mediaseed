let csrf = '';
let needsSetup = false;
const message = document.querySelector('#message');
async function api(path, body) {
  const response = await fetch('/api/' + path, {method: body ? 'POST' : 'GET', headers: {'Content-Type': 'application/json', 'X-MediaSeed-CSRF': csrf}, ...(body ? {body: JSON.stringify(body)} : {})});
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Kontrollér felterne og prøv igen.');
  return result;
}
function notify(error) { message.textContent = error.message; }
async function refresh() {
  message.textContent = '';
  try {
    const rows = await api('torrents');
    const target = document.querySelector('#torrents');
    target.replaceChildren();
    if (!rows.length) target.textContent = 'Ingen torrents';
    for (const row of rows) {
      const item = document.createElement('article'); item.className = 'torrent';
      const title = document.createElement('h2'); title.textContent = row.name;
      const progress = document.createElement('progress'); progress.max = 1; progress.value = row.progress;
      const status = document.createElement('p'); status.textContent = `${Math.round(row.progress * 100)} % · ${row.state || ''} · Ratio ${Number(row.ratio || 0).toFixed(2)}`;
      const actions = document.createElement('div'); actions.className = 'actions';
      for (const [action, label] of [['stop', 'Stop'], ['remove', 'Fjern torrent']]) {
        const button = document.createElement('button'); button.textContent = label;
        button.onclick = async () => { if (action === 'remove' && !confirm('Fjern torrenten? Downloadede filer bevares.')) return; try { await api('torrents/' + action, {torrent_hash: row.hash}); await refresh(); } catch (error) { notify(error); } };
        actions.append(button);
      }
      item.append(title, progress, status, actions); target.append(item);
    }
  } catch (error) { notify(error); }
}
function showDownloads() { document.querySelector('#identity').hidden = true; document.querySelector('#downloads').hidden = false; document.querySelector('#logout').hidden = false; refresh(); }
document.querySelector('#login').onsubmit = async (event) => {
  event.preventDefault(); const body = Object.fromEntries(new FormData(event.target));
  try { if (needsSetup) await api('auth/setup', body); csrf = (await api('auth/login', body)).csrf; event.target.reset(); showDownloads(); } catch (error) { notify(error); }
};
document.querySelector('#add').onsubmit = async (event) => { event.preventDefault(); try { await api('torrents', Object.fromEntries(new FormData(event.target))); event.target.reset(); await refresh(); } catch (error) { notify(error); } };
document.querySelector('#refresh').onclick = refresh;
document.querySelector('#logout').onclick = async () => { try { await api('auth/logout', {}); location.reload(); } catch (error) { notify(error); } };
(async () => { try { needsSetup = (await api('auth/status')).needsSetup; document.querySelector('#identity-title').textContent = needsSetup ? 'Opret administrator' : 'Log ind'; if (!needsSetup) { try { csrf = (await api('auth/me')).csrf; showDownloads(); } catch {} } } catch (error) { notify(error); } })();