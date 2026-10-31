import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';
const context=vm.createContext({});
vm.runInContext(readFileSync(new URL('../mediaseed/static/history-chart.js',import.meta.url),'utf8'),context);
const model=context.MediaSeedHistoryChart.model;
const sample=(at,value,extra={})=>({at,stats:{ratio:value,uploaded:value,downloaded:value},...extra});
test('history preserves real zero, negative buffer, unknown values and single points',()=>{
 const data=model([sample(900,0),sample(1800,null),sample(2700,2)],'ratio');
 assert.equal(data.known,2);assert.equal(data.points[1].value,null);assert.equal((data.path.match(/M/g)||[]).length,2);
 assert.equal(model([sample(900,0)],'ratio').points[0].x,300);
 assert.equal(model([{at:900,stats:{buffer:-100}}],'buffer').low,-100);
 assert.equal(model([sample(900,NaN),sample(1800,Infinity),sample(2700,'3')],'ratio').known,0);
 assert.equal(model([],'ratio').path,'');
});
test('history breaks time gaps, marked downsampling gaps and cumulative resets',()=>{
 for(const rows of [[sample(900,1),sample(3600,2)],[sample(900,1),sample(1800,2,{gapBefore:true})],[sample(900,1),sample(1800,2,{gapMetrics:['ratio']})]]){
  assert.equal((model(rows,'ratio').path.match(/M/g)||[]).length,2);
 }
 const reset=[sample(900,100),sample(1800,1)];
 for(const key of ['uploaded','downloaded'])assert.equal((model(reset,key).path.match(/M/g)||[]).length,2);
 assert.equal((model(reset,'ratio').path.match(/M/g)||[]).length,1);
 assert.equal((model([sample(900,1),sample(4500,2)],'ratio',3600).path.match(/M/g)||[]).length,1);
});

test('area shading follows each continuous segment and preserves isolated observations',()=>{
 const rows=[sample(900,1),sample(1800,2),sample(5400,3),sample(6300,4),sample(9000,5)];
 const data=model(rows,'ratio');
 assert.equal((data.path.match(/M/g)||[]).length,3);
 assert.equal((data.area.match(/M/g)||[]).length,2);
 assert.equal(data.points.at(-1).breakBefore,true);
 assert.equal((data.area.match(/Z/g)||[]).length,2);
});


test('smooth curves preserve observations, extrema and a readable right axis',()=>{
 const rows=[sample(900,1),sample(1800,1.01),sample(2700,4),sample(3600,2),sample(4500,2)];
 const data=model(rows,'ratio',900,360,{left:8,right:100});
 assert.equal(data.points[0].x,8);assert.equal(data.points.at(-1).x,260);
 const segments=[...data.path.matchAll(/C([\d.-]+),([\d.-]+) ([\d.-]+),([\d.-]+) ([\d.-]+),([\d.-]+)/g)];
 assert.equal(segments.length,4);
 for(const [i,segment] of segments.entries()){
  const a=data.points[i],b=data.points[i+1],lower=Math.min(a.y,b.y)-.01,upper=Math.max(a.y,b.y)+.01;
  for(let step=0;step<=20;step++){
   const t=step/20,y=(1-t)**3*a.y+3*(1-t)**2*t*Number(segment[2])+3*(1-t)*t*t*Number(segment[4])+t**3*b.y;
   assert(y>=lower&&y<=upper,'Smoothing must not invent an overshoot');
  }
  assert(Math.abs(Number(segment[5])-b.x)<.01);assert(Math.abs(Number(segment[6])-b.y)<.01);
 }
});

test('changed observations remove stair steps without joining missing periods or resets',()=>{
 const rows=[1,1,1,2,2,3,3].map((value,i)=>sample(900+i*900,value));
 const data=model(rows,'ratio',900,600,{left:8,right:100},{changes:true});
 assert.deepEqual(Array.from(data.points,p=>p.at),[900,3600,5400]);
 assert.equal((data.path.match(/M/g)||[]).length,1);
 assert.equal((data.path.match(/C/g)||[]).length,2);
 const gap=model([sample(900,1),sample(1800,1),sample(5400,2)],'ratio',900,600,{left:8,right:100},{changes:true});
 assert.equal((gap.path.match(/M/g)||[]).length,2);
 for(const key of ['real_uploaded','real_downloaded']){
  const physical=[100,101,1,2].map((value,i)=>({at:600+i*60,stats:{[key]:value}}));
  const reset=model(physical,key,60,600,{left:8,right:100},{changes:true});
  assert.equal((reset.path.match(/M/g)||[]).length,2);
 }
 const long=model(Array.from({length:100},(_,i)=>sample(900+i*900,i)),'ratio',900,600,{left:8,right:100},{changes:true});
 assert.equal(long.points.length,60);assert.equal(long.points[0].value,40);
});
