const pendingIntents=new Map(),pageContext=crypto.randomUUID();
let researcherIdentity='anonymous',authenticationEpoch=0;
export class StaleSessionReply extends Error{
 constructor(){super('The session changed before this read completed.');this.name='StaleSessionReply';}
}
const csrfToken=()=>document.cookie.split('; ').find(part=>part.startsWith('csrftoken='))?.split('=')[1]??document.querySelector('[name=csrfmiddlewaretoken]')?.value??'';
function contextFor(path,csrf){const parts=path.split('/');const authority=['participant','recruit'].includes(parts[1])?parts.slice(0,3).join('/'):'researcher';return JSON.stringify([pageContext,researcherIdentity,authenticationEpoch,csrf,authority]);}
export function hasPendingMutation(prefix){const context=contextFor(prefix,csrfToken());return [...pendingIntents.values()].some(item=>item.context===context&&item.path.startsWith(prefix));}
export function hasPendingIntent(path,{method,revision,data,key}){
 const intent=JSON.stringify([contextFor(path,csrfToken()),path,method,revision,data]);
 return pendingIntents.get(intent)?.key===key;
}
function validAcknowledgement(path,result,data){
 if(!result||typeof result!=='object'||Array.isArray(result))return false;
 if(/^\/workspaces\/\d+\/members\/\d+$/.test(path))return String(result.userId)===path.split('/').at(-1)&&result.active===data.active&&Number.isSafeInteger(result.revision)&&result.revision>=data.revision;
 if(/^\/workspaces\/\d+\/members$/.test(path))return Number.isSafeInteger(result.userId)&&result.active===true&&result.revision===1;
 if(/^\/workspaces\/\d+\/erasure-cases$/.test(path))return typeof result.caseId==='string'&&result.kind==='study'&&['accepted','retrying','incomplete','completed'].includes(result.status);
 if(/\/versions\/[^/]+\/tree-v2\/analysis-jobs$/.test(path))return typeof result.jobId==='string'&&result.versionId===path.split('/')[4]&&typeof result.cohortId==='string'&&typeof result.statusLocation==='string';
 if(/\/analysis-jobs(?:\/[^/]+\/cancel)?$/.test(path))return typeof result.jobId==='string'&&result.versionId===data.versionId&&typeof result.cohortId==='string'&&['queued','running','succeeded','failed','cancelled','stalled'].includes(result.state)&&typeof result.statusLocation==='string';
 if(path.endsWith('/analysis-annotations'))return result.versionId===data.versionId&&Number.isSafeInteger(result.annotationRevision)&&result.annotationRevision>0&&result.saveState==='saved';
 if(path.endsWith('/operations')||path.endsWith('/tree-v2/events'))return result.sequence===data.sequence&&Number.isSafeInteger(result.revision)&&result.revision===data.sequence&&result.saveState==='saved';
 if(path.endsWith('/submit'))return result.terminalState==='submitted'&&typeof result.responseId==='string';
 if(path.startsWith('/recruit/'))return typeof result.sessionCapability==='string'&&typeof result.sessionId==='string'&&Number.isSafeInteger(result.revision)&&!!result.participantView;
 if(path.endsWith('/versions'))return typeof result.versionId==='string'&&Number.isSafeInteger(result.versionNumber)&&typeof result.recruitmentLink==='string';
 if(path.endsWith('/preview'))return typeof result.previewId==='string'&&typeof result.draftId==='string'&&typeof result.expiresAt==='string';
 if(path.endsWith('/lifecycle'))return typeof result.studyId==='string'&&['closed','revoked'].includes(result.state);
 return path.startsWith('/studies')&&typeof result.draftId==='string'&&typeof result.studyId==='string'&&Number.isSafeInteger(result.revision);
}
export async function api(path,{method='GET',data,key,revision,acceptSession=()=>true}={}){
 const sessionRead=method==='GET'&&path==='/auth/session/current',sessionEpoch=authenticationEpoch;
 const csrf=csrfToken(),tracked=method!=='GET'&&path!=='/auth/session',context=contextFor(path,csrf),intent=JSON.stringify([context,path,method,revision,data]);
 let requestKey=key??crypto.randomUUID();
 if(tracked){
  const prior=pendingIntents.get(intent);
  if(prior){if(key&&key!==prior.key)throw new Error('Retry the pending request with its original key');requestKey=prior.key;}
  else{
   if([...pendingIntents.values()].some(item=>item.context===context))throw new Error('An earlier request has an uncertain outcome. Retry that exact request, or reload to resume server state');
   if(pendingIntents.size>=64)throw new Error('Pending-request limit reached. Reload to reconcile server state before sending another request');
   pendingIntents.set(intent,{context,path,key:requestKey});
  }
 }
 const headers={'Content-Type':'application/json','Idempotency-Key':requestKey,'X-CSRFToken':csrf};
 if(revision!==undefined)headers['If-Match']=String(revision);
 // An unreadable/lost acknowledgement keeps the exact intent; no automatic retry,
 // silent pending-intent eviction, or changed-intent dispatch takes place.
 const response=await fetch('/api/v1'+path,{method,headers,credentials:'same-origin',body:data===undefined?undefined:JSON.stringify(data),signal:AbortSignal.timeout(10000)});
 const result=await response.json();
 // A superseded authorization probe cannot rebind the exact-save context,
 // report a stale session failure, or adopt a previous member after a switch.
 if(sessionRead&&(sessionEpoch!==authenticationEpoch||!acceptSession()))throw new StaleSessionReply();
 if(!response.ok){
  if(tracked&&response.status>=400&&response.status<500&&typeof result?.code==='string'&&result.status===response.status)pendingIntents.delete(intent);
  throw Object.assign(new Error(result.title??'Request failed'),{problem:result});
 }
 if(tracked){if(!validAcknowledgement(path,result,data))throw new Error('Unrecognized acknowledgement — retry this exact request to confirm its outcome');pendingIntents.delete(intent);}
 if((path==='/auth/session'||path==='/auth/session/current')&&typeof result.researcherId==='string'){
  if(path==='/auth/session'||researcherIdentity!==result.researcherId)authenticationEpoch++;
  researcherIdentity=result.researcherId;
 }else if(path==='/auth/session'&&method==='DELETE'){authenticationEpoch++;researcherIdentity='anonymous';}
 return result;
}
export function announce(message,error=false){const node=document.getElementById(error?'error':'status');node.hidden=false;node.textContent=message;if(!error){const compact=/^preview/i.test(message)?'Preview ready':/^saved/i.test(message)?'Saved':/pending|saving/i.test(message)?'Saving…':/unsaved/i.test(message)?'Unsaved':/failed|offline/i.test(message)?'Not saved':/published/i.test(message)?'Published':/minimal persisted/i.test(message)?'Results loaded':/closed/i.test(message)?'Closed':/revoked/i.test(message)?'Revoked':/submitted/i.test(message)?'Submitted':/consent required/i.test(message)?'Not started':/workspace ready/i.test(message)?'Ready':message;node.replaceChildren(element('span',compact,{'aria-hidden':'true'}),element('span',message,{class:'sr-only'}));node.title=message;}const status=document.getElementById('status');status.dataset.state=error||/failed|offline/i.test(message)?'failed':/pending|saving|unsaved/i.test(message)?'pending':/^saved/i.test(message)?'saved':'neutral';if(error){status.replaceChildren(element('span','Needs attention'));const activeDialog=document.querySelector('dialog[open]'),home=document.querySelector('main')??document.body;(activeDialog??home).prepend(node);const visible=[...(activeDialog??document).querySelectorAll('input:not([type=hidden]):not(:disabled),textarea:not(:disabled),select:not(:disabled)')].filter(control=>control.getClientRects().length);const target=visible.find(control=>control.tagName==='TEXTAREA')??visible[0];if(target){target.id||='retained-input';target.setAttribute('aria-invalid','true');const link=element('a',' Review retained input',{'href':'#'+target.id,'data-testid':'error-field-link'});link.onclick=event=>{event.preventDefault();target.focus();};node.append(link);}node.focus();}else document.querySelectorAll('[aria-invalid=true]').forEach(control=>control.removeAttribute('aria-invalid'));}
export function element(tag,text,attributes={}){const node=document.createElement(tag);node.textContent=text;for(const [k,v] of Object.entries(attributes))node.setAttribute(k,v);return node;}
