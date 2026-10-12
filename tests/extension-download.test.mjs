import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';
const code = readFileSync(new URL('../mediaseed/static/i18n.js', import.meta.url), 'utf8') + '\n' + readFileSync(new URL('../mediaseed/static/extension-download.js', import.meta.url), 'utf8');
function setup(ua, brave = false, extra = {}) {
  const nodes = new Map();
  const get = id => {if (!nodes.has(id)) nodes.set(id, {addEventListener(type, fn) {this[type] = fn;}, setAttribute(k, v) {this[k] = v;}, getAttribute(k) {return this[k];}, removeAttribute(k) {delete this[k];}}); return nodes.get(id);};
  const c = vm.createContext({navigator: {userAgent: ua, brave: {isBrave: async () => brave}}, location: {origin: 'https://seed.example'}, document: {getElementById: get}, Blob, Uint8Array, AbortSignal, setTimeout: () => 1, clearTimeout() {}, ...extra});
  vm.runInContext(code, c); return {c, get};
}
test('browser detection chooses compatible packages without confusing Safari and Chromium', async () => {
  const {c} = setup('Chrome/130 Safari/537.36');
  const cases = [['Chrome/130 Safari/537.36', 'chromium'], ['Chrome/130 Edg/130 Safari/537.36', 'chromium'], ['Chrome/130 OPR/112', 'chromium'], ['Firefox/140', 'firefox'], ['Version/18 Safari/605', 'safari'], ['iPhone CriOS/130 Safari/605', 'safari']];
  for (const [ua, expected] of cases) assert.equal(c.detectMediaSeedBrowser(ua).package, expected);
  assert.equal(c.detectMediaSeedBrowser('Chrome/130', [], true).label, 'Brave');
  assert.match(c.detectMediaSeedBrowser('unknown').label, /manuelt/);
});

const zip = readFileSync(new URL('../mediaseed/static/extensions/mediaseed-chromium.zip', import.meta.url));
const response = (bytes = zip, headers = {}) => new Response(bytes, {headers: {'content-type': 'application/zip', 'content-length': String(zip.length), ...headers}});
test('download buffers a complete bounded ZIP and rejects partial, wrong or oversized responses', async () => {
  const {c} = setup('Chrome/130');
  const blob = await c.readExtensionPackage(response()); assert.equal(blob.size, zip.length); assert.equal(blob.type, 'application/zip');
  await assert.rejects(c.readExtensionPackage(response(zip.subarray(0, -1))), /afbrudt/);
  await assert.rejects(c.readExtensionPackage(response(zip, {'content-type': 'text/html'})), /ZIP/);
  await assert.rejects(c.readExtensionPackage(response(zip, {'content-length': String(3 * 1024 * 1024)})), /størrelse/);
  await assert.rejects(c.readExtensionPackage(response(Buffer.alloc(30), {'content-length': '30'})), /ugyldig/);
  await assert.rejects(c.saveExtensionPackage('https://evil.example/mediaseed-chromium.zip'), /Ukendt/);
});

test('download click fetches once and saves validated local bytes with the correct filename', async () => {
  let release, options, clicked = 0, appended = 0;
  const {c, get} = setup('Chrome/130', false, {URL: {createObjectURL: blob => {assert.equal(blob.size, zip.length); return 'blob:synthetic';}, revokeObjectURL() {}}, fetch: (path, opts) => {assert.equal(path, '/static/extensions/mediaseed-chromium.zip'); options = opts; return new Promise(resolve => {release = resolve;});}});
  c.document.createElement = () => ({click() {clicked++; assert.equal(this.download, 'mediaseed-chromium.zip'); assert.equal(this.href, 'blob:synthetic');}, remove() {}});
  c.document.body = {append() {appended++;}};
  await new Promise(r => setTimeout(r, 0));
  const first = get('extension-download').click({preventDefault() {}});
  await get('extension-download').click({preventDefault() {}}); assert.equal(clicked, 0);
  assert.equal(get('extension-browser').disabled, true);
  release(response()); await first;
  assert.equal(options.credentials, 'same-origin'); assert.equal(options.redirect, 'error'); assert.equal(options.cache, 'no-store');
  assert.equal(clicked, 1); assert.equal(appended, 1); assert.equal(get('extension-browser').disabled, false);
  assert.match(get('extension-progress').textContent, /hentet/);
  c.fetch = async () => response(zip.subarray(0, -1));
  await get('extension-download').click({preventDefault() {}});
  assert.equal(clicked, 1); assert.match(get('message').textContent, /afbrudt/);
  assert.equal(get('extension-download')['aria-disabled'], undefined);
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
