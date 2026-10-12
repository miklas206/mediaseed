import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import test from 'node:test';

function setup() {
  const nodes = new Map(), listeners = new Map(), uploads = [];
  const create = () => {
    const classes = new Set();
    return {value: '', files: [], dataset: {}, style: {setProperty() {}},
      classList: {add: x => classes.add(x), remove: x => classes.delete(x), contains: x => classes.has(x)},
      addEventListener() {}, append() {}, replaceChildren() {}, setAttribute() {}, querySelectorAll: () => [],
      showModal() {this.open = true; this.openCount = (this.openCount || 0) + 1;}, close() {this.open = false;},
    };
  };
  const find = selector => {if (!nodes.has(selector)) nodes.set(selector, create()); return nodes.get(selector);};
  const mode = {value: 'magnet'}, ratio = {value: '1'};
  const form = find('#add-torrent-form');
  form.elements = [mode, ratio];
  form.elements.namedItem = name => name === 'mode' ? mode : ratio;
  form.reset = () => {mode.value = 'magnet'; ratio.value = '1';};
  form.querySelector = () => ({set checked(value) {if (value) mode.value = 'file';}});
  const body = create(); body.classList.add('signed-in');
  const context = vm.createContext({document: {querySelector: find, querySelectorAll: () => [], createElement: create, body,
    addEventListener(type, fn) {if (!listeners.has(type)) listeners.set(type, []); listeners.get(type).push(fn);}},
    setInterval() {}, setTimeout() {}, clearTimeout() {},
    fetch: async (url, options) => {
      if (url.startsWith('/api/torrents/upload')) uploads.push({url, ...options});
      return {ok: true, json: async () => ({needsSetup: true})};
    },
  });
  vm.runInContext(readFileSync(new URL('../mediaseed/static/app.js', import.meta.url), 'utf8'), context);
  const refresh = context.refresh;
  context.refresh = async () => {};
  const refreshTorrents = context.refreshTorrents;
  context.refreshTorrents = async () => {};
  function drag(type, files = [], types = ['Files']) {
    const event = {dataTransfer: {types, files}, prevented: false, preventDefault() {this.prevented = true;}};
    for (const fn of listeners.get(type) || []) fn(event);
    return event;
  }
  return {find, body, context, drag, uploads, form, ratio, mode, refreshTorrents, refresh};
}
const torrent = (name = 'Sintel.torrent', size = 100) => ({name, size});

test('page drop opens a populated dialog, preserves its draft, and uploads only after confirmation', async () => {
  const {find, body, drag, uploads, form, ratio, mode} = setup();
  drag('dragenter'); assert.equal(body.classList.contains('torrent-dragging'), true);
  const file = torrent(); assert.equal(drag('drop', [file]).prevented, true);
  assert.equal(body.classList.contains('torrent-dragging'), false);
  assert.equal(find('#add-torrent').open, true);
  assert.equal(mode.value, 'file'); assert.equal(find('#file-name').textContent, file.name);
  assert.equal(find('#add-magnet').disabled, true); assert.equal(find('#add-file').required, false);
  assert.equal(uploads.length, 0);
  ratio.value = '3'; const replacement = torrent('Other.TORRENT'); drag('drop', [replacement]);
  assert.equal(ratio.value, '3'); assert.equal(find('#add-torrent').openCount, 1);
  await form.onsubmit({preventDefault() {}});
  assert.equal(uploads.length, 1); assert.equal(uploads[0].body, replacement);
  assert.match(uploads[0].url, /filename=Other.TORRENT&ratio=3&start=false$/);
  assert.equal(find('#add-torrent').open, false);
});

test('start button submits the explicit start choice; pause remains available without VPN', async () => {
  const {find, context, drag, form, uploads} = setup();
  drag('drop', [torrent()]);
  assert.equal(find('#add-start').disabled, true);
  assert.equal(find('#add-start-help').hidden, false);
  find('#add-torrent').close();
  vm.runInContext('downloadsEnabled = true', context);
  drag('drop', [torrent()]);
  assert.equal(find('#add-start').disabled, false);
  await form.onsubmit({preventDefault() {}, submitter: {value: 'start'}});
  assert.match(uploads[0].url, /&start=true$/);
});

