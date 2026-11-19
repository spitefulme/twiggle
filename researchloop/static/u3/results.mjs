import {api,element,announce} from '../u1/api.mjs';
import {evidencePanel,protectedEvidenceAvailable} from '../u4/evidence.mjs';

const rateNames={'S/E':'Accepted location','R/E':'Correct resource reached',
  'D/E':'Direct success','I/E':'Indirect success','U/E':'Unknown directness',
  'S/F':'Accepted location among terminal attempts','R/F':'Correct resource among terminal attempts',
  'F/E':'Terminal attempts'};

function table(title,head,rows){
  const table=element('table',''),caption=element('caption',title),thead=element('thead',''),
    header=element('tr',''),body=element('tbody','');
  for(const label of head)header.append(element('th',label,{scope:'col'}));
  thead.append(header);
  for(const row of rows){
    const tr=element('tr','');
    for(const value of row)tr.append(element('td',String(value)));
    body.append(tr);
  }
  table.append(caption,thead,body);return table;
}

function treeOverview(result){
  const summary=element('section','',{class:'u4-tree-overview','aria-label':'Captured tree cohort overview'});
  const counts=element('dl','',{class:'u4-tree-counts'}),data=result.resultTables;
  for(const [title,value] of [
    ['Included attempts',result.cohortManifest.includedAttemptIds.length],
    ['Excluded attempts',Object.keys(result.cohortManifest.exclusionReasons).length],
    ['Terminal / exposed',data.counts.F+'/'+data.counts.E],
  ]){
    const fact=element('div','');fact.append(element('dt',title),element('dd',String(value)));counts.append(fact);
  }
  const content=element('div','',{class:'u4-tree-measures'}),bar=element('div','',{class:'u4-tree-overview-bar'});
  const actions=element('div','',{class:'u2-result-tabs','aria-label':'Tree overview presentation'});
  const chart=element('button','Chart',{type:'button'}),structured=element('button','Table',{type:'button'});
  actions.append(chart,structured);
  const values=[
    ['Accepted location (E)',data.rates['S/E']],
    ['Correct resource reached (E)',data.rates['R/E']],
    ['Direct success (E)',data.rates['D/E']],
    ['Gave up (F)',{numerator:data.destinations.find(row=>row.category==='give-up')?.count??0,denominator:data.counts.F}],
  ].map(([name,item])=>[name,item.numerator,item.denominator,item.denominator
    ? (item.numerator/item.denominator*100).toFixed(1)+'%' : 'Unavailable']);
  function draw(asChart){
    chart.setAttribute('aria-pressed',String(asChart));structured.setAttribute('aria-pressed',String(!asChart));
    if(!asChart){
      content.replaceChildren(table('Exact overview counts and rates',['Measure','Count / denominator','Rate'],
        values.map(([name,numerator,denominator,rate])=>[name,numerator+'/'+denominator,rate])));
      return;
    }
    const list=element('ul','',{class:'u4-tree-bars','aria-label':'Tree overview counts and rates'});
    for(const [name,numerator,denominator,rate] of values){
      const row=element('li',''),caption=element('div','');
      caption.append(element('strong',name),element('span',numerator+'/'+denominator+' · '+rate));
      const measure=element('meter','',{'aria-hidden':'true',value:String(denominator?numerator/denominator:0),min:'0',max:'1'});
      row.append(caption,measure);list.append(row);
    }
    content.replaceChildren(list);
  }
  chart.onclick=()=>draw(true);structured.onclick=()=>draw(false);draw(true);
  bar.append(element('h2','Outcome overview'),actions);
  summary.append(counts,bar,content,element('p','E: started task exposures. F: terminal tasks.',{class:'u4-evidence-context'}));
  return summary;
}

