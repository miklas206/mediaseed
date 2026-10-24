import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import test from 'node:test';
const source = readFileSync(new URL('../mediaseed/static/app.js', import.meta.url), 'utf8');
test('refresh preserves an open menu and keyboard focus by row identity after reordering', () => {
  const document = {activeElement: null};
  function row(id, open=false) {
    const menu = {open}, peers = {open:false};
    const controls = [{focus(){document.activeElement=this;}}, {focus(){document.activeElement=this;}}];
    return {dataset:{key:id},menu,peers,controls,contains:node=>controls.includes(node),
      querySelector:selector=>selector==='details' || (selector==='details[open]' && menu.open) ? menu : null,
      querySelectorAll:selector=>selector==='details'?[peers,menu]:controls};
  }
  const first=row('one',true), second=row('two');
  const target={children:[first,second]};document.activeElement=first.controls[1];
  const context=vm.createContext({document});
  const begin=source.indexOf('function preserveListInteraction(');
  vm.runInContext(source.slice(begin,source.indexOf('\nasync function refreshProfiles',begin)>0?source.indexOf('\nasync function refreshProfiles',begin):source.indexOf('\nfunction renderTorrents',begin)),context);
  const restore=context.preserveListInteraction(target,'key');
  const fresh=row('one');target.children=[row('two'),fresh];restore();
  assert.equal(fresh.peers.open,false);assert.equal(fresh.menu.open,true);assert.equal(document.activeElement,fresh.controls[1]);
  document.activeElement={outside:true};const outside=document.activeElement;
  const noSteal=context.preserveListInteraction(target,'key');target.children=[row('one')];noSteal();
  assert.equal(document.activeElement,outside);
  document.activeElement=target.children[0].controls[0];const removed=context.preserveListInteraction(target,'key');target.children=[];
  assert.doesNotThrow(removed);
});

test('source polling coalesces slow requests and rejects responses superseded by a manual refresh', async () => {
  const pending=[];let paints=0;
  const target={replaceChildren(){paints++;},append(){}};
  const context=vm.createContext({api:()=>new Promise(resolve=>pending.push(resolve)),document:{querySelector:s=>s==='#feed-preview'?null:target},
    preserveListInteraction:()=>()=>{},element:()=>({}),t:s=>s});
  vm.runInContext(source.slice(source.indexOf('async function loadSources('), source.indexOf('let trackerSearchGeneration')),context);
  const first=context.pollSources('feeds');
  await context.pollSources('feeds');assert.equal(pending.length,1);
  const manual=context.loadSources('feeds');assert.equal(pending.length,2);
  pending[1]([]);await manual;assert.equal(paints,1);
  pending[0]([]);await first;assert.equal(paints,1);
  const next=context.pollSources('feeds');assert.equal(pending.length,3);pending[2]([]);await next;assert.equal(paints,2);
});
