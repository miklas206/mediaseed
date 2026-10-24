/* MediaSeed workspace. Data stays on the existing authenticated APIs. */
function seedPoster(row, large = false) {
  const holder = element('span', undefined, 'seed-poster' + (large ? ' large' : ''));
  holder.setAttribute('aria-hidden', 'true'); holder.textContent = '↓';
  if (row.cover && /^\/api\/covers\/[a-f0-9]{40,64}$/.test(row.cover)) {
    const image = document.createElement('img'); image.src = row.cover; image.alt = ''; image.loading = 'lazy';
    image.onerror = () => image.remove(); holder.append(image);
  }
  return holder;
}
function seedBadges(row) {
  const wrap = element('span', undefined, 'seed-badges'), benefits = row.tracker_benefits;
  if (benefits) {
    for (const [visible, text, tone] of [[benefits.freeleech > 0, `${benefits.freeleech}% Freeleech`, 'free'],
      [benefits.doubleUpload, t('Double upload'), 'double'], [benefits.featured, 'Featured', ''], [benefits.internal, 'Internal', '']]) {
      if (visible) wrap.append(element('span', text, 'seed-badge ' + tone));
    }
  }
  if (row.green_active) wrap.append(element('span', 'Green · 0.5×', 'seed-badge green'));
  return wrap;
}
function seedSource(row) {
  if (row.category === 'MediaSeed-Ratio') return t('Build ratio');
  return String(row.category || '').startsWith('MediaSeed-RSS-') ? t('RSS') : t('Manual torrent');
}
function seedEta(value) {
  const seconds = Number(value); if (!Number.isFinite(seconds) || seconds < 0 || seconds >= 8640000) return t('Waiting for estimate');
  return seconds < 60 ? t('Less than a minute') : seconds < 3600 ? `${Math.ceil(seconds / 60)} min` : `${Math.floor(seconds / 3600)} h ${Math.ceil(seconds % 3600 / 60)} min`;
}
function seedCreditRatio(row) {
  // Cached credit is for display only. Backend goal checks keep rejecting stale reads.
  const value = row.credit_observation_stale ? row.last_credited_ratio : (row.credited_ratio ?? row.ratio);
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : undefined;
}
function seedCleanupGoal(policy) {
  const goals = {
    time: t('{0} hours of active seeding', {0: policy.hours}),
    ratio: t('credited upload ratio {0}', {0: policy.ratio}),
    both: t('{0} hours of active seeding AND credited ratio {1}', {0: policy.hours, 1: policy.ratio}),
    either: t('{0} hours of active seeding OR credited ratio {1}', {0: policy.hours, 1: policy.ratio})
  };
  return goals[policy.mode] || '';
}

