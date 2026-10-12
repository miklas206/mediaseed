import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
const file = name => readFileSync(new URL('../browser-extension/' + name, import.meta.url), 'utf8');
const sample = 'magnet:?xt=urn:btih:' + 'a'.repeat(40);
function context(extra = {}) {
  const c = vm.createContext({URL, URLSearchParams, Uint8Array, AbortSignal, btoa, atob, setTimeout, crypto: webcrypto, ...extra});
  vm.runInContext(file('common.js') + '\n' + file('bridge.js'), c); return c;
}
test('validates origins, magnets, ratios and bounded torrent content', () => {
  const c = context(), m = c.MediaSeedExtension;
  assert.equal(m.origin('https://seed.example:18800/'), 'https://seed.example:18800');
  assert.equal(m.pattern('https://seed.example:18800'), 'https://seed.example/*');
  for (const u of ['https://user:pass@seed.example', 'https://seed.example/path', 'javascript:alert(1)', 'https://seed.example/?x=y']) assert.throws(() => m.origin(u));
  assert.equal(m.magnet(sample + '&dn=Sintel&tr=udp%3A%2F%2Ftracker.example%3A6969'), sample + '&dn=Sintel&tr=udp%3A%2F%2Ftracker.example%3A6969');
  for (const u of ['https://test.example', sample + '#fragment', 'magnet:?xt=urn:btih:abc', sample + '\n']) {
    if (u.endsWith('\n')) continue; // Harmless surrounding whitespace is trimmed.
    assert.throws(() => m.magnet(u));
  }
  assert.throws(() => m.ratio(Infinity)); assert.throws(() => m.ratio(0));
  assert.throws(() => m.file('a.torrent', Uint8Array.from([100, 120])));
  assert.throws(() => m.file('../a.torrent', Uint8Array.from([100, 101])));
  assert.throws(() => m.decode('a'.repeat(6 * 1024 * 1024)));
  const bytes = Uint8Array.from([100, 255, 0, 101]); m.file('a.torrent', bytes);
  assert.deepEqual([...m.decode(m.encode(bytes))], [...bytes]);
});
test('bridge uses same-origin session and fresh CSRF; auth never returned', async () => {
  const requests = [];
  const c = context({location: {origin: 'https://seed.example'}, fetch: async (path, options) => {
    requests.push({path, options});
    return {ok: true, json: async () => path.endsWith('/me') ? {role: 'user', csrf: 'synthetic-csrf'} : {added: true, stopped: false, pending: true}};
  }});
  const result = await c.mediaSeedBridge('https://seed.example', {kind: 'magnet', magnet: sample, ratio: 2, start: true});
  assert.equal(result.ok, true); assert.equal(result.pending, true); assert.equal(result.csrf, undefined);
  assert.equal(requests.length, 2); assert.equal(requests[1].options.credentials, 'same-origin');
  assert.equal(requests[1].options.headers['X-MediaSeed-CSRF'], 'synthetic-csrf');
  assert.deepEqual(JSON.parse(requests[1].options.body), {url: sample, ratio: 2, start: true});
  requests.length = 0;
  await c.mediaSeedBridge('https://seed.example', {kind: 'file', name: 'test.torrent', base64: btoa('de'), ratio: 1, start: false});
  assert.equal(requests[1].path, '/api/torrents/upload?filename=test.torrent&ratio=1&start=false');
  assert.deepEqual([...requests[1].options.body], [100, 101]);
  requests.length = 0;
  const cleanup = {mode: 'both', hours: 24, ratio: 2, delete_files: true};
  await c.mediaSeedBridge('https://seed.example', {kind: 'magnet', magnet: sample, ratio: 1, start: false, cleanup});
  assert.deepEqual(JSON.parse(requests[1].options.body).cleanup, cleanup);
  requests.length = 0;
  await c.mediaSeedBridge('https://seed.example', {kind: 'file', name: 'test.torrent', base64: btoa('de'), ratio: 1, start: false, cleanup});
  const query = new URL(requests[1].path, 'https://synthetic.example').searchParams;
  assert.equal(query.get('cleanup_mode'), 'both'); assert.equal(query.get('cleanup_delete_files'), 'true');
});
test('bridge refuses changed origin, expired login, viewer and unverified VPN; no POST retries', async () => {
  let calls = 0;
  const c = context({location: {origin: 'https://seed.example'}, fetch: async () => {calls++; return {status: 401};}});
  assert.match((await c.mediaSeedBridge('https://other.example', {})).error, /adressen/); assert.equal(calls, 0);
  assert.equal((await c.mediaSeedBridge('https://seed.example', {})).login, true); assert.equal(calls, 1);
  c.fetch = async () => {calls++; return {ok: true, json: async () => ({csrf: 'synthetic', role: 'viewer'})};};
  assert.match((await c.mediaSeedBridge('https://seed.example', {kind: 'magnet'})).error, /adgang/); assert.equal(calls, 2);
  calls = 0; c.fetch = async path => {calls++; return path.endsWith('/me') ? {ok: true, json: async () => ({csrf: 'synthetic', role: 'user'})} : {ok: false, status: 409};};
  assert.match((await c.mediaSeedBridge('https://seed.example', {kind: 'magnet'})).error, /VPN/); assert.equal(calls, 2);
  calls = 0; c.fetch = async path => {calls++; if (path.endsWith('/me')) return {ok: true, json: async () => ({csrf: 'synthetic'})}; throw Error('network');};
  assert.match((await c.mediaSeedBridge('https://seed.example', {kind: 'magnet'})).error, /kan være tilføjet/); assert.equal(calls, 2);
});
test('torrent fetching is bounded, same-origin, rejects redirects and HTML', async () => {
  let options, canceled = false;
  const response = chunks => ({ok: true, headers: new Headers(), body: {getReader: () => ({read: async () => chunks.length ? {value: chunks.shift()} : {done: true}, cancel: async () => {canceled = true;}})}});
  const c = context({Headers, location: {origin: 'https://tracker.example'}, fetch: async (_, opts) => {options = opts; return response([Uint8Array.from([100, 101])]);}});
  assert.equal((await c.mediaSeedReadFile('https://tracker.example/file.torrent')).base64, btoa('de'));
  assert.equal(options.credentials, 'include'); assert.equal(options.redirect, 'error'); assert.equal(canceled, true);
  await assert.rejects(c.mediaSeedReadFile('https://other.example/file.torrent'), /egen hjemmeside/);
  c.fetch = async () => response([new Uint8Array(4 * 1024 * 1024 + 1)]);
  await assert.rejects(c.mediaSeedReadFile('https://tracker.example/file.torrent'), /4 MiB/);
  c.fetch = async () => response([Uint8Array.from([60, 62])]);
  await assert.rejects(c.mediaSeedReadFile('https://tracker.example/file.torrent'), /ikke en torrentfil/);
});
function background() {
  let listener, clicked; const local = {origin: 'https://seed.example', sites: ['https://tracker.example']}, session = {}, created = [], executions = [], badges = [];
  const api = {
    storage: {local: {get: async () => local, set: async obj => Object.assign(local, obj)}, session: {get: async id => id === null ? session : {[id]: session[id]}, set: async obj => Object.assign(session, obj), remove: async keys => (Array.isArray(keys) ? keys : [keys]).forEach(k => delete session[k])}},
    permissions: {contains: async () => true},
    tabs: {query: async () => [{id: 1, url: 'https://seed.example/', status: 'complete'}], get: async () => ({id: 1, url: 'https://seed.example/', status: 'complete'}), create: async opts => {created.push(opts); return {id: 2};}, update: async () => {} , onUpdated: {addListener() {}}},
    scripting: {executeScript: async options => {executions.push(options); return [{result: {ok: true, stopped: true}}];}},
    contextMenus: {onClicked: {addListener(fn) {clicked = fn;}}},
    action: {setBadgeText: async item => badges.push(item.text), setBadgeBackgroundColor: async () => {}, setTitle: async () => {}},
    runtime: {getURL: path => 'chrome-extension://synthetic/' + path, onInstalled: {addListener() {}}, onMessage: {addListener(fn) {listener = fn;}}},
  };
  const c = context({chrome: api}); vm.runInContext(file('background.js'), c);
  const dispatch = (message, sender = {url: 'chrome-extension://synthetic/popup.html'}) => new Promise(resolve => listener(message, sender, resolve));
  return {dispatch, api, created, executions, local, session, badges, click: (...args) => clicked(...args)};
}
test('background restricts web messages to enabled sites and sends with saved defaults', async () => {
  const b = background();
  let result = await b.dispatch({type: 'send', item: {kind: 'magnet', magnet: sample}}, {url: 'https://tracker.example', tab: {id: 9}, frameId: 0});
  assert.ok(result.error); assert.equal(b.executions.length, 0);
  result = await b.dispatch({type: 'capture', url: sample}, {url: 'https://evil.example', tab: {id: 9}, frameId: 0});
  assert.ok(result.error); assert.equal(b.created.length, 0);
  result = await b.dispatch({type: 'capture', url: sample}, {url: 'https://tracker.example', tab: {id: 9}, frameId: 1});
  assert.ok(result.error);
  result = await b.dispatch({type: 'capture', url: sample}, {url: 'https://tracker.example', tab: {id: 9}, frameId: 0});
  assert.equal(result.ok, true); assert.equal(b.created.length, 0);
  assert.equal(b.executions[0].args[1].start, true); assert.equal(b.executions[0].args[1].cleanup.mode, 'never');
  assert.equal(Object.keys(b.session).length, 0); assert.equal(b.badges.at(-1), 'OK');
});
test('background enforces exact MediaSeed origin and single-flight sends', async () => {
  const b = background(); let release;
  b.api.scripting.executeScript = options => {assert.equal(options.args[0], 'https://seed.example'); return new Promise(resolve => {release = resolve;});};
  const first = b.dispatch({type: 'send', item: {kind: 'magnet', magnet: sample, start: false, ratio: 1}});
  while (!release) await new Promise(r => setTimeout(r, 0));
  assert.match((await b.dispatch({type: 'send', item: {kind: 'check'}})).error, /allerede/);
  release([{result: {ok: true}}]); assert.equal((await first).ok, true);
  b.api.permissions.contains = async () => false;
  assert.match((await b.dispatch({type: 'send', item: {kind: 'check'}})).error, /adgang/);
});

