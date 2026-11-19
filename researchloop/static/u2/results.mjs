import {api,element} from '../u1/api.mjs';
import {evidencePanel,protectedEvidenceAvailable} from '../u4/evidence.mjs';

// Each view owns a generation; no response may cross version/member/navigation.
export function cardResults({studyId,getVersion,isCurrent,reauthorize,versionControl}){
 const root=element('section','',{class:'u2-results','aria-label':'Card sort analysis'});
 let generation=0,detailGeneration=0,resultGeneration=0,analysisCurrent=false,result=null,metric='immediate',tab='overview',pendingAnnotation=null,annotationBusy=false;
 const controls=element('div','',{class:'u2-result-controls'}),metricLabel=element('label','Similarity metric'),metricSelect=element('select','',{'aria-label':'Similarity metric'});
 for(const [value,label]of [['immediate','Same immediate group'],['ancestor','Shared explicit ancestor']])metricSelect.append(element('option',label,{value}));
 metricLabel.append(metricSelect);
 const status=element('p','Choose a published version, then run analysis.',{'data-testid':'analysis-status',role:'status'}),summary=element('div','',{class:'u2-cohort-summary','data-testid':'analysis-summary'});
 const workspace=element('div','',{class:'u2-results-workspace'}),rail=element('aside','',{class:'u2-result-filters'}),main=element('div','',{class:'u2-result-main'}),inspector=element('aside','',{class:'u2-result-inspector','aria-label':'Selected pair inspector'});
 const evidenceFilters=element('div','',{class:'u4-card-filters'}),right=element('div','',{class:'u4-card-right'}),draftHost=element('section','',{class:'u4-card-draft'});
 function clearU4(){right.querySelector('.u4-evidence')?.remove();if(right.contains(inspector))right.replaceWith(inspector);evidenceFilters.replaceChildren();draftHost.remove();draftHost.replaceChildren();}
 function resetInspector(){inspector.replaceChildren(element('h2','Raw label inspector'),element('p','Select a similarity pair to inspect its original response labels. Coding never replaces raw evidence.',{class:'muted'}));for(const button of root.querySelectorAll('[data-pair-key]'))button.setAttribute('aria-pressed','false');}
 resetInspector();
 const body=element('div'),tabs=element('div','',{class:'u2-result-tabs','aria-label':'Results view'}),recovery=element('div','',{class:'help-panel','data-testid':'annotation-recovery'});recovery.hidden=true;
 const denied=error=>['UNAUTHORIZED','FORBIDDEN','NOT_FOUND','COHORT_STALE','ERASURE_PENDING','ANALYSIS_ERASURE_PENDING','ANALYSIS_FENCED','SUPPRESSED','GOVERNANCE_UNAVAILABLE'].includes(error.problem?.code);
 function invalidateEvidence(error){pendingAnnotation=null;analysisCurrent=false;result=null;generation++;detailGeneration++;resultGeneration++;body.replaceChildren();summary.replaceChildren();resetInspector();root.querySelector('.u2-cohort-explanation')?.remove();clearU4();syncAnnotationLock();status.textContent=error.message+' — previous evidence cleared. Reload or choose an authorized version before viewing evidence.';}
 function action(text,id,handler,style='secondary'){const b=element('button',text,{type:'button',class:style,'data-testid':id});b.onclick=async()=>{try{await handler();}catch(error){try{await reauthorize();}catch{return;}if(isCurrent()&&root.isConnected){if(denied(error)){invalidateEvidence(error);return;}status.textContent=error.message+' — input retained. Retry the exact action if its acknowledgement was lost.';}}};return b;}
 async function authenticatedRead(path){const payload=await api(path);await reauthorize();return payload;}
 const run=action('Run analysis','analysis-run',async()=>{
  const versionId=getVersion();if(!versionId)throw new Error('Publish or select a version first');
  const request=++generation,valid=()=>isCurrent()&&root.isConnected&&getVersion()===versionId&&request===generation;
  detailGeneration++;resultGeneration++;analysisCurrent=false;resetInspector();summary.replaceChildren();clearU4();if(tab==='overview')body.replaceChildren(element('p','Computing a new captured cohort. No current analysis displayed.'));
  try{
  status.textContent='Requesting analysis…';
  const job=await api('/studies/'+studyId+'/analysis-jobs',{method:'POST',data:{versionId,metric}});
  if(!valid())return;await reauthorize();if(!valid())return;
  const deadline=Date.now()+31000;
  while(valid()){
   if(!job.statusLocation.startsWith('/api/v1/studies/'+studyId+'/analysis-jobs/'))throw new Error('Analysis status identity mismatch');
   const current=await api(job.statusLocation.slice('/api/v1'.length));if(!valid())return;
   if(current.state==='succeeded'){
    const payload=await api('/studies/'+studyId+'/results?versionId='+versionId+'&jobId='+job.jobId);if(!valid())return;
    await reauthorize();if(!valid())return;
    if(payload.studyId!==studyId||payload.versionId!==versionId||payload.cohort.cohortId!==job.cohortId)throw new Error('Analysis identity mismatch');
    result=payload;resultGeneration++;detailGeneration++;analysisCurrent=true;resetInspector();status.textContent='Ready — captured revision '+payload.evidenceRevision;await render();
    clearU4();
    if(await protectedEvidenceAvailable()&&valid()&&analysisCurrent&&result===payload){
      inspector.replaceWith(right);right.append(inspector,evidencePanel({studyId,versionId,jobId:job.jobId,cohortId:payload.cohort.cohortId,
        isCurrent:()=>isCurrent()&&analysisCurrent&&result===payload&&getVersion()===versionId,reauthorize,filterHost:evidenceFilters,draftHost}));main.append(draftHost);
    }
    return;
   }
   status.textContent=current.state==='stalled'?'Worker unavailable — analysis stalled. Retry after the local worker recovers.':current.state+' — '+current.progress+'%';
   if(['failed','cancelled','stalled'].includes(current.state))return;
   if(Date.now()>=deadline){status.textContent='Analysis deadline reached. No completed result claimed.';return;}
   await new Promise(resolve=>setTimeout(resolve,1000));
  }
  }catch(error){if(valid())throw error;}
 },'primary');
 const annotationRetry=action('Retry exact annotation','annotation-retry',async()=>{const pending=pendingAnnotation;if(pending)await annotate(pending.data,pending.revision,pending.message);});
 recovery.append(element('p','The original annotation input is retained. Resolve this outcome before another change.'),annotationRetry);
 function syncAnnotationLock(){const locked=pendingAnnotation!==null;body.inert=locked;metricSelect.disabled=locked;run.disabled=locked;const selector=versionControl.querySelector('select');if(selector)selector.disabled=locked;for(const button of tabs.children)button.disabled=locked;recovery.hidden=!locked;annotationRetry.disabled=annotationBusy;annotationRetry.textContent=pendingAnnotation?.phase==='acknowledged'?'Retry saved annotation view':'Retry exact annotation';}
 metricSelect.onchange=()=>{generation++;detailGeneration++;resultGeneration++;analysisCurrent=false;metric=metricSelect.value;result=null;resetInspector();body.replaceChildren();summary.replaceChildren();clearU4();status.textContent='Metric changed — run analysis for a new captured cohort.';};
 controls.append(metricLabel,run);rail.append(versionControl,controls,status,recovery,evidenceFilters,summary);workspace.append(rail,main,inspector);main.append(tabs,body);root.append(workspace);
 for(const name of ['Overview','Raw responses'])tabs.append(action(name,'results-'+name.toLowerCase().replaceAll(' ','-'),async()=>{tab=name==='Overview'?'overview':'raw';await render();}));
 function captured(){return result&&isCurrent()&&getVersion()===result.versionId;}
 async function annotate(data,revision,message){
  if(annotationBusy)return;
  if(pendingAnnotation&&JSON.stringify([pendingAnnotation.revision,pendingAnnotation.data])!==JSON.stringify([revision,data]))throw new Error('Resolve the original pending annotation before changing its input');
  pendingAnnotation??={data:structuredClone(data),revision,message,phase:'unacknowledged'};
  const pending=pendingAnnotation;annotationBusy=true;
  generation++;detailGeneration++;resultGeneration++;resetInspector();
  analysisCurrent=false;summary.replaceChildren();root.querySelector('.u2-cohort-explanation')?.remove();clearU4();syncAnnotationLock();root.setAttribute('aria-busy','true');status.textContent=pending.phase==='acknowledged'?'Refreshing saved annotation…':'Saving annotation…';
  try{if(pending.phase!=='acknowledged'){await api('/studies/'+studyId+'/analysis-annotations',{method:'POST',revision:pending.revision,data:pending.data});pending.phase='acknowledged';}if(!captured())return;await raw();if(!captured())return;pendingAnnotation=null;status.textContent=pending.message+' Run analysis again for a new captured cohort.';}
  catch(error){try{await reauthorize();}catch{return;}if(!isCurrent()||!root.isConnected)return;if(denied(error)){invalidateEvidence(error);return;}if(error.problem?.status>=400&&error.problem.status<500){pendingAnnotation=null;throw error;}status.textContent=pending.phase==='acknowledged'?'Saved on server; view refresh failed. Retry the saved annotation view without sending another change.':'Annotation outcome unknown; retry the exact original request.';}
  finally{annotationBusy=false;root.removeAttribute('aria-busy');syncAnnotationLock();if(isCurrent()&&root.isConnected){if(pendingAnnotation)annotationRetry.focus();else body.querySelector('button')?.focus();}}
 }
 async function render(){
  if(!captured())return;
  summary.replaceChildren(...(analysisCurrent?['included','excluded','completion']:[]).map(key=>{const cell=element('div');cell.append(element('strong',key==='completion'?result.counts.submitted+'/'+result.counts.started:String(result.counts[key])),element('span',' '+(key==='completion'?'completed':key)));return cell;}));
  const explanation=element('details','',{class:'u2-cohort-explanation'});explanation.append(element('summary','Cohort rules and counts'),element('p',result.populationExplanation,{class:'muted'}),element('p',result.counts.partial+' partial · '+result.counts.invalid+' invalid'),element('p','Eligible completed responses only; preview and researcher-excluded responses do not enter clustering.',{class:'muted'}));
  rail.replaceChildren(versionControl,controls,status,recovery,evidenceFilters,summary,explanation);
  for(const b of tabs.children)b.setAttribute('aria-pressed',String((tab==='overview')===(b.textContent==='Overview')));
  body.replaceChildren();if(tab==='raw'){await raw();return;}
  if(!analysisCurrent){body.append(element('p','Annotations changed — run analysis for the current cohort.'));return;}
  const heading=element('div','',{class:'title-row'});heading.append(element('h2','Similarity matrix'),element('span',result.counts.included+' included',{class:'pill'}));body.append(heading);
  const metricExplanation=element('p',metric==='immediate'?'Fraction of included responses placing each pair in the same immediate group.':'Fraction sharing an explicit ancestor; the synthetic root is excluded.',{class:'muted'});body.append(metricExplanation);
  const chart=element('section','',{'data-testid':'analysis-chart',class:'u2-analysis-chart'});
  if(!result.dendrogram)chart.append(element('p','No clustering — at least two cards and one included response are required.'));
  else{
   chart.append(element('h3','Average-linkage clustering'),element('p',result.dendrogram.interpretation,{class:'muted'}));
   const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg'),height=Math.max(120,result.cardIds.length*32+40),positions=new Map();
   svg.setAttribute('viewBox','0 0 640 '+height);svg.setAttribute('role','img');svg.setAttribute('aria-label','Average-linkage dendrogram; exact merge distances are listed below');svg.dataset.testid='dendrogram';
   const title=document.createElementNS(ns,'title');title.textContent='Average-linkage dendrogram using '+metric+' similarity';svg.append(title);
   for(const [index,id]of result.cardIds.entries()){
    const y=index*32+28;positions.set('card:'+id,{x:240,y});
    const text=document.createElementNS(ns,'text');text.setAttribute('x','0');text.setAttribute('y',String(y+5));text.textContent=label(id).length>28?label(id).slice(0,27)+'…':label(id);const full=document.createElementNS(ns,'title');full.textContent=label(id);text.append(full);svg.append(text);
   }
   const list=element('ol');for(const node of result.dendrogram.nodes.filter(node=>node.leftNodeId)){
    const left=positions.get(node.leftNodeId),right=positions.get(node.rightNodeId),x=250+node.distance*360,y=(left.y+right.y)/2;
    const path=document.createElementNS(ns,'path');path.setAttribute('d',`M ${left.x} ${left.y} H ${x} V ${right.y} H ${right.x}`);path.setAttribute('fill','none');path.setAttribute('stroke','#557608');path.setAttribute('stroke-width','2');svg.append(path);positions.set(node.nodeId,{x,y});
    list.append(element('li',node.memberCardIds.map(label).join(' + ')+' · distance '+node.exactDistance.numerator+'/'+node.exactDistance.denominator));
   }chart.append(svg,list);
  }
  const table=element('table','',{'data-testid':'similarity-table',class:'u2-similarity-table'}),caption=element('caption','Similarity · percent and exact numerator / denominator');table.append(caption);
  if(!result.counts.included)table.append(element('tbody','Unavailable — 0 included responses'));
  else{
   const head=element('tr');head.append(element('th','Card',{scope:'col'}));for(const id of result.cardIds)head.append(element('th',label(id),{scope:'col'}));table.append(element('thead'));table.tHead.append(head);
   const rows=element('tbody');for(const left of result.cardIds){
    const row=element('tr');row.append(element('th',label(left),{scope:'row'}));
    for(const right of result.cardIds){
     const cell=element('td');if(left===right)cell.textContent='—';else{
      const value=result[metric==='immediate'?'immediateSimilarity':'ancestorSimilarity'].find(pair=>pair.leftCardId===left&&pair.rightCardId===right||pair.leftCardId===right&&pair.rightCardId===left);
      if(value){const visible=Math.round(value.value*100)+'% · '+value.numerator+'/'+value.denominator,b=action(visible,'pair-'+left+'-'+right,()=>inspectPair(left,right,value));
       b.dataset.pairKey=JSON.stringify([left,right].sort());b.setAttribute('aria-pressed','false');b.setAttribute('aria-label',visible+' — '+label(left)+' and '+label(right));cell.append(b);cell.style.backgroundColor=`hsl(86 48% ${98-value.value*34}%)`;
      }
     }row.append(cell);
    }rows.append(row);
   }table.append(rows);
  }
  const scroller=element('div','',{class:'u2-matrix-scroll',tabindex:'0','aria-label':'Similarity matrix scroll region'});scroller.append(table);
  const switcher=element('div','',{class:'u2-result-tabs','aria-label':'Similarity presentation'});
  const chartButton=action('Chart','similarity-chart',async()=>{chart.hidden=false;scroller.hidden=true;chartButton.setAttribute('aria-pressed','true');tableButton.setAttribute('aria-pressed','false');});
  const tableButton=action('Table','similarity-table-tab',async()=>{chart.hidden=true;scroller.hidden=false;chartButton.setAttribute('aria-pressed','false');tableButton.setAttribute('aria-pressed','true');});
  chartButton.setAttribute('aria-pressed','false');tableButton.setAttribute('aria-pressed','true');chart.hidden=true;switcher.append(chartButton,tableButton);body.append(switcher,scroller,chart);
 }
 function label(id){const card=result.cards.find(card=>card.cardId===id);const raw=card?.label??id;return result.cards.filter(card=>card.label===raw).length>1?raw+' ['+id+']':raw;}
 async function inspectPair(left,right,value){
  if(!captured()||!analysisCurrent)return;const selected=result,request=++detailGeneration,epoch=resultGeneration,selectedMetric=metric;
  const matches=()=>captured()&&analysisCurrent&&result===selected&&request===detailGeneration&&epoch===resultGeneration&&metric===selectedMetric;
  let pair;try{pair=await authenticatedRead('/studies/'+studyId+'/analysis-pairs?'+new URLSearchParams({versionId:selected.versionId,cohortId:selected.cohort.cohortId,leftCardId:left,rightCardId:right}));}catch(error){if(matches())throw error;return;}
  if(!matches())return;
  if(pair.versionId!==selected.versionId||pair.cohortId!==selected.cohort.cohortId||pair.metric!==selectedMetric||pair.leftCardId!==left||pair.rightCardId!==right)throw new Error('Pair identity mismatch');
  if(pair.numerator!==value.numerator||pair.denominator!==value.denominator)throw new Error('Captured pair evidence mismatch');
  for(const button of body.querySelectorAll('[data-pair-key]'))button.setAttribute('aria-pressed',String(button.dataset.pairKey===JSON.stringify([left,right].sort())));
  inspector.replaceChildren(element('h2','Raw label inspector'),element('h3',label(left)+' ↔ '+label(right)),element('p',value.numerator+' / '+value.denominator+' included responses · '+metric));
  const list=element('ul','',{'data-testid':'pair-raw-labels'});for(const entry of pair.rawLabels)list.append(element('li',entry.label+' · '+entry.count));if(!pair.rawLabels.length)list.append(element('li','No included response groups this pair together.'));inspector.append(list,element('p','Original labels, not coding replacements. Ancestor labels use the deepest shared explicit ancestor.',{class:'muted'}),action('Inspect and code responses','pair-code',async()=>{tab='raw';await render();body.scrollIntoView({block:'start'});}));
 }
 async function raw(){
  const versionId=getVersion(),request=++detailGeneration,epoch=resultGeneration;
  const matches=()=>tab==='raw'&&captured()&&request===detailGeneration&&epoch===resultGeneration&&getVersion()===versionId;
  let index;try{index=await authenticatedRead('/studies/'+studyId+'/analysis-evidence?versionId='+versionId);}catch(error){if(matches())throw error;return;}
  if(!matches())return;
  if(index.versionId!==versionId)throw new Error('Raw index version mismatch');
  body.replaceChildren();const list=element('div','',{'data-testid':'raw-responses'});body.append(list);
  if(!index.rows.length){list.append(element('p','No participant evidence for this version.'));return;}
  const filter=element('select','',{'aria-label':'Response status'});for(const value of ['all','started','partial','submitted'])filter.append(element('option',value,{value}));body.prepend(filter);
  function entries(){list.replaceChildren();for(const row of index.rows.filter(row=>filter.value==='all'||row.status===filter.value)){
   const detail=element('article','',{class:'evidence-row'});detail.append(element('h3',row.status+' · '+row.structuralStatus),element('p',row.included?'Included in eligible cohort':row.exclusionReason??'Not included in clustering',{class:'muted'}));
   detail.append(action('Inspect original response','inspect-'+row.evidenceId,async()=>{
    const inspection=++detailGeneration;let original;try{original=await authenticatedRead('/studies/'+studyId+'/analysis-evidence/'+row.evidenceId+'?versionId='+versionId);}catch(error){if(captured()&&epoch===resultGeneration&&inspection===detailGeneration)throw error;return;}if(!captured()||epoch!==resultGeneration||inspection!==detailGeneration)return;
    if(original.versionId!==versionId||original.evidenceId!==row.evidenceId||original.responseId!==row.responseId)throw new Error('Raw response identity mismatch');
    const panel=element('div','',{class:'u2-raw-detail'});panel.append(element('p','Original evidence · revision '+original.stateRevision));
    const originalGroups=original.groups.filter(group=>group&&typeof group==='object'&&typeof group.groupId==='string'&&typeof group.label==='string');
    if(original.structuralStatus==='malformed')panel.append(element('p','Malformed historical evidence — excluded, displayed without repair.'),element('pre',JSON.stringify(original.rawState,null,2)));
    else{
     const table=element('table','',{'data-testid':'raw-sort-memberships',class:'u2-similarity-table'});table.append(element('caption','Original ordered card placements — no coding replacements'));
     const head=element('tr');for(const name of ['Card','Original group path','Position'])head.append(element('th',name,{scope:'col'}));const thead=element('thead');thead.append(head);table.append(thead);const tbody=element('tbody');
     for(const placement of [...original.rawState.placements].sort((a,b)=>(a.groupId??'').localeCompare(b.groupId??'')||a.order-b.order)){
      const path=[],seen=new Set();let id=placement.groupId;while(id!==null&&!seen.has(id)){seen.add(id);const group=originalGroups.find(group=>group.groupId===id);if(!group)break;path.unshift(group.label);id=group.parentGroupId??null;}
      const tr=element('tr');tr.append(element('th',label(placement.cardId),{scope:'row'}),element('td',path.join(' › ')||'Unsorted'),element('td',String(placement.order+1)));tbody.append(tr);
     }table.append(tbody);const scroll=element('div','',{class:'u2-matrix-scroll',tabindex:'0','aria-label':'Original sort placements'});scroll.append(table);panel.append(scroll);
    }
    for(const group of originalGroups){const field=element('label','Original: '+group.label),input=element('input','',{'aria-label':'Coding label for '+group.label});input.value=group.displayLabel??group.label;field.append(input);panel.append(field);if(original.codingAvailable){for(const active of [true,false])panel.append(action(active?'Apply coding':'Reverse coding','coding-'+active+'-'+group.groupId,async()=>{await annotate({versionId,kind:'coding',responseId:row.responseId,groupId:group.groupId,label:input.value,active},original.annotationRevision,active?'Coding saved.':'Coding reversed.');}));}}
    const history=element('ol','',{'aria-label':'Annotation history'});for(const event of original.annotationHistory)history.append(element('li','Revision '+event.revision+' · '+event.kind+' · '+(event.active?'applied':'reversed')+' · '+event.occurredAt));panel.append(history);detail.querySelector('.u2-raw-detail')?.remove();detail.append(panel);
   }));
   if(row.responseId&&row.status==='submitted'&&row.structuralStatus==='valid'){const reason=element('input','',{'aria-label':'Exclusion reason'});reason.value=row.exclusionReason??'Researcher review';detail.append(reason,action(row.included?'Exclude response':'Include response','exclusion-'+row.evidenceId,async()=>{await annotate({versionId,kind:'exclusion',responseId:row.responseId,reason:reason.value,active:row.included},index.annotationRevision,'Exclusion saved.');}));}list.append(detail);
  }}filter.onchange=entries;entries();
 }
 return root;
}
