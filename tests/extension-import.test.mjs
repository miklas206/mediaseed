import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';

test('extension appends a batch, preserves settings and deduplicates receipts across intervening files',async()=>{
 let receive,reply,opened=0,dialogOpen=false;
 const dialog={}, mode={dispatchEvent(){}};
 const document={querySelector:selector=>selector==='#workspace'?{hidden:false}:selector==='dialog[open]'&&dialogOpen?dialog:null,addEventListener:(type,fn)=>receive=fn,dispatchEvent:event=>reply=JSON.parse(event.detail)};
 const c=vm.createContext({document,Uint8Array,File,atob,Event,CustomEvent,crypto:webcrypto,appRole:'user',addingTorrent:false,retryAddDraft:true,t:s=>s,addDialog:dialog,torrentFiles:[],
  openAdd:()=>{assert.equal(c.retryAddDraft,false);opened++;dialogOpen=true;},addForm:{elements:{namedItem:()=>({value:'file'})},querySelector:()=>mode},selectTorrentFiles:files=>c.torrentFiles.push(...files.map(file=>({file})))});
 vm.runInContext(readFileSync(new URL('../mediaseed/static/extension-import.js',import.meta.url),'utf8'),c);
 const send=(id,body='de')=>receive({detail:JSON.stringify({id,item:{kind:'file',name:id+'.torrent',base64:btoa(body)}})});
 for(let i=0;i<15;i++){await send('transfer-'+i);assert.equal(reply.opened,true);}
 assert.equal(opened,1);assert.equal(c.torrentFiles.length,15);
 await send('transfer-0');assert.equal(c.torrentFiles.length,15);assert.equal(reply.opened,true);
 c.addingTorrent=true;await send('during-submit');assert.ok(reply.error);assert.equal(c.torrentFiles.length,15);c.addingTorrent=false;
 dialogOpen=false;c.retryAddDraft=true;
 await send('invalid','html');assert.ok(reply.error);assert.equal(c.retryAddDraft,true);assert.equal(opened,1);
 c.appRole='viewer';await send('readonly');assert.ok(reply.error);assert.equal(opened,1);
});
