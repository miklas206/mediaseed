import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
const file = name => readFileSync(new URL('../browser-extension/' + name, import.meta.url), 'utf8');
const sample = 'magnet:?xt=urn:btih:' + 'a'.repeat(40);
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
function context(extra={}) {
 const c=vm.createContext({URL,URLSearchParams,Uint8Array,TextDecoder,AbortSignal,btoa,atob,setTimeout,clearTimeout,CustomEvent,crypto:webcrypto,...extra});
 vm.runInContext(file('i18n.js')+'\n'+file('common.js')+'\n'+file('bridge.js'),c);return c;
}
test('one source classifier recognizes torrents, magnets and RSS without separate actions',()=>{
 const m=context().MediaSeedExtension;
 for(const url of ['https://nordicbytes.org/rss/private?passkey=synthetic','https://sagmire.org/torrents/rss/123','https://tracker.example/feed.xml','https://tracker.example/feed.atom','https://tracker.example/rss.php?token=synthetic'])assert.equal(m.sourceLink(url).kind,'rss');
 for(const url of ['https://nordicbytes.org/torrents/download/123','https://tracker.example/a.torrent','https://tracker.example/download.php?id=1'])assert.equal(m.sourceLink(url).kind,'link');
 assert.equal(m.sourceLink(sample).magnet,sample);
 for(const url of ['javascript:alert(1)','https://user:pass@tracker.example/rss','https://tracker.example/rss/manage','http://tracker.example/rss'])assert.throws(()=>m.sourceLink(url));
 assert.throws(()=>m.sourceLink('magnet:?xt=urn:btih:abc'));
 assert.equal(m.origin('https://seed.example:18800/'),'https://seed.example:18800');
 for(const url of ['https://user:pass@seed.example','https://seed.example/path','https://seed.example/?key=a'])assert.throws(()=>m.origin(url));
 m.file('a.torrent',new Uint8Array([100,255,0,101]));
 assert.throws(()=>m.file('../a.torrent',new Uint8Array([100,101])));
 assert.throws(()=>m.decode('a'.repeat(6*1024*1024)));
});
test('tab selection reuses the app on the exact origin and port, preferring its pending tab',()=>{
 const m=context().MediaSeedExtension;
 const tabs=[{id:1,url:'https://seed.example:18801/'},{id:2,url:'https://seed.example:18800/static/extensions.html'},{id:3,url:'https://seed.example:18800/',lastAccessed:3},{id:4,url:'https://seed.example:18800/static/index.html',active:true}];
 assert.equal(m.selectSeedTab(tabs,'https://seed.example:18800').id,4);
 assert.equal(m.selectSeedTab(tabs,'https://seed.example:18800',3).id,3);
 assert.equal(m.selectSeedTab(tabs,'https://other.example'),undefined);
});
test('bridge only opens app dialogs and never posts additions or applies browser defaults',async()=>{
 const calls=[],listeners=new Map();let envelope;
 const c=context({location:{origin:'https://seed.example'},fetch:async(path,opts)=>{calls.push({path,opts});return {ok:true,json:async()=>({role:'administrator',language:'da'})};},document:{
  querySelector:()=>({hidden:false}),addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener:name=>listeners.delete(name),dispatchEvent:event=>{envelope=JSON.parse(event.detail);listeners.get('mediaseed-import-result')({detail:JSON.stringify({id:envelope.id,ok:true,opened:true})});}
 }});
 const item={kind:'magnet',magnet:sample,transferId:'stable-transfer',start:true,cleanup:{mode:'time',delete_files:true}};
 assert.equal((await c.mediaSeedBridge('https://seed.example',item)).opened,true);
 assert.equal(envelope.id,'stable-transfer');assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/auth/me');assert.equal(calls[0].opts.credentials,'same-origin');
 assert.equal((await c.mediaSeedBridge('https://other.example',item)).error,'MediaSeed-adressen ændrede sig.');assert.equal(calls.length,1);
 c.fetch=async()=>({status:401});assert.equal((await c.mediaSeedBridge('https://seed.example',item)).login,true);
 c.fetch=async()=>({ok:true,json:async()=>({role:'viewer'})});assert.match((await c.mediaSeedBridge('https://seed.example',item)).error,/adgang/);
 c.fetch=async()=>({ok:true,json:async()=>({role:'user'})});assert.match((await c.mediaSeedBridge('https://seed.example',{kind:'rss',transferId:'rss'})).error,/adgang/);
});
test('torrent fetching remains bounded and same-origin, with cache bypass and unchanged bytes',async()=>{
 let options;const c=context({location:{origin:'https://tracker.example'},fetch:async(_,opts)=>{options=opts;return new Response('de');}});
 assert.equal((await c.mediaSeedReadFile('https://tracker.example/a.torrent')).base64,btoa('de'));
 assert.equal(options.credentials,'include');assert.equal(options.redirect,'error');assert.equal(options.cache,'no-store');
 await assert.rejects(c.mediaSeedReadFile('https://other.example/a.torrent'),/egen hjemmeside/);
 c.fetch=async()=>new Response(new Uint8Array(4*1024*1024+1));await assert.rejects(c.mediaSeedReadFile('https://tracker.example/a.torrent'),/4 MiB/);
 c.fetch=async()=>new Response('<html>not a torrent</html>');await assert.rejects(c.mediaSeedReadFile('https://tracker.example/a.torrent'),/ikke en torrentfil/);
});
function background() {
 let listener,clicked,installed,updated;const local={origin:'https://seed.example',defaults:{start:true,cleanup:{delete_files:true}}},session={},created=[],executions=[],menus=[],reports=[];
 const tabs=new Map([[1,{id:1,url:'https://seed.example/',status:'complete',windowId:1}],[9,{id:9,url:'https://nordicbytes.org/torrents/123',status:'complete'}]]);
 const get=keys=>Object.fromEntries((Array.isArray(keys)?keys:[keys]).map(key=>[key,session[key]]));
 const api={storage:{local:{get:async()=>local,set:async value=>Object.assign(local,value)},session:{get:async keys=>keys===null?session:get(keys),set:async value=>Object.assign(session,value),remove:async key=>delete session[key]}},
 permissions:{contains:async()=>true},tabs:{query:async()=>[...tabs.values()],get:async id=>tabs.get(id),update:async(id,value)=>{Object.assign(tabs.get(id),value);return tabs.get(id);},create:async value=>{const tab={id:10+created.length,status:'complete',windowId:1,...value};created.push(tab);tabs.set(tab.id,tab);return tab;},onUpdated:{addListener:fn=>updated=fn}},windows:{update:async()=>{}},
 scripting:{executeScript:async options=>{executions.push(options);return [{result:options.func.name==='mediaSeedReadFile'?{name:'Real.torrent',base64:btoa('de')}:{ok:true,opened:true}}];}},
 contextMenus:{onClicked:{addListener:fn=>clicked=fn},removeAll:async()=>{menus.length=0;},create:value=>menus.push(value)},action:{setBadgeText:async()=>{},setBadgeBackgroundColor:async()=>{},setTitle:async value=>reports.push(value.title)},
 runtime:{id:'synthetic',getURL:path=>'chrome-extension://synthetic/'+path,onInstalled:{addListener:fn=>installed=fn},onMessage:{addListener:fn=>listener=fn}}};
 const c=context({chrome:api});vm.runInContext(file('background.js'),c);
 const dispatch=(msg,sender={id:'synthetic',url:'chrome-extension://synthetic/popup.html'})=>new Promise(resolve=>listener(msg,sender,resolve));
 return {c,api,local,session,tabs,created,executions,menus,reports,dispatch,install:()=>installed(),click:(url,id=9)=>clicked({menuItemId:'mediaseed-add',linkUrl:url},tabs.get(id)),update:id=>updated(id,{status:'complete'})};
}
async function settled(){for(let i=0;i<8;i++)await tick();}
test('exactly one context menu routes both types to MediaSeed; old defaults never travel',async()=>{
 const b=background();await b.install();assert.equal(b.menus.length,1);assert.equal(b.menus[0].title,'Send til MediaSeed');
 b.click('https://nordicbytes.org/torrents/download/123');await settled();
 const reads=b.executions.filter(x=>x.func.name==='mediaSeedReadFile'),imports=b.executions.filter(x=>x.func.name==='mediaSeedBridge');
 assert.equal(reads.length,1);assert.equal(reads[0].target.tabId,9);assert.equal(imports.length,1);assert.equal(imports[0].target.tabId,1);
 assert.equal(imports[0].args[1].name,'Real.torrent');assert.equal(imports[0].args[1].start,undefined);assert.equal(imports[0].args[1].cleanup,undefined);assert.equal(b.tabs.get(1).active,true);assert.equal(b.created.length,0);assert.equal(b.session.pending,undefined);
 b.executions.length=0;
 b.click('https://nordicbytes.org/rss/private?passkey=synthetic');await settled();
 assert.equal(b.executions.length,1);assert.equal(b.executions[0].args[1].kind,'rss');assert.equal(b.executions[0].args[1].url,'https://nordicbytes.org/rss/private?passkey=synthetic');
 assert.equal(b.local.pending,undefined);assert.equal(b.local.lastResult,undefined);assert.equal(b.session.pending,undefined);
});
test('opens the saved MediaSeed address when there is no existing app tab',async()=>{
 const b=background();b.tabs.delete(1);b.click(sample);await settled();assert.equal(b.created.length,1);assert.equal(b.created[0].url,'https://seed.example/');assert.equal(b.created[0].active,true);assert.equal(b.session.pending,undefined);
});
test('pending survives login and worker restart, delivers once after login without downloading again',async()=>{
 const b=background();let ready=false;
 b.api.scripting.executeScript=async options=>{b.executions.push(options);return [{result:options.func.name==='mediaSeedReadFile'?{name:'Real.torrent',base64:btoa('de')}:ready?{ok:true,opened:true}:{login:true,error:'Log ind i MediaSeed'}}];};
 b.click('https://nordicbytes.org/torrents/download/123');await settled();const id=b.session.pending.transferId;
 assert.equal(b.session.pending.kind,'file');assert.equal((await b.dispatch({type:'state'})).pending.name,'Real.torrent');
 // Recreate the service worker globals while retaining actual session storage.
 const c=context({chrome:b.api});vm.runInContext(file('background.js'),c);
 ready=true;b.update(1);await settled();assert.equal(b.session.pending,undefined);
 assert.equal(b.executions.filter(x=>x.func.name==='mediaSeedReadFile').length,1);
 assert.equal(b.executions.filter(x=>x.func.name==='mediaSeedBridge').at(-1).args[1].transferId,id);
 b.update(1);await settled();assert.equal(b.executions.filter(x=>x.func.name==='mediaSeedBridge').length,2);
});
test('busy dialog, expiry, source permissions and destination changes preserve safe handoff',async()=>{
 const b=background();b.api.scripting.executeScript=async options=>{b.executions.push(options);return [{result:{error:'Finish the open dialog'}}];};
 b.click(sample);await settled();const original=b.session.pending;
 b.click('https://nordicbytes.org/rss/private');await settled();assert.equal(b.session.pending.transferId,original.transferId);assert.match(b.session.status.text,/allerede/);
 b.local.origin='https://different.example';await b.dispatch({type:'resume'});assert.equal(b.session.pending.transferId,original.transferId);assert.match(b.session.status.text,/ændret/);
 await b.dispatch({type:'clear'});assert.equal(b.session.pending,undefined);
 b.local.origin='https://seed.example';b.session.pending={...original,created:Date.now()-16*60*1000};
 assert.match((await b.dispatch({type:'resume'})).error,/udløbet/);assert.equal(b.session.pending,undefined);
});
test('first-use setup retains the source, asks for the address, then resumes the same link',async()=>{
 const b=background();b.local.origin='';b.click('https://nordicbytes.org/rss/private?key=synthetic');await settled();
 assert.equal(b.created.length,0);assert.equal(b.executions.length,0);assert.equal(b.session.pending.kind,'rss');
 b.local.origin='https://seed.example';await b.dispatch({type:'resume'});assert.equal(b.session.pending,undefined);assert.equal(b.executions[0].args[1].kind,'rss');
});
test('web pages cannot read drafts or trigger extension actions; errors redact private links',async()=>{
 const b=background();assert.ok((await b.dispatch({type:'state'},{url:'https://tracker.example',tab:{id:9}})).error);
 b.api.scripting.executeScript=async()=>{throw Error('Cannot access https://tracker.example/download?passkey=synthetic-private');};b.click(sample);await settled();
 assert.doesNotMatch(b.session.status.text,/passkey|synthetic-private|https:/);assert.equal(b.local.lastResult,undefined);assert.ok(b.session.pending);
});
test('setup has only an address and pending controls, with grants requested before saving',async()=>{
 const html=file('popup.html');assert.doesNotMatch(html,/id="(?:settings|cleanup-mode|add-default|magnet|file|enable)"/);assert.match(file('popup.css'),/width:560px/);
 const nodes=new Map(),requests=[],local={origin:'https://seed.example',defaults:{start:true}},state={pending:null};
 const node=id=>{if(!nodes.has(id))nodes.set(id,{value:'',dataset:{}});return nodes.get(id);};
 const c=context({location:{hash:''},document:{getElementById:node},chrome:{storage:{local:{get:async()=>local,set:async value=>{requests.push('save');Object.assign(local,value);}}},permissions:{request:async value=>{requests.push(value.origins);return true;}},runtime:{sendMessage:async value=>{requests.push(value.type);return value.type==='state'?state:{ok:true};}}}});
 vm.runInContext(file('popup.js'),c);await settled();assert.equal(node('origin').value,'https://seed.example');
 node('origin').value='https://new.example:18800';await node('connect').onsubmit({preventDefault(){}});
 assert.equal(local.origin,'https://new.example:18800');assert.equal(local.defaults.start,true);assert.ok(requests.indexOf('save')>requests.findIndex(x=>Array.isArray(x)));assert.ok(requests.includes('open'));
 state.pending={kind:'rss'};await vm.runInContext('refresh()',c);assert.equal(node('pending').hidden,false);await node('resume').onclick();assert.ok(requests.includes('resume'));
});
test('source import uses real metadata name and preserves original bytes', async () => {
  const encode = value => Buffer.from(value).length + ':' + value;
  const name = 'Julekalender æøå';
  const raw = Buffer.concat([Buffer.from('d4:infod4:name8:download10:name.utf-8' + encode(name) + '6:pieces4:'), Buffer.from([0,255,128,1]), Buffer.from('ee')]);
  const c = context({location:{origin:'https://tracker.example'}, fetch:async()=>new Response(raw,{headers:{'Content-Disposition':'attachment; filename="download.torrent"'}})});
  const result = await c.mediaSeedReadFile('https://tracker.example/download?id=secret');
  assert.equal(result.name, name + '.torrent');
  assert.deepEqual(Buffer.from(result.base64,'base64'),raw);
  c.fetch=async()=>new Response('de',{headers:{'Content-Disposition':"attachment; filename*=UTF-8''Sintel%20%C3%A6.torrent"}});
  assert.equal((await c.mediaSeedReadFile('https://tracker.example/download')).name,'Sintel æ.torrent');
  c.fetch=async()=>new Response('de',{headers:{'Content-Disposition':'attachment; filename="../../bad.torrent"'}});
  assert.equal((await c.mediaSeedReadFile('https://tracker.example/download')).name,'_.._bad.torrent');
  c.fetch=async()=>new Response('de');
  assert.equal((await c.mediaSeedReadFile('https://tracker.example/private-passkey')).name,'download.torrent');
});

