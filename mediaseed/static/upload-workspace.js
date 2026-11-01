import {mountUploadPreview} from '/static/upload-preview.js?v=dce15b179561';
'use strict';
export default async function(root){
const $=selector=>root.querySelector(selector),form=$('#upload-prepare');
const locationPreference='mediaupload.location.v1';
let destinationLocations=[],mainDestinationPicker=null;
let trackerConfigured=false,planReady=false,releaseReady=false,episodeOverrides=new Map(),automaticChecksRunning=false;const automaticChecksAttempted=new Set();
let csrf='',folder='',files=[],busy=false,browseGeneration=0,latestJobs='',step=1,selectionKey='';
const editedUploadTitles=new Set();
let uploadDrafts=new Map(),uploadViews=[],activeUploadKey='',uploadDefaults=null,previewReviewMessage='';
const advancedKeys=['title','synopsis','source_credit','source_service','source_type','edition','release_format','release_type','screenshots','screenshot_count'];
let episodeEditing=false;
let descriptionTimer,descriptionRunning=false,descriptionPending=false,descriptionGeneration=0;
let furthestStep=1,descriptionTab='preview',hasIdentifiedUploads=false;
const editingStep=()=>step>=2&&step<=5;
let activeJob=/^#pakke-([a-f0-9]{24})$/.exec(location.hash)?.[1]||'';
function node(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;}
function message(text,error=false){for(const selector of ['#upload-message','#upload-form-feedback']){$(selector).textContent=text;$(selector).dataset.error=String(error);}}
async function api(path,body,method='GET',timeoutMs=0){
 const controller=new AbortController(),timer=timeoutMs?setTimeout(()=>controller.abort(),timeoutMs):null;
 try{
 const response=await fetch('/api/upload/'+path,{method,signal:controller.signal,headers:{'Content-Type':'application/json','X-MediaSeed-CSRF':csrf},...(body?{body:JSON.stringify(body)}:{})});
 let data;try{data=await response.json();}catch{throw Error('Forbindelsen blev afbrudt. Kontrollér dine uploadpakker før du prøver igen.');}
 if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'Kontrollér felterne og prøv igen.');return data;
 }catch(e){if(controller.signal.aborted)throw Error('Afsnitslisten tog for lang tid. Prøv igen med Opdatér beskrivelsen.');throw e;}
 finally{if(timer!==null)clearTimeout(timer);}
}
function chosenReleaseGroup(){const group=form.elements.release_owner.value==='other'?form.elements.original_group.value.trim():'MDTECH';return /^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$/.test(group)?group:'';}
function taggedDestinationName(name){const group=chosenReleaseGroup();if(!group)return name;const suffix=new RegExp('[.-](?:MDTECH|'+group+')$','i');return name.replace(suffix,'')+'.'+group;}
function originalReleaseGroup(value){
 const title=String(value).split('/').at(-1).replace(/\.(?:mkv|mp4|m4v|avi|ts|m2ts|mov)$/i,'');
 const dash=/(?:WEB-DL|WEB-Rip|source-[a-z0-9]+)$/i.test(title)?null:/-([A-Za-z0-9][A-Za-z0-9_-]{0,31})$/.exec(title);
 if(dash)return dash[1];
 const info=/\(([^()]*)\)$/.exec(title);if(!info||!/(?:\d{3,4}p|BluRay|WEB|[xh]26[45])/i.test(info[1]))return '';
 const tag=info[1].trim().split(/\s+/).at(-1);return /^[A-Za-z][A-Za-z0-9_-]{0,31}$/.test(tag)&&!/^(?:BluRay|WEB|WEB-DL|HEVC|AVC|[xh]26[45]|HDR|SDR|DV|AAC|EAC3|DTS|Atmos|10bit)$/i.test(tag)?tag:'';
}
function detectedReleaseGroup(){const groups=[...new Set(selectedVideos().map(originalReleaseGroup).filter(Boolean))];return groups.length===1?groups[0]:groups.length?'':originalReleaseGroup(folder.split('/').at(-1)||'');}
let previousReleaseOwner='self',ownMediaMode='release',ownIncludeNfo=true;
function ownershipControls(){
 const other=form.elements.release_owner.value==='other',input=form.elements.original_group;
 $('#upload-original-group-field').hidden=!other;input.required=other;
 if(other&&previousReleaseOwner!=='other'){ownMediaMode=form.elements.media_mode.value;ownIncludeNfo=form.elements.include_nfo.checked;if(!input.value)input.value=detectedReleaseGroup();form.elements.media_mode.value='copy';}
 else if(!other&&previousReleaseOwner==='other'){form.elements.media_mode.value=ownMediaMode;form.elements.include_nfo.checked=ownIncludeNfo;}
 form.elements.media_mode.querySelector('[value=release]').disabled=other;form.elements.media_mode.querySelector('[value=in_place]').disabled=other;
 $('#upload-release-owner-status').textContent=other?'Originale videofiler og gruppenavnet bevares. Vores egen NFO kan medtages med kredit til den oprindelige udgiver.':'Egne releases bruger MDTECH. Originalkilden krediteres ved bearbejdning.';
 previousReleaseOwner=other?'other':'self';mediaControls();
}
form.elements.release_owner.onchange=()=>{ownershipControls();saveUploadDraft();scheduleDescription();};
form.elements.original_group.oninput=()=>{saveUploadDraft();scheduleDescription();};
function folderSearch(container,list,labelText){
 const label=node('label',labelText),input=node('input'),count=node('p',undefined,'hint folder-search-count');input.type='search';input.autocomplete='off';input.placeholder='Søg i mapper på dette niveau';input.setAttribute('aria-label',labelText);label.className='folder-search';label.append(input);container.insertBefore(label,list);container.insertBefore(count,list);count.setAttribute('role','status');
 const key=value=>value.toLocaleLowerCase().replace(/[._\s-]+/g,' ').trim();
 function refresh(){const query=key(input.value),buttons=Array.from(list.querySelectorAll('button'));let shown=0;for(const button of buttons){button.hidden=!key(button.textContent).includes(query);if(!button.hidden)shown++;}count.textContent=buttons.length?(shown?shown+' af '+buttons.length+' mapper':'Ingen mapper matcher søgningen.'):'Ingen undermapper på dette niveau.';}
 input.oninput=refresh;return {refresh,reset:()=>{input.value='';refresh();}};
}
const sourceFolderSearch=folderSearch(root.querySelector('.folder-options'),$('#upload-folders'),'Søg kildemapper');
function destinationPicker(container,initial={},preparation=false){
 const label=node('label','Gem på mediedrevet'),select=node('select');select.setAttribute('aria-label','Destination på mediedrevet');label.append(select);
 for(const row of destinationLocations){const option=new Option(row.label,row.id);option.disabled=!row.available;select.append(option);}
 select.value=destinationLocations.some(r=>r.id===initial.location&&r.available)?initial.location:(destinationLocations.find(r=>r.available)?.id||'');
 let folder=initial.folder||'',ready=false,generation=0;
 const path=node('p',undefined,'hint'),status=node('p',undefined,'hint'),details=node('details'),summary=node('summary','Vælg undermappe (valgfrit)'),up=node('button','← En mappe op'),list=node('div',undefined,'destination-folders');up.type='button';details.append(summary,up,list);const folderFilter=folderSearch(details,list,'Søg destinationsmapper');container.append(label,path,details,status);
 const layoutLabel=node('label','Mappeopdeling'),layout=node('select');layout.setAttribute('aria-label','Mappeopdeling');
 for(const [value,label] of [['series_episodes','Fælles seriemappe → en mappe pr. episode'],['episode','En mappe pr. fil i den valgte destination'],['flat','Kun videofiler · ingen nye mapper'],['folder','Samlet pakke · egen mappe']])layout.append(new Option(label,value));
 layout.value='series_episodes';
 const seriesLabel=node('label','Fælles seriemappe · tomt bruger forslaget'),seriesName=node('input'),nameLabel=node('label','Mappenavn til denne fil · tomt bruger filnavnet'),folderName=node('input'),suggestion=node('p',undefined,'hint'),structure=node('p',undefined,'hint destination-layout-preview');
 for(const input of [seriesName,folderName])input.maxLength=200;
 seriesName.setAttribute('aria-label','Fælles seriemappe');folderName.setAttribute('aria-label','Mappenavn til seeding');seriesLabel.append(seriesName);nameLabel.append(folderName);layoutLabel.append(layout);
 if(preparation)container.append(layoutLabel,seriesLabel,nameLabel,suggestion,structure);
 let proposed={seriesName:'',episodeName:'',filename:''};
 const currentLayout=()=>['season','series'].includes(form.elements.kind.value)?'folder':layout.value==='series_episodes'&&form.elements.kind.value==='movie'?'episode':layout.value;
 const rawOptions=()=>preparation?{destination_layout:currentLayout(),destination_name:folderName.value,series_name:seriesName.value}:{};
 const options=()=>{const value=rawOptions();if(!preparation)return value;value.destination_name=value.destination_layout==='flat'?'':folderName.value.trim()?taggedDestinationName(folderName.value.trim()):value.destination_layout==='folder'?proposed.seriesName:'';value.series_name=seriesName.value.trim()?taggedDestinationName(seriesName.value.trim()):'';return value;};
 function display(){
  nfoControls();
  const mode=currentLayout(),shared=mode==='series_episodes';seriesLabel.hidden=!shared;nameLabel.hidden=mode==='flat';
  const packed=['season','series'].includes(form.elements.kind.value);layout.querySelector('[value=series_episodes]').disabled=form.elements.kind.value!=='episode';layout.querySelector('[value=episode]').disabled=packed;layout.querySelector('[value=flat]').disabled=packed;layout.querySelector('[value=folder]').hidden=!packed;
  folderName.placeholder=proposed.episodeName||'Afventer videoens færdige filnavn';seriesName.placeholder=proposed.seriesName||'Seriens navn.'+chosenReleaseGroup();
  const episode=folderName.value.trim()?taggedDestinationName(folderName.value.trim()):proposed.episodeName||'[videofilens navn]',series=seriesName.value.trim()?taggedDestinationName(seriesName.value.trim()):proposed.seriesName||'[seriens navn]';
  suggestion.textContent=mode==='flat'?'Videoerne lægges direkte i den valgte destination.':'Episodemappen foreslås som videofilens navn uden .mkv: '+(proposed.episodeName||'afventer videodata');
  structure.textContent=(shared?series+' / ':'')+(mode==='flat'?'':mode==='folder'?series+' / ':episode+' / ')+(proposed.filename||'[video].mkv');
 }
 const result={value:()=>({destination_location:select.value,destination_folder:folder,...options()}),valid:()=>ready,options,draftOptions:rawOptions,sharedSeriesName:()=>seriesName.value,
 setOptions:value=>{layout.value=['series_episodes','episode','flat','folder'].includes(value.destination_layout)?value.destination_layout:form.elements.kind.value==='episode'?'series_episodes':'episode';folderName.value=value.destination_name||'';seriesName.value=value.series_name||'';display();},
 suggest:value=>{proposed=value;display();}};
 layout.onchange=()=>{saveUploadDraft();for(const [key,draft] of uploadDrafts)uploadDrafts.set(key,{...draft,destination_layout:currentLayout()});display();scheduleDescription();};
 seriesName.oninput=()=>{saveUploadDraft();for(const [key,draft] of uploadDrafts)uploadDrafts.set(key,{...draft,series_name:seriesName.value});display();scheduleDescription();};folderName.oninput=()=>{saveUploadDraft();display();scheduleDescription();};
 async function load(){const current=++generation;ready=false;updateSubmit();list.replaceChildren();folderFilter.reset();const selected=destinationLocations.find(r=>r.id===select.value);path.textContent=(selected?.label||'Ingen tilgængelig destination')+(folder?' / '+folder:'');up.disabled=!folder;
  if(!selected?.available){status.textContent=destinationLocations.map(r=>r.label+': '+r.reason).join(' · ');return;}
  status.textContent='Kontrollerer destinationsmappen…';
  try{const data=await api('destinations/folders?'+new URLSearchParams({location:select.value,folder}));if(current!==generation)return;
   for(const name of data.folders){const button=node('button','▣ '+name);button.type='button';button.onclick=()=>{folder=folder?folder+'/'+name:name;void load();};list.append(button);}
   if(!data.folders.length)list.append(node('p','Ingen undermapper. Du kan bruge den valgte mappe.','hint'));
   folderFilter.refresh();status.textContent='MediaSeed-gemmesti: '+data.clientPath;ready=true;
  }catch(e){if(current===generation)status.textContent=e.message;}finally{updateSubmit();}
 }
 select.onchange=()=>{folder='';void load();};up.onclick=()=>{folder=folder.split('/').slice(0,-1).join('/');void load();};
 void load();return result;
}
function selectedVideos(){return Array.from($('#upload-files').querySelectorAll('input:checked'),e=>e.value);}
function suggestedTitle(file){return file.split('/').at(-1).replace(/\.[^.]+$/,'').replace(/\[(?:[a-z0-9-]+\.)+(?:to|org|net|com|io|tv)\]/gi,'').replace(/[\[\]]/g,'');}
function sourceCreditFor(file){const title=suggestedTitle(file),group=originalReleaseGroup(file)||originalReleaseGroup(folder.split('/').at(-1)||'');return 'Original release: '+title+'\n'+(group?'Original group: '+group:'Original group not identified');}
function showStep(value){
 if(editingStep())saveUploadDraft();
 step=value;root.classList.toggle('preparing',editingStep());$('#upload-apply-upload-settings').hidden=value!==4||descriptionTab!=='settings';if(value!==6)furthestStep=Math.max(furthestStep,value);
 for(const n of [1,2,3,4,5,6]){
  $('#upload-step-'+n).hidden=n!==value;const indicator=$('#upload-indicator-'+n);indicator.disabled=n!==value&&(n>furthestStep||n===6);
  if(n===value)indicator.setAttribute('aria-current','step');else indicator.removeAttribute('aria-current');
 }
 $('#upload-upload-choices').hidden=![3,4].includes(value)||!uploadViews.length;
 if(value===2){$('#upload-selected-summary').textContent=selectedVideos().length===1?suggestedTitle(selectedVideos()[0]):selectedVideos().length+' videoer valgt';}
 if(editingStep()&&selectedVideos().length&&!uploadViews.length)scheduleDescription();
 const prompts={2:'Find det rigtige indhold og kontrollér afsnittene.',3:'Vælg placeringen til seeding.',4:'Gennemse hver upload eller ret dens indstillinger.',5:'Vælg torrentmetode og start klargøringen.'};if(prompts[value])message(prompts[value]);
 if(value===5)renderFinalPreviews();
 if(value===5)$('#upload-review-summary').textContent=uploadViews.length+' uploads klargøres hver for sig med egen beskrivelse og NFO. Originalfilerne bevares.';
 updateSubmit();$('#upload-step-'+value+' h2').focus();window.scrollTo({top:0,behavior:'instant'});
}
for(const button of root.querySelectorAll('[data-step]'))button.onclick=()=>showStep(Number(button.dataset.step));
$('#upload-identify-next').onclick=()=>{try{parseTmdb(form.elements.tmdb.value);showStep(3);}catch(e){message(e.message,true);$('#upload-tmdb-reference').focus();}};

