/* Measured cumulative totals; tracker credit and physical traffic keep separate scopes. Missing observations and counter resets are never interpolated. */
globalThis.MediaSeedHistoryChart = (() => {
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  function curve(points) {
    if (!points.length) return '';
    const pair = point => point.x.toFixed(2)+','+point.y.toFixed(2);
    let path = 'M'+pair(points[0]);
    const slopes = points.slice(1).map((p,i)=>(p.y-points[i].y)/Math.max(.000001,p.x-points[i].x));
    const tangent = i => {
      if (i===0) return slopes[0];
      if (i===points.length-1) return slopes.at(-1);
      const a=slopes[i-1], b=slopes[i];
      return a*b<=0 ? 0 : Math.sign(a)*Math.min(Math.abs((a+b)/2),3*Math.min(Math.abs(a),Math.abs(b)));
    };
    for(let i=1;i<points.length;i++){
      const a=points[i-1],b=points[i],dx=(b.x-a.x)/3;
      path+=' C'+pair({x:a.x+dx,y:a.y+dx*tangent(i-1)})+' '+pair({x:b.x-dx,y:b.y-dx*tangent(i)})+' '+pair(b);
    }
    return path;
  }
  function model(samples, key, interval = 900, width = 600, padding = {left:48,right:16}, options = {}) {
    let points = samples.filter(row => finite(row.at) && row.at > 0)
      .map(row => ({at:row.at, value:finite(row.stats?.[key]) ? row.stats[key] : null, gap:row.gapBefore === true || row.gapMetrics?.includes(key)}))
      .sort((a,b) => a.at-b.at);
    if(options.changes){
      let previous=null;
      points=points.filter(point=>{
        const interrupted=previous && point.at-previous.at>Math.max(1,interval)*1.5;
        point.gap=point.gap||interrupted;
        const keep=!previous||point.value!==previous.value||point.gap;
        previous=point;return keep;
      }).slice(-60);
    }
    const values=points.filter(p=>p.value!==null).map(p=>p.value);
    const low=values.length?Math.min(...values):0, high=values.length?Math.max(...values):1;
    const pad=high===low?Math.max(1,Math.abs(high)*.05):(high-low)*.08;
    const first=options.domain?.[0]??points[0]?.at??0, span=Math.max(1,(options.domain?.[1]??points.at(-1)?.at??first)-first);
    let previous=null, gaps=0;const segments=[];
    for(const point of points){
      point.x=points.length===1&&!options.domain?width/2:padding.left+(point.at-first)/span*(width-padding.left-padding.right);
      point.y=point.value===null?null:148-(point.value-low+pad)/(high-low+2*pad)*124;
      const broken=!previous || previous.value===null || point.gap || (!options.changes&&point.at-previous.at>Math.max(1,interval)*1.5) ||
        (['uploaded','downloaded','real_uploaded','real_downloaded'].includes(key) && point.value!==null && point.value<previous.value);
      point.breakBefore=broken;
      if(previous && (broken || point.value===null))gaps++;
      if(point.value!==null){
        if(broken)segments.push([]);segments.at(-1).push(point);
      }
      previous=point;
    }
    const path=segments.map(curve).join(' ');
    const area=segments.filter(segment=>segment.length>1).map(segment=>`${curve(segment)} L${segment.at(-1).x.toFixed(2)},148 L${segment[0].x.toFixed(2)},148 Z`).join(' ');
    return {points,path,area,width,low,high,yLow:148-pad/(high-low+2*pad)*124,yHigh:148-(high-low+pad)/(high-low+2*pad)*124,gaps,known:values.length};
  }
  function mount(){
    const figure=element('figure',undefined,'insight-chart'),caption=element('span',undefined,'history-metric-label');
    const controls=element('div',undefined,'history-controls'),toolbar=element('div',undefined,'history-toolbar');
    const headline=element('figcaption',undefined,'history-headline'),valueGroup=element('div'),value=element('strong',undefined,'history-value');
    const badge=element('span',t('Tracker history'),'history-source-badge');
    valueGroup.append(caption,value);headline.append(valueGroup,badge);
    const source=element('p',undefined,'muted insight-chart-source'),empty=element('p',undefined,'muted');
    const ns='http://www.w3.org/2000/svg';
    const svgNode=(name,attrs={})=>{const node=document.createElementNS(ns,name);for(const [key,value] of Object.entries(attrs))node.setAttribute(key,value);return node;};
    const svg=svgNode('svg',{viewBox:'0 0 600 174',preserveAspectRatio:'none',role:'slider',tabindex:'0','aria-orientation':'horizontal'});
    const axis=svgNode('g',{'aria-hidden':'true',class:'history-axis'}),series=svgNode('g',{'aria-hidden':'true',class:'history-series'});
    const fill=svgNode('path',{class:'history-area','aria-hidden':'true'});series.append(fill);
    const marker=svgNode('g',{'aria-hidden':'true',class:'history-marker'}),guide=svgNode('line',{y1:20,y2:150}),dot=svgNode('circle',{r:4});marker.append(guide,dot);svg.append(axis,series,marker);
    const readout=element('output',undefined,'history-readout'),range=element('div',undefined,'history-range'),startLabel=element('small'),endLabel=element('small');range.append(startLabel,endLabel);
    const gapNote=element('p',undefined,'muted history-gap-note');
    const details=element('details'),summary=element('summary');details.append(summary);
    figure.append(headline,controls,toolbar,empty,svg,range,readout,details);
    let data={points:[]},key='',identity='',index=0,format=String,exact=String,when=String,signature='',tableSignature='',animation=null,lastArgs=null,multiple=[];
    function select(next){
      index=Math.max(0,Math.min(data.points.length-1,next));const point=data.points[index];if(!point)return;
      const selectedValue=multiple.length ? multiple.map(series=>{const value=series.data.points.find(p=>p.at===point.at)?.value;return series.label+': '+(value==null?'—':series.exact(value));}).join(' · ') : point.value===null?t('Missing measurement'):exact(point.value);
      value.textContent=point.value===null?t('Unknown'):format(point.value);
      value.title=selectedValue;
      readout.textContent=when(point.at)+' · '+selectedValue;
      const located=multiple.length?multiple.map(series=>series.data.points.find(p=>p.at===point.at)).find(p=>p&&p.value!==null):point;
      marker.style.display=!located||located.value===null?'none':'';
      guide.setAttribute('x1',point.x);guide.setAttribute('x2',point.x);dot.setAttribute('cx',point.x);dot.setAttribute('cy',located?.y??0);
      svg.setAttribute('aria-valuenow',index);svg.setAttribute('aria-valuetext',readout.textContent);
    }
    const pointAt=event=>{
      if(!data.points.length)return;const box=svg.getBoundingClientRect(),x=(event.clientX-box.left)/box.width*data.width;
      let nearest=0;data.points.forEach((point,i)=>{if(Math.abs(point.x-x)<Math.abs(data.points[nearest].x-x))nearest=i;});select(nearest);
    };
    svg.addEventListener('pointermove',event=>{if(event.pointerType==='mouse'||event.buttons)pointAt(event);});
    svg.addEventListener('pointerdown',event=>{pointAt(event);svg.focus({preventScroll:true});});
    svg.addEventListener('keydown',event=>{
      const next={ArrowLeft:index-1,ArrowDown:index-1,ArrowRight:index+1,ArrowUp:index+1,Home:0,End:data.points.length-1}[event.key];
      if(next!==undefined){event.preventDefault();select(next);}
    });
    const reduced=()=>document.documentElement.dataset.reducedMotion==='true'||matchMedia('(prefers-reduced-motion: reduce)').matches;
    const stopMotion=()=>{if(document.hidden||reduced())animation?.cancel();};
    document.addEventListener('visibilitychange',stopMotion);
    matchMedia('(prefers-reduced-motion: reduce)').addEventListener('change',stopMotion);
    new MutationObserver(stopMotion).observe(document.documentElement,{attributes:true,attributeFilter:['data-reduced-motion']});
    const view={figure,controls,toolbar,interacting:()=>details.open||figure.matches(':hover,:focus-within'),update(samples,metric,options){
      lastArgs=[samples,metric,options];
      const width=Math.max(180,Math.round(svg.getBoundingClientRect().width||data.width||600));
      svg.setAttribute('viewBox',`0 0 ${width} 174`);
      const padding=options.rightAxis?{left:8,right:Math.min(108,width*.34)}:{left:48,right:16};
      const build=(entry,domain)=>model(entry.samples||samples,entry.key,entry.interval||options.interval,width,padding,{changes:options.changes,domain});
      multiple=(options.series||[]).map(entry=>({...entry,data:build(entry)}));
      if(multiple.length){
        const timestamps=multiple.flatMap(entry=>entry.data.points.map(p=>p.at));
        const domain=timestamps.length?[Math.min(...timestamps),Math.max(...timestamps)]:undefined;
        multiple=multiple.map(entry=>({...entry,data:build(entry,domain)}));
      }
      const next=multiple.length?{...multiple[0].data}:model(samples,metric,options.interval,width,padding,{changes:options.changes}),selectedAt=data.points[index]?.at;
      if(multiple.length){
        const union=new Map();for(const entry of multiple)for(const point of entry.data.points)union.set(point.at,point);
        next.points=[...union.values()].sort((a,b)=>a.at-b.at);
        next.known=Math.max(...multiple.map(entry=>entry.data.known));next.gaps=Math.max(...multiple.map(entry=>entry.data.gaps));
      }
      const nextIdentity=options.identity+':'+metric,changed=signature!==JSON.stringify([samples,options.series?.map(entry=>[entry.key,entry.samples]),metric,options.identity,locale(),width]);
      const wasSame=identity===nextIdentity;identity=nextIdentity;key=metric;data=next;format=options.format;exact=options.exact;when=options.when;
      const first=data.points.find(p=>p.value!==null),last=data.points.findLast(p=>p.value!==null);
      caption.textContent=options.label;
      headline.hidden=options.hideHeadline===true;
      figure.classList.toggle('history-combined',multiple.length>0);
      figure.style.setProperty('--history-color',options.color||'var(--accent)');
      source.textContent=options.source||t('Tracker account totals · not local transfer speed');
      badge.textContent=t(data.gaps?'Incomplete history':'Tracker history');
      badge.title=t(data.gaps?'Gaps indicate missing measurements or counter resets':'Tracker account totals · not local transfer speed');
      value.textContent=data.points.length?value.textContent:t('Unknown');
      gapNote.textContent=t('Gaps indicate missing measurements or counter resets');gapNote.hidden=!data.gaps;
      empty.hidden=data.known>1;
      empty.textContent=options.changes&&data.known===1&&samples.filter(row=>finite(row.stats?.[metric])).length>1?t('The value is unchanged in this period.'):!data.points.length?t('History starts collecting now. Earlier account totals cannot be reconstructed.'):!data.known?t('No measurements for this metric.'):t('One sample saved. The chart appears as history grows.');
      svg.toggleAttribute('hidden',!data.points.length);readout.hidden=!data.points.length;range.hidden=!data.points.length;details.hidden=!data.points.length;
      svg.setAttribute('aria-label',options.label+' · '+(options.source||t('Tracker account totals'))+' · '+t('Use arrow keys to inspect recorded values'));
      svg.setAttribute('aria-valuemin',0);svg.setAttribute('aria-valuemax',Math.max(0,data.points.length-1));
      svg.setAttribute('aria-disabled',String(!data.points.length));svg.setAttribute('tabindex',data.points.length?'0':'-1');
      if(!changed)return;
      signature=JSON.stringify([samples,options.series?.map(entry=>[entry.key,entry.samples]),metric,options.identity,locale(),width]);axis.replaceChildren();series.replaceChildren();
      for(const [y,value] of (data.high===data.low?[[data.yHigh,data.high]]:[[data.yHigh,data.high],[(data.yHigh+data.yLow)/2,(data.high+data.low)/2],[data.yLow,data.low]])){
        const line=svgNode('line',{x1:padding.left,x2:data.width-padding.right,y1:y,y2:y});
        const text=svgNode('text',{x:options.rightAxis?data.width-padding.right+10:44,y:y+3,'text-anchor':options.rightAxis?'start':'end'});text.textContent=multiple.length?'':options.axisFormat?options.axisFormat(value,data.low,data.high):format(value);axis.append(line,text);
      }
      for(const [number,entry] of (multiple.length?multiple:[{data,color:options.color}]).entries()){
        const path=svgNode('path',{d:entry.data.path.trim(),class:'history-line'});
        if(entry.color)path.style.stroke=entry.color;
        if(multiple.length){path.setAttribute('stroke-dasharray',['','8 4','3 3','10 3 2 3','5 3'][number%5]);const title=svgNode('title');title.textContent=entry.label;path.append(title);}
        else {fill.setAttribute('d',data.area);series.append(fill);}
        series.append(path);
      // Dots preserve isolated observations on either side of gaps.
        for(const [i,point] of entry.data.points.entries())if(point.value!==null&&(entry.data.points.length===1||point.breakBefore||!entry.data.points[i-1]||entry.data.points[i-1].value===null||!entry.data.points[i+1]||entry.data.points[i+1].value===null||entry.data.points[i+1].breakBefore)){const dot=svgNode('circle',{cx:point.x,cy:point.y,r:entry.data.points.length===1?4:2});if(entry.color)dot.style.fill=entry.color;series.append(dot);}
      }
      const shortTime=at=>new Date(at*1000).toLocaleString(locale(),{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'});
      startLabel.textContent=data.points.length?shortTime(data.points[0].at):'';
      endLabel.textContent=data.points.length?shortTime(data.points.at(-1).at):'';
      startLabel.title=data.points.length?when(data.points[0].at):'';
      endLabel.title=data.points.length?when(data.points.at(-1).at):'';
      summary.textContent=t('Show recorded values');
      const nextTableSignature=JSON.stringify([samples,options.series?.map(entry=>[entry.key,entry.samples]),metric,options.identity,locale()]);
      if(tableSignature!==nextTableSignature){
        const table=detailTable(t('History'),[['at',t('Time')],['value',t('Value')]],data.points.map((p,i)=>({at:when(p.at),value:multiple.length?multiple.map(series=>{const value=series.data.points.find(point=>point.at===p.at)?.value;return series.label+': '+(value==null?'—':series.exact(value));}).join(' · '):p.value===null?t('Missing measurement'):exact(p.value)})));
        details.replaceChildren(summary,source,gapNote,table);tableSignature=nextTableSignature;
      }
      select(wasSame&&data.points.some(p=>p.at===selectedAt)?data.points.findIndex(p=>p.at===selectedAt):data.points.length-1);
      animation?.cancel();
      if(!reduced()&&!document.hidden&&!figure.closest('[hidden]')&&typeof series.animate==='function')animation=series.animate([{opacity:.35},{opacity:1}],{duration:320,easing:'ease-out'});
    }};
    if(typeof ResizeObserver!=='undefined')new ResizeObserver(()=>{if(lastArgs)view.update(...lastArgs);}).observe(svg);
    return view;
  }
  return {model,mount};
})();