function seedCleanupProgress(row) {
  const policy = row.cleanup || {mode: 'never'}, ratio = seedCreditRatio(row);
  const ratioGoal = ['ratio', 'both', 'either'].includes(policy.mode) && Number(policy.ratio) >= 1 ? Number(policy.ratio) : undefined;
  const hoursGoal = ['time', 'both', 'either'].includes(policy.mode) && Number(policy.hours) > 0 ? Number(policy.hours) : undefined;
  const hours = Math.max(0, Number(row.seeding_time) || 0) / 3600;
  const fractions = [];
  if (ratioGoal) fractions.push(Number.isFinite(ratio) ? ratio / ratioGoal : 0);
  if (hoursGoal) fractions.push(hours / hoursGoal);
  const fraction = fractions.length ? Math.max(0, Math.min(1, policy.mode === 'both' ? Math.min(...fractions) : Math.max(...fractions))) : undefined;
  return {ratio, ratioGoal, hours, hoursGoal, fraction};
}
function seedSeeding(row) {
  const goal = seedCleanupProgress(row);
  const ratio = Number.isFinite(goal.ratio) ? trackerNumber(goal.ratio) : t('Unknown');
  return `${t('Ratio')} ${ratio}${goal.ratioGoal ? ' / ' + trackerNumber(goal.ratioGoal) : ''}`;
}
function seedProgressTone(row) {
  if (/error|missing/i.test(row.state)) return 'error';
  if (/stopped|paused/i.test(row.state)) return 'paused';
  if (/checking|moving/i.test(row.state)) return 'checking';
  if (/queued|stalled|meta/i.test(row.state)) return 'waiting';
  return Number(row.progress) >= 1 ? 'seeding' : 'downloading';
}
function seedPeerCount(value) {
  return Number.isSafeInteger(value) && value >= 0 ? trackerNumber(value) : '?';
}
function seedSwarmView(row) {
  const view = element('details', undefined, 'seed-swarm');
  const summary = element('summary', undefined, 'seed-peer-summary');
  summary.title = t('Connected peers. Open for swarm and tracker API details.');
  summary.setAttribute('aria-label', `${t('Connected peers')}: ${t('Seeders')} ${seedPeerCount(row.num_seeds)}, ${t('Leechers')} ${seedPeerCount(row.num_leechs)}`);
  for (const [name, value, tone] of [['Seeders', row.num_seeds, 'seeders'], ['Leechers', row.num_leechs, 'leechers']]) {
    const part = element('span', t(name) + ' ', tone);
    part.append(element('strong', seedPeerCount(value))); summary.append(part);
  }
  const icon = element('span', 'i', 'seed-peer-info'); icon.setAttribute('aria-hidden', 'true'); summary.append(icon);
  const panel = element('div', undefined, 'seed-peer-popover');
  panel.append(element('p', t('The visible counts are connected peers. Swarm totals are estimates from tracker/DHT announcements and may lag behind current connections. Tracker API counts are a separate observation.'), 'seed-peer-explanation'));
  function counts(label, seeders, leechers, className = '') {
    const line = element('small', undefined, 'seed-peer-line ' + className);
    line.append(element('span', label, 'seed-peer-source'));
    for (const [name, value, tone] of [['Seeders', seeders, 'seeders'], ['Leechers', leechers, 'leechers']]) {
      const part = element('span', t(name) + ' ', tone);
      part.append(element('strong', seedPeerCount(value))); line.append(part);
    }
    return line;
  }
  panel.append(counts(t('Connected peers'), row.num_seeds, row.num_leechs), counts(t('Swarm'), row.num_complete, row.num_incomplete));
  const api = row.swarm;
  if (api) {
    const current = api.status === 'ok' && Number.isFinite(api.checkedAt) && Date.now() / 1000 - api.checkedAt >= -5 && Date.now() / 1000 - api.checkedAt <= 180;
    const line = counts(t('Tracker API'), current ? api.seeders : undefined, current ? api.leechers : undefined, 'seed-peer-api');
    if (!current) line.append(element('span', t(api.status === 'pending' ? 'Waiting for API data' : 'API data unavailable or stale'), 'seed-peer-note'));
    const updated = api.checkedAt ? t('Updated {0}', {0: new Date(api.checkedAt * 1000).toLocaleString(locale())}) : '';
    line.title = api.tracker + (updated ? ' · ' + updated : '');
    if (updated) line.append(element('span', updated, 'seed-peer-note'));
    panel.append(line);
  }
  view.append(summary, panel);
  return view;
}
function seedTorrent(row, index) {
  const item = element('article', undefined, 'torrent seed-rich-row'); item.dataset.state = state(row); item.dataset.hash = row.hash;
  const identity = element('div', undefined, 'seed-identity');
  const heading = command(row.name, () => showDetails(row)); heading.className = 'torrent-name'; heading.title = row.name;
  const info = element('small', `${trackerBytes(row.size)} · ${row.tracker_name || row.tracker_benefits?.tracker || t('Tracker unknown')} · ${seedSource(row)}`, 'seed-subline');
  identity.append(heading, info, seedBadges(row));
  const uploaded=row.account_credited_uploaded??row.credited_uploaded;identity.append(element('small',t('Estimated credited upload')+': '+(typeof uploaded==='number'?'≈ '+trackerBytes(uploaded):t('Unknown')),'seed-upload-credit'));
  const sortKey = document.querySelector('#sort').value;
  const hints = {
    added_on: ['Added', row.added_on > 0 ? new Date(row.added_on * 1000).toLocaleString(locale()) : '—'],
    completion_on: ['Completed', row.completion_on > 0 ? new Date(row.completion_on * 1000).toLocaleString(locale()) : '—'],
    uploaded: ['Uploaded', trackerBytes(row.uploaded)],
    num_seeds: ['Seeders', row.num_seeds ?? '—'],
    ratio: ['Ratio', trackerNumber(seedCreditRatio(row))],
  };
  if (hints[sortKey]) identity.append(element('small', `${t(hints[sortKey][0])}: ${hints[sortKey][1]}`, 'seed-subline'));
  const transfer = element('div', undefined, 'seed-progress-cell');
  const complete = Number(row.progress) >= 1, goal = seedCleanupProgress(row);
  const progress = element('progress'); progress.max = 1;
  progress.value = complete ? (goal.fraction ?? 0) : Math.max(0, Math.min(1, Number(row.progress) || 0));
  progress.dataset.tone = seedProgressTone(row);
  progress.setAttribute('aria-label', t(complete ? 'Seeding progress' : 'Download progress'));
  const percent = complete && goal.fraction === undefined ? t('No cleanup goal') : `${Math.round(progress.value * 100)}%`;
  progress.setAttribute('aria-valuetext', complete ? `${seedSeeding(row)} · ${percent}` : percent);
  if (row.credit_observation_stale) transfer.title = t('Showing the last verified ratio while waiting for a fresh measurement.');
  const label = element('strong', complete ? seedSeeding(row) : `${percent} · ${seedEta(row.eta)}`);
  transfer.append(label, progress);
  if (complete) {
    const hours = trackerNumber(goal.hours) + (goal.hoursGoal ? ' / ' + trackerNumber(goal.hoursGoal) : '');
    transfer.append(element('small', `${t('Active seeding')} ${hours} h`));
    transfer.append(element('small', goal.fraction === undefined ? percent : `${t('Cleanup goal')} · ${percent}${['both', 'either'].includes(row.cleanup?.mode) ? ' · ' + t(row.cleanup.mode === 'both' ? 'Both goals required' : 'Either goal required') : ''}`, 'seed-goal-caption'));
  } else transfer.append(element('small', `${trackerBytes(row.completed ?? Number(row.size || 0) * progress.value)} / ${trackerBytes(row.size)}`));
  const activity = element('div', undefined, 'seed-activity');
  activity.append(element('span', torrentStatus(row.state), 'torrent-state'), element('small', `↓ ${bytes(row.dlspeed || 0)}/s · ↑ ${bytes(row.upspeed || 0)}/s`));
  activity.append(seedSwarmView(row));
  const actions = element('div', undefined, 'actions');
  if (appRole !== 'viewer') { const stopped = state(row) === 'stopped'; const toggle = command('', () => torrentAction(row, stopped ? 'start' : 'stop')); toggle.className = 'torrent-toggle'; toggle.innerHTML = stopped ? '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" aria-hidden="true"><path d="m8 5 11 7-11 7Z"/></svg>' : '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" aria-hidden="true"><rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/></svg>'; toggle.title = stopped ? t('Start') : t('Stop'); toggle.setAttribute('aria-label', toggle.title + ': ' + row.name); toggle.disabled = stopped && !downloadsEnabled; actions.append(toggle); }
  const menu = element('details', undefined, 'torrent-menu'), trigger = element('summary', '⋯'); trigger.setAttribute('aria-label', t('Actions'));
  const commands = element('div', undefined, 'torrent-menu-content'); commands.append(command(t('Details'), () => showDetails(row)));
  if (appRole !== 'viewer') commands.append(command(t('Check files'), () => chooseRecheck(row)), command(t('Remove'), () => chooseRemoval(row)));
  menu.append(trigger, commands); actions.append(menu);
  item.append(seedPoster(row), identity, transfer, activity, actions); return item;
}
document.addEventListener('click', event => {
  for (const view of document.querySelectorAll('.seed-swarm[open]')) if (!view.contains(event.target)) view.open = false;
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') for (const view of document.querySelectorAll('.seed-swarm[open]')) view.open = false;
});
const seedDetailCache = new Map();
let seedDetailRequest = 0;
function cachedDetail(hash, section, force=false) {
  const key=csrf+':'+hash+':'+section;
  if(force)seedDetailCache.delete(key);
  const saved=seedDetailCache.get(key);
  if(saved && (saved.pending || Date.now()-saved.at<15000))return saved.promise;
  const entry={pending:true,at:0};
  entry.promise=api(`torrents/${hash}/details?section=${section}`).then(data=>{entry.pending=false;entry.at=Date.now();return data;},error=>{if(seedDetailCache.get(key)===entry)seedDetailCache.delete(key);throw error;});
  seedDetailCache.set(key,entry);while(seedDetailCache.size>60)seedDetailCache.delete(seedDetailCache.keys().next().value);
  return entry.promise;
}
async function richDetails(row) {
  const request=++seedDetailRequest;selectedHash=row.hash;
  document.querySelector('#detail-name').textContent=row.name;document.querySelector('#detail-message').textContent='';
  document.querySelector('#policy-form input').value=Number(row.ratio_limit)>=1?row.ratio_limit:1;
  document.querySelector('#policy-form').hidden=true;
  const target=document.querySelector('#detail-data'),hero=element('div',undefined,'seed-detail-hero'),summary=element('div');
  summary.append(element('span',torrentStatus(row.state),'seed-badge'),seedBadges(row),element('p',`${trackerBytes(row.size)} · ${seedSource(row)}`,'muted'));hero.append(seedPoster(row,true),summary);
  const controls=element('div',undefined,'actions'),notice=element('small',t('Fetching details…'),'muted'),reload=command(t('Refresh'),()=>load(true));reload.type='button';controls.append(notice,reload);
  const tabs=element('div',undefined,'seed-detail-tabs'),content=element('div',undefined,'seed-detail-content');tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label',t('Torrent details'));content.id='seed-detail-tabpanel';content.setAttribute('role','tabpanel');
  target.replaceChildren(hero,controls,tabs,content);if(!dialog.open)dialog.showModal();
  let details={properties:{total_downloaded:row.downloaded,total_uploaded:row.uploaded,addition_date:row.added_on},files:[],trackers:[],peers:[],credit:{}},selected=0,overviewLoaded=false,loadCycle=0;
  const sectionFor=index=>index===2?'files':index===3?'connections':'overview';
  function paint(){
    content.replaceChildren();const credited={...row,...details.credit,cleanup:details.cleanup || row.cleanup};
    if(selected===0){const list=element('dl',undefined,'seed-detail-facts');
      const amount=credited.account_credited_uploaded??credited.credited_uploaded;
      for(const [label,value] of [['Downloaded',trackerBytes(details.properties.total_downloaded)],['Uploaded',trackerBytes(details.properties.total_uploaded)],['Estimated credited upload',typeof amount==='number'?'≈ '+trackerBytes(amount):t('Unknown')],['Source',seedSource(row)],['Added',details.properties.addition_date?new Date(details.properties.addition_date*1000).toLocaleString(locale()):'—'],['Download folder',details.downloadFolder?`${t(details.downloadFolder.label)} · ${details.downloadFolder.path}`:overviewLoaded?t('Download folder is outside approved storage or unavailable'):t('Fetching details…')]])list.append(element('dt',t(label)),element('dd',value));content.append(list);
    } else if(selected===1){content.append(trackerCreditView(credited));if(overviewLoaded)renderCleanupDetails(details.cleanup,row,content);}
    else if(selected===2){if(appRole!=='viewer')content.append(command(t('Check files'),()=>chooseRecheck(row)));content.append(element('p',t('File progress is the last client observation. Check files to verify existing data and detect missing or damaged pieces.'),'muted'),detailTable(t('Files'),[['name',t('Name')],['displaySize',t('Size')],['displayProgress',t('Progress')]],details.files.map(file=>({...file,displaySize:trackerBytes(file.size),displayProgress:typeof file.progress==='number'?`${Math.round(file.progress*100)}%`:'—'}))));}
    else if(selected===3)content.append(detailTable(t('Trackers'),[['host','Tracker'],['status',t('Status')],['num_peers','Peers']],details.trackers),detailTable('Peers',[['ip','IP'],['client',t('Client')],['progress',t('Progress')]],details.peers));
    else {content.append(element('p',torrentStatus(row.state)),element('p',t('VPN availability is checked before starting. Historical controller events are available in MediaHub Logs.'),'muted'));if(overviewLoaded)renderCleanupDetails(details.cleanup,row,content);}
    [...tabs.children].forEach((button,i)=>{button.setAttribute('aria-selected',String(i===selected));button.tabIndex=i===selected?0:-1;});content.setAttribute('aria-labelledby','seed-detail-tab-'+selected);
    document.querySelector('#policy-form').hidden=selected!==1||!overviewLoaded||appRole==='viewer'||(details.cleanup?.mode&&details.cleanup.mode!=='never');
  }
  async function load(force=false){const section=sectionFor(selected),cycle=++loadCycle;reload.disabled=true;notice.textContent=t('Fetching details…');
    try{const data=await cachedDetail(row.hash,section,force);if(request!==seedDetailRequest||cycle!==loadCycle||!dialog.open)return;details={...details,...data};if(section==='overview')overviewLoaded=true;if(section===sectionFor(selected)){notice.textContent=t('Updated {0}',{0:new Date(seedDetailCache.get(csrf+':'+row.hash+':'+section)?.at||Date.now()).toLocaleTimeString(locale())});paint();}}
    catch(error){if(request===seedDetailRequest&&cycle===loadCycle&&dialog.open)notice.textContent=t(error.message);}
    finally{if(cycle===loadCycle)reload.disabled=false;}
  }
  for(const [index,name] of ['Overview','Seeding','Files','Connections','Activity'].entries()){const button=command(t(name),()=>{selected=index;paint();void load();});button.id='seed-detail-tab-'+index;button.setAttribute('role','tab');button.setAttribute('aria-controls',content.id);button.onkeydown=event=>{if(!['ArrowLeft','ArrowRight'].includes(event.key))return;event.preventDefault();selected=(index+(event.key==='ArrowRight'?1:4))%5;paint();void load();tabs.children[selected].focus();};tabs.append(button);}
  paint();await load();
}
function richTrackerSummary(rows) {
  const target = document.querySelector('#seed-accounts'); if (!target) return;
  target.replaceChildren();
  for (const row of rows) { const card = element('div', undefined, 'seed-account'); card.append(element('strong', row.name));
    card.append(element('span', row.stats ? `${t('Ratio')} ${trackerNumber(row.stats.ratio)} · ${t('Buffer')} ${trackerBytes(row.stats.buffer)} · ${t('Warnings')} ${trackerNumber(row.stats.warnings)}` : t('Account data unavailable')));
    if (row.checkedAt) card.append(element('small', new Date(row.checkedAt * 1000).toLocaleTimeString(locale()), 'muted')); target.append(card); }
}
async function richPreview() {
  const target=document.querySelector('#seed-add-preview'),generation=++richPreview.request;
  target.replaceChildren(element('strong',t('Account ratio after adding')),element('p',t('Reading torrent information…'),'muted'));
  try {
    const fileMode=addForm.elements.namedItem('mode').value==='file',files=torrentFiles.filter(item=>item.state!=='added').map(item=>item.file);
    if(fileMode&&!files.length)throw new Error(t('Choose a torrent file up to 4 MiB'));
    const results=[];
    const sources=fileMode?files:[document.querySelector('#add-magnet').value];
    for(let offset=0;offset<sources.length;offset+=4){const group=await Promise.all(sources.slice(offset,offset+4).map(async source=>{
      const response=await fetch('/api/torrent-preview',{method:'POST',headers:{'Content-Type':fileMode?'application/x-bittorrent':'application/json','X-MediaSeed-CSRF':csrf},body:fileMode?source:JSON.stringify({magnet:source})});const data=await response.json();if(!response.ok)throw new Error(t('Torrent information unavailable'));return data;
    }));if(generation!==richPreview.request||!addDialog.open)return;results.push(...group);}
    const accounts=new Map();for(const result of results)for(const estimate of result.estimates){const key=estimate.accountId||estimate.name;const saved=accounts.get(key)||{...estimate,charged:0,count:0};saved.charged+=estimate.charged??0;saved.count++;accounts.set(key,saved);}
    target.replaceChildren(element('strong',t('Account ratio after adding')));
    for(const estimate of accounts.values()){
      const known=estimate.count===results.length&&results.every(result=>typeof result.size==='number'),denominator=estimate.downloaded+estimate.charged;
      const projected=known&&typeof estimate.downloaded==='number'?(denominator?estimate.uploaded/denominator:null):results.length===1?results[0].estimates.find(e=>e.name===estimate.name)?.projected:undefined;
      const card=element('article',undefined,'ratio-estimate-card'),numbers=element('div',undefined,'ratio-estimate-numbers');card.append(element('span',estimate.name));
      const format=value=>value===null?'∞':typeof value==='number'?Number(value).toLocaleString(locale(),{minimumFractionDigits:2,maximumFractionDigits:4}):t('Unknown');
      numbers.append(element('span',format(estimate.current)),element('span','→'),element('strong',format(projected)));card.append(numbers,element('small',t('Current ratio → Estimated ratio'),'muted'));card.append(element('p',t('Counts as tracker download: {0}',{0:trackerBytes(estimate.charged)}),'muted'));if(typeof projected==='number'&&projected<=.5)card.classList.add('seed-ratio-warning');target.append(card);
    }
    for(const result of results){if(result.benefits?.status==='matched')target.append(seedBadges({tracker_benefits:result.benefits}));}
    if(results.some(result=>result.metadataPending)){target.append(element('p',t('Tracker benefits are being checked in the background.'),'muted'));if((richPreview.retries||0)<8){richPreview.retries=(richPreview.retries||0)+1;setTimeout(()=>{if(generation===richPreview.request&&addDialog.open)void richPreview();},3000);}}else richPreview.retries=0;
    if(!accounts.size)target.append(element('p',t('Ratio estimate needs a known torrent size and fresh tracker account data.'),'muted'));
    const total=results.every(r=>typeof r.size==='number')?results.reduce((sum,r)=>sum+r.size,0):null;
    target.append(element('p',`${sources.length} · ${t('Size')}: ${total===null?t('Unknown'):trackerBytes(total)}`,'muted'),element('small',t('Estimate excludes future uploads and other downloads. Unknown freeleech counts as a normal download.'),'muted'));
  }catch(error){if(generation===richPreview.request)target.replaceChildren(element('strong',t('Account ratio after adding')),element('p',t(error.message),'muted'));}
}
richPreview.request=0;
async function richFeedHistory(id,name,page=1){
  const request=++richFeedHistory.request;
  let popup=document.querySelector('#feed-history-dialog');if(!popup){popup=element('dialog');popup.id='feed-history-dialog';popup.setAttribute('aria-labelledby','feed-history-title');document.body.append(popup);}
  const heading=element('div',undefined,'heading'),title=element('h2',name);title.id='feed-history-title';const close=command(t('Close'),()=>popup.close());close.type='button';heading.append(title,close);const box=element('div');popup.replaceChildren(heading,box);box.append(element('p',t('Loading…'),'muted'));if(!popup.open)popup.showModal();
  try{const result=await api(`feeds/${id}/history?page=${page}&pageSize=25`);if(request!==richFeedHistory.request||!popup.open)return;box.replaceChildren();
    const sum=result.upload;box.append(element('p',t('Estimated credited upload')+': '+(typeof sum?.creditedUploadedBytes==='number'?'≈ '+trackerBytes(sum.creditedUploadedBytes):t('Unknown')),'feed-upload-total'));
    if(sum?.unknownDownloads)box.append(element('p',t('{0} downloads without measurements',{0:sum.unknownDownloads}),'muted'));
    if(!result.entries.length)box.append(element('p',t('No downloaded torrents in this feed yet.'),'muted'));
    for(const row of result.entries){const line=element('article',undefined,'seed-history-row');line.append(element('strong',row.title||t('RSS entry')),element('span',t('Estimated credited upload')+': '+(typeof row.creditedUploadedBytes==='number'?'≈ '+trackerBytes(row.creditedUploadedBytes):t('Unknown'))),element('small',new Date(row.created*1000).toLocaleString(locale()),'muted'));box.append(line);}
    const nav=element('div',undefined,'actions'),previous=command(t('Previous page'),()=>richFeedHistory(id,name,page-1)),next=command(t('Next page'),()=>richFeedHistory(id,name,page+1));previous.disabled=page<=1;next.disabled=page*25>=result.total;nav.append(previous,element('span',`${page} / ${Math.max(1,Math.ceil(result.total/25))}`),next);box.append(nav);
  }catch(error){if(request===richFeedHistory.request)box.replaceChildren(element('p',t(error.message),'muted'));}
}
richFeedHistory.request=0;