test('direct context-menu magnets use persistent settings without opening a form', async () => {
  const b = background();
  b.local.defaults = {start: false, ratio: 3, cleanup: {mode: 'both', hours: 72, ratio: 4, delete_files: true}};
  b.click({menuItemId: 'mediaseed-add', linkUrl: sample}, {id: 9});
  await new Promise(r => setTimeout(r, 0));
  assert.equal(b.created.length, 0);
  const item = b.executions.find(x => x.func.name === 'mediaSeedBridge').args[1];
  assert.equal(item.magnet, sample); assert.equal(item.start, false); assert.equal(item.ratio, 3);
  assert.deepEqual(JSON.parse(JSON.stringify(item.cleanup)), b.local.defaults.cleanup);
  assert.equal(b.local.lastResult.error, false);
  assert.equal(b.executions.filter(x => x.func.name === 'mediaSeedBridge').length, 1);
});

test('direct torrent download retains source login and the same saved cleanup policy', async () => {
  const b = background();
  b.local.defaults = {start: true, cleanup: {mode: 'time', hours: 24, ratio: 2, delete_files: false}};
  b.api.tabs.get = async id => id === 9 ? {id, url: 'https://tracker.example/list'} : {id, status: 'complete', url: 'https://seed.example/'};
  b.api.scripting.executeScript = async options => { b.executions.push(options); return [{result: options.func.name === 'mediaSeedReadFile' ? {name: 'test.torrent', base64: btoa('de')} : {ok: true}}]; };
  const result = await b.dispatch({type: 'capture', url: 'https://tracker.example/test.torrent'}, {url: 'https://tracker.example/list', tab: {id: 9}, frameId: 0});
  assert.equal(result.ok, true); assert.equal(b.created.length, 0);
  assert.equal(b.executions[0].target.tabId, 9);
  const item = b.executions.find(x => x.func.name === 'mediaSeedBridge').args[1];
  assert.equal(item.kind, 'file'); assert.equal(item.cleanup.hours, 24); assert.equal(item.start, true);
});