function filterFiles(){const query=$('#upload-file-search').value.trim().toLocaleLowerCase(),rows=Array.from($('#upload-files').querySelectorAll('.video-row'));for(const row of rows)row.hidden=!row.textContent.toLocaleLowerCase().includes(query);$('#upload-file-count').textContent=rows.filter(r=>!r.hidden).length+' videoer · '+selectedVideos().length+' valgt';}
function updateSubmit(){const serviceMissing=uploadViews.some(v=>v.result?.sourceService?.status==='needs_input'),disabled=busy||!selectedVideos().length;$('#upload-submit').disabled=disabled||serviceMissing||(form.elements.kind.value!=='movie'&&!planReady)||(['release','in_place'].includes(form.elements.media_mode.value)&&!releaseReady)||(!['source','in_place'].includes(form.elements.media_mode.value)&&destinationLocations.length>0&&!mainDestinationPicker?.valid());$('#upload-next').disabled=disabled;
 $('#upload-identify-next').disabled=disabled||!form.elements.tmdb.value.trim()||(form.elements.kind.value!=='movie'&&!planReady&&!hasIdentifiedUploads)||episodeEditing;
 $('#upload-identify-next').textContent=!planReady&&hasIdentifiedUploads?'Se kendte afsnit →':'Vælg placering →';
 $('#upload-location-next').disabled=disabled||(!['source','in_place'].includes(form.elements.media_mode.value)&&destinationLocations.length>0&&!mainDestinationPicker?.valid());
 $('#upload-description-next').disabled=disabled||serviceMissing||!planReady||(['release','in_place'].includes(form.elements.media_mode.value)&&!releaseReady)||episodeEditing;
 filterFiles();}
