import {element,announce} from '../u1/api.mjs';
import {validateLabel,tokens,graphemes} from './unicode.mjs';
import {destinations} from './destinations.mjs';

function button(text,id,action,style='secondary'){
 const node=element('button',text,{type:'button','data-testid':id,class:style});node.onclick=action;return node;
}
const messages={LABEL_REQUIRED:'Enter a group name.',LABEL_TOKEN_LIMIT:'Use 7 words or fewer.',
 LABEL_LENGTH_LIMIT:'Use 120 graphemes or fewer.',UNSAFE_TEXT:'Remove line breaks or unsafe formatting controls.'};

export function openNaming({root,source,target,controller,commit,pending,back,raw='',dialogName='Name new group',confirmText='Create'}){
 const blocked=[...root.querySelectorAll('button,input,select,textarea,summary')].map(node=>({node,inert:node.inert}));
 for(const {node}of blocked)node.inert=true;
 const footer=document.querySelector('.participant-footer'),footerWasHidden=footer?.hidden;
 if(footer)footer.hidden=true;
 controller.bar.hidden=true;root.classList.remove('u2-pickup-active');root.classList.add('u2-staged');
 const restore=()=>{for(const {node,inert}of blocked)node.inert=inert;root.classList.remove('u2-staged');if(footer)footer.hidden=footerWasHidden;};
 const panel=element('section','',{role:'dialog','aria-label':dialogName,class:'u2-naming-panel','data-testid':'group-naming'});
 const label=element('label',''),input=element('input','',{'aria-label':'Group name','data-testid':'group-name-input'});
 input.value=raw;label.append(input);
 const title=element('h2',target.label),counter=element('p','',{class:'muted','data-testid':'group-name-count'});
 const error=element('p','',{class:'field-error',role:'alert','data-testid':'group-name-error',id:'group-name-error'});
 counter.id='group-name-count';input.setAttribute('aria-describedby','group-name-count group-name-error');
 const create=button(confirmText,'group-name-confirm',async()=>{
  const value=validateLabel(input.value,{participant:true});if(!value.valid){update();return;}
  create.disabled=true;input.disabled=true;
  try{
   await commit(source,target,input.value);restore();panel.remove();controller.clear();
  }catch(problem){
   error.textContent=problem.message;announce(problem.message+' — your name and source are retained.',true);
   const uncertain=pending();input.disabled=uncertain;create.textContent=uncertain?'Retry exact change':confirmText;
  }finally{create.disabled=false;}
 },'primary');
 const cancel=button('Cancel','group-name-cancel',()=>{
  if(pending()){error.textContent='Creation may already be saved. Retry exact creation or reload saved state.';return;}
  restore();panel.remove();controller.cancel();
 });
 const actions=element('div','',{class:'dialog-actions'});actions.append(create,cancel);
 if(back)actions.append(button('Back','group-name-back',()=>{if(pending()){error.textContent='Resolve the pending creation first.';return;}restore();panel.remove();back(input.value);}));
 const reload=button('Reload saved state','group-name-reload',()=>location.reload(),'text-button');
 reload.hidden=true;
 function update(){const value=validateLabel(input.value,{participant:true});
  counter.textContent=tokens(input.value).length+' of 7 words · '+graphemes(input.value).length+'/120 graphemes';
  error.textContent=value.valid?'':messages[value.code]??'Invalid name';create.disabled=!value.valid;input.setAttribute('aria-invalid',String(!value.valid));}
 input.oninput=update;
 panel.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();cancel.click();}
  if(event.key==='Enter'&&event.target===input){event.preventDefault();if(!create.disabled)create.click();}});
 const observe=new MutationObserver(()=>{reload.hidden=!pending();if(!panel.isConnected)observe.disconnect();});
 observe.observe(panel,{childList:true,subtree:true});
 panel.append(title,label,counter,error,element('p',source.label,{class:'u2-source-preview'}),actions,reload,
  element('p','Cancel changes nothing and restores the source and focus.',{class:'muted'}));
 (target.node?.parentElement??root).append(panel);update();input.focus({preventScroll:true});
 if(innerWidth<768)window.scrollTo(0,0);
 requestAnimationFrame(()=>{const bottom=panel.getBoundingClientRect().bottom,available=visualViewport?.height??innerHeight;if(bottom>available-16)window.scrollBy(0,bottom-available+16);});
}

export function openParentPicker({root,source,groups,ranks=[],mode='hybrid',eligible,choose,cancel,raw='',action='create'}){
 const dialog=element('dialog','',{'aria-label':'Choose parent group',class:'editor-sheet u2-parent-picker','data-testid':'parent-picker'});
 const heading=element('h2',(action==='move'?'Move group “':'Create group with “')+source.label+'”'),fieldset=element('fieldset');
 fieldset.append(element('legend','Parent group'));
 let selected=null;
 for(const destination of destinations(groups,{source,action,ranks,mode})){
  const target={kind:action,parent:destination.parent,label:(action==='move'?'Move to ':'Create in ')+destination.label};
  const reason=destination.reason||eligible(source,target),row=element('label',target.label+(reason?' — '+reason:''));
  const radio=element('input','',{type:'radio',name:'new-group-parent',value:target.parent??'','data-testid':'parent-'+(target.parent??'root')});
  radio.disabled=!!reason;if(!selected&&!reason){selected=target;radio.checked=true;}
  radio.onchange=()=>selected=target;row.prepend(radio);fieldset.append(row);
 }
 dialog.append(heading,fieldset,button('Continue','parent-continue',()=>{if(!selected)return;dialog.close();choose(selected,raw);},'primary'),
  button('Cancel','parent-cancel',()=>{dialog.close();cancel();}));
 dialog.addEventListener('cancel',event=>{event.preventDefault();dialog.close();cancel();});
 dialog.onclose=()=>dialog.remove();root.append(dialog);dialog.showModal();dialog.querySelector('input:checked')?.focus();
}
