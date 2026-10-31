/* Presentation only: reuse the app's navigation and existing command buttons. */
(() => {
  const workspace = document.querySelector('#workspace');
  const header = workspace?.querySelector('.seed-page-heading');
  if (!header) return;
  const title = header.querySelector('h1'), description = header.querySelector('p');
  const intro = element('div', undefined, 'workspace-intro');
  const eyebrow = element('span', 'MEDIASEED', 'workspace-eyebrow');
  intro.append(eyebrow, title, description);
  const actions = element('div', undefined, 'workspace-page-actions');
  header.append(intro, actions);
  const pages = {
    downloads: ['Seedbox', 'Your downloads, sharing and connection at a glance.'],
    feeds: ['RSS', 'Your feeds. Your filters. Automatic downloads under your control.'],
    trackers: ['Trackers', 'Account totals and history, collected in one place.'],
    insights: ['Insights', 'Alerts and seeding suggestions that need your attention.'],
    vpn: ['VPN', 'Your protected connection, with clear status and control.'],
  };
  const selectors = {insights:'#insights-refresh',downloads:'#new-torrent',feeds:'#feeds > button[data-admin-only]',trackers:'#trackers > button[data-admin-only]'};
  const commands = Object.entries(selectors).map(([view, selector]) => {
    const button = document.querySelector(selector);
    if (!button) return null;
    const home = document.createComment('Original command position'); button.before(home);
    return {view, button, home};
  }).filter(Boolean);
  function render() {
    const view = document.querySelector('[data-view][aria-pressed="true"]')?.dataset.view || 'downloads';
    const page = pages[view] || pages.downloads;
    workspace.dataset.currentView = view;
    title.textContent = t(page[0]); description.textContent = t(page[1]);
    workspace.querySelector('.seed-stats').hidden = view !== 'downloads';
    for (const command of commands) {
      if (command.view === view) (view === 'downloads' && document.querySelector('.seed-list-actions') || actions).append(command.button);
      else command.home.after(command.button);
    }
  }
  for (const button of document.querySelectorAll('[data-view]')) button.addEventListener('click', render);
  new MutationObserver(render).observe(document.documentElement, {attributes:true, attributeFilter:['lang']});
  render();
})();
