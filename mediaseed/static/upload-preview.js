/* Local, read-only previews. Tracker text is rendered with DOM nodes, never HTML. */
const element=(tag,text,className)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(className)e.className=className;return e;};
let sequence=0;
function safeUrl(value){try{const u=new URL(value);return ['https:','http:'].includes(u.protocol)?u.href:null;}catch{return null;}}
export function renderBBCode(text){
 const root=element('div',undefined,'upload-rendered-description'),stack=[{tag:'',node:root}];
 const tags={b:'strong',i:'em',u:'u',s:'s',quote:'blockquote',code:'pre',center:'div'};
 const tokens=String(text||'').split(/(\[\/?(?:b|i|u|s|quote|code|center|url)(?:=[^\]\r\n]*)?\])/gi);
 for(const token of tokens){
  const match=/^\[(\/)?(b|i|u|s|quote|code|center|url)(?:=([^\]\r\n]*))?\]$/i.exec(token),current=stack.at(-1);
  if(current.tag==='code'&&!(match?.[1]&&match[2].toLowerCase()==='code')){current.node.append(document.createTextNode(token));continue;}
  if(!match){current.node.append(document.createTextNode(token));continue;}
  const tag=match[2].toLowerCase();
  if(match[1]){if(current.tag===tag)stack.pop();else current.node.append(document.createTextNode(token));continue;}
  if(stack.length>30){current.node.append(document.createTextNode(token));continue;}
  let node;
  if(tag==='url'){
   const href=safeUrl(match[3]);if(!href){current.node.append(document.createTextNode(token));continue;}
   node=element('a');node.href=href;node.target='_blank';node.rel='noopener noreferrer';
  }else{node=element(tags[tag]);if(tag==='center')node.className='upload-preview-center';}
  current.node.append(node);stack.push({tag,node});
 }
 return root;
}
function subtitleText(tracks,languages){
 if(tracks==null)return (languages||[]).join(', ')||'Ikke oplyst';if(!tracks.length)return 'Ingen indlejrede tekster';
 const groups=new Map();for(const track of tracks){const lang=track.language||'ukendt sprog';if(!groups.has(lang))groups.set(lang,new Set());groups.get(lang).add((track.format||'ukendt format')+(track.forced?' forced':'')+(track.sdh?' SDH':''));}
 return [...groups].map(([lang,formats])=>lang+' ('+[...formats].join(', ')+')').join(' · ');
}
function rate(value){return Number.isFinite(value)&&value>0?(value>=1e6?(value/1e6).toFixed(2)+' Mbit/s':(value/1e3).toFixed(0)+' kbit/s'):'Ikke oplyst';}
function copyControl(panel,label,read){
 const actions=element('div',undefined,'upload-preview-actions'),button=element('button',label),status=element('p','','hint');button.type='button';status.setAttribute('role','status');
 button.onclick=async()=>{button.disabled=true;try{const value=await read();await navigator.clipboard.writeText(value);status.textContent='Kopieret.';}catch{status.textContent='Kunne ikke kopiere automatisk. Markér teksten og kopiér den manuelt.';}finally{button.disabled=false;}};
 actions.append(button,status);panel.append(actions);return actions;
}
function downloadLink(panel,label,url){if(!url)return;const link=element('a',label);link.href=url;panel.append(link);}
function lazyText(view,read,initial,kind){
 const text=element('pre',initial||kind+' er ikke indlæst endnu.',kind==='NFO'?'upload-preview-nfo':'upload-preview-code');text.tabIndex=0;text.setAttribute('aria-label',kind+' i uploadpakken');view.panel.append(text);
 let loaded=!read,loading=null;
 const status=element('p','','hint'),retry=element('button','Prøv at hente '+kind+' igen');retry.type='button';retry.hidden=true;view.panel.append(status,retry);
 const load=()=>{
  if(loaded)return Promise.resolve(text.textContent);if(loading)return loading;
  retry.hidden=true;status.textContent='Henter '+kind+'…';loading=Promise.resolve().then(read).then(value=>{text.textContent=value;loaded=true;status.textContent='Den færdige '+kind+' er indlæst.';return value;}).catch(error=>{status.textContent=kind+' kunne ikke hentes. Prøv igen.';retry.hidden=false;throw error;}).finally(()=>{loading=null;});return loading;
 };
 const select=view.button.onclick;view.button.onclick=()=>{select();void load().catch(()=>{});};retry.onclick=()=>void load().catch(()=>{});
 copyControl(view.panel,'Kopiér '+kind,load);return text;
}
export function mountUploadPreview(host,model){
 const id='upload-preview-'+(++sequence),section=element('section',undefined,'upload-package-preview');
 section.append(element('h3',model.planned?'Forhåndsvisning før klargøring':'Færdig uploadpakke · forhåndsvisning'),element('p',model.title||'Uploadpakke','upload-preview-title'));
 section.append(element('p',model.planned?'Se det planlagte indhold. Screenshots og endelige bilag dannes under klargøringen og kan gennemgås før publicering.':'Beskrivelsen, NFO’en og billederne her kommer fra den klargjorte pakke.','hint'));
 const nav=element('div',undefined,'upload-preview-tabs');nav.setAttribute('role','tablist');nav.setAttribute('aria-label','Forhåndsvisning af '+(model.title||'uploadpakke'));section.append(nav);
 const panels=[],buttons=[];
 function tab(label,key){
  const button=element('button',label);button.type='button';button.id=id+'-'+key+'-tab';button.setAttribute('role','tab');button.setAttribute('aria-controls',id+'-'+key);
  const panel=element('div',undefined,'upload-preview-panel');panel.id=id+'-'+key;panel.setAttribute('role','tabpanel');panel.setAttribute('aria-labelledby',button.id);panel.tabIndex=0;panel.hidden=true;
  const index=buttons.length;buttons.push(button);panels.push(panel);nav.append(button);section.append(panel);
  button.onclick=()=>{buttons.forEach((b,i)=>{b.setAttribute('aria-selected',String(i===index));b.tabIndex=i===index?0:-1;panels[i].hidden=i!==index;});};
  button.onkeydown=e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const next=e.key==='Home'?0:e.key==='End'?buttons.length-1:(index+(e.key==='ArrowRight'?1:-1)+buttons.length)%buttons.length;buttons[next].click();buttons[next].focus();}};
  return {button,panel};
 }
 const description=tab('Beskrivelse','description');description.panel.append(renderBBCode(model.description||'Beskrivelsen er ikke klar endnu.'));
 copyControl(description.panel,'Kopiér beskrivelse',()=>model.description||'');
 downloadLink(description.panel,'Hent beskrivelse (.txt)',model.descriptionUrl);
 if(model.descriptionContent)description.panel.append(model.descriptionContent);
 const raw=element('details');raw.append(element('summary','Vis BBCode'));const code=element('pre',model.description||'','upload-preview-code');raw.append(code);description.panel.append(raw);
 const nfo=tab('NFO','nfo');
 nfo.panel.append(element('p',model.includeNfo?'NFO’en medtages også i torrenten.':'NFO’en er et separat uploadbilag.','hint'));
 const nfoText=lazyText(nfo,model.loadNfo,model.nfo,'NFO');nfoText.classList.add('upload-preview-nfo');
 downloadLink(nfo.panel,'Hent NFO (.nfo)',model.nfoUrl);
 if(model.nfoContent)nfo.panel.append(model.nfoContent);
 const files=tab('Filer og oplysninger','files');if(model.mode)files.panel.append(element('p',model.mode,'hint'));
 const list=element('ul',undefined,'upload-preview-files');for(const name of model.files||[])list.append(element('li',name));if(model.includeNfo)list.append(element('li',model.payloadNfoFile||'NFO med samme navn som videoen'));files.panel.append(list);
 for(const video of model.videos||[]){
  const facts=video.comparison||{},info=element('dl',undefined,'upload-preview-facts');
  const entries=[['Fil',video.file],['Størrelse',video.sizeBytes?((video.sizeBytes/1024**3).toFixed(2)+' GiB'):null],['Videobitrate',rate(facts.videoBitrate)],['Samlet bitrate',rate(facts.totalBitrate)],['HDR',facts.hdr?.join(', ')],['Undertekster',subtitleText(facts.subtitles,video.languages)]];
  for(const audio of facts.audio||[])entries.push(['Lyd', [audio.codec,audio.channels?audio.channels+' kanaler':null,audio.atmos?'Atmos':null,audio.language,rate(audio.bitrate)].filter(Boolean).join(' · ')]);
  for(const [label,value] of entries){if(value){info.append(element('dt',label),element('dd',String(value)));}}files.panel.append(info);
 }
 if(model.filesContent)files.panel.append(model.filesContent);
 if(model.loadMediaInfo){const media=tab('MediaInfo','mediainfo');lazyText(media,model.loadMediaInfo,'','MediaInfo');downloadLink(media.panel,'Hent samlet MediaInfo (.txt)',model.mediaInfoUrl);if(model.mediaInfoContent)media.panel.append(model.mediaInfoContent);}
 const pictures=tab('Screenshots','screenshots');
 if(model.planned){pictures.panel.append(element('p',model.screenshotCount?'Der laves '+model.screenshotCount+' screenshots under klargøringen. De vises i den færdige pakke før publicering.':'Screenshots er fravalgt.','hint'));}
 else if(model.screenshotCount){const grid=element('div',undefined,'upload-preview-images');for(let i=1;i<=Math.min(5,model.screenshotCount);i++){const figure=element('figure'),img=element('img');img.src=model.screenshotUrl(i);img.alt='Screenshot '+i;img.loading='lazy';img.onerror=()=>{figure.replaceChildren(element('p','Screenshot '+i+' kunne ikke hentes.','hint'));};const caption=element('figcaption','Screenshot '+i+' · '),download=element('a','Hent billede');download.href=img.src;download.download='screenshot-'+i+'.png';caption.append(download);figure.append(img,caption);grid.append(figure);}pictures.panel.append(grid);}
 else pictures.panel.append(element('p','Ingen screenshots i denne pakke.','hint'));
 if(model.checksContent){const checks=tab('Kontroller','checks');checks.panel.append(model.checksContent);}
 if(model.torrentUrl)downloadLink(section,'Hent torrentfil (.torrent)',model.torrentUrl);
 buttons[0].click();host.append(section);return section;
}
