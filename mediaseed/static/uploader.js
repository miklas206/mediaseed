/* Native upload workspace: one page, one login and MediaSeed's own backend. */
(() => {
 const section=document.getElementById('uploader');if(!section)return;
 const status=document.getElementById('uploader-state');
 let initialized=false,loading=null;
 window.MediaSeedUpload={handoff:null};
 async function open(){
  if(section.hidden)return;
  if(typeof appRole!=='undefined'&&appRole!=='administrator'){status.textContent='Upload kræver administratoradgang.';return;}
  if(initialized)return;
  if(loading)return loading;
  loading=(async()=>{try{
   status.textContent='Åbner upload…';
   const response=await fetch('/static/upload-workspace.html');if(!response.ok)throw Error('Uploadformularen kunne ikke hentes. Genindlæs MediaSeed.');
   const root=document.getElementById('uploader-workspace');root.innerHTML=await response.text();
   const {default:initialize}=await import('/static/upload-workspace.js?v=09d1e010be48');await initialize(root);
   initialized=true;status.textContent='Gennemgå oplysningerne og dubletkontrollen før publicering.';
  }catch(e){status.textContent=e.message;}finally{loading=null;}})();
  return loading;
 }
 new MutationObserver(()=>void open()).observe(section,{attributes:true,attributeFilter:['hidden']});
 window.addEventListener('uploader-open',async event=>{
  document.querySelector('[data-view=uploader]')?.click();await open();
  if(!event.detail?.conversion||!initialized)return;
  try{const hint=await api('uploader/conversions/'+event.detail.conversion);await window.MediaSeedUpload.handoff(hint);await window.MediaSeedUploaderReview.conversion(hint,event.detail.conversion);}
  catch(e){status.textContent=e.message;}
 });
})();