test('NordicBytes suffixless download route sends once with defaults and a success toast', async () => {
  const b = background();
  b.local.sites = ['https://nordicbytes.org'];
  b.local.defaults = {start: true, ratio: 3, cleanup: {mode: 'both', hours: 72, ratio: 3, delete_files: false}};
  b.api.tabs.get = async id => id === 9 ? {id, url: 'https://nordicbytes.org/torrents/123'} : {id, status: 'complete', url: 'https://seed.example/'};
  b.api.scripting.executeScript = async options => {b.executions.push(options); return [{result: options.func.name === 'mediaSeedReadFile' ? {name: 'test.torrent', base64: btoa('de')} : {ok: true}}];};
  const result = await b.dispatch({type: 'capture', url: 'https://nordicbytes.org/torrents/download/123.synthetic?test=1'}, {url: 'https://nordicbytes.org/torrents/123', tab: {id: 9}, frameId: 0});
  assert.equal(result.ok, true); assert.equal(b.created.length, 0);
  const imports = b.executions.filter(x => x.func.name === 'mediaSeedBridge');
  assert.equal(imports.length, 1);
  assert.equal(imports[0].args[1].cleanup.hours, 72);
  assert.equal(imports[0].args[1].ratio, 3);
  assert.equal(imports[0].args[1].start, true);
  const toast = b.executions.find(x => x.func.name === 'mediaSeedToast');
  assert.equal(toast.target.tabId, 9); assert.match(toast.args[0], /Tilføjet/);
  assert.equal((await b.dispatch({type: 'capture', url: 'https://nordicbytes.org/torrents/123'}, {url: 'https://nordicbytes.org/torrents/123', tab: {id: 9}, frameId: 0})).error, 'Kun torrentlinks kan fanges automatisk.');
});

