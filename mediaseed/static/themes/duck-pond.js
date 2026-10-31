/* Andedammen: decorative, local SVG artwork. No API, media or account writes. */
(() => {
  if (globalThis.MediaHubDuckPond) return;
  const root=document.documentElement, motion=matchMedia('(prefers-reduced-motion: reduce)');
  let scene=null,button=null,paused=false,playLayer=null,controls=null,active=null,frameId=0;
  const storageKey='mediahub-duck-pond-paused';
  try{paused=localStorage.getItem(storageKey)==='true';}catch{}
  const duck='<svg viewBox="0 0 180 140" fill="none" aria-hidden="true"><ellipse cx="86" cy="119" rx="62" ry="9" fill="#60b9a8" opacity=".17"/><path class="pond-wing pond-wing-far" d="M70 78Q20 12 14 48Q20 83 70 92" fill="#efb63e"/><path d="M29 83 10 67c1 37 20 51 67 51 39 0 66-15 65-37-1-13-11-22-26-25l-13 19Z" fill="#efb63e"/><path d="M25 81c8 34 101 48 112 6l-30-12-44-4Z" fill="#ffdc72"/><path d="M84 76c-4-12-12-18-11-35C74 20 90 8 108 11c21 3 31 23 24 41-4 11-10 17-9 33Z" fill="#ffda68"/><path d="m131 43 27 8c4 2 3 7-1 9l-29 5-4-13Z" fill="#f28a3e"/><path d="m134 53 22 1" stroke="#d86a2c" stroke-width="2" stroke-linecap="round"/><circle cx="119" cy="36" r="4.5" fill="#193832"/><circle cx="120" cy="35" r="1.3" fill="#fff7db"/><path class="pond-wing" d="M49 83c10-8 33-8 47 2-3 18-32 22-47 9" fill="#f8c44e"/><path d="M48 82c11-8 28-6 39-1M87 25c5-6 12-9 20-8" stroke="#fff0b0" stroke-width="4" stroke-linecap="round"/></svg>';
  const blocked='a,button,input,select,textarea,label,summary,dialog,[role="button"],[role="dialog"],[contenteditable],.panel,.card,.workspace-card,.hub-app-card,.resource-card,.profile,header,footer,nav,.sidebar,.topbar,.notifications';
  const clamp=(value,min,max)=>Math.max(min,Math.min(Math.max(min,max),value));
  const quiet=()=>paused||motion.matches||root.dataset.reducedMotion==='true'||document.hidden;
  function exposedDuck(event){
    if(!scene||event.target?.closest?.(blocked))return null;
    // A visible opaque surface always owns its clicks, even if it is a custom app card.
    for(let el=event.target;el&&el!==document.body;el=el.parentElement){
      if(!(el instanceof Element))continue;
      const colour=getComputedStyle(el).backgroundColor;
      if(colour!=='transparent'&&!colour.endsWith(', 0)')&&colour.startsWith('rgb('))return null;
      const alpha=colour.match(/,\s*([\d.]+)\)$/);if(alpha&&Number(alpha[1])>=.9)return null;
    }
    return [...scene.querySelectorAll('.pond-duck-lane:not(.pond-away)')].reverse().find(lane=>{
      const r=lane.getBoundingClientRect();
      return event.clientX>=r.left+r.width*.08&&event.clientX<=r.right-r.width*.08&&event.clientY>=r.top+r.height*.08&&event.clientY<=r.bottom-r.height*.13;
    })||null;
  }
  function paint(){
    if(active)active.sprite.style.transform=`translate3d(${active.x}px,${active.y}px,0) rotate(${active.angle}deg)`;
  }
  function releaseCapture(id){try{if(id!=null&&root.hasPointerCapture(id))root.releasePointerCapture(id);}catch{}}
  function abandon(){
    cancelAnimationFrame(frameId);frameId=0;
    if(active){const previous=active;active=null;previous.lane.classList.remove('pond-away');previous.sprite.remove();releaseCapture(previous.pointer);}
    root.classList.remove('pond-grabbing','pond-over-duck');
    controls?.querySelectorAll('button').forEach(control=>control.setAttribute('aria-pressed','false'));
  }
  function splash(x,y,width){
    if(!scene||quiet())return;
    const splat=document.createElement('span');splat.className='pond-splash';splat.style.cssText=`left:${x+width/2}px;top:${y}px;--splash-size:${width}px`;
    for(let i=0;i<8;i++){
      const drop=document.createElement('i'),angle=Math.PI*(i+1)/9;
      drop.style.cssText=`--splash-x:${Math.cos(angle)*width*.65}px;--splash-y:${-Math.sin(angle)*width*.55}px;--drop-delay:${i%3*25}ms`;splat.append(drop);
    }
    splat.addEventListener('animationend',event=>{if(event.target===splat)splat.remove();});
    scene.append(splat);const splashes=scene.querySelectorAll('.pond-splash');if(splashes.length>8)splashes[0].remove();
  }
  function land(withSplash=true){
    if(!active)return;
    const bird=active;
    const x=clamp(bird.x,8,innerWidth-bird.width-8),y=clamp(bird.y,8,innerHeight-bird.height-8);
    bird.lane.style.left=`${100*x/innerWidth}%`;bird.lane.style.top=`${100*y/innerHeight}%`;
    bird.lane.style.setProperty('--duck-drift',x>innerWidth/2?'-24px':'24px');
    bird.lane.classList.add('pond-relocated');
    for(const animation of bird.lane.getAnimations())if(animation.animationName==='pond-relocated-swim')animation.currentTime=0;
    abandon();if(withSplash)splash(x,y+bird.height*.84,bird.width);
  }
  function pick(lane,event,control=null){
    if(active||!lane)return;
    const r=lane.getBoundingClientRect();if(!r.width||!r.height)return;
    const sprite=document.createElement('div');sprite.className='pond-duck pond-toss-duck pond-held';sprite.innerHTML=duck;
    sprite.style.cssText=`width:${r.width}px;--duck-facing:${getComputedStyle(lane).getPropertyValue('--duck-facing')}`;
    playLayer.append(sprite);lane.classList.add('pond-away');
    const now=performance.now();
    active={lane,sprite,control,pointer:event?.pointerId,x:r.left,y:r.top,width:r.width,height:r.height,angle:0,vx:0,vy:0,held:true,samples:[{x:event?.clientX??r.left,y:event?.clientY??r.top,time:now}],offsetX:event?event.clientX-r.left:r.width/2,offsetY:event?event.clientY-r.top:r.height/2};
    control?.setAttribute('aria-pressed','true');root.classList.add('pond-grabbing');paint();
    if(event){try{root.setPointerCapture(event.pointerId);}catch{}}
  }
  function move(event){
    if(!scene)return;
    if(!active?.held){root.classList.toggle('pond-over-duck',!!exposedDuck(event));return;}
    if(active.pointer!==event.pointerId)return;
    event.preventDefault();
    const now=performance.now();active.samples.push({x:event.clientX,y:event.clientY,time:now});active.samples=active.samples.filter(sample=>now-sample.time<120).slice(-8);
    active.x=clamp(event.clientX-active.offsetX,0,innerWidth-active.width);
    active.y=clamp(event.clientY-active.offsetY,0,innerHeight-active.height);
    const first=active.samples[0],dt=Math.max(16,now-first.time);
    active.vx=clamp((event.clientX-first.x)*1000/dt,-1600,1600);active.vy=clamp((event.clientY-first.y)*1000/dt,-1600,1600);
    active.angle=clamp(active.vx/70,-18,18);paint();
  }
  function toss(){
    if(!active?.held)return;
    active.held=false;active.sprite.classList.remove('pond-held');releaseCapture(active.pointer);
    root.classList.remove('pond-grabbing');active.control?.setAttribute('aria-pressed','false');
    const last=active.samples.at(-1);if(performance.now()-last.time>120){active.vx=0;active.vy=0;}
    if(quiet()){land(false);return;}
    active.floor=clamp(active.y+Math.max(65,Math.abs(active.vy)*.08),8,innerHeight-active.height-8);
    active.last=performance.now();active.started=active.last;
    const fly=now=>{
      if(!active||active.held)return;
      const dt=Math.min(.032,Math.max(0,(now-active.last)/1000));active.last=now;
      active.vy+=2100*dt;active.x+=active.vx*dt;active.y+=active.vy*dt;
      active.vx*=Math.exp(-.9*dt);active.angle=clamp(active.angle+active.vx*.08*dt,-38,38);
      if(active.x<0||active.x>innerWidth-active.width){active.x=clamp(active.x,0,innerWidth-active.width);active.vx*=-.45;}
      if(active.y<0){active.y=0;active.vy=Math.max(80,-active.vy*.2);}
      paint();
      if((active.y>=active.floor&&active.vy>=0)||now-active.started>2400){active.y=active.floor;land();return;}
      frameId=requestAnimationFrame(fly);
    };
    frameId=requestAnimationFrame(fly);
  }
  function mountPlay(){
    playLayer=document.createElement('div');playLayer.className='pond-play-layer';playLayer.setAttribute('aria-hidden','true');playLayer.inert=true;document.body.append(playLayer);
    controls=document.createElement('div');controls.className='pond-play-controls';
    scene.querySelectorAll('.pond-duck-lane').forEach((lane,index)=>{
      const control=document.createElement('button');control.type='button';control.setAttribute('aria-pressed','false');control.dataset.duck=String(index+1);
      control.addEventListener('keydown',event=>{
        if(['Enter',' ','Escape','ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key))event.preventDefault();else return;
        if(event.repeat&&(event.key==='Enter'||event.key===' '))return;
        if(event.key==='Escape'){if(active?.control===control)land(false);return;}
        if(event.key==='Enter'||event.key===' '){if(active?.control===control)toss();else pick(lane,null,control);return;}
        if(active?.control!==control)return;
        const step=event.shiftKey?45:15;
        active.x=clamp(active.x+(event.key==='ArrowLeft'?-step:event.key==='ArrowRight'?step:0),0,innerWidth-active.width);
        active.y=clamp(active.y+(event.key==='ArrowUp'?-step:event.key==='ArrowDown'?step:0),0,innerHeight-active.height);paint();
      });
      control.addEventListener('click',()=>{if(active?.control===control)toss();else pick(lane,null,control);});
      control.addEventListener('blur',()=>{if(active?.control===control)land(false);});controls.append(control);
    });
    document.body.append(controls);
  }
  document.addEventListener('pointerdown',event=>{
    if(event.button!==0||event.isPrimary===false||active)return;
    const lane=exposedDuck(event);if(!lane)return;
    event.preventDefault();event.stopPropagation();pick(lane,event);
  },true);
  document.addEventListener('pointermove',move,{passive:false});
  document.addEventListener('pointerup',event=>{if(active?.held&&active.pointer===event.pointerId){event.preventDefault();toss();}},true);
  document.addEventListener('pointercancel',event=>{if(active?.pointer===event.pointerId)abandon();});
  root.addEventListener('lostpointercapture',event=>{if(active?.held&&active.pointer===event.pointerId)abandon();});
  for(const type of ['touchstart','touchmove'])document.addEventListener(type,event=>{if(active?.held&&active.pointer!=null)event.preventDefault();},{passive:false});
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&active){event.preventDefault();land(false);}});
  window.addEventListener('blur',abandon);
  window.addEventListener('resize',()=>{
    abandon();scene?.querySelectorAll('.pond-relocated').forEach(lane=>{
      const r=lane.getBoundingClientRect();lane.style.left=`${100*clamp(r.left,8,innerWidth-r.width-8)/innerWidth}%`;lane.style.top=`${100*clamp(r.top,8,innerHeight-r.height-8)/innerHeight}%`;
    });
  });
  function mount(){
    scene=document.createElement('div');scene.className='duck-pond-scene';scene.setAttribute('aria-hidden','true');scene.inert=true;
    const sky=document.createElement('div');sky.className='pond-moon';scene.append(sky);
    const shimmer=document.createElement('div');shimmer.className='pond-shimmer';scene.append(shimmer);
    for(const [i,[x,y,size,duration,delay,reverse]] of [[21,80,140,31,-12,false],[68,82,180,39,-20,true],[43,37,90,47,-5,false],[84,55,110,36,-15,true],[40,87,70,34,-8,false]].entries()){
      const lane=document.createElement('div');lane.className='pond-duck-lane';lane.style.cssText=`left:${x}%;top:${y}%;--duck-size:${size}px;--swim-duration:${duration}s;--swim-delay:${delay}s;--duck-facing:${reverse?-1:1}`;
      const bird=document.createElement('div');bird.className='pond-duck';bird.innerHTML=duck;
      const ripple=document.createElement('span');ripple.className='pond-ripple';lane.append(ripple,bird);scene.append(lane);
    }
    for(const [i,[x,y]] of [[8,86],[78,91],[92,63],[31,58],[61,78]].entries()){
      const pad=document.createElement('i');pad.className='pond-lily';pad.style.cssText=`left:${x}%;top:${y}%;--pad-rotation:${i*47}deg`;scene.append(pad);
    }
    for(let i=0;i<12;i++){
      const light=document.createElement('i');light.className='pond-firefly';light.style.cssText=`left:${(i*37+9)%98}%;top:${(i*19+12)%91}%;animation-delay:-${i*1.7}s;animation-duration:${6+i%5}s`;scene.append(light);
    }
    for(const side of ['left','right']){
      const reeds=document.createElement('div');reeds.className='pond-reeds pond-reeds-'+side;
      reeds.innerHTML='<svg viewBox="0 0 250 330" fill="none"><path d="M35 330Q80 170 22 23M62 330Q105 168 88 69M82 330Q137 196 144 102M18 330Q25 202 6 155" stroke="#538d72" stroke-width="5"/><path d="M40 330Q31 196 86 153 66 268 40 330M64 330Q158 231 189 230 161 284 64 330M19 330Q-8 245 4 206 25 244 19 330" fill="#386a56"/><path d="m22 25-2-21M88 70l-1-22M144 103l4-21" stroke="#ba9a57" stroke-width="10" stroke-linecap="round"/></svg>';scene.append(reeds);
    }
    document.body.prepend(scene);
    button=document.createElement('button');button.type='button';button.className='pond-motion';
    button.addEventListener('click',()=>{paused=!paused;try{localStorage.setItem(storageKey,String(paused));}catch{}sync();});
    document.body.append(button);mountPlay();
  }
  function sync(){
    if(root.dataset.look!=='duck-pond'){
      abandon();scene?.remove();button?.remove();playLayer?.remove();controls?.remove();scene=null;button=null;playLayer=null;controls=null;return;
    }
    if(!scene)mount();
    const reduced=motion.matches||root.dataset.reducedMotion==='true';
    scene.dataset.paused=String(paused||reduced||document.hidden);
    playLayer.dataset.paused=scene.dataset.paused;
    if(document.hidden)abandon();else if(active&&!active.held&&quiet())land(false);
    const da=root.lang.startsWith('da'),off=paused||reduced;
    for(const control of controls.querySelectorAll('button')){
      control.textContent=da?`And ${control.dataset.duck}: Enter løfter/slipper, piletaster flytter, Escape sætter ned`:`Duck ${control.dataset.duck}: Enter picks up/releases, arrow keys move, Escape puts down`;
    }
    button.textContent=off?(da?'▶ Start dammen':'▶ Animate pond'):(da?'Ⅱ Pause dammen':'Ⅱ Pause pond');
    button.setAttribute('aria-pressed',String(off));button.disabled=reduced;
    button.title=reduced?(da?'Reduceret bevægelse er slået til':'Reduced motion is enabled'):button.textContent;
  }
  globalThis.MediaHubDuckPond={sync};
  new MutationObserver(sync).observe(root,{attributes:true,attributeFilter:['data-look','data-reduced-motion','lang']});
  document.addEventListener('visibilitychange',sync);motion.addEventListener('change',sync);
  window.addEventListener('storage',event=>{if(event.key===storageKey){paused=event.newValue==='true';sync();}});
  sync();
})();
