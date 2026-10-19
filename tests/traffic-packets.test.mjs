import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import test from 'node:test';
const source=readFileSync(new URL('../mediaseed/static/traffic-packets.js',import.meta.url),'utf8');
function setup(){
  function node(){return {children:[],style:{setProperty(key,value){this[key]=value;}},listeners:{},
    setAttribute(){},append(child){child.parent=this;this.children.push(child);},replaceChildren(){this.children=[];},
    addEventListener(name,handler){this.listeners[name]=handler;},remove(){this.parent.children=this.parent.children.filter(child=>child!==this);},
    get lastElementChild(){return this.children.at(-1);}};}
  const document={createElement:node,documentElement:{dataset:{}}};
  const context=vm.createContext({document});vm.runInContext(source,context);
  const route=node();route.getBoundingClientRect=()=>({left:0,top:0,width:500});
  route.querySelectorAll=()=>[0,460].map(left=>({getBoundingClientRect:()=>({left,top:0,width:40,height:40})}));
  const packets=context.MediaSeedPackets.mount(route),overlay=route.children[0];
  return {api:context.MediaSeedPackets,packets,overlay,document};
}
test('packet density follows each measured rate and stays bounded',()=>{
  const {api}=setup();
  for(const value of [0,-1,NaN,Infinity,undefined,'1024'])assert.equal(api.count(value),0);
  const counts=[1,1024,16384,65536,1048576,100*1048576,Number.MAX_VALUE].map(api.count);
  for(let i=1;i<counts.length;i++)assert.ok(counts[i]>=counts[i-1]);
  assert.ok(counts[0]>0);assert.equal(counts.at(-1),32);
});
test('continuous journeys span the icons, keep their identity and drain at the end',()=>{
  const {api,packets,overlay}=setup();packets.update(1048576,65536,true);
  const [down,up]=overlay.children;
  assert.equal(down.children.length,api.count(1048576));assert.equal(up.children.length,api.count(65536));
  assert.equal(overlay.style.left,'20px');assert.equal(overlay.style.width,'460px');
  const originals=[...down.children];
  const durations=originals.slice(0,3).map(packet=>parseFloat(packet.style['--packet-duration']));
  assert.ok(durations[0]>durations[1]&&durations[1]>durations[2]);
  packets.update(1048576,65536,true);assert.deepEqual(down.children,originals);
  packets.update(1,0,true);assert.deepEqual(down.children,originals);
  for(const packet of [...down.children,...up.children])packet.listeners.animationiteration();
  assert.equal(down.children.length,1);assert.equal(up.children.length,0);
  packets.update(1048576,1048576,false);assert.equal(down.children.length+up.children.length,0);
  packets.update(1048576,NaN,true);assert.equal(down.children.length+up.children.length,0);
});
test('reduced motion removes obsolete static markers without waiting for an animation',()=>{
  const {packets,overlay,document}=setup();document.documentElement.dataset.reducedMotion='true';
  packets.update(1048576,1048576,true);packets.update(0,0,true);
  assert.equal(overlay.children[0].children.length+overlay.children[1].children.length,0);
});