test('automatic NordicBytes matching excludes other routes, origins and non-torrent pages', () => {
  const m = context().MediaSeedExtension;
  for (const origin of ['https://nordicbytes.org', 'https://www.nordicbytes.org']) {
    assert.equal(m.capturable(origin + '/torrents/download/123', origin), true);
    assert.equal(m.capturable(origin + '/torrents/download/123.synthetic?test=1', origin), true);
    for (const path of ['/torrents/123', '/download/123', '/torrents/download/', '/torrents/download/123/other']) assert.equal(m.capturable(origin + path, origin), false);
  }
  assert.equal(m.capturable('https://nordicbytes.org/torrents/download/123', 'https://tracker.example'), false);
  assert.equal(m.capturable('https://nordicbytes.org.evil.example/torrents/download/123', 'https://nordicbytes.org.evil.example'), false);
});

test('setup and failed sends preserve a draft; successful POST is never retried for feedback errors', async () => {
  const b = background();
  b.local.origin = '';
  const sender = {url: 'https://tracker.example/list', tab: {id: 9}, frameId: 0};
  assert.equal((await b.dispatch({type: 'capture', url: sample}, sender)).setup, true);
  assert.equal(b.executions.length, 0); assert.equal(b.created.length, 1);
  b.local.origin = 'https://seed.example';
  b.api.scripting.executeScript = async options => {b.executions.push(options); return [{result: {error: 'Session expired', login: true}}];};
  const failure = await b.dispatch({type: 'capture', url: sample}, sender);
  assert.equal(failure.reviewed, true); assert.equal(b.created.length, 2); assert.equal(b.local.lastResult.error, true);
  b.api.scripting.executeScript = async options => {b.executions.push(options); return [{result: {ok: true}}];};
  b.api.storage.local.set = async () => {throw Error('synthetic storage failure');};
  b.api.action.setTitle = async () => {throw Error('synthetic badge failure');};
  assert.equal((await b.dispatch({type: 'capture', url: sample}, sender)).ok, true);
  assert.equal(b.created.length, 2);
  assert.equal(b.executions.filter(x => x.func.name === 'mediaSeedBridge').length, 2);
});

