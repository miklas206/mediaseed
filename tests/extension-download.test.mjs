import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';
const code = readFileSync(new URL('../mediaseed/static/extension-download.js', import.meta.url), 'utf8');
function setup(ua, brave = false) {
  const nodes = new Map();
  const get = id => {if (!nodes.has(id)) nodes.set(id, {addEventListener(_, fn) {this.change = fn;}}); return nodes.get(id);};
  const c = vm.createContext({navigator: {userAgent: ua, brave: {isBrave: async () => brave}}, location: {origin: 'https://seed.example'}, document: {getElementById: get}});
  vm.runInContext(code, c); return {c, get};
}
test('browser detection chooses compatible packages without confusing Safari and Chromium', async () => {
  const {c} = setup('Chrome/130 Safari/537.36');
  const cases = [['Chrome/130 Safari/537.36', 'chromium'], ['Chrome/130 Edg/130 Safari/537.36', 'chromium'], ['Chrome/130 OPR/112', 'chromium'], ['Firefox/140', 'firefox'], ['Version/18 Safari/605', 'safari'], ['iPhone CriOS/130 Safari/605', 'safari']];
  for (const [ua, expected] of cases) assert.equal(c.detectMediaSeedBrowser(ua).package, expected);
  assert.equal(c.detectMediaSeedBrowser('Chrome/130', [], true).label, 'Brave');
  assert.match(c.detectMediaSeedBrowser('unknown').label, /manuelt/);
});
test('download target, installation instructions and origin follow detected browser and manual override', async () => {
  const {get} = setup('Firefox/140'); await new Promise(r => setTimeout(r, 0));
  assert.equal(get('extension-download').href, '/static/extensions/mediaseed-firefox.zip');
  assert.equal(get('install-firefox').hidden, false); assert.equal(get('install-safari').hidden, true);
  assert.equal(get('extension-origin').textContent, 'https://seed.example');
  get('extension-browser').value = 'safari'; get('extension-browser').change();
  assert.equal(get('extension-download').href, '/static/extensions/mediaseed-safari-source.zip');
  assert.equal(get('install-firefox').hidden, true); assert.equal(get('install-safari').hidden, false);
});
