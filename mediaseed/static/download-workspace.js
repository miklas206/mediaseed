/* Downloads is the working overview; existing charts, forms and APIs stay intact. */
(() => {
  const workspace = document.querySelector('#workspace'); if (!workspace) return;
  const go = view => document.querySelector(`[data-view="${view}"]`)?.click();
  const flow = element('section', undefined, 'download-flow'); flow.id='download-flow';flow.setAttribute('aria-label',t('Protected transfer route'));
  const description=element('strong',t('Waiting for status check'));description.id='download-flow-status';description.setAttribute('role','status');
  const route=element('div',undefined,'download-route');
  const svgIcon = path => {const holder=element('span',undefined,'route-icon');holder.setAttribute('aria-hidden','true');holder.innerHTML=`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${path}</svg>`;return holder;};
  const routePaths=['<circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><path d="M3 12h18"/>','<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z"/><path d="m8 12 3 3 5-6"/>','<path d="M12 3v12m-4-4 4 4 4-4M4 16v5h16v-5"/>','<path d="m5 4-3 12v4h20v-4L19 4Z M2 16h20M17 18h2"/>'];
  for(const [index,label] of ['Internet','MediaVPN','MediaSeed','Media storage'].entries()) {
    if(index) {const link=element('span',undefined,'download-route-link');link.setAttribute('aria-hidden','true');route.append(link);}
    const node=element('span',undefined,'download-route-node');node.append(svgIcon(routePaths[index]),element('strong',t(label)));route.append(node);
  }
  flow.append(description,route);workspace.querySelector('.seed-stats').before(flow);
  const packetFlow=MediaSeedPackets.mount(route);
  for(const [index,article] of [...workspace.querySelectorAll('.seed-stats article')].entries()) {
    const paths=['<path d="M12 3v18m-6-6 6 6 6-6"/>','<path d="M12 21V3m-6 6 6-6 6 6"/>','<rect x="4" y="3" width="16" height="18" rx="1"/><path d="M4 9h16M4 15h16"/>'];
    const mark=svgIcon(paths[index]);mark.className='stat-icon';article.querySelector(':scope > span').replaceWith(mark);
  }
  const stats=workspace.querySelector('.seed-stats article:last-child');const activityCount=element('small');activityCount.id='torrent-activity-count';stats.append(activityCount);
  const cards=element('section',undefined,'download-status-cards');cards.setAttribute('aria-label',t('Download status'));
  const vpn=element('button',undefined,'download-status-card');vpn.type='button';vpn.onclick=()=>go('vpn');
  const account=element('button',undefined,'download-status-card');account.type='button';account.onclick=()=>go('trackers');
  const queue=element('div',undefined,'download-status-card');const counts=element('strong'),queueTitle=element('span',t('Download queue'));
  const queueActions=element('div',undefined,'actions');
  const filterTo = value => {document.querySelector('#filter').value=value;torrentPage=1;renderTorrents();document.querySelector('#torrents').scrollIntoView({block:'start',behavior:document.documentElement.dataset.reducedMotion==='true'?'instant':'smooth'});};
  queueActions.append(command(t('Show downloads'),()=>filterTo('downloading')),command(t('Check files'),()=>filterTo('checking')));queue.append(queueTitle,counts,queueActions);
  const queueActivity=element('small');queue.append(queueActivity);
  cards.append(vpn,account,queue);document.querySelector('#downloads').prepend(cards);
  const events=element('details',undefined,'automatic-activity');events.id='automatic-activity';const summary=element('summary',t('Latest automatic activity'));const eventList=element('div');events.append(summary,eventList);cards.after(events);
  const technical=element('details',undefined,'client-details');technical.append(element('summary',t('Client details')));document.querySelector('#transfer').before(technical);technical.append(document.querySelector('#transfer'));
  const editors={};
  for(const [kind,id,title] of [['feeds','feed-form','Add feed'],['trackers','tracker-form','Add tracker']]) {
    const form=document.querySelector('#'+id),card=form.closest('.workspace-card'),dialog=element('dialog',undefined,'source-editor');dialog.id=kind+'-editor';dialog.setAttribute('aria-label',t(title));
    const close=command(t('Close'),()=>dialog.close());close.type='button';close.className='source-editor-close';dialog.append(close,card);document.body.append(dialog);editors[kind]=dialog;
    const add=command(t(title),()=>{form.reset();form.elements.namedItem('id').value='';openEditor(kind);});add.type='button';add.className='primary';add.dataset.adminOnly='true';document.querySelector('#'+kind).prepend(add);
  }
  document.querySelector('#tracker-account-list').append(document.querySelector('#tracker-list'));
  document.querySelector('#manage-trackers').onclick=()=>{document.querySelector('#tracker-form').reset();openEditor('trackers');};
  const badges=document.querySelector('#feed-form [name="badges"]'),group=element('fieldset',undefined,'badge-choices');group.append(element('legend',t('Required badges')));
  const boxes=[];for(const option of badges.options){const label=element('label'),box=document.createElement('input');box.type='checkbox';box.value=option.value;box.onchange=()=>{option.selected=box.checked;};label.append(box,element('span',option.textContent));group.append(label);boxes.push([box,option]);}
  badges.closest('label').replaceWith(group);badges.hidden=true;group.append(badges);group.append(element('p',t('Every selected badge must match.'),'muted'));
  function syncBadges(){for(const [box,option] of boxes)box.checked=option.selected;}
  document.querySelector('#feed-form').addEventListener('reset',()=>setTimeout(syncBadges,0));
  function openEditor(kind){if(appRole!=='administrator')return;editors[kind]?.showModal();setTimeout(syncBadges,0);}
  function render(){
    const active=document.querySelector('#connection-flow').dataset.state==='active' && downloadsEnabled;
    const traffic=torrentSnapshotValid&&Date.now()-lastTorrentSample<30000?torrentTraffic():null;
    packetFlow.update(traffic?.dl_info_speed,traffic?.up_info_speed,active&&!!traffic);
    flow.dataset.active=String(active);flow.dataset.download=String(active&&!!traffic&&traffic.dl_info_speed>0);flow.dataset.upload=String(active&&!!traffic&&traffic.up_info_speed>0);
    const status=active?traffic&&(traffic.dl_info_speed>0||traffic.up_info_speed>0)?'Protected transfers active':'VPN ready · waiting for peers':'Transfers blocked · check VPN';
    description.textContent=t(status);
    flow.hidden=document.querySelector('#downloads').hidden;
    const runtime=document.querySelector('#connection-country').textContent;
    vpn.replaceChildren(element('span',t('Connection')),element('strong',active?t('VPN verified'):t('Status cannot be verified')),element('small',active?runtime:t('Check the connection under VPN')));
    if(active)vpn.append(element('small',document.querySelector('#connection-ip').textContent+' · '+document.querySelector('#connection-port').textContent),element('small',document.querySelector('#connection-checked').textContent));
    const accounts=globalThis.MediaSeedTrackerRows||[],row=accounts[0];
    account.replaceChildren(element('span',t('Tracker and ratio')),element('strong',row?.stats?t('Ratio {0} · Buffer {1}',{0:trackerNumber(row.stats.ratio),1:trackerBytes(row.stats.buffer)}):t('Account data unavailable')));
    if(row?.stats){
      const totals=element('dl',undefined,'download-account-totals');
      for(const [label,value] of [['Tracker upload',row.stats.uploaded],['Tracker download',row.stats.downloaded]]){const metric=element('div');metric.append(element('dt',t(label)),element('dd',trackerBytes(value)));totals.append(metric);}
      const identity=[row.name,row.ranks?.automatic?t('Rank: {0}',{0:row.ranks.automatic}):t('Rank unavailable from tracker'),t('Warnings')+': '+trackerNumber(row.stats.warnings)].join(' · ');
      account.append(totals,element('small',identity));
      if(row.status==='stale')account.append(element('small',t('Showing last known tracker statistics')));
      if(row.rankProgress?.target)account.append(element('small',t('Next rank: {0} · View requirements',{0:row.rankProgress.target.title})));
      else account.append(element('small',t('View rank requirements')));
    } else account.append(element('small',t('Manage trackers')));
    account.classList.toggle('attention',!!row?.stats?.warnings);account.hidden=appRole==='viewer';
    const q=globalThis.MediaSeedQueue,checking=torrentSnapshotValid?torrents.filter(r=>String(r.state).startsWith('checking')).length:null;
    counts.textContent=q?t('Downloads {0}/{1} · Waiting {2}',{0:q.active,1:q.limit===-1?t('Unlimited'):q.limit??'—',2:q.waiting}):t('Queue status unavailable');
    queueTitle.textContent=checking===null?t('Download queue'):t('Download queue · Checking {0}',{0:checking});
    queueActivity.textContent=torrentSnapshotValid?t('{0} seeding · {1} stopped',{0:torrents.filter(row=>['uploading','forcedUP','stalledUP'].includes(row.state)).length,1:torrents.filter(row=>/^(stopped|paused)(DL|UP)$/.test(row.state)).length}):t('Status unavailable');
    for(const button of queueActions.querySelectorAll('button'))button.disabled=!torrentSnapshotValid;
    activityCount.textContent=torrentSnapshotValid?t('{0} uploading · {1} downloading',{0:torrents.filter(r=>r.upspeed>0).length,1:torrents.filter(r=>r.dlspeed>0).length}):t('Status unavailable');
    for(const button of document.querySelectorAll('[data-admin-only]'))button.hidden=appRole!=='administrator';
  }
  let activityLoading=false;
  async function refreshActivity(){if(activityLoading||document.querySelector('#workspace').hidden)return;activityLoading=true;
    try{const data=await api('activity');const rows=data.entries||[];events.hidden=!rows.length;summary.textContent=t('Latest automatic activity ({0})',{0:rows.length});eventList.replaceChildren(...rows.map(row=>{
      const item=element('article',undefined,'automation-event');const statusLabels={added:'Torrent added',started:'Added to download queue',paused:'Added paused',intent:'Import not confirmed; automatic retry is blocked',feed_error:'Feed could not be fetched',import_unconfirmed:'Import not confirmed; automatic retry is blocked',too_large:'Torrent exceeds the size limit',badges_unverified:'Required badges could not be verified',metadata_unknown:'Torrent metadata is unknown',size_unknown:'Torrent size is unknown'};
      item.append(element('strong',row.title||t('RSS entry')),element('span',`${row.source==='rss'?'RSS':t('Build ratio')} · ${t(statusLabels[row.status]||row.status)}`),element('small',new Date(row.created*1000).toLocaleString(locale())));
      const link=command(t('View details'),()=>{if(row.hash){const torrent=torrents.find(r=>r.hash===row.hash);if(torrent){void showDetails(torrent);return;}}go(row.source==='rss'?'feeds':'ratio-scout');if(row.source==='rss' && row.feed && appRole!=='viewer')void richFeedHistory(row.feed,'RSS').catch(notify);});item.append(link);return item;
    }));}catch{events.hidden=false;summary.textContent=t('Automatic activity unavailable');}finally{activityLoading=false;}}
  for(const button of document.querySelectorAll('[data-view]'))button.addEventListener('click',()=>{render();if(button.dataset.view==='downloads')void refreshActivity();});
  globalThis.MediaSeedWorkspace={render,openEditor,syncBadges,closeEditor:kind=>editors[kind]?.close()};
  setInterval(()=>{render();void refreshActivity();if(!document.querySelector('#trackers').hidden)void loadSources('trackers').catch(()=>{});},15000);
  const captured=[];
  for(const owner of [flow,cards,events,technical,...Object.values(editors),...document.querySelectorAll('[data-admin-only]')]) {
    const walker=document.createTreeWalker(owner,NodeFilter.SHOW_TEXT);
    for(let node;(node=walker.nextNode());)if(node.nodeValue.trim())captured.push([node,node.nodeValue]);
  }
  new MutationObserver(()=>{for(const [node,source] of captured)if(node.isConnected)node.nodeValue=t(source);render();}).observe(document.documentElement,{attributes:true,attributeFilter:['lang']});
  render();
})();
