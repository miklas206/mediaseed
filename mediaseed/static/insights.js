/* Saved tracker observations and local suggestions; never controls downloads. */
(() => {
  const overviewPanel=document.querySelector('#insights'),panel=document.querySelector('#trackers');if(!panel||!overviewPanel)return;
  let snapshot=null,loading=false,reloadPending=false;
  const historyChart=MediaSeedHistoryChart.mount();
  const overviewHolder=element('div',undefined,'insights-content'),holder=element('div',undefined,'tracker-history-content'),status=element('p',t('Fetching insights…'),'muted');status.setAttribute('role','status');
  const heading=element('div',undefined,'heading'),refreshButton=command(t('Refresh'),()=>load());heading.append(element('h2',t('Your tracker accounts')),refreshButton);refreshButton.id='tracker-history-refresh';
  const filters=element('div',undefined,'insight-controls');
  const account=document.createElement('select'),days=document.createElement('select'),metric=document.createElement('select');
  account.id='insight-account';days.id='insight-days';metric.id='insight-metric';
  for(const value of [7,30,90])days.append(new Option(t('{0} days',{0:value}),String(value)));
  for(const [key,label] of [['ratio','Ratio'],['buffer','Buffer'],['uploaded','Credited upload'],['real_uploaded','Real upload'],['downloaded','Credited download'],['real_downloaded','Real download'],['seedbonus','Bonus points'],['all','All']])metric.append(new Option(t(label),key));
  for(const [label,control] of [['Tracker account',account],['Period',days],['Metric',metric]]){const field=element('label',t(label));field.append(control);filters.append(field);}
  const subtitle=element('p',undefined,'muted');
  const workspace=element('section',undefined,'workspace-card tracker-workspace');
  const workspaceHeader=element('header',undefined,'tracker-workspace-header');workspaceHeader.append(heading,subtitle,filters);workspace.append(workspaceHeader,status,holder);panel.prepend(workspace);
  const overviewStatus=element('p',undefined,'muted');overviewStatus.setAttribute('role','status');
  const overviewHeading=element('div',undefined,'heading'),overviewRefresh=command(t('Refresh'),()=>load());overviewRefresh.id='insights-refresh';overviewHeading.append(element('h1',t('Insights')),overviewRefresh);overviewPanel.append(overviewHeading,overviewStatus,overviewHolder);
  const setStatus=text=>{status.textContent=text;overviewStatus.textContent=text;};
  const manager=document.querySelector('#tracker-overview'),search=document.querySelector('.tracker-search-card');
  function fold(node,label){if(!node)return;const details=element('details',undefined,'tracker-secondary');const summary=element('summary',t(label));summary.dataset.trackerLabel=label;details.append(summary);node.before(details);details.append(node);return details;}
  fold(manager,'Manage tracker accounts');fold(search,'Search tracker releases');

  let cycling=false;
  try{cycling=localStorage.getItem('mediaseed-history-cycle')==='true';}catch{}
  const cycleButton=element('button',t('Auto'));cycleButton.onclick=()=>{
    cycling=!cycling;cycleButton.setAttribute('aria-checked',String(cycling));
    try{localStorage.setItem('mediaseed-history-cycle',String(cycling));}catch{}
  };
  cycleButton.setAttribute('aria-label',t('Auto cycle'));cycleButton.title=t('Cycles every 10 seconds. Pauses while you inspect the chart.');
  cycleButton.type='button';cycleButton.id='insight-cycle';cycleButton.setAttribute('role','switch');cycleButton.setAttribute('aria-checked',String(cycling));historyChart.toolbar.append(cycleButton);
  // The native selects retain state; compact buttons provide the visible controls.
  filters.children[1].hidden=true;filters.children[2].hidden=true;
  const metricGroup=element('div',undefined,'history-metrics'),periodGroup=element('div',undefined,'history-periods');
  metricGroup.setAttribute('role','group');periodGroup.setAttribute('role','group');
  const labels={ratio:'Ratio',buffer:'Buffer',uploaded:'Credited upload',real_uploaded:'Real upload',downloaded:'Credited download',real_downloaded:'Real download',seedbonus:'Bonus',all:'All'};
  function groupButtons(group,select,kind){
    for(const option of select.options){
      const button=element('button');button.onclick=()=>{select.value=option.value;select.dispatchEvent(new Event('change'));};
      button.type='button';button.dataset[kind]=option.value;
      button.addEventListener('keydown',event=>{
        const buttons=[...group.children],current=buttons.indexOf(button);
        const next={ArrowRight:(current+1)%buttons.length,ArrowLeft:(current+buttons.length-1)%buttons.length,Home:0,End:buttons.length-1}[event.key];
        if(next!==undefined){event.preventDefault();buttons[next].click();buttons[next].focus({preventScroll:true});}
      });
      group.append(button);
    }
  }
  groupButtons(metricGroup,metric,'metric');groupButtons(periodGroup,days,'period');
  historyChart.controls.append(metricGroup);historyChart.toolbar.prepend(periodGroup);
  function syncChartControls(){
    metricGroup.setAttribute('aria-label',t('Metric'));periodGroup.setAttribute('aria-label',t('Period'));
    for(const [group,select,kind] of [[metricGroup,metric,'metric'],[periodGroup,days,'period']])for(const button of group.children){
      const chosen=button.dataset[kind]===select.value;
      if(kind==='metric'){
        const key=button.dataset.metric,row=snapshot?.accounts?.find(r=>r.id===account.value),value=(key.startsWith('real_')?snapshot?.traffic?.stats:row?.stats)?.[key];
        const label=element('span',t(labels[key]),'metric-name'),total=element('strong',key==='all'?'↗':metricValue(key,value),'metric-total');
        button.replaceChildren(label,total);if(['uploaded','downloaded','real_uploaded','real_downloaded'].includes(key))button.append(element('small',t(key.startsWith('real_')?'MediaSeed':'Tracker account'),'metric-scope'));button.style.setProperty('--metric-color',colors[key]||'var(--accent)');
      }else button.textContent=button.dataset.period+'d';
      button.title=kind==='metric'?select.options[[...select.options].findIndex(option=>option.value===button.dataset.metric)].textContent:t('{0} days',{0:button.dataset.period});
      button.setAttribute('aria-label',button.title+(kind==='metric'?' · '+button.textContent:''));button.setAttribute('aria-pressed',String(chosen));button.tabIndex=chosen?0:-1;
    }
  }
  const busy=()=>historyChart.interacting()||overviewHolder.contains(document.activeElement)||filters.matches(':hover,:focus-within')||holder.contains(document.activeElement);
  function cycle(){
    if(!cycling||document.hidden||panel.hidden||busy()||loading||!snapshot?.accounts?.length)return;
    metric.selectedIndex=(metric.selectedIndex+1)%metric.options.length;paint();
  }
  setInterval(cycle,10000);
  days.onchange=()=>load();account.onchange=paint;metric.onchange=paint;
  const colors={ratio:'var(--tracker-ratio,#69d9b0)',uploaded:'var(--tracker-upload,#ef8cbe)',downloaded:'var(--tracker-download,#bfa0f5)',real_uploaded:'var(--tracker-real-upload,#7cb6f4)',real_downloaded:'var(--tracker-real-download,#e8b46e)',buffer:'var(--tracker-buffer,#7cb6f4)',seedbonus:'var(--tracker-bonus,#e4cb66)'};
  const metricValue=(key,value)=>['buffer','uploaded','downloaded','real_uploaded','real_downloaded'].includes(key)?size(value):number(value);
  const number=value=>typeof value==='number'&&Number.isFinite(value)?new Intl.NumberFormat(locale(),{maximumFractionDigits:2}).format(value):t('Unknown');
  const when=value=>value?new Date(value*1000).toLocaleString(locale(),{dateStyle:'short',timeStyle:'short'}):t('Unknown');
  const size=value=>typeof value==='number'?trackerBytes(value):t('Unknown');
  function card(title){const section=element('section',undefined,'workspace-card insight-card');section.append(element('h2',t(title)));return section;}
  function disclosure(label,...children){const box=element('details',undefined,'insight-more');box.append(element('summary',t(label)),...children);return box;}
  function fact(list,label,value){list.append(element('dt',t(label)),element('dd',value));}
  function sourceName(value){const id=String(value||'');if(id.startsWith('MediaSeed-RSS-'))return (globalThis.MediaSeedFeedNames||{})[id.slice(14)]||t('RSS')+' · '+id.slice(-6);return id==='MediaSeed-Ratio'?t('Earlier automatic downloads'):id&&id!=='manual'?id:t('Manual torrent');}
  function chart(samples,key){
    const precise=value=>new Intl.NumberFormat(locale(),{maximumFractionDigits:6}).format(value);
    const exact=(key,value)=>['buffer','uploaded','downloaded','real_uploaded','real_downloaded'].includes(key)?(()=>{const power=Math.min(4,Math.max(0,Math.floor(Math.log2(Math.abs(value)||1)/10)));return precise(value/1024**power)+' '+['B','KiB','MiB','GiB','TiB'][power];})():precise(value)+(key==='seedbonus'?' '+t('Bonus points'):key==='ratio'?'×':'');
    const format=value=>['buffer','uploaded','downloaded','real_uploaded','real_downloaded'].includes(key)?size(value):precise(value)+(key==='ratio'?'×':'');
    const real=key.startsWith('real_'),accountHistory=snapshot.history?.find(h=>h.account===account.value),physical=snapshot.traffic;
    if(real)samples=physical?.samples||[];
    historyChart.update(samples,key,{changes:true,source:t(real?'Physical traffic · MediaSeed':'Credited traffic · tracker account'),identity:real?'seedbox':account.value,interval:real?(physical?.sampleInterval||60):(accountHistory?.sampleInterval||900),label:metric.selectedOptions[0].textContent,format,exact:value=>exact(key,value),when:value=>new Date(value*1000).toLocaleString(locale(),{dateStyle:'short',timeStyle:'medium'}),rightAxis:true,hideHeadline:true,color:colors[key],series:key==='all'?Object.keys(colors).map(key=>({key,label:t(labels[key]),color:colors[key],samples:key.startsWith('real_')?physical?.samples||[]:samples,interval:key.startsWith('real_')?physical?.sampleInterval||60:accountHistory?.sampleInterval||900,exact:value=>exact(key,value)})):[],axisFormat:(value,low,high)=>{
      if(!['buffer','uploaded','downloaded','real_uploaded','real_downloaded'].includes(key))return precise(value);
      const unit=Math.max(Math.abs(low),Math.abs(high)),power=Math.min(4,Math.max(0,Math.floor(Math.log2(unit||1)/10))),scale=1024**power;
      const decimals=Math.min(6,Math.max(2,Math.ceil(-Math.log10((high-low)/scale||1))+1));
      return new Intl.NumberFormat(locale(),{maximumFractionDigits:decimals}).format(value/scale)+' '+['B','KiB','MiB','GiB','TiB'][power];
    }});
    return historyChart.figure;
  }
  const alertLabels={hit_and_runs:'Hit-and-runs',warnings:'Warnings',dm:'Messages',notifications:'Notifications',tickets:'Support tickets',low_buffer:'Low tracker buffer',rank_changed:'Rank changed',event:'Tracker event',premium_expiring:'Premium expires soon'};
  const reasons={connected_downloaders:'Connected downloaders',tracker_demand:'Downloaders on tracker',rare_seed:'Few remaining seeders',local_goal_met:'Local ratio goal met',no_observed_demand:'No observed demand'};
  function torrentCards(title,rows){const section=card(title);if(!snapshot.torrentDataFresh){section.append(element('p',t('Waiting for a fresh torrent observation.'),'muted'));return section;}
    if(!rows.length)section.append(element('p',t('No suggestions from current observations.'),'muted'));
    const more=disclosure('More suggestions');
    for(const [index,row] of rows.slice(0,10).entries()){const item=element('article',undefined,'insight-torrent');const open=command(row.name,()=>{const torrent=torrents.find(t=>t.hash===row.hash);if(torrent)void showDetails(torrent);else void refreshTorrents().then(()=>{const found=torrents.find(t=>t.hash===row.hash);if(found)void showDetails(found);});});open.className='torrent-name';open.title=row.name;item.append(open,element('p',(row.reasons||[]).map(reason=>t(reasons[reason]||reason)).join(' · '),'muted'),element('small',size(row.size)+' · '+t('Seeders')+': '+number(row.seeders)+' · '+t('Leechers')+': '+number(row.leechers)));(index<3?section:more).append(item);}if(rows.length>3)section.append(more);return section;}
  function paint(){
    if(!snapshot)return;const chartFocus=historyChart.figure.contains(document.activeElement)?document.activeElement:null;syncChartControls();holder.replaceChildren();overviewHolder.replaceChildren();
    const row=(snapshot.accounts||[]).find(r=>r.id===account.value);
    const grid=element('div',undefined,'insight-grid'),left=element('div',undefined,'insight-column'),right=element('div',undefined,'insight-column');grid.append(left,right);
    const overview=element('aside',undefined,'tracker-account-summary'),facts=element('dl',undefined,'insight-facts'),extraFacts=element('dl',undefined,'insight-facts');
    if(row){overview.append(element('h3',t('Account overview')),element('p',t(row.status==='ok'?'Connected':row.status==='stale'?'Showing last known tracker statistics':'Account data unavailable'),'muted'));
      for(const [label,value] of [['Seeding',number(row.stats?.seeding)],['Leeching',number(row.stats?.leeching)],['Seeding size',size(row.stats?.seeding_size)],['Average seed time (tracker value)',number(row.stats?.avg_seedtime)],['Total seed time (tracker value)',number(row.stats?.total_seedtime)],['Releases',number(row.stats?.total_uploads)],['Hit-and-runs',number(row.stats?.hit_and_runs)],['Warnings',number(row.stats?.warnings)],['Last seen',row.lastSeen?new Date(row.lastSeen).toLocaleString(locale()):t('Unknown')]])fact(['Seeding','Leeching','Hit-and-runs','Warnings','Seeding size','Releases'].includes(label)?facts:extraFacts,label,value);
      overview.append(facts);
      const rank=element('div');for(const [key,label] of [['automatic','Rank'],['upload','Uploader rank'],['internal','Staff rank']])if(row.ranks?.[key])rank.append(element('p',t(label)+': '+row.ranks[key]));
      if(row.rankProgress?.target)rank.append(rankRequirements(row.rankProgress.target,true));
      const accountDetails=disclosure('Account details',extraFacts,rank);overview.append(accountDetails);
      if(row.rankProgress?.table?.length)rank.append(command(t('View rank requirements'),()=>showRankDialog(row)));
      for(const [key,label] of [['dm','Messages'],['notifications','Notifications'],['tickets','Support tickets']])if(typeof row.notifications?.[key]==='number')fact(extraFacts,label,number(row.notifications[key]));
      if(row.base_url){const link=element('a',t('Open tracker'));link.href=row.base_url;link.target='_blank';link.rel='noopener noreferrer';accountDetails.append(link);}
      accountDetails.append(element('p',t('Premium')+': '+(row.premium?.type||t('Not supplied'))+(row.premium?.expiresAt?' · '+new Date(row.premium.expiresAt).toLocaleString(locale()):'')));
      for(const event of row.events||[]){const supplied=typeof event.title==='string'?event.title.trim():'';const title=supplied&&supplied.toLowerCase()!=='null'?supplied:t(event.type==='global_freeleech'?'Global freeleech':'Tracker event');overview.append(element('p',title+' · '+new Date(event.endsAt).toLocaleString(locale()),'seed-badge free'));}
      overview.append(element('p',t('Updated {0}',{0:when(row.checkedAt)}),'muted'));
    }else overview.append(element('p',t('Add a tracker account under Trackers.'),'muted'));
    const history=element('div',undefined,'tracker-chart');
    const real=metric.value.startsWith('real_');
    let scope=metric.value==='all'?'Credited totals are from the selected tracker account. Physical totals are from MediaSeed, across all trackers.':real?'Physical traffic · MediaSeed':'Credited traffic · tracker account';
    if(real&&snapshot.traffic?.status!=='ok')scope=snapshot.traffic?.checkedAt?'Physical traffic is stale. Showing last known counters.':'Physical counters require an updated MediaVPN connection. No session counters are used as lifetime totals.';
    const note=element('p',t(scope),'muted tracker-scale-note');
    const changes=element('p',t('Latest 60 changes in the selected period. Curves connect recorded changes.'),'muted tracker-change-note');
    history.append(chart(snapshot.history?.find(h=>h.account===row?.id)?.samples||[],metric.value),changes,note);
    if(metric.value==='all')history.append(element('p',t('Each curve uses its own scale. Select a metric for exact axis values.'),'muted tracker-scale-note'));
    holder.append(overview,history);
    const alerts=card('Alerts');const entries=snapshot.alerts||[];
    const olderAlerts=disclosure('More alerts');
    for(const [index,alert] of entries.slice(0,12).entries()){const item=element('p');let value=alert.kind==='premium_expiring'?new Date(alert.value).toLocaleString(locale()):alert.kind==='low_buffer'?size(alert.value):typeof alert.value==='object'?(alert.value?.title||Object.values(alert.value||{}).join(' · ')):number(alert.value);item.append(element('span',snapshot.accounts.find(row=>row.id===alert.account)?.name||'','muted'),element('strong',t(alertLabels[alert.kind]||alert.kind)+': '+value),element('small',when(alert.createdAt),'muted'));(index<3?alerts:olderAlerts).append(item);}
    if(entries.length>3)alerts.append(olderAlerts);
    if(!entries.length)alerts.append(element('p',t('No recorded alerts.'),'muted'));
    if(appRole==='administrator'){const form=element('form',undefined,'insight-controls'),label=element('label',t('Warn below buffer (GiB)')),input=document.createElement('input');input.type='number';input.min='0';input.max='100000';input.required=true;input.value=snapshot.settings.lowBufferGiB;label.append(input);const save=element('button',t('Save'));form.append(label,save);form.onsubmit=async e=>{e.preventDefault();save.disabled=true;try{await api('insights/settings',{lowBufferGiB:Number(input.value)},'PUT');showNotification(t('Saved palette'),'success');await load();}catch(error){notify(error);}finally{save.disabled=false;}};alerts.append(disclosure('Alert settings',form));}
    left.append(alerts);if(snapshot.keepSeeding?.length)right.append(torrentCards('Worth keeping seeded',snapshot.keepSeeding));
    const review=torrentCards('Review storage manually',snapshot.reviewStorage||[]);review.append(disclosure('Storage planning',element('p',t('Estimated remaining download: {0}',{0:size(snapshot.remainingBytes)})),element('p',t('Suggestions never remove files or confirm tracker seeding obligations.'),'muted')));if(snapshot.reviewStorage?.length)right.append(review);else left.append(disclosure('Storage planning',element('p',t('Estimated remaining download: {0}',{0:size(snapshot.remainingBytes)}))));if(!right.children.length)right.append(torrentCards('Seeding suggestions',[]));overviewHolder.append(grid);
    const results=card('Upload results');results.append(element('p',t('Observed upload starts when monitoring begins. Total upload is the local client counter, not tracker credit.'),'muted'),detailTable(t('Sources'),[['name',t('Source')],['count',t('Torrents')],['size',t('Size')],['total',t('Total local upload')],['observed',t('Observed upload')],['efficiency',t('Upload per GiB stored')]],(snapshot.results||[]).map(r=>({name:sourceName(r.source),count:r.torrents,size:size(r.size),total:size(r.totalUpload),observed:size(r.observedUpload),efficiency:number(r.uploadPerGiB)+'×'}))),disclosure('Top torrents by observed upload',detailTable(t('Top torrents by observed upload'),[['name',t('Name')],['observed',t('Observed upload')],['total',t('Total local upload')]],(snapshot.topTorrents||[]).slice(0,10).map(r=>({name:r.name,observed:size(r.observedUpload),total:size(r.uploaded)})))));overview.append(disclosure('Upload results',results));
    if(chartFocus?.isConnected)chartFocus.focus({preventScroll:true});
  }
  async function load(background=false){if(background&&busy())return;if(loading){reloadPending=true;return;}const requestedDays=days.value;for(const summary of panel.querySelectorAll('[data-tracker-label]'))summary.textContent=t(summary.dataset.trackerLabel);overviewRefresh.textContent=t('Refresh');heading.querySelector('h2').textContent=t('Your tracker accounts');refreshButton.textContent=t('Refresh');cycleButton.textContent=t('Auto');cycleButton.setAttribute('aria-label',t('Auto cycle'));cycleButton.title=t('Cycles every 10 seconds. Pauses while you inspect the chart.');subtitle.textContent=t('Includes activity from your other torrent clients.');for(const [index,label] of ['Tracker account','Period','Metric'].entries())filters.children[index].firstChild.nodeValue=t(label);for(const option of days.options)option.textContent=t('{0} days',{0:option.value});for(const [index,label] of ['Ratio','Buffer','Credited upload','Real upload','Credited download','Real download','Bonus points','All'].entries())metric.options[index].textContent=t(label);loading=true;refreshButton.disabled=true;setStatus(t('Fetching insights…'));
    try{const data=await api('insights?days='+requestedDays);if(!data||!Array.isArray(data.accounts))throw Error(t('Insights unavailable'));
      const selected=account.value;account.replaceChildren(...data.accounts.map(row=>new Option(row.name,row.id)));if(data.accounts.some(row=>row.id===selected))account.value=selected;else account.value=data.accounts.find(row=>row.status==='ok')?.id||data.accounts[0]?.id||'';
      snapshot=data;
      if(requestedDays===days.value&&(!background||!busy()))paint();setStatus('');
    }catch(error){setStatus(t('Insights unavailable')+' · '+t(error.message));}finally{loading=false;refreshButton.disabled=false;if(reloadPending){reloadPending=false;void load();}}}
  function renderMedia(data,target){
    if(!data){target.append(element('p',t('Metadata is still being matched in the background. Try refreshing later.'),'muted'));return;}
    const facts=element('dl',undefined,'insight-facts');for(const [label,value] of [['Category',data.category],['Resolution',data.resolution],['Release year',data.release_year],['Genres',data.genres],['Uploader',data.uploader],['Seeders',number(data.seeders)],['Leechers',number(data.leechers)],['Completed downloads',number(data.times_completed)]])fact(facts,label,value||t('Unknown'));target.append(facts,element('p',t('Updated {0}',{0:when(data.checkedAt)}),'muted'));
    const flags=element('div',undefined,'seed-badges');for(const label of data.labels||[])flags.append(element('span',label.name,'seed-badge'));target.append(flags);
    if(data.description){const box=element('details');box.append(element('summary',t('Description')),element('p',data.description,'insight-description'));target.append(box);}
    if(data.media_info){const box=element('details');box.append(element('summary','MediaInfo'),element('pre',data.media_info,'insight-mediainfo'));target.append(box);}
    const screenshots=element('div',undefined,'actions');for(const [index,shot] of (data.screenshots||[]).entries()){const a=element('a',t('Screenshot {0}',{0:index+1}));a.href=shot.url;a.target='_blank';a.rel='noopener noreferrer';screenshots.append(a);}target.append(screenshots);
    const ids=element('p',undefined,'muted');ids.textContent=['tmdb','imdb','tvdb','mal','igdb','tpdb'].filter(key=>data[key+'_id']>0).map(key=>key.toUpperCase()+': '+data[key+'_id']).join(' · ');target.append(ids);
    if(data.files?.length)target.append(detailTable(t('Tracker file list'),[['name',t('Name')],['size',t('Size')]],data.files.map(f=>({name:f.name,size:size(f.size)}))));
  }
  for(const view of ['insights','trackers'])document.querySelector('[data-view="'+view+'"]').addEventListener('click',()=>void load());
  setInterval(()=>{if(!document.hidden&&(!panel.hidden||!overviewPanel.hidden)&&document.body.classList.contains('signed-in'))void load(true);},30000);
  const indicator=element('span','•','insight-indicator');indicator.hidden=true;indicator.setAttribute('aria-label',t('Tracker alerts available'));document.querySelector('[data-view="insights"]').append(indicator);
  function updateBadge(rows){indicator.hidden=!(rows||[]).some(row=>row.stats?.warnings>0||row.stats?.hit_and_runs>0||Object.values(row.notifications||{}).some(value=>typeof value==='number'&&value>0));}
  updateBadge(globalThis.MediaSeedTrackerRows||[]);
  function manageCard(row){
    const card=element('article',undefined,'source-row tracker-management-row');
    card.append(element('h3',row.name),element('p',(row.username?row.username+' · ':'')+t(row.status==='ok'?'Connected':row.status==='stale'?'Showing last known tracker statistics':'Account data unavailable'),'muted'));
    return card;
  }
  globalThis.MediaSeedIntelligence={renderMedia,updateBadge,manageCard,refreshIfVisible:()=>{if(!panel.hidden||!overviewPanel.hidden)void load(true);}};
})();
