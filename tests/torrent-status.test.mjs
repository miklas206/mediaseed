import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
const source=readFileSync(new URL('../mediaseed/static/rich-ui.js',import.meta.url),'utf8');
const context=vm.createContext({t:value=>value,torrentStatus:value=>value});
vm.runInContext(source.slice(source.indexOf('function seedCreditRatio('),source.indexOf('function seedStatusView(')),context);
const status=context.seedTorrentStatus;
test('each client state has an explanation without changing the underlying state',()=>{
 for(const [state,word] of [['stalledDL','waiting for peers'],['metaDL','metadata'],['queuedDL','download slot'],['stalledUP','Waiting for peers'],['pausedUP','files are kept'],['checkingUP','Checking'],['missingFiles','cannot find'],['moving','moving']]){
  assert(status({state,progress:.5}).reason.includes(word),state);
 }
 assert.match(status({state:'future-client-state'}).reason,/unknown/);
});
test('goal reached requires a configured active policy, achieved combined goal and measured upload',()=>{
 const row={state:'uploading',progress:1,upspeed:1024,credited_ratio:2,seeding_time:3600,cleanup:{mode:'ratio',ratio:2,hours:2,status:'active'}};
 assert.equal(status(row).label,'Goal reached · still uploading');
 for(const changed of [{upspeed:0},{upspeed:'1024'},{state:'pausedUP'},{credited_ratio:1},{credit_observation_stale:true,last_credited_ratio:2},{cleanup:{mode:'never',status:'disabled'}},{cleanup:{...row.cleanup,mode:'both'}}]){
  assert.notEqual(status({...row,...changed}).label,'Goal reached · still uploading',JSON.stringify(changed));
 }
});
