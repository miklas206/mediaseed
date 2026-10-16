import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import test from 'node:test';

const source = readFileSync(new URL('../mediaseed/static/app.js', import.meta.url), 'utf8');
const picker = source.slice(source.indexOf("const feedForm ="), source.indexOf('async function loadSources'));
function setup(api) {
  const nodes = new Map();
  const find = id => {
    if (!nodes.has(id)) nodes.set(id, {value: '', children: [], disabled: false,
      replaceChildren(...items) {this.children = items;}, append(item) {this.children.push(item);},
      addEventListener() {}, elements: {namedItem: () => find('hidden-folder')}});
    return nodes.get(id);
  };
  const context = vm.createContext({document: {querySelector: find}, api, t: x => x, bytes: x => String(x),
    Option: function(text, value) {this.textContent = text; this.value = value;}, queueMicrotask, notify() {}});
  vm.runInContext(picker, context);
  return {context, find};
}
const locations = [
  {id: 'local', label: 'Local downloads', available: true, writable: true},
  {id: 'tv', label: 'Serier', available: true, writable: true},
];
test('RSS chooses an approved mount, browses Unicode folders and preserves the choice for editing', async () => {
  const requests = [];
  const {context, find} = setup(async path => {
    requests.push(path);
    return path === 'storage' ? {locations, defaultLocation: 'tv'} : {folders: [{name: 'Dansk TV æøå', writable: true}], writable: true};
  });
  await context.loadFeedLocations();
  assert.equal(find('#feed-location').value, 'tv');
  assert.equal(find('#feed-save').disabled, false);
  await context.loadFeedFolders('Dansk TV æøå');
  assert.match(requests.at(-1), /location=tv&folder=Dansk%20TV%20%C3%A6%C3%B8%C3%A5/);
  assert.equal(find('hidden-folder').value, 'Dansk TV æøå');
  assert.equal(find('#feed-folder-path').textContent, '/Dansk TV æøå');
  await context.loadFeedLocations('tv', 'Dansk TV æøå');
  assert.equal(find('hidden-folder').value, 'Dansk TV æøå');
  await context.loadFeedFolders('');
  assert.equal(find('#feed-folder-up').disabled, true);
});
test('missing and read-only destinations cannot be saved or silently replaced with local downloads', async () => {
  const {context, find} = setup(async path => {
    if (path === 'storage') return {locations, defaultLocation: 'local'};
    if (path.includes('location=missing')) throw new Error('Storage unavailable');
    return {folders: [], writable: false};
  });
  await context.loadFeedLocations('tv');
  assert.equal(find('#feed-save').disabled, true);
  await assert.rejects(context.loadFeedLocations('missing', 'old-folder'));
  assert.equal(find('#feed-location').value, 'missing');
  assert.equal(find('#feed-save').disabled, true);
  assert.equal(find('#feed-location').children.at(-1).disabled, true);
});
test('a slow folder response cannot overwrite a newer folder selection', async () => {
  let finishOld;
  const {context, find} = setup(async path => {
    if (path === 'storage') return {locations, defaultLocation: 'tv'};
    if (path.includes('folder=old')) return new Promise(resolve => {finishOld = resolve;});
    return {folders: [], writable: true};
  });
  await context.loadFeedLocations();
  const previous = context.loadFeedFolders('old');
  await context.loadFeedFolders('new');
  finishOld({folders: [], writable: false}); await previous;
  assert.equal(find('hidden-folder').value, 'new');
  assert.equal(find('#feed-save').disabled, false);
});

test('Edit fetches the saved address before opening the form and preserves it on a failed read', async () => {
  const start = source.indexOf('actions.append(command(t("Edit"), ');
  const handler = source.slice(start + 'actions.append(command(t("Edit"), '.length, source.indexOf('), command(t("Remove")', start));
  const fields = new Map();
  const field = key => {
    if (!fields.has(key)) fields.set(key, {value: '', options: [], focus() {}});
    return fields.get(key);
  };
  const form = {reset() {for (const input of fields.values()) input.value = '';},
    elements: {namedItem: field}, querySelector: () => field('name')};
  const requests = [], opened = [], storage = [];
  let fail = false;
  const detail = {id: 'synthetic', name: 'Saved feed', url: 'https://nordicbytes.org/rss/synthetic?passkey=private',
    include: ['2160p'], location: 'tv', folder: 'Shows', max_bytes: 1024 ** 3};
  const context = vm.createContext({row: {id: 'synthetic', name: 'Old list name'}, kind: 'feeds', feedEditing: false,
    document: {querySelector: () => form}, t: x => x,
    api: async path => {requests.push(path); if (fail) throw new Error('Unavailable'); return detail;},
    MediaSeedWorkspace: {openEditor: kind => opened.push(kind), syncBadges() {}},
    loadFeedLocations: async (...args) => storage.push(args), notify() {}});
  const edit = vm.runInContext('(' + handler + ')', context);
  await edit();
  assert.deepEqual(requests, ['feeds/synthetic']);
  assert.deepEqual(opened, ['feeds']);
  assert.equal(field('url').value, detail.url);
  assert.equal(field('name').value, detail.name);
  assert.equal(field('include').value, '2160p');
  assert.equal(field('max_gib').value, 1);
  assert.deepEqual(storage, [['tv', 'Shows']]);
  fail = true; field('url').value = 'unsaved change';
  await assert.rejects(edit(), /Unavailable/);
  assert.equal(field('url').value, 'unsaved change');
  assert.equal(opened.length, 1);
});
