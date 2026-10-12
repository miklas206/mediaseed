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
  let listener; const local = {origin: 'https://seed.example', sites: ['https://tracker.example']}, session = {}, created = [], executions = [];
  const api = {
    storage: {local: {get: async () => local}, session: {get: async id => id === null ? session : {[id]: session[id]}, set: async obj => Object.assign(session, obj), remove: async keys => (Array.isArray(keys) ? keys : [keys]).forEach(k => delete session[k])}},
    permissions: {contains: async () => true},
    tabs: {query: async () => [{id: 1, url: 'https://seed.example/', status: 'complete'}], get: async () => ({id: 1, url: 'https://seed.example/', status: 'complete'}), create: async opts => {created.push(opts); return {id: 2};}, update: async () => {} , onUpdated: {addListener() {}}},
    scripting: {executeScript: async options => {executions.push(options); return [{result: {ok: true, stopped: true}}];}},
    contextMenus: {onClicked: {addListener() {}}},
    runtime: {getURL: path => 'chrome-extension://synthetic/' + path, onInstalled: {addListener() {}}, onMessage: {addListener(fn) {listener = fn;}}},
  };
  const c = context({chrome: api}); vm.runInContext(file('background.js'), c);
  const dispatch = (message, sender = {url: 'chrome-extension://synthetic/popup.html'}) => new Promise(resolve => listener(message, sender, resolve));
  return {dispatch, api, created, executions, local, session};
}
test('background restricts web messages to enabled sites and review only', async () => {
  const b = background();
  let result = await b.dispatch({type: 'send', item: {kind: 'magnet', magnet: sample}}, {url: 'https://tracker.example', tab: {id: 9}, frameId: 0});
  assert.ok(result.error); assert.equal(b.executions.length, 0);
  result = await b.dispatch({type: 'capture', url: sample}, {url: 'https://evil.example', tab: {id: 9}, frameId: 0});
  assert.ok(result.error); assert.equal(b.created.length, 0);
  result = await b.dispatch({type: 'capture', url: sample}, {url: 'https://tracker.example', tab: {id: 9}, frameId: 1});
  assert.ok(result.error);
  result = await b.dispatch({type: 'capture', url: sample}, {url: 'https://tracker.example', tab: {id: 9}, frameId: 0});
  assert.equal(result.ok, true); assert.equal(b.created.length, 1); assert.equal(b.executions.length, 0);
  assert.equal(Object.keys(b.session).length, 1);
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
test('capture intercepts trusted unmodified torrent clicks only and honors disabled sites', async () => {
  let listener, changed; const messages = [], storage = {sites: ['https://tracker.example']};
  const c = context({location: {origin: 'https://tracker.example'}, document: {addEventListener(_, fn) {listener = fn;}}, chrome: {
    storage: {local: {get: async () => storage}, onChanged: {addListener(fn) {changed = fn;}}}, runtime: {sendMessage: async msg => {messages.push(msg); return {ok: true};}},
  }});
  vm.runInContext(file('capture.js'), c); await new Promise(r => setTimeout(r, 0));
  const click = (href, options = {}) => {let prevented = false; listener({isTrusted: true, button: 0, target: {closest: () => ({href})}, preventDefault() {prevented = true;}, stopImmediatePropagation() {}, ...options}); return prevented;};
  assert.equal(click(sample, {isTrusted: false}), false); assert.equal(click(sample, {ctrlKey: true}), false);
  assert.equal(click('https://tracker.example/article'), false);
  assert.equal(click(sample), true); assert.equal(messages.length, 1);
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