function report(data,task){
  const result=task?.tables??data.resultTables,container=element('section','',{class:'u3-result-detail'});
  const e=result.counts.E, f=result.counts.F;
  function panel(title,context,tableNode){
    const group=element('section','',{class:'u3-result-section'});
    group.append(element('h3',title),element('p',context,{class:'u3-results-muted'}),tableNode);
    return group;
  }
  function rates(keys){
    return keys.map(key=>{
      const row=result.rates[key];
      return [rateNames[key],row.numerator+'/'+row.denominator,row.value===null?'Unavailable':(row.value*100).toFixed(1)+'%'];
    });
  }
  const giveUp=result.destinations.find(item=>item.category==='give-up')?.count??0;
  const shorthand=(numerator,denominator)=>denominator?(numerator/denominator*100).toFixed(1)+'%':'Unavailable';
  container.append(element('h2','Tree-test results'),
    element('p',task?.prompt??'All tasks'),
    element('p','Cohort '+data.cohortManifest.cohortId,{class:'u3-results-muted'}),
    element('p','E · started task exposures: '+e+'; F · terminal tasks: '+f+'. Never started: '+result.neverStarted+'.',{class:'u3-results-muted'}),
    panel('Outcome','Counted independently: accepted location and correct resource.',
      table('Location and resource outcomes',['Measure','Count / base','Rate'],rates(['S/E','R/E','S/F','R/F']))),
    panel('Directness of successes','Backtracking takes precedence; not enough evidence is reported as unknown.',
      table('Directness per acknowledged exposure',['Measure','Count / base','Rate'],rates(['D/E','I/E','U/E']))),
    panel('Abandonment','A missing terminal decision is not the same as giving up.',
      table('Completion and abandonment',['Measure','Count / base','Rate'],[
        ...rates(['F/E']),['Gave up',giveUp+'/'+f,shorthand(giveUp,f)],
        ['Incomplete',(e-f)+'/'+e,shorthand(e-f,e)],
      ])),
    panel('First choice from the root','Includes exposures with no forward choice.',
      table('First choice',['Occurrence','Count','E'],result.firstChoice.map(row=>[row.category,row.count,row.denominatorE]))),
    panel('Destinations','Final choice and giving up are counted over terminal attempts.',
      table('Destinations and give-up',['Destination','Count','F'],result.destinations.map(row=>[row.category,row.count,row.denominatorF]))),
    panel('Observed routes','Exact acknowledged occurrence order, not reconstructed resource paths.',
      table('Routes',['Recorded occurrence sequence','Count'],result.routes.map(row=>[
        row.route.map(node=>node||'Root').join(' › ')||'No navigation',row.count]))),
    panel('Timing','Elapsed server time includes pauses and reloads.',
      table('Server elapsed time',['Outcome','Median / IQR (ms)','Measured / unavailable'],
      [['Selected',result.timing],['Gave up',result.giveUpTiming]].map(([label,row])=>
        [label,row.sampleSize?row.medianMs+' / '+row.iqrMs:'Unavailable',row.sampleSize+' / '+row.anomalies]))),
    element('p','Elapsed time includes pauses and reloads. Small samples, repeated sessions and attrition can limit conclusions.',{class:'help-panel'}));
  return container;
}

export function treeResults({studyId,getVersion,isCurrent,reauthorize,button}){
  const shell=element('section','',{class:'tree-results'});
  const output=element('div','',{role:'region','aria-label':'Tree results detail'});
  let generation=0;
  const base=version=>'/studies/'+studyId+'/versions/'+version+'/tree-v2';
  async function request(start){
    const version=getVersion(),run=++generation;
    if(!version)throw new Error('Choose a published version');
    const current=()=>isCurrent()&&getVersion()===version&&run===generation;
    output.replaceChildren(element('p',start?'Requesting analysis…':'Loading results…',{role:'status'}));
    try {
      await reauthorize();if(!current())return;
      const prefix=base(version);
      let job=start?await api(prefix+'/analysis-jobs',{method:'POST',data:{}}):null;
      if(!current())return;
      if(job){
        for(let position=0;position<34&&['queued','running','stalled'].includes(job.state);position++){
          output.replaceChildren(element('p','Analysis '+job.state+' · '+job.progress+'%',{role:'status'}));
          await new Promise(resolve=>setTimeout(resolve,1000));
          if(!current())return;
          job=await api(prefix+'/analysis-jobs?jobId='+encodeURIComponent(job.jobId));
          if(!current())return;
        }
        if(job.state!=='succeeded')throw new Error(job.errorCode??'Analysis not ready');
      }
      const result=await api(prefix+'/results'+(job?'?jobId='+encodeURIComponent(job.jobId):''));
      await reauthorize();if(!current())return;
      if(result.versionId!==version)throw new Error('Unexpected result version');
      if(!result.resultTables)output.replaceChildren(element('p','Analysis '+result.state,{role:'status'}));
      else {
        const label=element('label','Task detail');
        const picker=element('select','',{'aria-label':'Task detail'});
        picker.append(element('option','All tasks',{value:''}));
        for(const task of result.taskResults??[])picker.append(element('option',task.prompt,{value:task.taskId}));
        const tables=element('div','');
        picker.onchange=()=>tables.replaceChildren(report(result,(result.taskResults??[]).find(task=>task.taskId===picker.value)));
        label.append(picker);output.replaceChildren(label,tables);picker.onchange();
        if(await protectedEvidenceAvailable()&&current())output.prepend(
          evidencePanel({studyId,versionId:version,jobId:job?.jobId,
            cohortId:result.cohortManifest.cohortId,isCurrent:current,reauthorize,overview:treeOverview(result)}));
      }
    }catch(error){
      if(current()){
        output.replaceChildren(element('p','No results delivered.',{role:'status'}));
        announce(error.message,true);
      }
    }
  }
  shell.append(button('Run tree analysis','u3-analysis-run',()=>request(true),'primary'),
    button('Load tree results','u3-analysis-load',()=>request(false)),output);
  return shell;
}
