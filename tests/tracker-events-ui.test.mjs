import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import test from 'node:test';

test('global event display expires and does not treat stale data as active',()=>{
  const source=readFileSync(new URL('../mediaseed/static/rich-ui.js',import.meta.url),'utf8');
  let now=Date.parse('2026-10-19T12:00:00Z');
  class Clock extends Date {static now(){return now;}}
  const ctx=vm.createContext({Date:Clock,locale:()=> 'da-DK',t:(s,args={})=>s.replace('{0}',args[0]||'')});
  vm.runInContext(source.slice(source.indexOf('function trackerEventSummary('),source.indexOf('function richTrackerSummary(')),ctx);
  const row={status:'ok',checkedAt:now/1000,globalEvents:[{type:'global_freeleech',endsAt:'2026-10-19T12:00:30Z'}]};
  assert.match(ctx.trackerEventSummary(row),/^Global freeleech · until /);
  assert.equal(ctx.trackerEventSummary({...row,status:'stale'}),'Global freeleech status unknown');
  assert.equal(ctx.trackerEventSummary({...row,globalEvents:null}),'Global freeleech status unknown');
  now+=31000;assert.equal(ctx.trackerEventSummary(row),'');
  now+=60000;assert.equal(ctx.trackerEventSummary(row),'Global freeleech status unknown');
  const workspace=readFileSync(new URL('../mediaseed/static/download-workspace.js',import.meta.url),'utf8');
  assert.match(workspace,/accounts\.find\(row=>row\.status==='ok'&&row\.stats\)/);
});

test('torrent cards use only download-time evidence and previews show current benefits',()=>{
  const source=readFileSync(new URL('../mediaseed/static/rich-ui.js',import.meta.url),'utf8');
  const element=(tag,text,className)=>({tag,textContent:text,className,children:[],append(...nodes){this.children.push(...nodes);}});
  const ctx=vm.createContext({element,t:s=>s});
  vm.runInContext(source.slice(source.indexOf('function seedBadges('),source.indexOf('function seedSource(')),ctx);
  const labels=(row,preview=false)=>Array.from(ctx.seedBadges(row,preview).children,node=>node.textContent);
  const current={freeleech:100,doubleUpload:true};
  assert.deepEqual(labels({green_active:true,tracker_benefits:current}),['Download history unknown']);
  assert.deepEqual(labels({tracker_benefits:current},true),['100% Freeleech','Double upload']);
  const observed={bytes:100,globalFreeleech:true,green:true,freeleech:50,doubleUpload:true};
  const row={download_history:{downloaded:100,unknownBytes:0,observations:[observed]}};
  assert.deepEqual(labels(row),['Global freeleech','50% Freeleech','Double upload','Green · 0.5×']);
  row.tracker_benefits={freeleech:100,doubleUpload:false};
  assert.deepEqual(labels(row),['Global freeleech','50% Freeleech','Double upload','Green · 0.5×']);
  row.download_history.unknownBytes=50;
  assert.match(ctx.seedBadges(row).children[0].title,/part of the download/);
  assert.deepEqual(labels({download_history:{downloaded:100,unknownBytes:0,observations:[{bytes:100,globalFreeleech:false,green:false,freeleech:0,doubleUpload:false}]}}),['Standard']);
  assert.deepEqual(labels({download_history:{downloaded:100,unknownBytes:0,observations:[{bytes:100,globalFreeleech:false}]}}),['Download history unknown']);
  assert.doesNotMatch(labels(row).join(' '),/Downloaded|now/);
});
