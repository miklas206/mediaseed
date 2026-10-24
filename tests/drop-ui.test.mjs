import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import test from 'node:test';

test('cleanup choices expose the correct goals and send file-removal intent explicitly', async () => {
  const {find, drag, form, uploads} = setup();
  drag('drop', [torrent('Cleanup.torrent')]);
  find('#add-cleanup-mode').value = 'both'; find('#add-cleanup-hours').value = '12';
  find('#add-cleanup-ratio').value = '3'; find('#add-cleanup-action').value = 'files';
  find('#add-cleanup-mode').onchange();
  assert.equal(find('#add-cleanup-hours-field').hidden, false);
  assert.equal(find('#add-cleanup-ratio-field').hidden, false);
  assert.equal(find('#add-cleanup-warning').hidden, false);
  await form.onsubmit({preventDefault() {}, submitter: {value: 'pause'}});
  const params = new URL(uploads[0].url, 'https://synthetic.example').searchParams;
  assert.equal(params.get('cleanup_mode'), 'both'); assert.equal(params.get('cleanup_hours'), '12');
  assert.equal(params.get('cleanup_ratio'), '3'); assert.equal(params.get('cleanup_delete_files'), 'true');
  find('#add-cleanup-mode').value = 'never'; find('#add-cleanup-mode').onchange();
  assert.equal(find('#add-cleanup-options').hidden, true);
  assert.equal(form.elements.namedItem('ratio').disabled, true);
  form.elements.namedItem('ratio').value = '';
  await form.onsubmit({preventDefault() {}, submitter: {value: 'pause'}});
  assert.equal(new URL(uploads.at(-1).url, 'https://synthetic.example').searchParams.get('ratio'), '1');
});

function setup() {
  const nodes = new Map(), listeners = new Map(), uploads = [];
  const create = () => {
    const classes = new Set();
    return {value: '', files: [], children: [], contains(){return false;}, querySelector(){return null;}, dataset: {}, style: {setProperty() {}},
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
    Option: function(text, value) {this.textContent = text; this.value = value;},
    MediaAppTheme: {setOverrideHandler() {}, applyIdentity() {}}, window: {addEventListener() {}},
    setInterval() {}, setTimeout() {}, clearTimeout() {},
    fetch: async (url, options) => {
      if (url === '/api/storage') return {ok: true, json: async () => ({locations: [{id: 'local', label: 'Local downloads', available: true, writable: true, freeBytes: 2000, totalBytes: 3000}], defaultLocation: 'local'})};
      if (url.startsWith('/api/storage/folders')) return {ok: true, json: async () => ({folders: [], writable: true})};
      if (url.startsWith('/api/torrents/upload')) uploads.push({url, ...options});
      return {ok: true, json: async () => ({needsSetup: true})};
    },
  });
  vm.runInContext(readFileSync(new URL('../mediaseed/static/i18n.js', import.meta.url), 'utf8') + '\n' + readFileSync(new URL('../mediaseed/static/rich-ui.js', import.meta.url), 'utf8') + '\n' + readFileSync(new URL('../mediaseed/static/app.js', import.meta.url), 'utf8'), context);
  const submit = form.onsubmit;
  form.onsubmit = async event => {if (!vm.runInContext("addStorageReady", context)) await context.loadAddStorage(); return submit(event);};
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

test('pagination limits rendered rows, clamps after filtering, and sorts missing values last', () => {
  const {find, context} = setup();
  const rows = Array.from({length: 26}, (_,i) => ({hash:String(i).padStart(40,'0'),name:'Torrent '+i,size:i,progress:.5,category:''}));
  context.seedTorrent = row => ({name:row.name});
  const rendered=[];
  find('#torrents').replaceChildren = () => {rendered.length=0;};
  find('#torrents').append = item => rendered.push(item);
  find('#page-size').value='10';find('#filter').value='all';find('#source-filter').value='all';find('#sort').value='name';find('#sort-direction').value='asc';
  vm.runInContext(`torrents=${JSON.stringify(rows)}`,context);
  context.renderTorrents();assert.equal(rendered.length,10);assert.equal(rendered[0].name,'Torrent 0');
  find('#next-page').onclick();assert.equal(rendered[0].name,'Torrent 10');
  find('#next-page').onclick();assert.equal(rendered.length,6);assert.equal(find('#next-page').disabled,true);
  find('#search').value='Torrent 25';context.renderTorrents();assert.equal(rendered.length,1);assert.equal(find('#previous-page').disabled,true);
  const sorted=context.sortTorrentRows([{hash:'a',eta:8640000},{hash:'b',eta:10},{hash:'c',eta:100},{hash:'d'}],'eta','desc');
  assert.deepEqual(Array.from(sorted,r=>r.hash),['c','b','a','d']);
});

test('file checking requires confirmation and sends one exact hash without a start or delete', async () => {
  const {find, context} = setup();
  const requests = [], messages = [];
  context.fetch = async (url, options) => {requests.push({url, body: JSON.parse(options.body)}); return {ok: true, json: async () => ({accepted: true, stopped: true})};};
  context.showNotification = message => messages.push(message);
  context.chooseRecheck({hash: 'a'.repeat(40), name: 'Synthetic'});
  assert.equal(find('#recheck-torrent').open, true);
  assert.equal(find('#recheck-name').textContent, 'Synthetic');
  assert.equal(requests.length, 0);
  find('#cancel-recheck').onclick();
  assert.equal(find('#recheck-torrent').open, false);
  assert.equal(requests.length, 0);
  context.chooseRecheck({hash: 'b'.repeat(40), name: 'Second'});
  await find('#confirm-recheck').onclick();
  assert.deepEqual(requests, [{url: '/api/torrents/recheck', body: {torrent_hash: 'b'.repeat(40)}}]);
  assert.equal(find('#recheck-torrent').open, false);
  assert.match(messages[0], /requested.*paused/);
});

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
  assert.equal(uploads.length, 2); assert.equal(uploads[1].body, replacement);
  assert.match(uploads[1].url, /filename=Other.TORRENT&ratio=1&start=false&location=local&folder=$/);
  assert.equal(find('#add-torrent').open, true);
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
  assert.match(uploads[0].url, /&start=true&location=local&folder=$/);
});

