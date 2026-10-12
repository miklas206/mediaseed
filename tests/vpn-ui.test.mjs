import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import test from 'node:test';

// Run the shipped script against a small DOM substitute, without a browser.
class Element {
  value = '';
  disabled = false;
  hidden = false;
  children = [];
  dataset = {};
  classList = { add() {}, remove() {}, contains() { return false; } };
  elements = { namedItem() { return { value: '' }; } };
  get firstChild() { return this.children[0]; }
  get options() { return this.children; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; this.value = ''; }
  setAttribute() {}
  addEventListener() {}
  querySelectorAll() { return []; }
  querySelector(selector) {
    const value = selector.match(/^option\[value="([^"]*)"\]$/)?.[1];
    return this.children.find(child => child.value === value) || null;
  }
}

test('shipped provider markup supports profile choice, polling, explicit connect and disconnect', async () => {
  const nodes = new Map();
  const find = selector => {
    if (!nodes.has(selector)) nodes.set(selector, new Element());
    return nodes.get(selector);
  };
  const requests = [];
  const html = readFileSync(new URL('../mediaseed/static/index.html', import.meta.url), 'utf8');
  const providerMarkup = html.match(/<select id="vpn-provider"[^>]*>(.*?)<\/select>/s)[1];
  for (const match of providerMarkup.matchAll(/<option value="([^"]*)">([^<]*)<\/option>/g)) {
    const option = new Element(); option.value = match[1]; option.textContent = match[2];
    find('#vpn-provider').append(option);
  }
  const state = { provider: null, integrationAvailable: true, profiles: [{ id: 'a'.repeat(24), name: 'Synthetic VPN' }] };
  const context = vm.createContext({
    document: { querySelector: find, querySelectorAll: () => [], createElement: () => new Element(), body: new Element() },
    setInterval() {}, setTimeout() {}, clearTimeout() {},
    fetch: async (url, options = {}) => {
      requests.push([url, options]);
      if (url === '/api/vpn' && options.method === 'PUT') {
        const body = JSON.parse(options.body);
        state.provider = body.enabled ? body.provider : null;
        state.profileId = body.enabled ? body.profileId : null;
      }
      return { ok: true, json: async () => url === '/api/vpn' ? state : url === '/api/auth/status' ? { needsSetup: true } : url === '/api/torrents' ? [] : {} };
    },
  });
  vm.runInContext(readFileSync(new URL('../mediaseed/static/app.js', import.meta.url), 'utf8'), context);
  await context.refreshVpn();
  assert.equal(find('#vpn-provider').value, '');
  assert.equal(find('#vpn-profile').value, '');
  assert.equal(find('#vpn-profile-field').hidden, true);
  assert.equal(find('#vpn-provider').querySelector('option[value="mediavpn"]').disabled, false);
  assert.equal(requests.some(([, options]) => options.method === 'PUT'), false);

  find('#vpn-provider').value = 'mediavpn'; find('#vpn-provider').onchange();
  assert.equal(find('#vpn-profile-field').hidden, false);
  assert.equal(find('#vpn-connect').disabled, true);
  find('#vpn-profile').value = 'a'.repeat(24); find('#vpn-profile').onchange();
  await context.refreshVpn();
  assert.equal(find('#vpn-provider').value, 'mediavpn');
  assert.equal(find('#vpn-profile').value, 'a'.repeat(24));
  assert.equal(find('#vpn-connect').disabled, false);
  assert.equal(requests.some(([, options]) => options.method === 'PUT'), false);

  await find('#vpn-form').onsubmit({ preventDefault() {} });
  assert.deepEqual(JSON.parse(requests.find(([, options]) => options.method === 'PUT')[1].body), { enabled: true, provider: 'mediavpn', profileId: 'a'.repeat(24) });
  await find('#vpn-disconnect').onclick();
  assert.equal(state.provider, null);
  assert.equal(find('#vpn-provider').value, '');
  assert.equal(find('#vpn-profile-field').hidden, true);
  state.integrationAvailable = false;
  await context.refreshVpn();
  assert.equal(find('#vpn-provider').querySelector('option[value="mediavpn"]').disabled, true);
});
