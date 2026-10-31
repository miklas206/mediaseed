/* Shared settings form for individual and batch conversions. */
(() => {
 const defaults={dolby_vision:false,preset:'P7',audio:'all',audio_languages:['dan','eng'],subtitles:'all',subtitle_languages:['dan','eng'],chapters:true,attachments:true,external_subtitles:true};
 function mount(parent,saved={}){
  const value={...defaults,...saved},set=element('fieldset',undefined,'conversion-options');set.append(element('legend','Indhold og kvalitet'));
  const controls={};
  function check(key,title){const label=element('label',undefined,'conversion-option-check'),input=element('input');input.type='checkbox';input.checked=!!value[key];input.dataset.option=key;label.append(input,element('span',title));set.append(label);controls[key]=input;}
  function select(key,title,choices){const label=element('label',title),input=element('select');input.dataset.option=key;for(const [v,t] of choices)input.append(new Option(t,v));input.value=value[key];label.append(input);set.append(label);controls[key]=input;}
  function languages(key,title){const label=element('label',title),input=element('input');input.value=value[key].join(', ');input.dataset.option=key;input.maxLength=70;input.pattern='[a-z]{3}(, *[a-z]{3})*';input.required=true;label.append(input);set.append(label);controls[key]=input;}
  check('dolby_vision','Bevar Dolby Vision (profil 7 bliver til 8.1)');
  set.append(element('p','Uden Dolby Vision bevares HDR10-grundlaget, hvis originalen har det. HDR bliver ikke konverteret til SDR.','muted'));
  select('preset','Encoderprofil',[['P7','Høj komprimeringskvalitet · P7'],['P5','Balanceret · P5'],['P4','Hurtigere · P4']]);
  set.append(element('p','En hurtigere profil kan give lavere billedkvalitet ved samme bitrate. Opløsning og billedhastighed bevares.','muted'));
  select('audio','Lydspor',[['all','Bevar alle lydspor'],['languages','Kun valgte sprog'],['none','Ingen lyd']]);
  languages('audio_languages','Lydsprog · fx dan, eng');
  select('subtitles','Indbyggede undertekster',[['all','Bevar alle undertekster'],['languages','Kun valgte sprog'],['none','Ingen undertekster']]);
  languages('subtitle_languages','Undertekstsprog · fx dan, eng');
  check('external_subtitles','Medtag eksterne undertekster, der matcher sprogvalget');
  check('chapters','Bevar kapitler');
  check('attachments','Bevar vedhæftninger, billeder og NFO');
  set.append(element('p','Lyd kopieres i original kvalitet. Sprogvalg kræver sprogmærkede spor. Vedhæftninger kan indeholde skrifttyper til undertekster.','muted'));
  function sync(){for(const [mode,key] of [['audio','audio_languages'],['subtitles','subtitle_languages']]){controls[key].disabled=controls[mode].value!=='languages';controls[key].parentElement.hidden=controls[key].disabled;}controls.external_subtitles.disabled=controls.subtitles.value==='none';}
  controls.audio.onchange=sync;controls.subtitles.onchange=sync;sync();parent.append(set);
  return {value:()=>Object.fromEntries(Object.entries(controls).map(([key,input])=>[key,input.type==='checkbox'?input.checked:key.endsWith('_languages')?(input.disabled?defaults[key]:input.value.split(',').map(v=>v.trim())):input.value]))};
 }
 function summary(options){const value={...defaults,dolby_vision:true,...options};return (value.dolby_vision?'DV bevares':'Uden Dolby Vision')+' · '+value.preset+' · Lyd: '+({all:'alle',languages:value.audio_languages.join('/'),none:'ingen'}[value.audio])+' · Undertekster: '+({all:'alle',languages:value.subtitle_languages.join('/'),none:'ingen'}[value.subtitles]);}
 window.MediaSeedConversionOptions={mount,summary};
})();
