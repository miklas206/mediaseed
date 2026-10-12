import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import test from 'node:test';

test('removal choice survives early-seeding confirmation and streams regenerate bits', async () => {
  const nodes = new Map();
  const create = () => ({value: '', dataset: {}, style: {setProperty() {}}, children: [], listeners: {},
    classList: {contains: () => false}, addEventListener(type, callback) {this.listeners[type] = callback;},
    append(...items) {this.children.push(...items);}, replaceChildren() {this.children = [];},
    showModal() {this.open = true;}, close() {this.open = false;}, focus() {this.focused = true;}, setAttribute() {}, querySelectorAll() {return [];},
    reset() {find('#remove-files').checked = false;},
  });
  const find = selector => {if (!nodes.has(selector)) nodes.set(selector, create()); return nodes.get(selector);};
  const lanes = Array.from({length: 4}, create);
  lanes.forEach((lane, index) => {lane.classList.contains = name => name === 'binary-download' && index % 2 === 1;});
  const requests = [];
  const confirmations = [];
  let refuseOnce = true;
  const context = vm.createContext({document: {querySelector: find, querySelectorAll: s => s === '.binary-lane' ? lanes : [], createElement: create, body: create(), addEventListener() {}},
    setInterval() {}, setTimeout() {}, clearTimeout() {}, confirm: () => {throw new Error('Browser confirmation must not be used');},
    fetch: async (url, options) => {
      if (url.endsWith('/presence')) return new Promise(resolve => confirmations.push(() => resolve({ok: true, json: async () => ({present: false})})));
      if (url === '/api/torrents/remove') {
        requests.push(JSON.parse(options.body));
        if (refuseOnce) {refuseOnce = false; return {ok: false, status: 409, json: async () => ({detail: 'Seeding goal'})};}
      }
      return {ok: true, json: async () => ({needsSetup: true})};
    },
  });
  vm.runInContext(readFileSync(new URL('../mediaseed/static/i18n.js', import.meta.url), 'utf8') + '\n' + readFileSync(new URL('../mediaseed/static/app.js', import.meta.url), 'utf8'), context);
  context.MediaAppI18n.setLanguage('da'); context.refresh = async () => {};
  find('#filter').value = 'all'; find('#source-filter').value = 'all'; find('#sort').value = 'name';
  vm.runInContext('torrents = [{hash: "a".repeat(40), name: "First"}, {hash: "b".repeat(40), name: "Next"}]', context);
  context.renderTorrents();
  assert.equal(context.countryLabel('Albania'), 'Albanien');
  assert.equal(context.countryFlag('Albania').src, '/static/flags/al.svg');
  assert.equal(context.countryFlag('DE').src, '/static/flags/de.svg');
  assert.equal(context.countryFlag(null), null);
  for (const lane of lanes) {
    assert.equal(lane.children.length, 1, 'one continuous strip prevents packet overlap');
    for (const packet of lane.children) {
      assert.match(packet.textContent, /^[01]{96}$/);
      const seen = new Set([packet.textContent]);
      for (let i = 0; i < 10; i++) {
        const previous = packet.textContent;
        packet.listeners.animationiteration(); seen.add(packet.textContent);
        assert.match(packet.textContent, /^[01]{96}$/);
        if (lane.classList.contains('binary-download')) assert.equal(packet.textContent.slice(0, -12), previous.slice(12));
        else assert.equal(packet.textContent.slice(12), previous.slice(0, -12));
      }
      assert.ok(seen.size > 5);
    }
  }
  context.chooseRemoval({hash: 'a'.repeat(40), name: 'Synthetic'});
  assert.equal(find('#remove-torrent').open, true);
  assert.equal(find('#remove-files').checked, false);
  find('#remove-files').checked = true;
  const removal = find('#remove-form').onsubmit({preventDefault() {}});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(requests.length, 1, 'wait for explicit in-app confirmation');
  assert.equal(find('#early-seeding').open, true);
  assert.equal(find('#cancel-early').focused, true);
  assert.match(find('#early-consequence').textContent, /filer slettes/);
  assert.equal(find('#confirm-early').textContent, 'Fjern alligevel');
  find('#confirm-early').onclick();
  await removal;
  assert.equal(requests.length, 2);
  assert.ok(requests.every(body => body.delete_files === true));
  assert.equal(requests[1].confirm_early, true);
  assert.equal(find('#remove-torrent').open, false);
  assert.equal(find('#message').dataset.tone, 'info');
  assert.equal(find('#count').textContent, '1 af 1 torrents · 1 fjernes');
  // A stale poll still containing the deleted hash must not resurrect its card.
  context.renderTorrents();
  assert.equal(find('#count').textContent, '1 af 1 torrents · 1 fjernes');
  assert.equal(vm.runInContext('pendingRemovals.get("a".repeat(40)).confirmed', context), false);
  confirmations.shift()();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(find('#message').dataset.tone, 'success');
  assert.equal(vm.runInContext('pendingRemovals.get("a".repeat(40)).confirmed', context), true);
  context.chooseRemoval({hash: 'b'.repeat(40), name: 'Next'});
  assert.equal(find('#remove-files').checked, false);
  await find('#remove-form').onsubmit({preventDefault() {}});
  assert.equal(requests[2].delete_files, false);
  confirmations.shift()();
  await new Promise(resolve => setImmediate(resolve));
  const stopped = context.confirmEarlyAction({name: 'Synthetic'}, 'stop', false);
  assert.equal(find('#confirm-early').textContent, 'Stop alligevel');
  assert.match(find('#early-consequence').textContent, /bevare/);
  find('#early-seeding').listeners.cancel({preventDefault() {}});
  assert.equal(await stopped, false, 'Escape cancels without accepting destructive action');
  assert.equal(find('#early-seeding').open, false);
  const kept = context.confirmEarlyAction({name: 'Synthetic'}, 'remove', false);
  assert.match(find('#early-consequence').textContent, /bevare/);
  find('#cancel-early').onclick();
  assert.equal(await kept, false);
});