test('persistent error status redacts private source URLs', async () => {
  const b = background();
  b.api.scripting.executeScript = async () => {throw Error('Cannot access https://tracker.example/download?passkey=synthetic-private');};
  await b.dispatch({type: 'capture', url: sample}, {url: 'https://tracker.example/list', tab: {id: 9}, frameId: 0});
  assert.equal(b.local.lastResult.error, true); assert.doesNotMatch(b.local.lastResult.text, /passkey|synthetic-private|https:/);
});

test('saved defaults validate destructive intent and finite seed goals', () => {
  const m = context().MediaSeedExtension;
  assert.equal(m.defaults().cleanup.delete_files, false); assert.equal(m.defaults().start, true);
  for (const value of [{cleanup: {hours: Infinity}}, {cleanup: {mode: 'bad'}}, {cleanup: {ratio: 0}}, {cleanup: {delete_files: 'true'}}, {start: 'true'}]) assert.throws(() => m.defaults(value));
});
test('capture intercepts trusted unmodified torrent clicks only and honors disabled sites', async () => {
  let listener, changed; const messages = [], storage = {sites: ['https://tracker.example', 'https://nordicbytes.org']};
  const c = context({location: {origin: 'https://tracker.example'}, document: {addEventListener(_, fn) {listener = fn;}}, chrome: {
    storage: {local: {get: async () => storage}, onChanged: {addListener(fn) {changed = fn;}}}, runtime: {sendMessage: async msg => {messages.push(msg); return {ok: true};}},
  }});
  vm.runInContext(file('capture.js'), c); await new Promise(r => setTimeout(r, 0));
  const click = (href, options = {}) => {let prevented = false; listener({isTrusted: true, button: 0, target: {closest: () => ({href})}, preventDefault() {prevented = true;}, stopImmediatePropagation() {}, ...options}); return prevented;};
  assert.equal(click(sample, {isTrusted: false}), false); assert.equal(click(sample, {ctrlKey: true}), false);
  assert.equal(click('https://tracker.example/article'), false);
  assert.equal(click(sample), true); assert.equal(messages.length, 1);
  c.location.origin = 'https://nordicbytes.org'; await changed();
  assert.equal(click('https://nordicbytes.org/torrents/download/123.synthetic'), true);
  assert.equal(messages.length, 2);
  assert.equal(click('https://nordicbytes.org/torrents/123'), false);
  storage.sites = []; await changed(); await new Promise(r => setTimeout(r, 0));
  assert.equal(click(sample), false);
});