test('invalid drops never open the dialog or upload; leaving clears the cue', () => {
  const {find, body, drag, uploads} = setup();
  for (const files of [[torrent(), torrent()], [torrent('notes.txt')], [torrent('huge.torrent', 4194305)]]) {
    assert.equal(drag('drop', files).prevented, true);
    assert.ok(find('#message').textContent); assert.ok(!find('#add-torrent').open);
  }
  drag('dragenter'); drag('dragenter'); drag('dragleave');
  assert.equal(body.classList.contains('torrent-dragging'), true);
  drag('dragleave'); assert.equal(body.classList.contains('torrent-dragging'), false);
  assert.equal(drag('drop', [], ['text/plain']).prevented, false);
  assert.equal(uploads.length, 0);
});

test('signed-out, viewer and busy states block file drops and browser navigation', () => {
  const {find, body, context, drag} = setup();
  for (const state of ['signed-out', 'viewer', 'busy']) {
    body.classList.add('signed-in');
    vm.runInContext("appRole = 'administrator'; addingTorrent = false", context);
    if (state === 'signed-out') body.classList.remove('signed-in');
    if (state === 'viewer') vm.runInContext("appRole = 'viewer'", context);
    if (state === 'busy') vm.runInContext('addingTorrent = true', context);
    assert.equal(drag('dragover').dataTransfer.dropEffect, 'none');
    assert.equal(drag('drop', [torrent()]).prevented, true);
    assert.ok(!find('#add-torrent').open);
  }
});

test('addition refreshes the list during a busy status poll and ignores an older list response', async () => {
  const {find, context, drag, form, refreshTorrents} = setup();
  // Exercise the actual list loader, keeping the other status endpoints pending.
  context.refreshTorrents = refreshTorrents;
  const pending = [];
  context.api = async path => {
    assert.equal(path, 'torrents');
    return new Promise(resolve => pending.push(resolve));
  };
  context.renderTorrents = () => {};
  vm.runInContext('refreshing = true', context);
  const oldRequest = context.refreshTorrents();
  drag('drop', [torrent()]);
  const submission = form.onsubmit({preventDefault() {}});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(find('#add-torrent').open, false);
  assert.equal(pending.length, 2, 'addition must request a fresh list even while polling');
  pending[1]([{hash: 'a'.repeat(40), name: 'New torrent'}]);
  await submission;
  pending[0]([]); await oldRequest;
  assert.equal(vm.runInContext('torrents[0].name', context), 'New torrent');
});

test('dashboard renders in one request without waiting for slow auxiliary data or fetching the list twice', async () => {
  const {find, context, refresh} = setup();
  const requests = [];
  find('#filter').value = find('#source-filter').value = 'all'; find('#sort').value = 'name';
  context.api = async path => {
    requests.push(path);
    if (path !== 'dashboard') return new Promise(() => {});
    return {runtime: {provider: 'mediavpn', ready: true, downloadsEnabled: true, checkedAt: Date.now()/1000},
      torrents: [{hash: 'a'.repeat(40), name: 'Synthetic', dlspeed: 4096, upspeed: 0}],
      queue: {active: 1, limit: 8, waiting: 0, rssActive: 0, manualActive: 1}};
  };
  await refresh();
  assert.equal(find('#connection-flow').dataset.download, 'true');
  assert.match(find('#queue-status').textContent, /1\/8/);
  assert.match(find('#count').textContent, /1 af 1/);
  assert.deepEqual(requests, ['dashboard', 'client', 'storage']);
  await refresh();
  assert.deepEqual(requests, ['dashboard', 'client', 'storage', 'dashboard']);
  context.api = async () => {throw new Error('unavailable');};
  await refresh();
  assert.equal(find('#connection-flow').dataset.state, 'unknown');
  assert.equal(find('#connection-flow').dataset.download, 'false');
  assert.equal(vm.runInContext('refreshing', context), false);
});
