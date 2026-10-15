import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import test from 'node:test';

// Exercise the production dialog and source-refresh functions together.
test('account refresh preserves the open rank dialog and selected rank', async () => {
  class Node {
    constructor(tag,text=''){this.tag=tag;this.textContent=text;this.children=[];this.value='';this.open=false;this.dataset={};}
    append(...nodes){this.children.push(...nodes);}
    replaceChildren(...nodes){this.children=[...nodes];}
    setAttribute(){}
    showModal(){this.open=true;}
    close(){this.open=false;}
    get firstChild(){return this.children[0];}
  }
  const body=new Node('body'),list=new Node('div'),account=new Node('select'),submit=new Node('button');
  body.append(list,account,submit);
  const find=(node,id)=>node.id===id?node:node.children.map(child=>find(child,id)).find(Boolean);
  const document={body,createElement:tag=>new Node(tag),querySelector:selector=>({'#tracker-list':list,'#tracker-account':account,'#tracker-search-submit':submit}[selector]||find(body,selector.slice(1)))};
  const row={id:'account',name:'Synthetic',ranks:{automatic:'Explorer'},rankProgress:{target:{title:'Contributor'},table:[{title:'Contributor',requirements:[{metric:'trueUpload',goal:1,unit:'bytes',missing:null}]},{title:'Guru',requirements:[]}]}};
  const context=vm.createContext({document,preserveListInteraction:()=>()=>{},appRole:'viewer',api:async()=>[row],renderTrackerOverview(){},trackerCard:()=>new Node('article'),element:(tag,text,cls)=>Object.assign(new Node(tag,text),{className:cls}),command:(text,onclick)=>Object.assign(new Node('button',text),{onclick}),t:(text,args={})=>text.replace(/\{(\d+)\}/g,(_,key)=>args[key]),trackerBytes:String,trackerNumber:String});
  const source=readFileSync(new URL('../mediaseed/static/app.js',import.meta.url),'utf8');
  vm.runInContext(source.slice(source.indexOf('function rankGoal('),source.indexOf('function trackerCard('))+'\n'+source.slice(source.indexOf('async function loadSources('),source.indexOf('let trackerSearchGeneration')),context);
  context.showRankDialog(row);
  const dialog=document.querySelector('#rank-requirements-dialog'),select=find(dialog,'rank-choice');
  assert.equal(dialog.open,true);assert.equal(select.value,'Contributor');
  select.value='Guru';select.onchange();dialog.scrollTop=80;
  for(let i=0;i<3;i++)await context.loadSources('trackers');
  assert.equal(document.querySelector('#rank-requirements-dialog'),dialog);
  assert.equal(dialog.open,true);assert.equal(select.value,'Guru');assert.equal(dialog.scrollTop,80);
  dialog.children[0].children[1].onclick();assert.equal(dialog.open,false);
});
