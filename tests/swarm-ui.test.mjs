import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import test from 'node:test';
const source=readFileSync(new URL('../mediaseed/static/rich-ui.js',import.meta.url),'utf8');
function setup(){
  const element=(tag,text='',className='')=>({tag,textContent:text,className,dataset:{},setAttribute(){},children:[],append(...children){this.children.push(...children);}});
  const t=(s,params={})=>s.replace(/\{(\d+)\}/g,(_,n)=>params[n]??'');
  const ctx=vm.createContext({element,t,trackerNumber:n=>n.toLocaleString('da-DK'),locale:()=> 'da-DK'});
  vm.runInContext(source.slice(source.indexOf('function seedPeerCount('),source.indexOf('function seedTorrent(')),ctx);
  return ctx;
}
function lines(view){return view.children[1].children.filter(node=>node.className.includes('seed-peer-line'));}
function text(node){return node.textContent+' '+node.children.map(text).join(' ');}
test('external torrents show total swarm and connected peers as different counts',()=>{
 const ctx=setup(),row={hash:'a',num_complete:24,num_incomplete:8,num_seeds:2,num_leechs:0};
 const view=ctx.seedSwarmView(row);
 assert.equal(view.tag,'details');
 assert.match(text(view.children[0]),/Seeders.*2.*Leechers.*0/);
 assert.equal(lines(view).length,2);
 assert.match(text(lines(view)[1]),/Swarm.*Seeders.*24.*Leechers.*8/);
 assert.match(text(lines(view)[0]),/Connected.*Seeders.*2.*Leechers.*0/);
});
test('unknown is not zero, negative sentinels and invalid types stay unknown',()=>{
 const ctx=setup();
 for(const value of [undefined,null,-1,true,NaN,Infinity,'3',1.3])assert.equal(ctx.seedPeerCount(value),'?');
 assert.equal(ctx.seedPeerCount(0),'0');
 const view=ctx.seedSwarmView({num_complete:-1,num_incomplete:null,num_seeds:0,num_leechs:0});
 assert.match(text(lines(view)[1]),/Seeders.*\?.*Leechers.*\?/);
 assert.match(text(lines(view)[0]),/Seeders.*0.*Leechers.*0/);
});
test('tracker API counts have their own source, timestamp and stale state',()=>{
 const ctx=setup(),row={hash:'a',num_complete:12,num_incomplete:3,swarm:{tracker:'Synthetic',status:'ok',seeders:19,leechers:4,checkedAt:Date.now()/1000}};
 let view=ctx.seedSwarmView(row);
 assert.match(text(lines(view)[2]),/Tracker API.*Seeders.*19.*Leechers.*4/);
 assert.match(lines(view)[2].title,/Synthetic.*Updated/);
 row.swarm.checkedAt-=181;
 view=ctx.seedSwarmView(row);
 assert.match(text(lines(view)[2]),/Seeders.*\?.*unavailable or stale/);
 assert.match(text(lines(view)[1]),/Seeders.*12/);
 row.swarm.status='pending';
 assert.match(text(lines(ctx.seedSwarmView(row))[2]),/Waiting for API data/);
});