test('rapid context sends serialize and deliver directly without creating extension tabs',async()=>{
 const b=background();
 for(let i=0;i<15;i++)b.click('https://nordicbytes.org/torrents/download/'+i);
 for(let i=0;i<100;i++)await tick();
 assert.equal(b.executions.filter(x=>x.func.name==='mediaSeedBridge').length,15);
 assert.equal(b.session.pending,undefined);assert.equal(b.created.length,0);
});
test('first use can recover a recognizable MediaSeed tab only on an already granted host',async()=>{
 const b=background();b.local.origin='';b.tabs.get(1).title='MediaSeed';
 b.api.scripting.executeScript=async options=>{b.executions.push(options);return [{result:options.func.name==='mediaSeedReadFile'?{name:'Real.torrent',base64:btoa('de')}:options.func.name==='mediaSeedBridge'?{opened:true}:true}];};
 b.click(sample);await settled();assert.equal(b.local.origin,'https://seed.example');assert.equal(b.session.pending,undefined);assert.equal(b.created.length,0);
 const denied=background();denied.local.origin='';denied.tabs.get(1).title='MediaSeed';denied.api.permissions.contains=async()=>false;
 denied.click(sample);await settled();assert.equal(denied.local.origin,'');assert.equal(denied.executions.length,0);assert.ok(denied.session.pending);assert.equal(denied.created.length,0);
});