function inferSelection(){
 const selected=selectedVideos(),key=[$('#upload-location').value,folder,...selected].join('\n');if(key===selectionKey)return;selectionKey=key;furthestStep=1;
 for(const name of ['source_verified','subtitles_verified','duplicates_checked','complete'])form.elements[name].checked=false;
 form.elements.tmdb.value='';episodeOverrides.clear();episodeEditing=false;resetUploadViews();planReady=false;$('#upload-episode-plan').hidden=true;
 if(!selected.length)return;
 const titles=selected.map(suggestedTitle);form.elements.title.value=titles[0];
 form.elements.source_credit.value=selected.map(sourceCreditFor).join('\n\n').slice(0,2000);
 form.elements.kind.value=selected.length===1&&/S\d{2}E\d{2}/i.test(titles[0])?'episode':selected.length>1?'season':'movie';
 const title=titles[0];form.elements.source_type.value=/WEB[ .-]?DL/i.test(title)?'WEB-DL':/WEB[ .-]?Rip/i.test(title)?'WEBRip':/BluRay/i.test(title)?'BluRay':/HDTV/i.test(title)?'HDTV':/DVD/i.test(title)?'DVD':/(?:^|[ ._-])WEB(?:[ ._-]|$)/i.test(title)?'WEB':'Other';
 const query=title.replace(/[._]/g,' ').replace(/\bS\d{2}E\d{2}\b.*|\b(?:19|20)\d{2}\b.*|\b\d{3,4}p\b.*/i,'').trim();
 $('#upload-tmdb-search').href='https://www.themoviedb.org/search?'+new URLSearchParams({query});
}
function renderSubtitles(){
 const selected=selectedVideos();
 if(['source','in_place'].includes(form.elements.media_mode.value)){$('#upload-subtitles').replaceChildren();return;}
 const saved=new Map(Array.from($('#upload-subtitles').querySelectorAll('.subtitle-row'),r=>[r.dataset.file,{video:r.querySelector('.target').value,language:r.querySelector('.language').value}]));
 const subtitles=files.filter(f=>f.kind==='subtitle'&&(f.file.startsWith('@uploaded/')||selected.some(v=>f.file.startsWith(v.replace(/\.[^.]+$/,'')+'.'))));
 $('#upload-subtitles').replaceChildren();
 if(!subtitles.length){$('#upload-subtitles').append(node('p','Ingen ekstra undertekstfiler valgt.','hint'));return;}
 for(const sub of subtitles){
  const row=node('div',undefined,'subtitle-row');row.dataset.file=sub.file;const filename=sub.displayName||sub.file.split('/').at(-1);const heading=node('label',undefined,'subtitle-choice'),include=node('input');include.type='checkbox';const name=node('span',filename);name.title=filename;heading.append(include,name);row.append(heading);
  const fields=node('div',undefined,'two'),targetLabel=node('label','Brug til'),target=node('select');target.className='target';target.append(new Option('Udelad denne tekst',''));
  for(const video of selected)target.append(new Option(suggestedTitle(video),video));
  const matches=selected.filter(v=>(sub.displayName||sub.file).startsWith(v.split('/').at(-1).replace(/\.[^.]+$/,'')+'.')||sub.file.startsWith(v.replace(/\.[^.]+$/,'')+'.'));
  if(matches.length===1)target.value=matches[0];else if(selected.length===1&&sub.file.startsWith('@uploaded/'))target.value=selected[0];
  const languageLabel=node('label','Sprog'),language=node('select');language.className='language';language.append(new Option('Vælg sprog',''));
  for(const [value,label] of [['da','Dansk'],['sv','Svensk'],['no','Norsk'],['fi','Finsk'],['en','Engelsk']])language.append(new Option(label,value));language.value=sub.language;
  if(saved.has(sub.file)){const prior=saved.get(sub.file);if(!prior.video||selected.includes(prior.video))target.value=prior.video;language.value=prior.language;}
  targetLabel.append(target);targetLabel.hidden=selected.length===1;languageLabel.append(language);fields.append(targetLabel,languageLabel);const options=node('details'),summary=node('summary');include.checked=Boolean(target.value);function caption(){summary.textContent=!target.value?(include.checked?'Vælg video til teksten':'Udeladt'):language.value?language.selectedOptions[0].textContent+' · Ret':'Vælg sproget';}caption();options.open=Boolean(target.value&&!language.value);options.append(summary,fields);row.append(options);include.onchange=()=>{if(!include.checked)target.value='';else if(selected.length===1)target.value=selected[0];if(include.checked&&(!target.value||!language.value))options.open=true;caption();scheduleDescription();};language.onchange=()=>{caption();scheduleDescription();};target.onchange=()=>{include.checked=Boolean(target.value);caption();scheduleDescription();};$('#upload-subtitles').append(row);
 }
}
function renderFiles(){
 const chosen=new Set(selectedVideos()),hadFiles=$('#upload-files').querySelectorAll('input').length;
 const videos=files.filter(f=>f.kind==='video');$('#upload-files').replaceChildren();
 if(!videos.length)$('#upload-files').append(node('p','Ingen videoer i denne mappe. Vælg en anden mappe eller søg i undermapper.','hint'));
 for(const video of videos){
  const label=node('label',undefined,'check video-row'),check=node('input');check.type='checkbox';check.value=video.file;check.checked=hadFiles?chosen.has(video.file):videos.length===1;
  check.onchange=()=>{if(check.checked&&!$('#upload-allow-pack').checked)for(const other of $('#upload-files').querySelectorAll('input'))if(other!==check)other.checked=false;inferSelection();renderSubtitles();updateSubmit();};
  label.append(check,node('span',video.file+' · '+(video.size/1073741824).toFixed(2)+' GiB'));$('#upload-files').append(label);
 }
 inferSelection();renderSubtitles();updateSubmit();
}
async function browse(){
 const generation=++browseGeneration,location=$('#upload-location').value,selectedFolder=folder;
 $('#upload-path').textContent='/'+folder;$('#upload-up').disabled=!folder;$('#upload-folders').replaceChildren();sourceFolderSearch.reset();$('#upload-files').replaceChildren();$('#upload-subtitles').replaceChildren();files=[];updateSubmit();message('Finder videoer i mappen og undermapperne…');
 const [folders,media]=await Promise.all([api('storage/folders?'+new URLSearchParams({location,folder:selectedFolder})),api('upload-helper/files?'+new URLSearchParams({location,folder:selectedFolder,recursive:'true'}))]);
 if(generation!==browseGeneration)return;
 for(const name of folders.folders){const button=node('button','▣ '+name);button.type='button';button.onclick=()=>{folder=folder?folder+'/'+name:name;void browse().catch(e=>message(e.message,true));};$('#upload-folders').append(button);}
 if(!folders.folders.length)$('#upload-folders').append(node('p','Ingen undermapper.','hint'));
 sourceFolderSearch.refresh();files=media.files;renderFiles();message('Vælg din video.');
}
function parseTmdb(value){
 value=value.trim();if(/^\d+$/.test(value)&&Number(value)>0&&Number(value)<=2147483647)return Number(value);
 let url;try{url=new URL(value);}catch{throw Error('Indsæt linket til filmen eller serien på TMDB. Du kan også skrive dens tal-ID.');}
 const match=/^\/(movie|tv)\/(\d+)(?:[-/]|$)/.exec(url.pathname);
 if(url.protocol!=='https:'||!['www.themoviedb.org','themoviedb.org'].includes(url.hostname)||!match||Number(match[2])<1||Number(match[2])>2147483647)throw Error('Brug et link til en film eller serie på themoviedb.org.');
 if((form.elements.kind.value==='movie')!==(match[1]==='movie'))throw Error('TMDB-linket passer ikke til den valgte type. Vælg seriens side til et TV-afsnit og filmens side til en film.');
 return Number(match[2]);
}
function scheduleDescription(){
 hasIdentifiedUploads=false;
 for(const view of uploadViews){view.result=null;view.error='';}if(uploadViews.length)showUploadPreview();
 planReady=false;releaseReady=false;$('#upload-release-file-preview').textContent='';updateSubmit();descriptionGeneration++;clearTimeout(descriptionTimer);if(editingStep())descriptionTimer=setTimeout(refreshDescription,350);
}
function advancedValues(){return Object.fromEntries(advancedKeys.map(key=>[key,key==='screenshots'?form.elements[key].checked:key==='screenshot_count'?Number(form.elements[key].value):form.elements[key].value]));}
function resetUploadViews(){hasIdentifiedUploads=false;uploadDrafts.clear();editedUploadTitles.clear();uploadViews=[];activeUploadKey='';uploadDefaults=null;mainDestinationPicker?.setOptions({});$('#upload-upload-choices').hidden=true;form.elements.kind.disabled=false;}
function saveUploadDraft(){if(activeUploadKey)uploadDrafts.set(activeUploadKey,{...uploadDrafts.get(activeUploadKey),...advancedValues(),...(mainDestinationPicker?.draftOptions()||{})});}
function uploadKey(child){return child.videos.join('\n');}
function uploadOptions(){return Array.from(uploadDrafts,([file,value])=>{const option={file,...value};if(option.destination_name?.trim())option.destination_name=taggedDestinationName(option.destination_name.trim());if(option.series_name?.trim())option.series_name=taggedDestinationName(option.series_name.trim());if(['source','in_place'].includes(form.elements.media_mode.value)){option.destination_layout='folder';option.destination_name='';option.series_name='';}if(!editedUploadTitles.has(file))delete option.title;return option;}).filter(o=>selectedVideos().includes(o.file));}
function loadUploadDraft(applyFields=true){
 const draft=uploadDrafts.get(activeUploadKey);if(!draft)return;
 const view=uploadViews.find(v=>v.key===activeUploadKey);if(view)form.elements.kind.value=view.child.kind;
 if(applyFields){
  for(const key of advancedKeys)if(key==='screenshots')form.elements[key].checked=draft[key];else form.elements[key].value=draft[key];
  mainDestinationPicker?.setOptions(draft);
 }
 showUploadPreview();
}
function selectUploadViews(children){
 const previousKey=activeUploadKey;
 hasIdentifiedUploads=Boolean(children.length);
 uploadViews=children.map(child=>({key:uploadKey(child),child,result:null,error:''}));
 for(const view of uploadViews)if(!uploadDrafts.has(view.key))uploadDrafts.set(view.key,{...uploadDefaults,title:view.child.title,...Object.fromEntries(advancedKeys.filter(k=>k!=='title'&&view.child[k]!==undefined).map(k=>[k,view.child[k]])),destination_layout:['flat','episode','series_episodes'].includes(view.child.destination_layout)?view.child.destination_layout:view.child.kind==='episode'?'series_episodes':view.child.kind==='movie'?'episode':'folder',destination_name:view.child.destination_name||'',series_name:mainDestinationPicker?.sharedSeriesName()??view.child.series_name??''});
 if(uploadDefaults.source_credit===selectedVideos().map(sourceCreditFor).join('\n\n').slice(0,2000))for(const view of uploadViews)if(view.child.videos.length===1&&uploadDrafts.get(view.key).source_credit===uploadDefaults.source_credit)uploadDrafts.get(view.key).source_credit=sourceCreditFor(view.key);
 for(const view of uploadViews)if(!editedUploadTitles.has(view.key))uploadDrafts.get(view.key).title=view.child.title;
 if(!uploadViews.some(v=>v.key===activeUploadKey))activeUploadKey=uploadViews[0]?.key||'';
 const select=$('#upload-upload-preview-select');select.replaceChildren();
 for(const [index,view] of uploadViews.entries())select.append(new Option((index+1)+'. '+(view.child.videos.length===1?view.child.videos[0]:view.child.title),view.key));
 select.value=activeUploadKey;$('#upload-upload-choices').hidden=!uploadViews.length||![3,4].includes(step);
 $('#upload-upload-choice-status').textContent=uploadViews.length>1?uploadViews.length+' uploads · felterne gælder kun den valgte fil.':'Felterne gælder denne upload.';
 form.elements.kind.disabled=uploadViews.length>1;loadUploadDraft(previousKey!==activeUploadKey);
}
function renderFinalPreviews(){
 const host=$('#upload-final-preview');host.replaceChildren();
 if(!uploadViews.length){host.append(node('p','Venter på forhåndsvisningen…','hint'));return;}
 for(const view of uploadViews){
  const result=view.result,draft=uploadDrafts.get(view.key)||{},options={...uploadDefaults,...view.child,...draft};
  if(!result){host.append(node('p',view.error||'Opdaterer forhåndsvisningen…',view.error?'missing':'hint'));continue;}
  const packageHost=uploadViews.length>1?node('details',undefined,'upload-preview-package'):host;
  if(packageHost!==host){packageHost.open=view.key===activeUploadKey;packageHost.append(node('summary',result.releaseTitle||options.title||view.child.videos.join(', ')));host.append(packageHost);}
  mountUploadPreview(packageHost,{planned:true,title:result.releaseTitle||options.title,description:result.description,nfo:result.nfo,files:Object.values(result.outputFiles||{}) .length?Object.values(result.outputFiles):view.child.videos,videos:result.observedVideos,includeNfo:nfoIncluded(),screenshotCount:options.screenshots?Number(options.screenshot_count)||2:0,mode:form.elements.media_mode.selectedOptions[0]?.textContent});
 }
}
function clearDescriptionPreview(){const draft=uploadDrafts.get(activeUploadKey);if(draft?.description_override==null)$('#upload-description-preview').value='';}
function showUploadPreview(){
 const view=uploadViews.find(v=>v.key===activeUploadKey);if(!view)return;
 if(step===5)renderFinalPreviews();
 const result=view.result;form.elements.title.readOnly=['release','in_place'].includes(form.elements.media_mode.value);if(result?.releaseTitle)form.elements.title.value=result.releaseTitle;
 $('#upload-media-tags-status').textContent=result?.mediaTags?.message||'';
 const source=result?.sourceService;$('#upload-choose-source-service').hidden=source?.status!=='needs_input';form.elements.source_service.setAttribute('aria-invalid',String(source?.status==='needs_input'));$('#upload-source-service-status').textContent=source?.status==='needs_input'?source.message:source?.status==='ok'?'Downloadtjeneste: '+source.label+' · '+source.evidence.replace('declared','angivet af dig').replace('filename','filnavn'):form.elements.source_service.value==='none'?'Ingen downloadtjeneste. Der tilføjes ikke et tjenestetag.':'';
 const draft=uploadDrafts.get(view.key),manual=draft?.description_override!=null;
 const text=manual?draft.description_override:result?.automaticDescription??result?.description??'';
 if($('#upload-description-preview').value!==text)$('#upload-description-preview').value=text;
 $('#upload-automatic-description').disabled=!manual;$('#upload-nfo-preview').value=result?.nfo||'';
 $('#upload-naming-status').textContent=source?.status==='needs_input'?source.message:result?.namingError?result.namingError+' Ret indstillingen under Indstillinger. Beskrivelsen kan stadig redigeres.':'';
 const namingWarnings=(result?.namingChecks||[]).filter(check=>check.status!=='ok').map(check=>check.text);
 if(namingWarnings.length)$('#upload-naming-status').textContent+=' '+namingWarnings.join(' ');
 $('#upload-release-file-preview').textContent=result?.outputFiles?'Filen i torrenten: '+Object.values(result.outputFiles).join(' · '):'';
 const seriesTitle=view.child.kind!=='movie'?view.child.catalogue_title:'';
 const seriesSuggestion=seriesTitle?seriesTitle.normalize('NFKD').replace(/[\u0300-\u036f]/g,'').replace(/[^A-Za-z0-9]+/g,'.').replace(/^\.|\.$/g,'')+'.'+chosenReleaseGroup():'';
 const filename=Object.values(result?.outputFiles||{})[0]||(!['release','in_place'].includes(form.elements.media_mode.value)?view.child.videos[0]?.split('/').at(-1):'');
 mainDestinationPicker?.suggest({seriesName:result?.suggestedSeriesFolderName||seriesSuggestion,episodeName:result?.suggestedEpisodeFolderName||(filename?filename.replace(/\.[^.]+$/,''):''),filename:filename||''});
 const index=uploadViews.indexOf(view)+1;
 $('#upload-description-status').textContent=previewReviewMessage+(uploadViews.length>1?'Upload '+index+' af '+uploadViews.length+'. ':'')+(manual?'Din egen beskrivelse bruges til denne fil. ':'')+(view.error||(!result?'Laver beskrivelse og NFO for denne upload…':result.plotIncluded?'Engelsk handling er hentet til denne fil.':result.metadata?.message||'Handlingen er ikke hentet endnu.'));
}
function showDescriptionTab(value){descriptionTab=value;$('#upload-description-panel').hidden=value!=='preview';$('#upload-advanced').hidden=value!=='settings';$('#upload-preview-tab').setAttribute('aria-selected',String(value==='preview'));$('#upload-settings-tab').setAttribute('aria-selected',String(value==='settings'));$('#upload-preview-tab').tabIndex=value==='preview'?0:-1;$('#upload-settings-tab').tabIndex=value==='settings'?0:-1;$('#upload-apply-upload-settings').hidden=step!==4||value!=='settings';}
for(const tab of [$('#upload-preview-tab'),$('#upload-settings-tab')])tab.onkeydown=e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const target=e.key==='Home'?'preview':e.key==='End'?'settings':descriptionTab==='preview'?'settings':'preview';showDescriptionTab(target);$('#'+(target==='preview'?'preview-tab':'settings-tab')).focus();}};
$('#upload-preview-tab').onclick=()=>showDescriptionTab('preview');$('#upload-settings-tab').onclick=()=>showDescriptionTab('settings');
$('#upload-choose-source-service').onclick=()=>{showDescriptionTab('settings');form.elements.source_service.focus();};
$('#upload-upload-preview-select').onchange=()=>{saveUploadDraft();activeUploadKey=$('#upload-upload-preview-select').value;loadUploadDraft();};
$('#upload-apply-upload-settings').onclick=()=>{
 saveUploadDraft();const current=uploadDrafts.get(activeUploadKey);if(!current)return;
 for(const [key,draft] of uploadDrafts)uploadDrafts.set(key,{...draft,...Object.fromEntries(['source_type','source_service','edition','release_format','release_type','synopsis','screenshots','screenshot_count'].map(k=>[k,current[k]]))});
 scheduleDescription();
};
async function refreshDescription(){
 if(!editingStep()||!selectedVideos().length)return;
 if(episodeEditing){$('#upload-description-status').textContent='Tryk Brug sæson og afsnit for at gemme dit manuelle valg først.';return;}
 if(descriptionRunning){descriptionPending=true;return;}
 saveUploadDraft();if(!uploadDefaults)uploadDefaults=advancedValues();
 const generation=descriptionGeneration;let planning=false;descriptionRunning=true;$('#upload-refresh-description').disabled=true;$('#upload-description-status').textContent='Laver beskrivelser til de valgte uploads…';
 releaseReady=false;updateSubmit();
 try{
  const data=new FormData(form),body={...uploadDefaults};for(const key of ['profile','kind','release_owner','original_group'])body[key]=key==='original_group'?form.elements[key].value.trim():form.elements[key].value;
  if(activeUploadKey.includes('\n'))Object.assign(body,uploadDrafts.get(activeUploadKey));
  body.location=$('#upload-location').value;body.folder=folder;body.videos=selectedVideos();body.subtitles=[];body.tmdb=null;body.include_nfo=nfoIncluded();body.media_mode=data.get('media_mode');body.upload_options=uploadOptions();
  Object.assign(body,mainDestinationPicker?.options()||{});
  if(data.get('tmdb').trim())try{body.tmdb=parseTmdb(data.get('tmdb'));}catch{}
  for(const row of (form.elements.release_owner.value==='other'?[]:$('#upload-subtitles').querySelectorAll('.subtitle-row'))){const video=row.querySelector('.target').value,language=row.querySelector('.language').value;if(video&&language)body.subtitles.push({file:row.dataset.file,video,language});}
  body.episode_overrides=Array.from(episodeOverrides.values());
  let children=[body];previewReviewMessage='';
  if(body.kind!=='movie'){
   planning=true;planReady=false;updateSubmit();$('#upload-episode-plan').hidden=false;$('#upload-episode-plan-rows').replaceChildren();$('#upload-episode-plan-status').textContent='Identificerer afsnit og kontrollerer fuldstændighed…';
   if(!body.tmdb){$('#upload-episode-plan-status').textContent=$('#upload-description-status').textContent='Indsæt seriens TMDB-link for at identificere afsnittene.';clearDescriptionPreview();$('#upload-nfo-preview').value='';return;}
   const plan=await api('upload-helper/plan',body,'POST',45000);
   if(generation!==descriptionGeneration||!editingStep())return;
   renderEpisodePlan(plan,body.videos);planning=false;planReady=plan.status==='ready';updateSubmit();
   children=planReady?plan.uploads:plan.previewUploads;
   if(!children?.length){if(!uploadViews.length||!plan.entries?.length)selectUploadViews([]);else{hasIdentifiedUploads=false;for(const view of uploadViews){view.result=null;view.error=plan.message;}}clearDescriptionPreview();$('#upload-nfo-preview').value='';$('#upload-description-status').textContent=plan.message;return;}
   if(!planReady)previewReviewMessage=plan.message+' Klargøring er låst, indtil alle valgte filer er identificeret. ';
  }else{$('#upload-episode-plan').hidden=true;planReady=true;}
  selectUploadViews(children);
  for(const view of uploadViews){
   if(generation!==descriptionGeneration||!editingStep())return;
   const request={...body,...view.child,...uploadDrafts.get(view.key),upload_options:[]};
   request.subtitles=body.subtitles.filter(s=>request.videos.includes(s.video));
   try{view.result=await api('description/preview',request,'POST');}
   catch(e){view.error=e.message;if(['release','in_place'].includes(body.media_mode)&&request.source_type==='Other'&&view.key===activeUploadKey)showDescriptionTab('settings');}
   if(generation!==descriptionGeneration||!editingStep())return;
   const option=Array.from($('#upload-upload-preview-select').options).find(o=>o.value===view.key);if(option&&(view.error||view.result?.namingError||view.result?.sourceService?.status==='needs_input'))option.textContent+=' · kræver rettelse';
   showUploadPreview();
  }
  const failures=uploadViews.filter(v=>v.error||v.result?.namingError||v.result?.sourceService?.status==='needs_input').length;if(failures)$('#upload-upload-choice-status').textContent=failures+' af '+uploadViews.length+' uploads kræver rettelse. Vælg den markerede upload for at se fejlen.';
  releaseReady=!['release','in_place'].includes(body.media_mode)||uploadViews.every(v=>v.result?.releaseTitle&&!v.error);updateSubmit();
 }catch(e){if(generation===descriptionGeneration){clearDescriptionPreview();$('#upload-nfo-preview').value='';if(planning){planReady=false;updateSubmit();$('#upload-episode-plan-status').textContent=e.message+' Kontrollér TMDB-forbindelsen og vælg Opdatér beskrivelsen.';}$('#upload-description-status').textContent=planning?$('#upload-episode-plan-status').textContent:e.message;}}
 finally{descriptionRunning=false;$('#upload-refresh-description').disabled=false;updateSubmit();if(descriptionPending){descriptionPending=false;scheduleDescription();}}
}
function renderEpisodePlan(plan,selected){
 const container=$('#upload-episode-plan-rows');container.replaceChildren();$('#upload-episode-plan-status').textContent=plan.message;
 renderCatalogueCheck(plan.crossCheck,container);
 if(plan.seasonChecks?.length){
  const section=node('section',undefined,'episode-coverage');section.setAttribute('aria-label','Kontrol af valgte sæsoner');
  section.append(node('h4','Kontrol af valgte sæsoner'));
  for(const season of plan.seasonChecks)section.append(node('p','Sæson '+season.season+' · '+season.selected+' / '+season.total+' afsnit · '+(season.complete?'Komplet og færdigudsendt ('+season.source+').':season.reason),season.complete?'ok':'missing'));
  section.append(node('p','En fuld, færdigudsendt sæson kan uploades samlet. Manglende andre sæsoner og specials gør ikke sæsonen ufuldstændig. Flere sæsoner i samme torrent kræver hele den afsluttede serie.','hint'));
  container.append(section);
 }
 renderMissingEpisodes(plan,container);
 for(const file of selected){
  const entry=plan.entries?.find(e=>e.file===file),saved=episodeOverrides.get(file),row=node('details',undefined,'episode-row');row.open=!entry;
  row.append(node('summary',(entry?'S'+String(entry.season).padStart(2,'0')+'E'+String(entry.episode).padStart(2,'0')+' · ':'')+file,'episode-file'));
  const fields=node('div',undefined,'two'),seasonLabel=node('label','Sæson · 0 = special'),episodeLabel=node('label','Afsnit'),season=node('input'),episode=node('input');
  for(const input of [season,episode]){input.type='number';input.min=0;input.max=10000;}
  season.value=saved?.season??entry?.season??'';episode.value=saved?.episode??entry?.episode??'';
  seasonLabel.append(season);episodeLabel.append(episode);fields.append(seasonLabel,episodeLabel);row.append(fields);
  if(entry)row.append(node('p','S'+String(entry.season).padStart(2,'0')+'E'+String(entry.episode).padStart(2,'0')+' · '+entry.title+(entry.special?' · Special':'')+(entry.numberingSource?' · Nummerering: '+entry.numberingSource:''),'hint'));
  else{
   row.append(node('p',plan.unresolved?.find(e=>e.file===file)?.message||'Afsnittet er ikke identificeret endnu.','missing'));
   const remove=node('button','Tag denne fil ud af udvalget');remove.type='button';
   remove.onclick=()=>{
    for(const input of $('#upload-files').querySelectorAll('input'))if(input.value===file)input.checked=false;
    episodeOverrides.delete(file);episodeEditing=false;selectionKey=[$('#upload-location').value,folder,...selectedVideos()].join('\n');
    for(const name of ['source_verified','subtitles_verified','duplicates_checked','complete'])form.elements[name].checked=false;
    $('#upload-selected-summary').textContent=selectedVideos().length+' videoer valgt';renderSubtitles();scheduleDescription();
    if(!selectedVideos().length)showStep(1);
   };row.append(remove);
  }
  const titleLabel=node('label','Titel på denne upload'),title=node('input');title.maxLength=240;
  const suggested=plan.mode==='episodes'?plan.uploads?.find(u=>u.videos.includes(file))?.title:'';
  title.value=saved?.release_title||suggested||'';titleLabel.append(title);titleLabel.hidden=plan.mode!=='episodes'||['release','in_place'].includes(form.elements.media_mode.value);row.append(titleLabel);
  const manualLabel=node('label',undefined,'check'),manual=node('input');manual.type='checkbox';
  manual.checked=(saved?.numbering_source||entry?.numberingSource)==='Manual';manualLabel.append(manual,node('span','Brug manuel nummerering, hvis afsnittet mangler i kataloget'));row.append(manualLabel);
  const episodeTitleLabel=node('label','Afsnitstitel (manuel)'),episodeTitle=node('input');episodeTitle.maxLength=300;episodeTitle.value=saved?.episode_title||entry?.title||'';episodeTitleLabel.append(episodeTitle);episodeTitleLabel.hidden=!manual.checked;row.append(episodeTitleLabel);
  const apply=node('button','Brug sæson og afsnit'),feedback=node('p',undefined,'hint');apply.type='button';row.append(apply,feedback);
  function edited(){
   episodeEditing=true;planReady=false;releaseReady=false;descriptionGeneration++;clearTimeout(descriptionTimer);updateSubmit();
   feedback.textContent='Tryk Brug sæson og afsnit for at gemme dit valg.';
  }
  for(const input of [season,episode,title,episodeTitle])input.oninput=edited;
  manual.onchange=()=>{episodeTitleLabel.hidden=!manual.checked;edited();};
  apply.onclick=()=>{
   if(season.value===''||episode.value===''||!season.checkValidity()||!episode.checkValidity()){feedback.textContent='Udfyld både sæson og afsnit med hele tal.';return;}
   if(manual.checked&&!episodeTitle.value.trim()){feedback.textContent='Angiv også afsnitstitlen ved manuel nummerering.';episodeTitle.focus();return;}
   episodeEditing=false;episodeOverrides.set(file,{file,season:Number(season.value),episode:Number(episode.value),release_title:title.value.trim(),episode_title:episodeTitle.value.trim(),numbering_source:manual.checked?'Manual':'TMDB'});
   scheduleDescription();
  };container.append(row);
 }
}
function appendCataloguePanel(container,section,label){const details=node('details',undefined,'catalogue-panel');details.append(node('summary',label),section);container.append(details);}
function renderCatalogueCheck(check,container){
 if(!check)return;
 const section=node('section',undefined,'episode-coverage catalogue-check');section.append(node('h4','Krydstjek · TMDB og TheTVDB'));
 section.append(node('p',check.message,check.status==='ok'&&!check.conflicts?'hint':'missing'));
 if(check.status==='not_configured'){const link=node('a','Tilknyt TheTVDB for at aktivere krydstjek');link.href='#upload-tvdb-settings';link.onclick=()=>{$('#upload-tools-panel').open=true;$('#upload-tvdb-settings').open=true;};section.append(link);}
 if(check.status==='ok'){
  section.append(node('p','TMDB: '+check.tmdbCount+' afsnit · TheTVDB: '+check.tvdbCount+' afsnit.','hint'));
  if(check.conflicts)section.append(node('p','Modstridende nummerering kræver gennemgang. Berørte sæsoner kan ikke bekræftes som komplette.','missing'));
  if(check.datesUnconfirmed)section.append(node('p','Kilderne er uenige om en ukendt eller kommende udgivelsesdato. Kontrollér vurderingen af den berørte sæson.','missing'));
  const differences=node('ul',undefined,'catalogue-differences');
  for(const difference of check.differences||[]){const e=difference.tvdb||difference.tmdb,marker='S'+String(e.season).padStart(2,'0')+'E'+String(e.episode).padStart(2,'0');
   const labels={tvdb_only:'Kun i TheTVDB · medtaget i oversigten',tmdb_only:'Kun i TMDB',identity:'Uenig titel eller nummerering',date:'Forskellig udgivelsesdato'};
   const row=node('li',marker+' · '+e.title+' · '+labels[difference.type]);
   if(difference.type==='identity'||difference.type==='date'){const other=difference.tmdb;row.append(node('p','TMDB: S'+other.season+'E'+other.episode+' · '+other.title+' · '+(other.airDate||'ukendt dato')+' / TheTVDB: S'+e.season+'E'+e.episode+' · '+e.title+' · '+(e.airDate||'ukendt dato'),'hint'));}
   differences.append(row);
  }
  if(differences.childNodes.length)section.append(differences);else section.append(node('p','Katalogerne stemmer overens.','ok'));
  const attribution=node('a','Afsnitsdata fra TheTVDB');attribution.href=check.tvdb?'https://www.thetvdb.com/dereferrer/series/'+check.tvdb:'https://www.thetvdb.com/';attribution.target='_blank';attribution.rel='noopener noreferrer';section.append(attribution);
 }
 appendCataloguePanel(container,section,section.classList.contains('catalogue-check')?'Krydstjek · TMDB og TheTVDB':section.querySelector('h4')?.textContent||'Fuldstændighed');
}
function renderMissingEpisodes(plan,container){
 const missing=plan.missingSeries??plan.missing;
 if(!Array.isArray(missing)||(!plan.seasonCoverage?.length&&!missing.length))return;
 const section=node('section',undefined,'episode-coverage');section.setAttribute('aria-label','Fuldstændighed for hele serien');
 const manualCount=(plan.entries||[]).filter(e=>e.numberingSource==='Manual').length,count=(plan.entries||[]).length-manualCount,total=count+missing.length;
 section.append(node('h4',missing.length?'Mangler til hele serien · '+missing.length:'Alle katalogiserede afsnit er valgt'),node('p',count+' af '+total+' afsnit valgt, inklusive specials. Opgørelsen gælder dit udvalg her.','hint'));
 if(manualCount)section.append(node('p',manualCount+' manuelt angivne afsnit er valgt ud over kataloget. De bekræfter ikke fuldstændighed.','hint'));
 if(plan.unresolved?.length)section.append(node('p','Nogle filer er ikke identificeret endnu. Mangellisten kan ændre sig, når du retter dem.','missing'));
 if(plan.seasonCoverage?.length){const list=node('ul',undefined,'season-coverage');for(const group of plan.seasonCoverage)list.append(node('li',(group.season===0?'Specials':'Sæson '+group.season)+' · '+group.selected+' / '+group.total+' valgt',group.selected===group.total?'ok':'missing'));section.append(list);}
 if(plan.mode==='season'&&missing.length)section.append(node('p','Disse mangler ligger uden for den valgte sæson og blokerer ikke sæsonpakken.','ok'));
 if(!missing.length){section.append(node('p','En sæsonpakke kræver alle sæsonens afsnit og bekræftede udgivelsesdatoer. Kun en samlet seriepakke kræver, at hele serien er afsluttet. Se uploadfordelingen ovenfor.','hint'));appendCataloguePanel(container,section,section.classList.contains('catalogue-check')?'Krydstjek · TMDB og TheTVDB':section.querySelector('h4')?.textContent||'Fuldstændighed');return;}
 section.append(node('p','Find afsnittene: links åbner søgninger. Tilgængelighed og adgang afhænger af land og udbyder; et søgeresultat bekræfter ikke, at hele afsnittet kan ses.','hint'));
 const list=node('ul',undefined,'missing-episodes');
 for(const item of missing){
  const row=node('li'),marker='S'+String(item.season).padStart(2,'0')+'E'+String(item.episode).padStart(2,'0');
  row.append(node('strong',marker+' · '+item.title+(item.season===0?' · Special':'')));
  row.append(node('p',item.releaseStatus==='upcoming'?'Udgives i dag eller senere · '+item.airDate:item.releaseStatus==='unknown'?'Udgivelsesdato ukendt':item.airDate?'Udgivet · '+item.airDate:'Udgivelsesdato ukendt','hint'));
  const links=node('div',undefined,'episode-searches'),query=[plan.title,item.title].filter(Boolean).join(' ');
  for(const [label,url] of [['Søg på TheTVDB','https://www.thetvdb.com/search?'+new URLSearchParams({query:plan.title})],['Søg på BBC iPlayer','https://www.bbc.co.uk/iplayer/search?'+new URLSearchParams({q:query})],['Søg efter afsnittet','https://www.google.com/search?'+new URLSearchParams({q:'"'+plan.title+'" "'+item.title+'" watch'})]]){const link=node('a',label);link.href=url;link.target='_blank';link.rel='noopener noreferrer';links.append(link);}
  if(Number.isInteger(item.tvdbEpisode)&&item.tvdbEpisode>0){const link=node('a','Se afsnittet på TheTVDB');link.href='https://www.thetvdb.com/episodes/'+item.tvdbEpisode;link.target='_blank';link.rel='noopener noreferrer';links.append(link);}
  if(item.numberingSource!=='TheTVDB'&&Number.isInteger(plan.tmdb)&&plan.tmdb>0){const link=node('a','Se på TMDB');link.href='https://www.themoviedb.org/tv/'+plan.tmdb+'/season/'+item.season+'/episode/'+item.episode;link.target='_blank';link.rel='noopener noreferrer';links.append(link);}
  row.append(links);list.append(row);
 }
 section.append(list);appendCataloguePanel(container,section,section.classList.contains('catalogue-check')?'Krydstjek · TMDB og TheTVDB':section.querySelector('h4')?.textContent||'Fuldstændighed');
}
function trackerControls(){
 const local=form.elements.torrent_mode.value==='local',connected=trackerConfigured&&form.elements.profile.value==='nordicbytes';
 $('#upload-announce-field').hidden=!local||connected;form.elements.announce.required=local&&!connected;
 $('#upload-tracker-config').hidden=!local||form.elements.profile.value!=='nordicbytes';
 $('#upload-tracker-status').textContent=!local?'Trackeren opretter torrenten via nem upload. Ingen trackeradresse er nødvendig her.':connected?'NordicBytes er tilknyttet. Der laves én torrentfil pr. upload i fordelingen.':'Gem din passkey eller indsæt trackerens HTTPS announce-URL.';
}
async function trackerStatus(){const value=await api('tracker/settings');trackerConfigured=value.configured;$('#upload-tracker-passkey').placeholder=trackerConfigured?'••••••••••••••••':'';$('#upload-tracker-save-status').textContent=trackerConfigured?'Passkey er gemt. Lad feltet stå tomt for at beholde den.':'Ingen passkey er gemt endnu.';trackerControls();}
$('#upload-save-tracker').onclick=async()=>{try{await api('tracker/settings',{passkey:$('#upload-tracker-passkey').value.trim()},'PUT');$('#upload-tracker-passkey').value='';await trackerStatus();$('#upload-tracker-save-status').textContent='Passkey gemt.';}catch(e){message(e.message,true);}};
form.elements.torrent_mode.addEventListener('change',trackerControls);form.elements.profile.addEventListener('change',trackerControls);
async function metadataStatus(){
 const data=await api('metadata/settings');$('#upload-tmdb-credential').placeholder=data.configured?'••••••••••••••••':'';$('#upload-metadata-status').textContent=data.configured?'TMDB er tilknyttet. Den engelske handling hentes automatisk, når du indsætter linket.':data.message||'Tilknyt TMDB nederst på siden for at hente handlingen automatisk.';$('#upload-metadata-setup-status').textContent=data.configured?'TMDB er tilknyttet. Din nøgle vises ikke igen.':'';
 $('#upload-tvdb-apikey').placeholder=data.tvdbConfigured?'••••••••••••••••':'';$('#upload-tvdb-setup-status').textContent=data.tvdbConfigured?'TheTVDB er tilknyttet. Afsnitsplanen krydstjekker automatisk begge kataloger.':'Krydstjek afventer en TheTVDB API-nøgle og eventuel PIN. En almindelig konto er ikke tilstrækkelig.';
}
$('#upload-connect-tvdb').onclick=async()=>{const button=$('#upload-connect-tvdb');button.disabled=true;$('#upload-tvdb-setup-status').textContent='Kontrollerer TheTVDB-forbindelsen…';try{await api('metadata/tvdb/settings',{apikey:$('#upload-tvdb-apikey').value.trim(),pin:$('#upload-tvdb-pin').value.trim()},'PUT');$('#upload-tvdb-apikey').value='';$('#upload-tvdb-pin').value='';await metadataStatus();scheduleDescription();}catch(e){$('#upload-tvdb-setup-status').textContent=e.message;}finally{button.disabled=false;}};
$('#upload-connect-tmdb').onclick=async()=>{const button=$('#upload-connect-tmdb');button.disabled=true;$('#upload-metadata-setup-status').textContent='Kontrollerer TMDB-forbindelsen…';try{await api('metadata/settings',{credential:$('#upload-tmdb-credential').value.trim()},'PUT');$('#upload-tmdb-credential').value='';await metadataStatus();scheduleDescription();message('TMDB er tilknyttet. Handlingen hentes automatisk.');}catch(e){$('#upload-metadata-setup-status').textContent=e.message;}finally{button.disabled=false;}};
$('#upload-tmdb-reference').addEventListener('input',()=>{const match=/^https:\/\/(?:www\.)?themoviedb\.org\/(movie|tv)\//.exec(form.elements.tmdb.value.trim());if(match){if(match[1]==='tv'&&form.elements.kind.value==='movie')form.elements.kind.value=selectedVideos().length>1?'season':'episode';else if(match[1]==='movie')form.elements.kind.value='movie';}if($('#upload-message').dataset.error==='true'&&/^(TMDB-linket|Brug et link|Indsæt linket)/.test($('#upload-message').textContent))try{parseTmdb(form.elements.tmdb.value);message('TMDB-linket er klar. Handlingen hentes automatisk.');}catch{}});
$('#upload-copy-description-preview').onclick=()=>navigator.clipboard.writeText($('#upload-description-preview').value).then(()=>message('Beskrivelsen for den valgte upload er kopieret.')).catch(()=>message('Markér teksten og kopiér den manuelt.',true));
$('#upload-description-preview').oninput=()=>{
 if(!activeUploadKey)return;
 saveUploadDraft();uploadDrafts.get(activeUploadKey).description_override=$('#upload-description-preview').value;
 $('#upload-automatic-description').disabled=false;scheduleDescription();
};
$('#upload-automatic-description').onclick=()=>{
 if(!activeUploadKey)return;
 uploadDrafts.get(activeUploadKey).description_override=null;scheduleDescription();
};
$('#upload-refresh-description').onclick=()=>{descriptionGeneration++;void refreshDescription();};
function nfoIncluded(){return form.elements.include_nfo.checked&&(['source','in_place'].includes(form.elements.media_mode.value)||!['flat','series'].includes(mainDestinationPicker?.options().destination_layout));}
function nfoControls(){const direct=['source','in_place'].includes(form.elements.media_mode.value),unavailable=!direct&&['flat','series'].includes(root.querySelector('[aria-label="Mappeopdeling"]')?.value);form.elements.include_nfo.disabled=unavailable;$('#upload-nfo-inclusion-hint').textContent=direct?'NFO oprettes ved siden af den færdige konverterede film og medtages i torrenten. Eksisterende filer overskrives ikke.':'NFO får videoens navn med .nfo. Enkeltfiler uden mappe gemmer NFO som separat bilag.';}

function mediaControls(){nfoControls();$('#upload-destination-section').hidden=['source','in_place'].includes(form.elements.media_mode.value)||!destinationLocations.length;$('#upload-subtitle-section').hidden=form.elements.release_owner.value==='other'||['source','in_place'].includes(form.elements.media_mode.value);renderSubtitles();updateSubmit();}
form.elements.media_mode.addEventListener('change',()=>{if(form.elements.media_mode.value==='in_place')form.elements.include_nfo.checked=true;mediaControls();scheduleDescription();});
for(const name of ['title','kind','synopsis','source_credit','source_service','source_type','edition','release_format','release_type','screenshots','screenshot_count','include_nfo','profile','tmdb','torrent_mode'])form.elements[name].addEventListener('input',()=>{if(name==='title'&&activeUploadKey)editedUploadTitles.add(activeUploadKey);saveUploadDraft();scheduleDescription();});
const keywordDrafts=new Map();
function keywordSuggestions(job){
 const title=job.metadata?.title||(job.title||'').split(/[ ._-]S\d+|[ ._-]\d{3,4}[pi]/i)[0].replace(/[._]/g,' ').trim();
 const labels=[title,...(job.episodeMapping||[]).map(e=>e.episode_title)].filter(Boolean);
 const stop=new Set('about after again against also been before being between both could during each first from have into just last more most much only other over same some such than that their them then there these they this those through under very were what when where which while will with would years year series episode story tells told follows following featuring include includes including'.split(' '));
 const counts=new Map();for(const word of (job.metadata?.overview||'').toLowerCase().match(/[a-z]{4,}/g)||[])if(!stop.has(word))counts.set(word,(counts.get(word)||0)+1);
 const words=[...counts].sort((a,b)=>b[1]-a[1]).map(([word])=>word).filter(word=>!labels.some(label=>label.toLowerCase().split(/\W+/).includes(word)));
 return [...new Set([...labels.map(x=>x.toLowerCase()),...words])].slice(0,10).join(', ');
}
function keywordEditor(job){
 const box=node('section',undefined,'keyword-box'),label=node('label','Nøgleord · forslag til trackeren'),input=node('textarea');input.className='keyword-editor';input.rows=2;input.maxLength=2000;input.setAttribute('aria-label','Nøgleord til '+job.id);
 const key='mediaupload.keywords.v1.'+job.id;
 if(!keywordDrafts.has(job.id)){let saved=null;try{saved=localStorage.getItem(key);}catch{}keywordDrafts.set(job.id,saved??keywordSuggestions(job));}
 input.value=keywordDrafts.get(job.id);input.oninput=()=>{keywordDrafts.set(job.id,input.value);try{localStorage.setItem(key,input.value);}catch{}};label.append(input);
 const copy=node('button','Kopiér nøgleord');copy.type='button';copy.onclick=()=>navigator.clipboard.writeText(input.value).then(()=>message('Nøgleordene er kopieret.')).catch(()=>message('Markér nøgleordene og kopiér dem manuelt.',true));
 const reset=node('button','Brug forslag');reset.type='button';reset.onclick=()=>{input.value=keywordSuggestions(job);input.oninput();};
 box.append(label,node('p','Forslag fra titel og handling. Ret dem, så de passer til indholdet og trackerens felter. Rettelser gemmes i denne browser.','hint'),copy,reset);return box;
}
function jobAccordion(job,open){
 const wrapper=node('details',undefined,'job-accordion'),summary=node('summary');wrapper.open=open;
 const missing=(job.checks||[]).filter(c=>c.status!=='ok').length;
 const status=job.state==='done'?(missing?'Klar · '+missing+' kontroller mangler':'Klar'):job.state==='working'?(job.progress?.percent!=null?'Klargør · '+job.progress.percent+' %':'Klargør'):job.state==='queued'?'I kø':'Fejl';
 summary.append(node('span',job.title||'Uploadpakke','job-title'),node('span',status,'job-state'));wrapper.append(summary,renderJob(job));return wrapper;
}
function preparedPreviewModel(job){
 const base='/api/upload/upload-helper/'+job.id,asset=name=>base+'/asset/'+name;
 const filesContent=node('div'),nfoContent=node('div'),mediaInfoContent=node('div');
 if(job.releaseOwner==='other')filesContent.append(node('p','Original releasegruppe: '+job.originalGroup+' · originale videofilnavne er bevaret.','hint'));
  if(job.artifactExport){filesContent.append(node('h3','Uploadbilag gemt på mediedrevet'),node('p',job.artifactExport.displayFolder||job.artifactExport.folder,'manual-instructions'),node('p',job.artifactExport.files.join(' · '),'hint'));}
  if(['source','in_place'].includes(job.mediaMode)){
   filesContent.append(node('h3','Brug eksisterende medier'),node('p','Ingen mediekopi er oprettet. Bevar originalfilernes navne og mapper.','hint'));
   if(job.source?.clientSavePath){filesContent.append(node('p','MediaSeed-gemmesti: '+job.source.clientSavePath,'manual-instructions'));const copyPath=node('button','Kopiér MediaSeed-gemmesti');copyPath.type='button';copyPath.onclick=()=>navigator.clipboard.writeText(job.source.clientSavePath).then(()=>message('Gemmestien er kopieret.')).catch(()=>message('Markér stien og kopiér den manuelt.',true));filesContent.append(copyPath);}
   filesContent.append(node('p','Torrentens '+(job.source?.singleFile?'filnavn: ':'rodmappe: ')+(job.source?.torrentName||''),'hint'));
  }else if(job.destination){
   const destination=job.destination;filesContent.append(node('h3','Klar til MediaSeed'),node('p','Når du tilføjer torrenten i MediaSeed, vælg '+destination.label+(destination.folder?' / '+destination.folder:'')+(destination.layout==='series'?' / '+destination.folderName:'')+' som gemmemappe.','manual-instructions'),node('p',destination.clientSavePath,'hint'),node('p',destination.layout==='series'?'Afsnittene ligger i den fælles seriemappe. Hver torrent indeholder kun sin egen episodefil.':destination.layout==='flat'?'Videoen ligger direkte i denne mappe. Torrenten indeholder én fil.':'Vælg denne overordnede mappe. Torrenten indeholder selv releasemappen '+(destination.folderName||job.releaseName||job.title)+'.','hint'));
   const copyPath=node('button','Kopiér MediaSeed-gemmesti');copyPath.type='button';copyPath.onclick=()=>navigator.clipboard.writeText(destination.clientSavePath).then(()=>message('Gemmestien er kopieret.')).catch(()=>message('Markér stien og kopiér den manuelt.',true));filesContent.append(copyPath);
  }else if(destinationLocations.length){
   const box=node('section',undefined,'description-box');box.append(node('h3','Gør denne pakke klar til MediaSeed'),node('p','Vælg hvor mediekopien skal ligge. De eksisterende uploadbilag bevares.','hint'));
   const picker=destinationPicker(box),copy=node('button','Kopiér til mediedrevet');copy.type='button';copy.onclick=async()=>{if(!picker.valid()){message('Vælg en tilgængelig destinationsmappe.',true);return;}copy.disabled=true;try{const value=picker.value();await api('upload-helper/'+job.id+'/destination',{location:value.destination_location,folder:value.destination_folder},'POST');activeJob=job.id;window.history.replaceState(null,'','#pakke-'+activeJob);showStep(6);await refresh();message('Kopieringen er lagt i kø. Pakken er klar igen, når kopien er færdig.');}catch(e){message(e.message,true);}finally{copy.disabled=false;}};box.append(copy);filesContent.append(box);
  }
  if(job.mediaTags?.message)filesContent.append(node('p',job.mediaTags.message,'hint'));
  if(job.excludedFiles?.length){const omitted=node('details');omitted.append(node('summary','Udeladte filer · '+job.excludedFiles.length));const list=node('ul');for(const item of job.excludedFiles)list.append(node('li',item.file+' · '+item.reason));omitted.append(list);filesContent.append(omitted);}
  if(job.originalNfoFiles?.length){const nfos=node('details');nfos.append(node('summary','Original NFO · separat uploadbilag'));for(const [index,name] of job.originalNfoFiles.entries()){const link=node('a',name);link.href='/api/upload/upload-helper/'+job.id+'/asset/original-'+String(index+1).padStart(3,'0')+'.nfo';nfos.append(link);}nfoContent.append(nfos);}
  const notes=node('div');const checks=node('ul');for(const check of job.checks||[])checks.append(node('li',(check.status==='ok'?'✓ ':'○ ')+check.text,check.status==='ok'?'ok':'missing'));notes.append(checks,node('p','Mappe til seeding: '+job.seedingFolder,'hint'));for(const note of job.notes||[])notes.append(node('p',note,'hint'));
 for(const [index,video] of (job.videos||[]).entries()){
  const details=node('details');details.append(node('summary','MediaInfo · '+video.file));const text=node('textarea');text.readOnly=true;text.value=video.report||'';text.setAttribute('aria-label','MediaInfo for '+video.file);
  details.addEventListener('toggle',async()=>{if(details.open&&!text.value)try{text.value=(await api('upload-helper/'+job.id)).videos[index].report;}catch(e){message(e.message,true);}});details.append(text);mediaInfoContent.append(details);
 }
 return {title:job.title,description:job.description,files:(job.videos||[]).map(v=>v.file),videos:job.videos,includeNfo:Boolean(job.payloadNfoFile),payloadNfoFile:job.payloadNfoFile,screenshotCount:job.screenshotCount,screenshotUrl:i=>asset('screenshot-001-'+i+'.png'),
  loadNfo:async()=>{const response=await fetch(asset('captain-quack.nfo'));if(!response.ok)throw Error('NFO unavailable');return response.text();},
  loadMediaInfo:async()=>(await api('upload-helper/'+job.id+'/mediainfo')).text,
  torrentUrl:job.hasTorrent||(!job.torrentCreation&&job.checks?.some(c=>c.rule==='1.7'&&c.status==='ok'))?base+'/download':null,
  nfoUrl:asset('captain-quack.nfo'),descriptionUrl:asset(job.descriptionFile||'description.txt'),mediaInfoUrl:asset('mediainfo.txt'),
  descriptionContent:keywordEditor(job),filesContent,nfoContent,mediaInfoContent,checksContent:notes};
}
function renderJob(job){
 const row=node('article',undefined,'job');
 if(job.state==='done'){
  mountUploadPreview(row,preparedPreviewModel(job));
  window.MediaSeedUploaderReview?.mount(row,job);
 }
 if(job.state==='queued'){row.append(node('p','Venter i kø'+(job.queuePosition?' · nr. '+job.queuePosition:''),'job-status'),node('p','Starter automatisk. Du kan vælge Ny upload og sætte den næste i kø.','hint'));return row;}
 if(job.state==='working'){row.append(node('p',job.message||'Vi gør din pakke klar…','job-status'));if(job.progress){const progress=node('progress');progress.max=100;progress.value=job.progress.percent;progress.setAttribute('aria-label',job.progress.phase);row.append(progress,node('p',job.progress.percent+' % · '+(job.progress.completedBytes/1073741824).toFixed(2)+' af '+(job.progress.totalBytes/1073741824).toFixed(2)+' GiB','hint'));}row.append(node('p','Du kan vælge Ny upload og sætte den næste i kø.','hint'));return row;}
 if(job.state==='done'){
  const missing=(job.checks||[]).filter(c=>c.status!=='ok');
  if(job.destinationError)row.append(node('p',job.destinationError,'missing'));
  if(job.artifactExportError)row.append(node('p',job.artifactExportError,'missing'));
  if(missing.length){const details=node('details');details.open=true;details.append(node('summary',missing.length+' ting mangler før publicering','checks-summary'));const list=node('ul');for(const check of missing)list.append(node('li',check.text));details.append(list);row.append(details);}

 }else row.append(node('p',job.message||'Pakken kunne ikke laves. Prøv igen efter at have kontrolleret filerne.','missing'));
 const remove=node('button','Fjern kladde · behold medier','delete');remove.type='button';remove.onclick=async()=>{try{await api('upload-helper/'+job.id,null,'DELETE');keywordDrafts.delete(job.id);try{localStorage.removeItem('mediaupload.keywords.v1.'+job.id);}catch{}if(activeJob===job.id){activeJob=Array.from(root.querySelectorAll('[data-job-id]')).map(e=>e.dataset.jobId).find(id=>id!==job.id)||'';window.history.replaceState(null,'',activeJob?'#pakke-'+activeJob:location.pathname);if(!activeJob)showStep(1);}await refresh();}catch(e){message(e.message,true);}};row.append(remove);return row;
}
function renderJobs(jobs){
 const signature=activeJob+JSON.stringify(jobs);if(signature===latestJobs)return;latestJobs=signature;
 const targets=[$('#upload-jobs'),$('#upload-queue-jobs'),$('#upload-older-jobs')],existing=new Map(),desired=new Map(targets.map(target=>[target,[]])),restoreFocus=[];
 for(const target of targets)for(const child of Array.from(target.children)){if(child.dataset.jobId)existing.set(child.dataset.jobId,child);else child.remove();}
 const ordered=activeJob?[...jobs].sort((a,b)=>Number(b.id===activeJob)-Number(a.id===activeJob)||(a.created??0)-(b.created??0)):jobs;
 for(const job of ordered){
  const target=activeJob?$('#upload-jobs'):['working','queued'].includes(job.state)?$('#upload-queue-jobs'):$('#upload-older-jobs'),value=JSON.stringify(job);let row=existing.get(job.id);
  existing.delete(job.id);if(!row||row.dataset.jobSignature!==value){const open=row?row.open:job.id===activeJob,focused=row?.contains(document.activeElement)&&document.activeElement.classList.contains('keyword-editor')?document.activeElement:null,selection=focused?[focused.selectionStart,focused.selectionEnd]:null;if(row)row.remove();row=jobAccordion(job,open);row.dataset.jobId=job.id;row.dataset.jobSignature=value;if(selection)restoreFocus.push([row,selection]);}desired.get(target).push(row);
 }
 for(const row of existing.values())row.remove();
 for(const [target,rows] of desired)for(const [index,row] of rows.entries())if(target.children[index]!==row)target.insertBefore(row,target.children[index]||null);
 for(const [row,selection] of restoreFocus){const input=row.querySelector('.keyword-editor');if(input){input.focus({preventScroll:true});input.setSelectionRange(...selection);}}
 $('#upload-queue-panel').hidden=!$('#upload-queue-jobs').childNodes.length;
 if(activeJob&&!jobs.length)$('#upload-jobs').append(node('p','Denne uploadpakke findes ikke længere. Vælg Ny upload for at starte igen.','hint'));
 if(!$('#upload-older-jobs').childNodes.length)$('#upload-older-jobs').append(node('p','Ingen tidligere uploadpakker.','hint'));
}
function refreshAutomaticChecks(jobs){
 if(automaticChecksRunning)return;
 const job=jobs.find(job=>job.state==='done'&&(job.automaticChecksVersion||0)<3&&!automaticChecksAttempted.has(job.id)&&(job.mediaMode==='in_place'||job.checks?.some(check=>['1.4 MDTECH','1.9','2.2 / 3.6'].includes(check.rule)&&check.status==='missing')));
 if(!job)return;automaticChecksRunning=true;automaticChecksAttempted.add(job.id);
 void api('upload-helper/'+job.id+'/refresh-checks',{},'POST').then(()=>{latestJobs='';}).catch(e=>message('De automatiske kontroller kunne ikke opdateres: '+e.message,true)).finally(()=>{automaticChecksRunning=false;void refresh().catch(e=>message(e.message,true));});
}
async function refresh(){const data=await api('upload-helper');renderJobs(data.jobs);refreshAutomaticChecks(data.jobs);const missing=Object.entries(data.tools).filter(([,ready])=>!ready).map(([name])=>name);$('#upload-tool-warning').hidden=!missing.length;$('#upload-tool-warning').textContent='Forberedelse afventer medieværktøjer: '+missing.join(', ')+'. Installationen eller opdateringen skal fuldføres.';updateSubmit();}
$('#upload-next').onclick=()=>{if(!selectedVideos().length)return;showStep(2);message('Indsæt TMDB-linket og kontrollér afsnittene, før du går videre.');};
$('#upload-refresh-plan').onclick=()=>{descriptionGeneration++;void refreshDescription();};
$('#upload-new-upload').onclick=()=>{activeJob='';window.history.replaceState(null,'',location.pathname);const selectedLocation=$('#upload-location').value,destinationValue=mainDestinationPicker?.value();form.reset();previousReleaseOwner='self';ownershipControls();furthestStep=1;showDescriptionTab('preview');resetUploadViews();mediaControls();episodeOverrides.clear();episodeEditing=false;planReady=false;trackerControls();$('#upload-episode-plan').hidden=true;if(destinationValue)$('#upload-destination-picker select').value=destinationValue.destination_location;clearDescriptionPreview();$('#upload-nfo-preview').value='';descriptionGeneration++;$('#upload-location').value=selectedLocation;files=files.filter(file=>!file.file.startsWith('@uploaded/'));selectionKey='';for(const e of $('#upload-files').querySelectorAll('input'))e.checked=false;renderSubtitles();updateSubmit();showStep(1);message('Vælg en video for at starte en ny uploadpakke.');void refresh().catch(e=>message(e.message,true));};
$('#upload-allow-pack').onchange=()=>{if(!$('#upload-allow-pack').checked){const checked=Array.from($('#upload-files').querySelectorAll('input:checked'));for(const input of checked.slice(1))input.checked=false;inferSelection();renderSubtitles();updateSubmit();}};
$('#upload-file-search').oninput=filterFiles;
$('#upload-up').onclick=()=>{folder=folder.split('/').slice(0,-1).join('/');void browse().catch(e=>message(e.message,true));};
$('#upload-location').onchange=()=>{try{localStorage.setItem(locationPreference,$('#upload-location').value);}catch{}folder='';void browse().catch(e=>message(e.message,true));};
$('#upload-scan').onclick=async()=>{const button=$('#upload-scan'),generation=++browseGeneration,location=$('#upload-location').value,selectedFolder=folder;button.disabled=true;message('Finder videoer i undermapper…');try{const data=await api('upload-helper/files?'+new URLSearchParams({location,folder:selectedFolder,recursive:'true'}));if(generation!==browseGeneration)return;files=data.files;renderFiles();message('Vælg din video.');}catch(e){if(generation===browseGeneration)message(e.message,true);}finally{button.disabled=false;}};
$('#upload-subtitle-files').onchange=async event=>{
 const picker=event.target;picker.disabled=true;
 try{for(const file of picker.files){if(file.size>1048576)throw Error('En undertekst må højst være 1 MiB.');const response=await fetch('/api/upload/subtitles?'+new URLSearchParams({filename:file.name}),{method:'POST',headers:{'Content-Type':'application/octet-stream','X-MediaSeed-CSRF':csrf},body:file});const data=await response.json();if(!response.ok)throw Error(data.detail||'Underteksten kunne ikke importeres.');files.push(data);}renderSubtitles();scheduleDescription();message('Underteksterne er tilføjet. Kontrollér sproget for hver fil.');}catch(e){renderSubtitles();message(e.message,true);}finally{picker.disabled=false;picker.value='';}
};
$('#upload-rules').addEventListener('toggle',async()=>{if($('#upload-rules').open&&!$('#upload-rule-text').childNodes.length)try{const data=await api('rules/nordicbytes');$('#upload-rule-text').append(node('p',data.source,'hint'));for(const [key,label] of [['upload','Uploadregler'],['general','Generelle regler'],['faq','FAQ']])$('#upload-rule-text').append(node('h3',label),node('pre',data[key]));}catch(e){message(e.message,true);}});
$('#upload-refresh').onclick=()=>void refresh().catch(e=>message(e.message,true));
let invalidField=null;
form.addEventListener('invalid',event=>{
 event.preventDefault();if(invalidField)return;invalidField=event.target;
 setTimeout(()=>{const field=invalidField;invalidField=null;if(field.closest('#upload-advanced'))showDescriptionTab('settings');const panel=field.closest('.wizard-panel');if(panel)showStep(Number(panel.id.replace('step-','')));const details=field.closest('details');if(details)details.open=true;
 const labels={tmdb:'Indsæt filmens eller seriens TMDB-link.',title:'Titel på trackersiden skal udfyldes.',source_credit:'Originalkilde og kreditering skal udfyldes.'};
 message(labels[field.name]||'Udfyld det markerede felt.',true);field.setAttribute('aria-invalid','true');field.focus({preventScroll:true});field.scrollIntoView({behavior:'smooth',block:'center'});
 });
},true);
form.addEventListener('input',event=>event.target.removeAttribute('aria-invalid'));
form.onsubmit=async event=>{
 event.preventDefault();if(busy)return;saveUploadDraft();const data=new FormData(form),body={...uploadDefaults};
 try{body.tmdb=parseTmdb(data.get('tmdb'));}catch(e){message(e.message,true);$('#upload-tmdb-reference').focus();return;}
 for(const key of ['profile','kind','release_owner','original_group'])body[key]=key==='original_group'?form.elements[key].value.trim():form.elements[key].value;
 if(!uploadDefaults||activeUploadKey.includes('\n'))Object.assign(body,advancedValues());
 if(activeUploadKey.includes('\n'))body.description_override=uploadDrafts.get(activeUploadKey)?.description_override??null;
 body.upload_options=uploadOptions();
 body.include_nfo=nfoIncluded();body.media_mode=data.get('media_mode');body.torrent_mode=data.get('torrent_mode');body.upload_strategy=body.kind==='movie'?'single':'auto';body.episode_overrides=Array.from(episodeOverrides.values());if(body.torrent_mode==='local'&&data.get('announce').trim())body.announce=data.get('announce').trim();
 for(const key of ['source_verified','subtitles_verified','duplicates_checked','complete'])body[key]=data.has(key);
 if(body.screenshots===undefined)body.screenshots=data.has('screenshots');
 body.location=$('#upload-location').value;body.folder=folder;body.videos=selectedVideos();body.subtitles=[];
 if(!['source','in_place'].includes(body.media_mode)&&destinationLocations.length){if(!mainDestinationPicker?.valid()){message('Vælg en tilgængelig mappe på mediedrevet.',true);return;}Object.assign(body,mainDestinationPicker.value());}
 for(const row of (form.elements.release_owner.value==='other'?[]:$('#upload-subtitles').querySelectorAll('.subtitle-row'))){const video=row.querySelector('.target').value,language=row.querySelector('.language').value;if(!video)continue;if(!body.videos.includes(video)){message('Vælg den rigtige video til underteksten.',true);return;}if(!language){message('Vælg sproget for den ekstra undertekst.',true);row.querySelector('.language').focus();return;}body.subtitles.push({file:row.dataset.file,video,language});}
 busy=true;updateSubmit();message('Starter din uploadpakke…');
 try{const job=await api('upload-helper',body,'POST');activeJob=job.id;window.history.replaceState(null,'','#pakke-'+activeJob);showStep(6);form.elements.announce.value='';message((job.uploadCount>1?job.uploadCount+' separate uploads er lagt i kø. ':'Opgaven er lagt i kø. ')+'Du kan trykke Ny upload og tilføje den næste.');await refresh();busy=false;updateSubmit();}catch(e){busy=false;updateSubmit();message(e.message+' Kontrollér tidligere uploadpakker før du prøver igen.',true);void refresh().catch(()=>{});}
};
await (async()=>{try{csrf=(await fetch('/api/auth/me').then(r=>r.json())).csrf;const roots=await api('storage');for(const row of roots.locations)$('#upload-location').append(new Option(row.label,row.id));let preferred='';try{preferred=localStorage.getItem(locationPreference)||'';}catch{}if(roots.locations.some(r=>r.id===preferred))$('#upload-location').value=preferred;else if(roots.locations.some(r=>r.id==='tv'))$('#upload-location').value='tv';const destinations=await api('destinations');destinationLocations=destinations.locations;$('#upload-destination-section').hidden=!destinations.configured;if(destinations.configured)mainDestinationPicker=destinationPicker($('#upload-destination-picker'),{location:$('#upload-location').value},true);mediaControls();await Promise.all([browse(),refresh(),metadataStatus(),trackerStatus()]);
window.MediaSeedUpload.handoff=async hint=>{showStep(1);$('#upload-location').value=hint.location;folder=hint.folder;await browse();for(const input of $('#upload-files').querySelectorAll('input'))input.checked=hint.files.includes(input.value);inferSelection();form.elements.title.value=hint.title;form.elements.source_credit.value=hint.source_credit;form.elements.source_type.value=hint.source_type;if(hint.tmdb)form.elements.tmdb.value=String(hint.tmdb);form.elements.media_mode.value='source';form.elements.include_nfo.checked=false;mediaControls();if(trackerConfigured){form.elements.torrent_mode.value='local';trackerControls();}renderSubtitles();updateSubmit();};
if(activeJob)showStep(6);message(activeJob?'Din uploadpakke vises nedenfor.':'Klar. Vælg din video, og tryk Videre.');setInterval(()=>{if(!document.hidden&&!root.closest(".view").hidden)void refresh().catch(e=>message(e.message,true));},5000);}catch(e){message(e.message,true);throw e;}})();

}