test('invalid drops never open the dialog or upload; leaving clears the cue', () => {
  const {find, body, drag, uploads} = setup();
  for (const files of [[torrent('notes.txt')], [torrent('huge.torrent', 4194305)]]) {
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
  const originalApi = context.api;
  context.api = async path => {
    if (path.startsWith('storage')) return originalApi(path);
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
  assert.match(find('#count').textContent, /1 of 1/);
  assert.deepEqual(requests, ['dashboard', 'client', 'storage']);
  await refresh();
  assert.deepEqual(requests, ['dashboard', 'client', 'storage', 'dashboard']);
  context.api = async () => {throw new Error('unavailable');};
  await refresh();
  assert.equal(find('#connection-flow').dataset.state, 'unknown');
  assert.equal(find('#connection-flow').dataset.download, 'false');
  assert.equal(vm.runInContext('refreshing', context), false);
});


test('folder selection survives upload', async () => {
  const {context, drag, form, uploads, find} = setup();
  drag('drop', [torrent()]);
  await context.loadAddStorage();
  const originalApi = context.api;
  context.api = async path => path === 'storage' ? {locations: [{id: 'movies', label: 'Movies', writable: true, available: true, freeBytes: 12345, totalBytes: 54321}], defaultLocation: 'movies'} : path.startsWith('storage/folders') ? {folders: [{name: 'Danske film', writable: true}], writable: !path.includes('readonly')} : originalApi(path);
  await context.loadAddStorage();
  await context.loadAddFolders('Danske film');
  await form.onsubmit({preventDefault() {}, submitter: {value: 'pause'}});
  const params = new URL(uploads[0].url, 'https://synthetic.example').searchParams;
  assert.equal(params.get('location'), 'movies');
  assert.equal(params.get('folder'), 'Danske film');
  assert.equal(find('#add-folder-path').textContent, '/Danske film');
});


test('download destination controls belong to the add dialog, not a hidden feed form', () => {
  const html = readFileSync(new URL('../mediaseed/static/index.html', import.meta.url), 'utf8');
  const start = html.indexOf('<form id="add-torrent-form">');
  const addForm = html.slice(start, html.indexOf('</form>', start));
  for (const id of ['add-location', 'add-folder', 'add-folder-path']) assert.ok(addForm.includes(`id="${id}"`));
});


test('file picker accumulates selections and ignores duplicates', async () => {
  const {find, context, form, uploads} = setup();
  find('#new-torrent').onclick();
  form.querySelector('[name="mode"][value="file"]').checked = true;
  const picker = find('#add-file');
  picker.files = [torrent('One.torrent'), torrent('Two.torrent')]; picker.onchange();
  picker.files = [torrent('Two.torrent'), torrent('Three.torrent')]; picker.onchange();
  assert.equal(vm.runInContext('torrentFiles.length', context), 3);
  assert.equal(find('#seed-preview-button').disabled, false);
  await form.onsubmit({preventDefault() {}});
  assert.equal(uploads.length, 3);
  await form.onsubmit({preventDefault() {}});
  assert.equal(uploads.length, 3, 'successful files cannot be re-uploaded');
});

test('one rejection does not stop a batch and retry sends only unsuccessful files', async () => {
  const {context, drag, form, uploads} = setup();
  const original = context.fetch; let reject = true;
  context.fetch = async (url, options) => {
    const response = await original(url, options);
    if (url.includes('filename=Bad.torrent') && reject) return {ok:false,json:async()=>({detail:'Invalid torrent file'})};
    return response;
  };
  drag('drop', [torrent('Good.torrent'), torrent('Bad.torrent'), torrent('Last.torrent')]);
  await form.onsubmit({preventDefault() {},submitter:{value:'pause'}});
  assert.equal(uploads.length, 3);
  assert.equal(vm.runInContext('torrentFiles.filter(item => item.state === "added").length', context), 2);
  assert.equal(vm.runInContext('torrentFiles.filter(item => item.state === "error").length', context), 1);
  reject = false;
  await form.onsubmit({preventDefault() {},submitter:{value:'pause'}});
  assert.equal(uploads.length, 4); assert.match(uploads[3].url, /filename=Bad.torrent/);
});

test('mixed selections retain valid files and enforce file count and aggregate limits', () => {
  const {context, drag} = setup();
  drag('drop', [torrent('Valid.torrent'), torrent('Empty.torrent',0), torrent('Not.txt'),torrent('Huge.torrent',4194305)]);
  assert.equal(vm.runInContext('torrentFiles.length', context),1);
  drag('drop', Array.from({length:110},(_,index)=>torrent(index+'.torrent')));
  assert.equal(vm.runInContext('torrentFiles.length', context),100);
  const second = setup();
  second.drag('drop',Array.from({length:20},(_,index)=>torrent(index+'.torrent',4*1024*1024)));
  assert.equal(vm.runInContext('torrentFiles.length',second.context),16);
});


test('lost acknowledgement is not retried automatically and does not stop later files', async () => {
  const {context, drag, form, uploads} = setup();
  const original = context.fetch;
  context.fetch = async (url, options) => {
    const response = await original(url, options);
    if (url.includes('filename=Uncertain.torrent')) throw new Error('Synthetic connection lost');
    return response;
  };
  drag('drop', [torrent('Uncertain.torrent'),torrent('Next.torrent')]);
  await form.onsubmit({preventDefault() {}});
  assert.equal(uploads.length, 2);
  assert.equal(vm.runInContext('torrentFiles[0].state',context),'error');
  assert.equal(vm.runInContext('torrentFiles[1].state',context),'added');
});

test('seeding progress follows cleanup ratio, time and AND/OR goals instead of the stop limit', () => {
  const {context}=setup();
  const row={progress:1,ratio_limit:1,credited_ratio:1.3,seeding_time:24*3600,cleanup:{mode:'ratio',ratio:3,hours:48}};
  assert.match(context.seedSeeding(row),/1[.,]3 \/ 3/);
  assert.ok(Math.abs(context.seedCleanupProgress(row).fraction-1.3/3)<1e-9);
  row.seeding_time=72*3600;
  assert.ok(context.seedCleanupProgress(row).fraction<1); // No implicit 48/49-hour cutoff.
  row.cleanup.mode='both';
  assert.ok(context.seedCleanupProgress(row).fraction<1);
  row.cleanup.mode='either';
  assert.equal(context.seedCleanupProgress(row).fraction,1);
  row.cleanup.mode='time';row.seeding_time=12*3600;
  assert.equal(context.seedCleanupProgress(row).fraction,.25);
  row.cleanup.mode='never';
  assert.equal(context.seedCleanupProgress(row).fraction,undefined);
  assert.doesNotMatch(context.seedSeeding(row),/ \/ /);
  delete row.cleanup;
  assert.equal(context.seedCleanupProgress(row).fraction,undefined);
  row.cleanup={mode:'ratio',ratio:3};row.credited_ratio=9;
  assert.equal(context.seedCleanupProgress(row).fraction,1);
  row.credit_observation_stale=true;row.last_credited_ratio=1.3;
  assert.ok(context.seedCleanupProgress(row).fraction<1);
  row.last_credited_ratio=null;
  assert.equal(context.seedCleanupProgress(row).fraction,0);
  for(const [state,tone] of [['uploading','seeding'],['downloading','downloading'],['stalledUP','waiting'],['pausedUP','paused'],['checkingUP','checking'],['missingFiles','error']]) {
    assert.equal(context.seedProgressTone({state,progress:state==='downloading'?.5:1}),tone);
  }
});
