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
  let now = Date.now();
  class Clock extends Date { static now() { return now; } }
  const html = readFileSync(new URL('../mediaseed/static/index.html', import.meta.url), 'utf8');
  const providerMarkup = html.match(/<select id="vpn-provider"[^>]*>(.*?)<\/select>/s)[1];
  for (const match of providerMarkup.matchAll(/<option value="([^"]*)">([^<]*)<\/option>/g)) {
    const option = new Element(); option.value = match[1]; option.textContent = match[2];
    find('#vpn-provider').append(option);
  }
  const state = { provider: null, integrationAvailable: true, profiles: [{ id: 'a'.repeat(24), name: 'Synthetic VPN' }] };
  const context = vm.createContext({
    Date: Clock,
    document: { querySelector: find, querySelectorAll: () => [], createElement: () => new Element(), body: new Element(), addEventListener() {} },
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
  vm.runInContext(readFileSync(new URL('../mediaseed/static/i18n.js', import.meta.url), 'utf8') + '\n' + readFileSync(new URL('../mediaseed/static/app.js', import.meta.url), 'utf8'), context);
  context.MediaAppI18n.setLanguage('da'); await context.refreshVpn();
  assert.equal(find('#vpn-provider').value, '');
  assert.equal(find('#vpn-profile').value, '');
  assert.equal(find('#vpn-profile-field').hidden, true);
  assert.equal(find('#vpn-provider').querySelector('option[value="mediavpn"]').disabled, false);
  assert.equal(requests.some(([, options]) => options.method === 'PUT'), false);

  find('#vpn-provider').value = 'mediavpn'; find('#vpn-provider').onchange();
  assert.equal(find('#vpn-profile-field').hidden, false);
  assert.equal(find('#vpn-connect').disabled, true);
  find('#vpn-profile').value = 'a'.repeat(24); find('#vpn-profile').onchange();
  context.MediaAppI18n.setLanguage('da'); await context.refreshVpn();
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
  context.MediaAppI18n.setLanguage('da'); await context.refreshVpn();
  assert.equal(find('#vpn-provider').querySelector('option[value="mediavpn"]').disabled, true);
  for (const id of ['connection-flow', 'connection-status', 'connection-country', 'connection-ip', 'connection-port', 'connection-checked']) assert.ok(html.includes(`id="${id}"`));
  const connected = {provider: 'mediavpn', ready: true, downloadsEnabled: true, checkedAt: now / 1000, country: 'Sweden', publicIp: '203.0.113.7', forwardedPort: 43210};
  context.renderConnection(connected);
  assert.equal(find('#connection-flow').dataset.state, 'active');
  assert.equal(find('#connection-country').textContent, 'Sverige');
  assert.equal(find('#connection-ip').textContent, '203.0.113.7');
  assert.equal(find('#connection-port').textContent, 'VPN-port 43210');
  const flow = find('#connection-flow');
  vm.runInContext('torrents = [{dlspeed: 23428, upspeed: 0}, {dlspeed: 100, upspeed: 0}]', context);
  context.renderTraffic(context.torrentTraffic()); context.renderTransferSummary();
  assert.equal(flow.dataset.download, 'true');
  assert.equal(flow.dataset.upload, 'false');
  assert.match(find('#connection-download').textContent, /23.0 KiB\/s/);
  assert.match(find('#transfer').textContent, /23.0 KiB\/s/);
  vm.runInContext('torrents = [{dlspeed: 0, upspeed: 2048}]', context);
  context.renderTraffic(context.torrentTraffic());
  assert.equal(flow.dataset.download, 'false'); assert.equal(flow.dataset.upload, 'true');
  vm.runInContext('torrents = [{dlspeed: null, upspeed: 2048}]', context);
  context.renderTraffic(context.torrentTraffic());
  assert.equal(flow.dataset.download, 'false'); assert.equal(flow.dataset.upload, 'false');
  context.renderTraffic({dl_info_speed: 0, up_info_speed: 2048});
  assert.equal(flow.dataset.upload, 'true');
  assert.equal(flow.dataset.download, 'false');
  assert.match(find('#connection-activity').textContent, /MediaSeed → MediaVPN → Internet/);
  assert.match(find('#connection-upload').textContent, /2.0 KiB\/s/);
  context.renderTraffic({dl_info_speed: 4096, up_info_speed: 0});
  assert.equal(flow.dataset.upload, 'false');
  assert.equal(flow.dataset.download, 'true');
  assert.match(find('#connection-activity').textContent, /Internet → MediaVPN → MediaSeed/);
  context.renderTraffic({dl_info_speed: 1, up_info_speed: 1});
  assert.equal(flow.dataset.upload, 'true');
  assert.equal(flow.dataset.download, 'true');
  context.renderTraffic({dl_info_speed: 0, up_info_speed: 0});
  assert.equal(flow.dataset.upload, 'false');
  assert.equal(flow.dataset.download, 'false');
  assert.match(find('#connection-activity').textContent, /ingen aktiv overførsel/);
  for (const transfer of [undefined, {dl_info_speed: -1, up_info_speed: 10}, {dl_info_speed: Infinity, up_info_speed: 10}, {dl_info_speed: null, up_info_speed: 10}, {dl_info_speed: 10}]) {
    context.renderTraffic(transfer);
    assert.equal(flow.dataset.upload, 'false');
    assert.equal(flow.dataset.download, 'false');
  }
  context.renderTraffic({dl_info_speed: 100, up_info_speed: 0});
  now += 31000;
  context.renderConnection({...connected, checkedAt: now / 1000});
  context.expireConnection(); // VPN polling must not extend a stale traffic measurement.
  assert.equal(flow.dataset.state, 'active');
  assert.equal(flow.dataset.download, 'false');
  assert.equal(flow.dataset.upload, 'false');
  assert.equal((html.match(/class="binary-lane binary-upload"/g) || []).length, 2);
  assert.equal((html.match(/class="binary-lane binary-download"/g) || []).length, 2);
  const css = readFileSync(new URL('../mediaseed/static/style.css', import.meta.url), 'utf8');
  assert.match(css, /data-upload="true"[^}]+binary-upload b \{ animation: binary-travel/);
  assert.match(css, /data-download="true"[^}]+binary-download b \{ animation: binary-travel[^}]+reverse/);
  assert.match(css, /prefers-reduced-motion: reduce[^\n]+binary-lane b \{ animation: none/);
  connected.checkedAt = now / 1000;
  context.renderConnection({...connected, country: null});
  assert.equal(find('#connection-country').textContent, 'Land ukendt');
  now += 31000; context.expireConnection();
  assert.equal(find('#connection-flow').dataset.state, 'unknown');
  assert.equal(find('#connection-country').textContent, 'Afventer forbindelse');
  assert.equal(find('#connection-ip').textContent, 'Downloads blokeret');
  context.renderConnection({...connected, checkedAt: now / 1000, ready: false});
  assert.equal(find('#connection-flow').dataset.state, 'blocked');
  context.renderTraffic({dl_info_speed: 100, up_info_speed: 100});
  assert.equal(flow.dataset.upload, 'false');
  assert.equal(flow.dataset.download, 'false');
  context.renderConnection({...connected, checkedAt: (now - 100000) / 1000});
  assert.equal(find('#connection-flow').dataset.state, 'unknown');
  context.renderConnection({provider: null, checkedAt: now / 1000});
  assert.equal(find('#connection-flow').dataset.state, 'disconnected');
});
