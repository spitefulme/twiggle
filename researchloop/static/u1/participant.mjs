import {api,announce,element,hasPendingMutation} from './api.mjs';
import {SaveQueue} from './save-state.mjs';
import {sortWorkspace as fullSortWorkspace,groupPath} from '../u2/sort.mjs';
import {destinations} from '../u2/destinations.mjs';
import {renderTree} from '../u3/participant.mjs';
const area=document.getElementById('participant'),dialog=document.getElementById('move-dialog'),destination=document.getElementById('move-destination'),queue=new SaveQueue(message=>announce(message));
const kind=location.pathname.split('/')[1];let token=location.pathname.split('/')[2],session=null,reviewing=false,moving=null,moveTrigger=null;
const collapsedGroups=new Set();
let sortExpansionInitialized=false;
let lastUIChange=null;
let acknowledgedChange=null;
let treeIntent=null,treeRunning=false;
async function treeAction(operationKind,payload){
 if(treeRunning)return;
 const path='/participant/'+token+'/tree-v2', identity=session.sessionId;
 if(treeIntent&&treeIntent.identity!==identity)throw new Error('Session changed; reload to resume safely');
 treeIntent??={identity,key:crypto.randomUUID(),data:{sequence:session.revision+1,operationKind,payload},acknowledged:false};
 treeRunning=true;
 announce(treeIntent.acknowledged?'Saved on server — updating view':'Pending — not saved');
 try{
  if(!treeIntent.acknowledged){
   await api(path+'/events',{method:'POST',key:treeIntent.key,data:treeIntent.data});
   treeIntent.acknowledged=true;
  }
  const updated=await api(path);
  if(updated.sessionId!==identity||updated.revision<treeIntent.data.sequence)throw new Error('Updated state did not acknowledge this action');
  session=updated;treeIntent=null;render();area.querySelector('h2')?.focus();announce('Saved');
 }catch(error){
  const known=error.problem?.status>=400&&error.problem?.status<500;
  const committed=treeIntent.acknowledged;
  if(known&&!committed)treeIntent=null;
  render();
  announce(committed?'Saved on server — retry to refresh':known?'Action not saved — '+error.message:navigator.onLine?'Outcome unknown — retry the exact action':'Offline — outcome unknown',true);
 }finally{treeRunning=false;}
}
const pendingOperation=()=>!!acknowledgedChange||hasPendingMutation('/participant/'+token);
const guard=async action=>{try{document.getElementById('error').hidden=true;await action();}catch(e){announce(e.message+' — no submission has been claimed.',true);}};
function control(text,id,handler,style='secondary'){const button=element('button',text,{'data-testid':id,type:'button',class:style});button.onclick=()=>guard(handler);return button;}
function focusCard(cardId){const legacy=[...area.querySelectorAll('[data-card-id]')].find(node=>node.dataset.cardId===cardId&&node.getClientRects().length);(legacy??[...area.querySelectorAll('[data-element-id]')].find(node=>node.dataset.elementId===cardId))?.focus();}
async function refresh(){const active=document.activeElement,label=active?.getAttribute('aria-label'),id=active?.dataset.testid;session=await api('/participant/'+token);render();if(id){const controls=[...area.querySelectorAll('[data-testid]')];const target=controls.find(control=>label?control.getAttribute('aria-label')===label:control.dataset.testid===id)??controls.find(control=>['tree-select','task-start','participant-review'].includes(control.dataset.testid));target?.focus();}}
async function operate(operationKind,payload){return await queue.run(async()=>{
 const intent=JSON.stringify([operationKind,payload]);
 if(acknowledgedChange&&acknowledgedChange.intent!==intent)throw Object.assign(new Error('The previous change is saved. Retry its refresh or reload before another change.'),{savedOnServer:true});
 if(!acknowledgedChange){
  const prior=session.participantView,acknowledgement=await api('/participant/'+token+'/operations',{method:'POST',data:{sequence:session.revision+1,operationKind,payload}});
  session.revision=acknowledgement.revision;
  lastUIChange={revision:acknowledgement.revision,operationKind,payload:structuredClone(payload),sourceLabel:[...(prior.cards??[]),...(prior.groups??[])].find(row=>(row.cardId??row.groupId)===(payload.elementId??payload.cardId??payload.groupId))?.label??'item',parentLabel:prior.groups?.find(g=>g.groupId===payload.parentGroupId)?.label,priorGroupIds:(prior.groups??[]).map(g=>g.groupId)};
  acknowledgedChange={intent,revision:acknowledgement.revision};
 }
 try{await refresh();acknowledgedChange=null;return operationKind.startsWith('create-')?session.participantView.groups.find(group=>!lastUIChange.priorGroupIds.includes(group.groupId))?.groupId:null;}catch(error){throw Object.assign(error,{savedOnServer:true});}
});}
function header(title,progress=''){const head=element('header','',{class:'study-header'}),titles=element('div');titles.append(element('p',session?.method?.startsWith('tree-test')?'Navigation study':'Card sorting study',{class:'eyebrow'}),element('h1',title,{tabindex:'-1'}));head.append(titles,element('span',progress,{class:'progress','data-testid':'placement-progress'}));return head;}
function footer(text,id,action,hint=''){const foot=element('footer','',{class:'task-footer participant-footer'});foot.append(element('span',hint,{class:'muted'}),control(text,id,action,'primary'));return foot;}
function complete(){area.replaceChildren(header('Study complete'));const panel=element('section','',{class:'completion-panel'});panel.append(element('div','✓',{class:'completion-icon','aria-hidden':'true'}),element('h2','Response submitted'),element('p','Your committed response is recorded. Thank you.'),element('div','You can now close this tab. Your submitted response cannot be changed.',{class:'help-panel'}));area.append(panel);}
function openMove(card,trigger){document.getElementById('move-cancel').textContent='Cancel';destination.disabled=false;document.getElementById('move-confirm').textContent='Move card';moving=card;moveTrigger=trigger;document.getElementById('move-title').textContent='Move “'+card.label+'”';destination.replaceChildren();for(const target of destinations(session.participantView.groups,{source:{kind:'card',id:card.cardId},action:'move',ranks:session.participantView.groupOrder})){const option=element('option',target.parent===null?'Unsorted':target.label,{value:target.parent??''});option.disabled=!!target.reason;destination.append(option);}destination.value=session.participantView.membership[card.cardId]??'';dialog.showModal();destination.focus();}
function cardRow(card){const row=element('div','',{class:'sort-card'}),move=control('Move to…','card-move',async()=>openMove(card,move),'move-button');move.setAttribute('aria-label','Move '+card.label);move.dataset.cardId=card.cardId;row.append(element('span',card.label,{class:'card-label'}),move);return row;}
function sortWorkspace(){
 const view=session.participantView,placed=view.cards.filter(c=>view.membership[c.cardId]).length;
 if(!sortExpansionInitialized){
  if(view.mode!=='closed'){const firstOwned=view.groups.find(g=>g.owner==='participant'&&!g.parentGroupId);
   for(const group of view.groups)if(!group.parentGroupId&&group.groupId!==firstOwned?.groupId)collapsedGroups.add(group.groupId);}
  sortExpansionInitialized=true;
 }
 area.append(header('Sort the cards',placed+' of '+view.cards.length+' placed'));
 const instructions=element('details','',{class:'u2-sort-instructions','data-testid':'fixed-group-help'});
 instructions.append(element('summary','Instructions and sorting rules'),element('p',view.instructionText),
 element('p',view.mode==='closed'?'Groups are set by the researcher. Move cards only; fixed groups cannot be changed.':
 view.mode==='hybrid'?'Create and move your own groups, including subgroups beneath fixed groups. Fixed groups themselves cannot be changed.':'Create and arrange your own groups.'));
 area.append(instructions,fullSortWorkspace({view,operate,openMove,control,pending:pendingOperation,focusCard,collapsed:collapsedGroups,lastChange:lastUIChange?.revision===session.revision?lastUIChange:null}),
 footer('Review and submit','participant-review',async()=>{await queue.tail;if(queue.failed)throw queue.failed;reviewing=true;render();area.querySelector('h2')?.focus();},
 'Drag a handle, or use Move to… / Create group. Keyboard: Space · arrows · Enter · Escape.'));
}
function review(){area.append(header('Review your response'));const panel=element('section','',{class:'review-panel'}),view=session.participantView;panel.append(element('h2','Ready to submit?',{tabindex:'-1'}));if(session.method==='card-sort'){const missing=view.cards.filter(c=>!view.membership[c.cardId]);if(missing.length)panel.append(element('p',missing.length+' cards are still unsorted. Go back to place them.',{class:'help-panel'}));for(const group of view.groups){panel.append(element('h3',group.label));for(const card of view.cards.filter(c=>view.membership[c.cardId]===group.groupId))panel.append(element('p',card.label));}}else panel.append(element('p','Your saved task actions will be submitted.'));panel.append(control('Back to study','review-back',async()=>{reviewing=false;render();}));area.append(panel,footer('Submit response','participant-submit',async()=>{await queue.tail;if(queue.failed)throw queue.failed;await api('/participant/'+token+'/submit',{method:'POST',data:{expectedRevision:session.revision}});session.status='submitted';render();announce('Submitted');},'Submission records your acknowledged response once.'));}
function treeWorkspace(){const view=session.participantView,task=view.workingState.tasks.find(t=>t.terminalCategory==='incomplete');area.append(header('Find a destination','Task 1 of 1'));const panel=element('section','',{class:'tree-panel'});if(task){panel.append(element('p',view.instructionText,{class:'muted'}),element('h2',view.currentTask.prompt,{tabindex:'-1'}));if(!task.startedAt)panel.append(control('Start task','task-start',()=>operate('task-start',{}),'primary'));else{panel.append(element('p',task.current?'Current placement · '+task.current:'At the root of the navigation',{class:'eyebrow','data-testid':'tree-current'}));const navigation=element('div','',{class:'tree-navigation'});for(const node of view.rootOccurrences)navigation.append(control('Open '+node.label,'tree-enter',()=>operate('child-entry',{occurrenceId:node.occurrenceId}),'text-button'));if(!view.rootOccurrences.length)navigation.append(element('p','No further branches',{class:'muted'}));panel.append(navigation);if(task.current)panel.append(control('Back to root','tree-back',()=>operate('ancestor-return',{occurrenceId:null}),'text-button'),control('Choose this destination','tree-select',()=>operate('destination-selected',{occurrenceId:task.current}),'primary'));panel.append(control('Give up this task','tree-give-up',()=>operate('give-up',{}),'text-button'));}}else panel.append(element('h2','Task completed'),element('p','Review your saved task response before submitting.'));area.append(panel);if(!task)area.append(footer('Review and submit','participant-review',async()=>{reviewing=true;render();}));}
function render(){document.body.dataset.method=session.method;area.replaceChildren();if(session.status==='submitted'){complete();return;}if(reviewing){if(session.method==='tree-test-v2'){area.append(header('Review your response'),element('p','All tasks are saved.'),footer('Submit response','participant-submit',async()=>{await api('/participant/'+token+'/submit',{method:'POST',data:{expectedRevision:session.revision}});session.status='submitted';render();announce('Submitted');}));return;}review();return;}if(session.method==='card-sort')sortWorkspace();else if(session.method==='tree-test-v2')renderTree({area,session,header,control,act:treeAction,pending:treeIntent,recover:()=>treeAction(),review:async()=>{reviewing=true;render();}});else treeWorkspace();}
function cancelMove(){if(hasPendingMutation('/participant/'+token))throw new Error('This move may already be committed. Retry this exact move to confirm it, or reload to resume server state');dialog.close();}
document.getElementById('move-cancel').onclick=()=>{if(hasPendingMutation('/participant/'+token)){location.reload();return;}guard(cancelMove);};
dialog.addEventListener('cancel',event=>{if(hasPendingMutation('/participant/'+token)){event.preventDefault();guard(cancelMove);}});
dialog.onclose=()=>{if(moveTrigger?.isConnected)moveTrigger.focus();else if(moving)focusCard(moving.cardId);};
document.getElementById('move-confirm').onclick=()=>guard(async()=>{const card=moving,target=destination.value||null;document.getElementById('move-confirm').disabled=true;try{await operate('move-card',{cardId:card.cardId,targetGroupId:target});dialog.close();focusCard(card.cardId);}finally{document.getElementById('move-confirm').disabled=false;const pending=hasPendingMutation('/participant/'+token);destination.disabled=pending;document.getElementById('move-confirm').textContent=pending?'Retry move':'Move card';document.getElementById('move-cancel').textContent=pending?'Reload saved state':'Cancel';}});
function consent(){area.replaceChildren();const panel=element('section','',{class:'consent-panel'});panel.append(element('p','Research study',{class:'eyebrow'}),element('h1','Help us organise information'),element('h2','What you’ll do'));const facts=element('ul','',{class:'consent-facts'});for(const [icon,title,copy]of [['↔','Explore how information is organised','Follow the study instructions after starting.'],['✓','Progress saves in this browser session','Only acknowledged actions are saved. Keep this session link to return.'],['○','No account needed','You do not need a participant account.']]){const item=element('li'),description=element('div');description.append(element('strong',title),element('p',copy,{class:'muted'}));item.append(element('span',icon,{class:'fact-icon','aria-hidden':'true'}),description);facts.append(item);}panel.append(facts);const privacy=element('div','',{class:'help-panel'});privacy.append(element('strong','About your responses'),element('p','Participation is voluntary. Sessions are pseudonymous, not guaranteed anonymous or one-person verified. Saved research actions may be inspected by authorized researchers. Avoid personal details.'));panel.append(privacy);const label=element('label','I’ve read this and agree to take part',{class:'consent-checkbox'}),checkbox=element('input','',{type:'checkbox','data-testid':'consent-checkbox'});label.prepend(checkbox);panel.append(label);const start=control('Start study','consent-accept',async()=>{if(!checkbox.checked)return;start.disabled=true;try{session=await api('/recruit/'+token+'/sessions',{method:'POST',data:{accepted:true}});token=session.sessionCapability;history.replaceState({},'', '/participant/'+token);render();area.querySelector('h1')?.focus();announce('Saved');}catch(error){start.disabled=false;throw error;}},'primary');start.disabled=true;checkbox.onchange=()=>{start.disabled=!checkbox.checked;};panel.append(start,control('No thanks — leave','consent-decline',async()=>{if(hasPendingMutation('/recruit/'+token))throw new Error('Starting may already be committed. Retry Start study to recover the same session; no decline has been claimed');area.replaceChildren(header('Participation ended'));const done=element('section','',{class:'completion-panel'});done.append(element('h2','Participation declined'),element('p','No response was created. You can close this tab.'));area.append(done);announce('Declined — no response created');},'text-button decline'));area.append(panel);announce('Not started — consent required');}
if(kind==='recruit')consent();else guard(async()=>{await refresh();announce(session.status==='submitted'?'Submitted':'Saved');});
