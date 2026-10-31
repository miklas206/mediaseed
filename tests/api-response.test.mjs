import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
const source = readFileSync(new URL('../mediaseed/static/app.js', import.meta.url), 'utf8');
function client(fetch) {
  const context = vm.createContext({fetch, csrf:'synthetic', t:(s,args={})=>s.replace(/\{(\d+)\}/g,(_,n)=>args[n]), seedDetailCache:new Map()});
  vm.runInContext(source.slice(source.indexOf('async function api('), source.indexOf('globalThis.MediaAppTheme.setOverrideHandler')), context);
  return context.api;
}
test('blank rejection and proxy HTML produce actionable errors without leaking bodies or retrying imports', async () => {
  for (const status of [401,403,502,504,200]) {
    let count=0;
    const api=client(async()=>{count++;return {status,ok:status===200,json:async()=>{throw SyntaxError('The string did not match the expected pattern. private-passkey');}};});
    await assert.rejects(api('trackers/test/torrents/12',{ratio:1}), e=>{
      assert.equal(e.status,status);
      assert.doesNotMatch(e.message,/private-passkey|expected pattern/);
      assert.match(e.message,status===401?/Sign in/:status===403?/Session verification/:/HTTP/);
      return true;
    });
    assert.equal(count,1);
  }
});
test('network errors are not retried and do not expose private transport details',async()=>{
  let count=0;
  await assert.rejects(client(async()=>{count++;throw Error('private-passkey');})('trackers/test/torrents/12',{ratio:1}),/could not be confirmed/);
  assert.equal(count,1);
});
test('normal JSON errors and successful paused additions keep their contracts',async()=>{
  const added={added:true,stopped:true,downloadsEnabled:false};
  assert.equal(await client(async()=>({ok:true,status:200,json:async()=>added}))('trackers/test/torrents/12',{ratio:1}),added);
  await assert.rejects(client(async()=>({ok:false,status:422,json:async()=>({detail:'Choose an approved download folder'})}))('test'),e=>e.status===422&&e.message==='Choose an approved download folder');
});
test('JSON null cannot be mistaken for a confirmed import',async()=>{
  await assert.rejects(client(async()=>({ok:true,status:200,json:async()=>null}))('test',{ratio:1}),/unreadable/);
});
