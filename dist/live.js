const $=s=>document.querySelector(s);
const esc=s=>String(s??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const views=[['overview','◫','Overview'],['roadmap','⌁','Roadmap'],['tickets','▤','Tickets'],['agents','⬡','Agent roles'],['changes','↗','Changes'],['merges','⑂','Merge workbench'],['history','◷','Decision history'],['evaluations','✓','Evaluations'],['signals','≋','Signals'],['runtime','↻','Target run']];
let snapshot={},updated=null,busy=false,ticketSearch='',ticketStatus='Live work',ticketTier='All tiers',ticketDetail=null,documentDetail=null;
const data=k=>snapshot[k]?.ok?snapshot[k].data:null;
const panel=(title,body)=>`<section class="panel"><div class="panel-head"><h2>${esc(title)}</h2></div><div class="pad">${body}</div></section>`;
const empty=t=>`<p>${esc(t)}</p>`;
const details=x=>`<details><summary>Inspect evidence</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(JSON.stringify(x,null,2))}</pre></details>`;
const badge=t=>`<span class="badge">${esc(t)}</span>`;
function flatten(node){return node?[node,...(node.subGoals||[]).flatMap(flatten)]:[]}
function events(){return Array.isArray(data('live')?.recent)?data('live').recent:[]}
function evidence(rows,message){return rows.length?`<div class="timeline">${rows.map(e=>`<div class="event"><small>${esc(e.timestamp||e.at||'')} · ${esc(e.cycleId||'')}</small><h3>${esc(e.event||e.kind||e.subject||'Record')}</h3>${details(e)}</div>`).join('')}</div>`:empty(message)}
function render(){
 let view=location.hash.slice(1)||'overview';if(!views.some(v=>v[0]===view))view='overview';
 const status=data('status'),live=data('live'),home=data('home'),start=data('start');
 const connected=!!snapshot.status?.ok,goals=flatten(data('goals')),conflicts=data('conflicts');
 $('#nav').innerHTML=views.map(([id,icon,title])=>`<a href="#${id}" class="${id===view?'active':''}"><span>${icon}</span>${title}</a>`).join('');
 $('#crumb').textContent=$('#heading').textContent=views.find(v=>v[0]===view)[2];
 $('#subtitle').textContent=connected?'Autobuilder development evidence · refreshes every 5 seconds':'Run backend unavailable · reconnecting every 5 seconds';
 $('#connection').textContent=data('meta')?'Autobuilder development sources':'Development sources unavailable';
 $('#updated').textContent=updated?`Last checked ${updated.toLocaleTimeString()}`:'Connecting…';
 $('#workspace-name').textContent='Autobuilder development';
 $('#mode').textContent=connected?'LIVE WORKSPACE':'DISCONNECTED';
 const unavailable=k=>snapshot[k]&&!snapshot[k].ok?panel('Source unavailable',empty(snapshot[k].error)):'';
 let html='';
 if(view==='runtime'){
  const metrics=[['Cycle',status?.cycleName??'No cycle'],['Score',status?.score!=null?status.score.toFixed(3):'—'],['Merged',live?.merged??'—'],['Open conflicts',Array.isArray(conflicts)?conflicts.length:'—']];
  html=`<div class="stats">${metrics.map(([t,n])=>`<div class="stat"><div class="label">${esc(t)}</div><strong style="font-size:22px;overflow-wrap:anywhere">${esc(n)}</strong></div>`).join('')}</div>`;
  html+=panel('Current work',`<h3>${esc(status?.currentSubGoalDescription||live?.workingOn||'No current subgoal reported')}</h3><p>${esc(status?.goalProgress)} · ${esc(status?.mutationState)}</p>`);
  html+=panel('Current goal',`<p style="white-space:pre-wrap">${esc(status?.currentGoalDescription||'No goal reported')}</p>`);
  html+=panel('Recent activity',evidence(events().slice(0,12),'No recent events reported.'))+unavailable('status');
 }
 if(view==='roadmap'){
  const dev=data('development');
  html=panel('Autobuilder development roadmap',empty('Source: .claude-memory/roadmap.md · authoritative ticket markers. Built, declined, obsoleted, subsumed, and retracted remain distinct outcomes.'));
  if(dev){
   html+=panel('Queue census',Object.entries(dev.counts).map(([status,count])=>`<div class="row"><h3>${esc(status)}</h3>${badge(count)}</div>`).join(''));
   const tiers=[...new Set(dev.tickets.map(t=>t.tier))];
   html+=panel('Work by tier',tiers.map(tier=>{const rows=dev.tickets.filter(t=>t.tier===tier);const counts={};rows.forEach(t=>counts[t.status]=(counts[t.status]||0)+1);return `<div class="row"><div><h3>${tier==='M'?'Maintenance':`Tier ${esc(tier)}`}</h3><p>${Object.entries(counts).map(([k,v])=>`${v} ${esc(k)}`).join(' · ')}</p></div><a class="link" href="#tickets" data-tier="${esc(tier)}">Inspect tickets ↗</a></div>`}).join(''));
  }
 }
 if(view==='tickets'){
  const dev=data('development'),statuses=['Live work','All statuses',...Object.keys(dev?.counts||{})];
  const select=(id,values,value)=>`<select id="${id}" aria-label="${id}">${values.map(x=>`<option ${x===value?'selected':''}>${esc(x)}</option>`).join('')}</select>`;
  html=`<div class="toolbar"><input id="ticket-search" aria-label="Search development tickets" placeholder="Search ID or title…" value="${esc(ticketSearch)}">${select('ticket-status',statuses,ticketStatus)}${select('ticket-tier',['All tiers',...[...new Set((dev?.tickets||[]).map(t=>t.tier))]],ticketTier)}</div>`;
  const rows=(dev?.tickets||[]).filter(t=>(ticketStatus==='All statuses'||(ticketStatus==='Live work'?['open','in progress'].includes(t.status):t.status===ticketStatus))&&(ticketTier==='All tiers'||t.tier===ticketTier)&&`${t.id} ${t.title}`.toLowerCase().includes(ticketSearch.toLowerCase()));
  html+=panel(`${rows.length} matching development tickets`,rows.length?`<table><thead><tr><th>TICKET</th><th>STATUS</th><th>SOURCE</th></tr></thead><tbody>${rows.map(t=>`<tr><td><button data-ticket="${esc(t.id)}">${esc(t.id)}</button><p>${esc(t.title)}</p></td><td>${badge(t.status)}</td><td>roadmap.md:${t.line}</td></tr>`).join('')}</tbody></table>`:empty('No tickets match these filters.'));
  if(ticketDetail)html+=panel(ticketDetail.id,`<p>${esc(ticketDetail.status)} · ${esc(ticketDetail.source)}:${ticketDetail.line}</p><pre style="white-space:pre-wrap;overflow-wrap:anywhere;max-height:65vh;overflow:auto">${esc(ticketDetail.text||ticketDetail.error)}</pre>`);
 }
 const meta=data('meta'),repo=meta?.git?.data,dev=data('development');
 const docs=meta?.documents||[];
 const docList=rows=>rows.map(d=>`<div class="row"><div><h3>${esc(d.title)}</h3><small>${esc(d.path)}</small></div><button data-document="${esc(d.path)}">Read record</button></div>`).join('')||empty('No matching development records.');
 const ticketLinks=ids=>ids.map(id=>`<a href="#tickets" data-ticket-link="${esc(id)}">${esc(id)}</a>`).join(' · ');
 const commits=()=>repo?repo.commits.map(c=>`<div class="event"><small>${esc(c.at)} · ${esc(c.sha.slice(0,10))}</small><h3>${esc(c.title)}</h3><p>${ticketLinks(c.tickets)}</p></div>`).join(''):empty(meta?.git?.error||'Git data unavailable.');
 if(view==='overview'){
  const counts=dev?.counts||{};
  html=panel('Autobuilder development',`<p>Architecture, decisions, implementation and evidence for Autobuilder itself.</p><p>Branch: ${esc(repo?.branch)} · HEAD: ${esc(repo?.head?.slice(0,10))}</p>`);
  html+=`<div class="stats">${[['Open tickets',counts.open??'—'],['In progress',counts['in progress']??0],['Deferred',counts.deferred??'—'],['Changed tracked files',repo?.changed.length??'—']].map(([t,n])=>`<div class="stat"><div class="label">${esc(t)}</div><strong>${esc(n)}</strong></div>`).join('')}</div>`;
  html+=panel('Recent development',commits())+panel('Working principles and design',docList(docs.filter(d=>/meta_process|three_disciplines|meta_layer/.test(d.path))));
 }
 if(view==='agents'){
  const responsibilities={planner:'Plans subgoals and their acceptance criteria.',evolution:'Produces candidate code changes.',evaluation:'Judges candidate mutations with model and heuristic evidence.',merge:'Applies accepted mutations and records their provenance.',analysis:'Finds structural issues and proposes work.'};
  html=panel('Roles inside Autobuilder',empty('These are the execution roles implemented by Autobuilder. Development ownership is not inferred from these roles.'));
  html+=panel('Implementation and protocol contracts',(meta?.roles||[]).map(r=>`<div class="row"><div><h3>${esc(r.role)}</h3><p>${esc(responsibilities[r.role])}</p>${[...r.sources,...r.contracts].map(path=>`<small style="display:block">${esc(path)}</small>`).join('')}<p>Related development tickets: ${ticketLinks((dev?.tickets||[]).filter(t=>t.title.toLowerCase().includes(r.role)).slice(0,8).map(t=>t.id))}</p></div></div>`).join(''));
 }
 if(view==='changes')html=panel('Uncommitted Autobuilder changes',repo?`<p>Tracked changes: ${repo.changed.length}. Untracked paths: ${repo.untracked.length}. Includes work by other sessions.</p><pre style="white-space:pre-wrap">${esc(repo.changed.join('\n')||'No tracked changes.')}</pre><details><summary>Untracked paths</summary><pre style="white-space:pre-wrap">${esc(repo.untracked.join('\n'))}</pre></details>`:empty(meta?.git?.error||'Git unavailable.'))+panel('Recent commits · ticket references from commit titles',commits());
 if(view==='merges')html=panel('Autobuilder Git conflicts',repo?repo.conflicts.length?`<pre>${esc(repo.conflicts.join('\n'))}</pre>`:empty('No unresolved Git index conflicts in the Autobuilder checkout.'):empty('Git conflict status unavailable.'))+panel('Port and integration reviews',docList(docs.filter(d=>d.path.startsWith('process/'))))+panel('Review boundary',empty('These records concern changes entering Autobuilder itself. Review packets preserve proposed, loop-built and reference alternatives. This view does not apply merges or invent LLM options.'));
 if(view==='history')html=panel('Recorded development decisions',docList(docs.filter(d=>d.path.startsWith('.claude-memory/'))))+panel('Resulting development history',commits());
 if(view==='evaluations'){
  const tests=meta?.tests;
  html=panel('Saved Autobuilder test evidence',empty('Reports already on disk, from target/surefire-reports. They may come from different invocations and do not establish that the current HEAD or uncommitted work passes. No tests are started by this view.'));
  if(tests)html+=panel('Report totals',details(tests.totals))+panel('Test suites',tests.reports.length?tests.reports.map(r=>`<div class="row"><div><h3>${esc(r.name)}</h3><small>${esc(r.source)}</small><p>File modified: ${esc(r.modified)}</p></div>${badge(`${r.tests} tests · ${r.failures} failures · ${r.errors} errors · ${r.skipped} skipped`)}</div>`).join(''):empty('No saved Surefire reports found.'))+(tests.errors.length?panel('Unreadable reports',details(tests.errors)):'');
  html+=panel('Review evidence',docList(docs.filter(d=>/ASSESSMENT|REVIEW/.test(d.path))));
 }
 if(view==='signals'){
  html=panel('Development queue observations',`<p>Derived from authoritative ticket markers and the current Git checkout. These observations do not steer Autobuilder’s execution loop.</p><div class="row"><h3>Open work</h3>${badge(dev?.counts.open??'Unavailable')}</div><div class="row"><h3>Deferred work</h3>${badge(dev?.counts.deferred??'Unavailable')}</div><div class="row"><h3>Unresolved Git conflicts</h3>${badge(repo?.conflicts.length??'Unavailable')}</div><div class="row"><h3>Tracked files with uncommitted work</h3>${badge(repo?.changed.length??'Unavailable')}</div>`)+panel('Signal architecture and development principles',docList(docs.filter(d=>/signal_consumer|three_disciplines/.test(d.path))));
 }
 if(documentDetail&&['overview','history','merges','evaluations','signals'].includes(view))html+=panel(documentDetail.path,`<p>${esc(documentDetail.modified||'')}</p><pre style="white-space:pre-wrap;overflow-wrap:anywhere;max-height:65vh;overflow:auto">${esc(documentDetail.text||documentDetail.error)}</pre>`);
 const sourcesByView={overview:['development','meta'],roadmap:['development'],tickets:['development'],agents:['meta','development'],changes:['meta'],merges:['meta'],history:['meta'],evaluations:['meta'],signals:['meta','development'],runtime:['status','live','conflicts']};
 const sourcePaths={meta:'Autobuilder repository · Git, source files, decision records and saved test reports',development:'Local repository: .claude-memory/roadmap.md',status:'/autobuilder/status',live:'/autobuilder/live',goals:'/autobuilder/goals/tree',imports:'/autobuilder/inputs/imports',conflicts:'/autobuilder/inputs/conflicts',start:'/autobuilder/start-state'};
 const sources=sourcesByView[view];
 const available=sources.filter(k=>snapshot[k]?.ok).length;
 $('#mode').textContent=available===sources.length?'LIVE VIEW':available?'PARTIAL DATA':'DATA UNAVAILABLE';
 $('#subtitle').textContent=available===sources.length?'Autobuilder development evidence · refreshes every 5 seconds':available?'Some API sources are unavailable · retrying every 5 seconds':'This view cannot load its API sources · retrying every 5 seconds';
 const provenance=panel('Sources for this view',sources.map(k=>`<div class="row"><div><small>${esc(sourcePaths[k])}</small><p>${snapshot[k]?.ok?'Connected':esc(snapshot[k]?.error||'Waiting for first response')}</p></div>${badge(snapshot[k]?.ok?'Available':'Unavailable')}</div>`).join(''));
 if(!available)html=empty('Data is unavailable. This does not mean that the project has no records.');
 $('#app').innerHTML=provenance+html;
 document.querySelectorAll('[data-document]').forEach(b=>b.onclick=async()=>{const path=b.dataset.document;documentDetail={path,text:'Loading…'};render();try{const r=await fetch('/api/documents/'+encodeURIComponent(path),{cache:'no-store'}),v=await r.json();documentDetail=v.ok?v.data:{path,error:v.error}}catch(e){documentDetail={path,error:e.message}}render()});
 document.querySelectorAll('[data-ticket-link]').forEach(a=>a.onclick=()=>{ticketSearch=a.dataset.ticketLink;ticketStatus='All statuses';ticketTier='All tiers'});
 document.querySelectorAll('[data-tier]').forEach(a=>a.onclick=()=>{ticketTier=a.dataset.tier});
 document.querySelectorAll('[data-ticket]').forEach(b=>b.onclick=async()=>{const id=b.dataset.ticket;ticketDetail={id,text:'Loading…'};render();try{const r=await fetch('/api/tickets/'+encodeURIComponent(id),{cache:'no-store'}),v=await r.json();ticketDetail=v.ok?v.data:{id,error:v.error}}catch(e){ticketDetail={id,error:e.message}}render()});
 if($('#ticket-search')){
  $('#ticket-search').oninput=e=>{const cursor=e.target.selectionStart;ticketSearch=e.target.value;render();$('#ticket-search').focus();$('#ticket-search').setSelectionRange(cursor,cursor)};
  $('#ticket-status').onchange=e=>{ticketStatus=e.target.value;render()};
  $('#ticket-tier').onchange=e=>{ticketTier=e.target.value;render()};
 }

}
async function refresh(){if(busy)return;busy=true;$('#refresh').disabled=true;try{const r=await fetch('/api/snapshot',{cache:'no-store',signal:AbortSignal.timeout(8000)});if(!r.ok)throw Error(`HTTP ${r.status}`);snapshot=await r.json();updated=new Date()}catch(e){snapshot={status:{ok:false,error:`Cannot reach Observatory proxy: ${e.message}`}};updated=new Date()}finally{busy=false;$('#refresh').disabled=false;render()}}
$('#refresh').onclick=refresh;
$('#export').onclick=()=>{const a=document.createElement('a'),url=URL.createObjectURL(new Blob([JSON.stringify({checkedAt:updated,sources:snapshot},null,2)],{type:'application/json'}));a.href=url;a.download='autobuilder-live-snapshot.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};
window.onhashchange=render;render();refresh();setInterval(refresh,5000);