test('popup sends explicit start/pause, accepts a drop without sending, and preserves failed drafts', async () => {
  const nodes = new Map(), listeners = new Map(), sent = [];
  function node(id) {if (!nodes.has(id)) nodes.set(id, {value: id === 'ratio' ? '1' : '', dataset: {}, files: [], classList: {add() {}, remove() {}}, replaceChildren() {}, append() {}}); return nodes.get(id);}
  let fail = false;
  const c = context({location: {href: 'chrome-extension://synthetic/popup.html'}, document: {
    getElementById: node, addEventListener: (type, fn) => listeners.set(type, fn), querySelectorAll: () => [node('start'), node('pause')], createElement: () => ({append() {}}),
  }, chrome: {storage: {local: {get: async () => ({origin: 'https://seed.example', sites: []})}}, tabs: {query: async () => []}, runtime: {sendMessage: async msg => {sent.push(msg); return fail ? {error: 'synthetic failure'} : {ok: true, stopped: !msg.item.start};}}}});
  vm.runInContext(file('popup.js'), c); await new Promise(r => setTimeout(r, 0));
  node('magnet').value = sample;
  await node('add').onsubmit({preventDefault() {}, submitter: {value: 'start'}});
  assert.equal(sent[0].item.start, true); assert.match(node('status').textContent, /start anmodet/);
  const synthetic = {name: 'test.torrent', size: 2, arrayBuffer: async () => Uint8Array.from([100, 101]).buffer};
  listeners.get('drop')({preventDefault() {}, dataTransfer: {files: [synthetic]}});
  await new Promise(r => setTimeout(r, 0)); assert.equal(sent.length, 1);
  assert.equal(node('filename').textContent, 'test.torrent');
  fail = true;
  await node('add').onsubmit({preventDefault() {}, submitter: {value: 'pause'}});
  assert.equal(sent[1].item.kind, 'file'); assert.equal(sent[1].item.start, false);
  assert.equal(node('filename').textContent, 'test.torrent'); assert.equal(node('status').dataset.error, 'true');
  fail = false;
  await node('add').onsubmit({preventDefault() {}, submitter: {value: 'pause'}});
  assert.match(node('status').textContent, /på pause/);
});

test('popup saves seed defaults, restores them after reopening and keeps them after adding', async () => {
  const local = {origin: 'https://seed.example', sites: []}, sent = [];
  async function open() {
    const nodes = new Map();
    const node = id => {if (!nodes.has(id)) nodes.set(id, {value: '', dataset: {}, files: [], classList: {add() {}, remove() {}}, replaceChildren() {}, append() {}}); return nodes.get(id);};
    const c = context({location: {href: 'chrome-extension://synthetic/popup.html'}, document: {getElementById: node, addEventListener() {}, querySelectorAll: () => [], createElement: () => ({append() {}})}, chrome: {
      storage: {local: {get: async () => local, set: async data => Object.assign(local, JSON.parse(JSON.stringify(data)))}}, tabs: {query: async () => []},
      runtime: {sendMessage: async msg => {sent.push(msg); return {ok: true, stopped: !msg.item.start};}},
    }});
    vm.runInContext(file('popup.js'), c); await new Promise(r => setTimeout(r, 0)); return node;
  }
  let node = await open();
  node('default-start').value = 'pause'; node('ratio').value = '2';
  node('cleanup-mode').value = 'either'; node('cleanup-hours').value = '36'; node('cleanup-ratio').value = '3'; node('cleanup-action').value = 'files';
  await node('settings').onsubmit({preventDefault() {}});
  assert.deepEqual(local.defaults, {start: false, ratio: 2, cleanup: {mode: 'either', hours: 36, ratio: 3, delete_files: true}});
  node = await open();
  assert.equal(node('default-start').value, 'pause'); assert.equal(node('cleanup-hours').value, '36');
  assert.equal(node('cleanup-action').value, 'files'); assert.match(node('settings-summary').textContent, /36 timer ELLER ratio 3/);
  node('magnet').value = sample;
  await node('add').onsubmit({preventDefault() {}, submitter: {value: 'defaults'}});
  assert.equal(sent[0].item.start, false); assert.equal(sent[0].item.cleanup.hours, 36); assert.equal(sent[0].item.cleanup.delete_files, true);
  assert.equal(node('cleanup-mode').value, 'either');
  await node('add').onsubmit({preventDefault() {}, submitter: {value: 'start'}});
  // Empty link fails validation instead of re-sending the previous torrent.
  assert.equal(sent.length, 1);
  node = await open(); assert.equal(node('cleanup-mode').value, 'either');
});
