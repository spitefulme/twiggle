import {element,announce} from '../u1/api.mjs';
import {PickupController} from './pickup.mjs';
import {openNaming,openParentPicker} from './naming.mjs';

export function groupPath(group,groups){
 const names=new Map(groups.map(g=>[g.groupId,g])),parts=[group.label],seen=new Set([group.groupId]);
 let parent=names.get(group.parentGroupId);
 while(parent&&!seen.has(parent.groupId)){seen.add(parent.groupId);parts.unshift(parent.label);parent=names.get(parent.parentGroupId);}
 return parts.join(' › ');
}
function depth(group,groups){let count=0,node=group;const seen=new Set();while(node&&!seen.has(node.groupId)){seen.add(node.groupId);count++;node=groups.find(g=>g.groupId===node.parentGroupId);}return count;}

export function targetReason(view,source,target){
 const groupIds=new Set(view.groups.map(g=>g.groupId));
 if(target.parent!==null&&!groupIds.has(target.parent))return 'Destination no longer exists';
 if(source.kind==='group'&&view.groups.find(g=>g.groupId===source.id)?.owner!=='participant')return 'Fixed groups cannot be moved';
 if(target.kind==='create'&&(view.mode==='closed'||view.groups.length>=100))return view.mode==='closed'?'Closed sort: fixed groups only':'100 total groups reached';
 const nodes=view.groups.map(g=>({...g})),byId=new Map(nodes.map(g=>[g.groupId,g]));
 const selected=source.kind==='group'?byId.get(source.id):null;
 if(target.kind==='create'){
  const proposal={groupId:'proposal-'+source.id,parentGroupId:target.parent};nodes.push(proposal);byId.set(proposal.groupId,proposal);
  if(selected)selected.parentGroupId=proposal.groupId;
 }else if(selected)selected.parentGroupId=target.parent;
 for(const node of nodes){const seen=new Set();let cursor=node;
  while(cursor){if(seen.has(cursor.groupId))return 'Cannot put a group inside itself or its descendants';seen.add(cursor.groupId);
   if(seen.size>5)return 'Maximum five levels, including descendants';cursor=byId.get(cursor.parentGroupId);}
 }
 return '';
}

export function sortWorkspace({view,operate,openMove,control,pending,focusCard,collapsed=new Set(),lastChange=null}){
 const root=element('div','',{class:'sort-workspace u2-sort-workspace'});
 const unsorted=element('section','',{id:'unsorted-cards',class:'compact-sort','data-testid':'unsorted-cards'});
 const groups=element('section','',{class:'u2-group-forest','data-testid':'sort-groups'});
 const ranks=new Map((view.groupOrder??[]).map(r=>[r.groupId,r.order]));
 const groupOrder=parent=>view.groups.filter(g=>(g.parentGroupId??null)===parent).sort((a,b)=>(ranks.get(a.groupId)??a.order)-(ranks.get(b.groupId)??b.order));
 const orderedCards=parent=>(view.placements??view.cards.map((c,order)=>({cardId:c.cardId,groupId:view.membership[c.cardId],order})))
  .filter(p=>p.groupId===parent).sort((a,b)=>a.order-b.order).map(p=>view.cards.find(c=>c.cardId===p.cardId));
 const eligible=(source,target)=>target.accepts&&target.accepts!==source.kind?'Choose a '+source.kind+' target':targetReason(view,source,target);
 function destinationIndex(source,target){
  if(target.index===undefined)return {};
  const previous=source.kind==='card'?view.placements.find(p=>p.cardId===source.id):
   {groupId:view.groups.find(g=>g.groupId===source.id)?.parentGroupId??null,order:ranks.get(source.id)};
  return {targetIndex:target.index-(previous?.groupId===target.parent&&previous.order<target.index?1:0)};
 }
 const controller=new PickupController(root,{eligible,cancelAllowed:()=>!pending(),
  commit:async(source,target)=>{await operate(source.kind==='card'?'move-card':'move-group',source.kind==='card'?
   {cardId:source.id,targetGroupId:target.parent,...destinationIndex(source,target)}:
   {groupId:source.id,targetParentGroupId:target.parent,...destinationIndex(source,target)});focusCard(source.id);},
  create:(source,target)=>name(source,target),
 });
 function source(kind,id,label,handle){return {kind,id,label,handle};}
 function name(item,target,raw=''){
  let parent=target.parent;
  while(parent){collapsed.delete(parent);const node=root.querySelector('[data-group-id="'+parent+'"]');
   const body=node?.querySelector(':scope > .u2-group-body');if(body)body.hidden=false;
   const disclosure=node?.querySelector(':scope > .u2-group-header [data-testid=sort-group-disclosure]');
   if(disclosure){disclosure.textContent='▾';disclosure.setAttribute('aria-expanded','true');}
   parent=view.groups.find(g=>g.groupId===parent)?.parentGroupId;
  }
  const actual=controller.targets.find(t=>t.kind==='create'&&t.parent===target.parent)??{...target,node:groups};
  openNaming({root,source:item,target:actual,controller,pending,raw,
   commit:async(item,target,label)=>{const createdId=await operate(target.parent===null?'create-group':'create-subgroup',
    {elementId:item.id,parentGroupId:target.parent,label});const destination=document.querySelector('[data-group-id="'+createdId+'"] [data-testid=sort-group-target]');destination?.focus({preventScroll:true});destination?.scrollIntoView({block:'center'});},
   back:value=>picker(item,value),
  });
 }
 function picker(item,raw=''){
  for(const menu of root.querySelectorAll('.row-menu[open]'))menu.open=false;
  controller.pick(item);controller.busy=true;
  const ordered=[];function walk(parent){for(const node of groupOrder(parent)){ordered.push(node);walk(node.groupId);}}walk(null);
  openParentPicker({root,source:item,groups:ordered,ranks:view.groupOrder,mode:view.mode,eligible,raw,
   choose:(target,value)=>name(item,target,value),cancel:()=>controller.cancel()});
 }
 function creationTarget(parent,label,container){
  const button=control(label,parent===null?'create-root-target':'create-in-'+parent,async()=>{
   if(controller.source)return;
   const dialog=element('dialog','',{'aria-label':'Choose item for new group',class:'editor-sheet'});
   dialog.append(element('h2','Choose an item to put in the new group'));
   for(const card of view.cards)dialog.append(control(card.label,'create-choose-card',async()=>{dialog.close();
    const handle=root.querySelector('[data-element-id="'+card.cardId+'"]');const item=source('card',card.cardId,card.label,handle);controller.pick(item);controller.busy=true;name(item,{kind:'create',parent,label});},'text-button'));
   dialog.append(control('Cancel','create-item-cancel',async()=>dialog.close()));dialog.onclose=()=>dialog.remove();root.append(dialog);dialog.showModal();
  },'u2-create-target');
  const idle=button.onclick;
  controller.target(button,{kind:'create',parent,label});
  // target() owns click during pickup; retain actual tap-to-choose behavior at idle.
  const drop=button.onclick;button.onclick=event=>{if(controller.source)drop(event);else idle(event);};
  container.append(button);return button;
 }
 function reorderTarget(parent,index,kind,container){
  const next=(kind==='card'?orderedCards(parent):groupOrder(parent))[index];
  const label='Move '+kind+(next?' before “'+next.label+'”':' last');
  const line=control(label,'sort-reorder-target',async()=>{},'u2-reorder-target');
  controller.target(line,{kind:'move',parent,index,accepts:kind,label});
  line.dataset.targetKind=kind;
  line.dataset.targetParent=parent??'root';line.dataset.targetIndex=String(index);
  container.append(line);return line;
 }
 function cardRow(card,parent,index){
  const row=element('div','',{class:'sort-card u2-card-row','data-sort-row':'','data-testid':'sort-card'});
  const handle=control('⠿','card-handle',async()=>{},'u2-drag-handle');handle.dataset.elementId=card.cardId;
  const item=source('card',card.cardId,card.label,handle);controller.handle(handle,item);
  const move=control('Move to…','card-move',async()=>openMove(card,move),'move-button');move.setAttribute('aria-label','Move '+card.label);move.dataset.cardId=card.cardId;
  const menu=element('details','',{class:'row-menu'});menu.append(element('summary','···',{'aria-label':'Actions for '+card.label,'data-testid':'card-actions'}));
  if(view.mode!=='closed')menu.append(control('Create group with this item','create-with-item',async()=>picker(item),'row-action'));
  menu.append(control('Move first here','card-move-first',async()=>operate('move-card',{cardId:card.cardId,targetGroupId:parent,targetIndex:0}),'row-action'));
  row.append(handle,element('span',card.label,{class:'card-label'}));
  if(view.mode==='closed'){move.textContent='···';row.append(move);}
  else{move.classList.add('row-action');menu.append(move);row.append(menu);}
  return row;
 }
 function cardList(parent,container,compact=false){
  const cards=orderedCards(parent);
  cards.forEach((card,index)=>{const line=reorderTarget(parent,index,'card',container),row=cardRow(card,parent,index);
   if(compact&&index>=2){row.classList.add('overflow-card');line.classList.add('overflow-card');}
   if(compact&&index>=6){row.classList.add('overflow-wide-card');line.classList.add('overflow-wide-card');}container.append(row);});
  reorderTarget(parent,cards.length,'card',container);return cards;
 }
 const unassigned=orderedCards(null);unsorted.append(element('h2','Unsorted · '+unassigned.length));cardList(null,unsorted,true);
 function moreCards(container,count,id){
  const more=control('',id,async()=>{const expanded=container.classList.toggle('expanded');more.setAttribute('aria-expanded',String(expanded));if(expanded)more.textContent='Show fewer cards';else label();},'text-button list-toggle '+(count>6?'wide-and-phone-toggle':'phone-only-toggle'));
  function label(){more.replaceChildren(element('span','Show '+(count-6)+' more cards',{class:'wide-inventory-count'}),element('span','Show '+(count-2)+' more cards',{class:'phone-inventory-count'}));}
  label();more.setAttribute('aria-expanded','false');container.append(more);
 }
 if(unassigned.length>2)moreCards(unsorted,unassigned.length,'unsorted-more');
 if(!unassigned.length)unsorted.append(element('p','All cards placed',{class:'muted'}));
 groups.append(element('h2','Your groups · '+view.groups.length+' of 100'));
 function appendGroup(node,parent){
  const item=element('section','',{class:'u2-sort-group','data-testid':'sort-group','data-group-id':node.groupId});
  const newlyCreated=lastChange?.operationKind.startsWith('create-')&&!lastChange.priorGroupIds.includes(node.groupId);
  if(newlyCreated)item.classList.add('u2-created-group');
  const head=element('div','',{class:'u2-group-header','data-sort-row':''});
  const body=element('div','',{class:'u2-group-body compact-group'});body.hidden=collapsed.has(node.groupId);
  const disclosure=control(body.hidden?'▸':'▾','sort-group-disclosure',async()=>{body.hidden=!body.hidden;if(body.hidden)collapsed.add(node.groupId);else collapsed.delete(node.groupId);disclosure.textContent=body.hidden?'▸':'▾';disclosure.setAttribute('aria-expanded',String(!body.hidden));},'text-button');
  disclosure.setAttribute('aria-label','Expand '+node.label);disclosure.setAttribute('aria-expanded',String(!body.hidden));
  const title=control(node.label,'sort-group-target',async()=>{},'u2-group-title');
  controller.target(title,{kind:'move',parent:node.groupId,label:'Move into '+groupPath(node,view.groups)});
  head.append(disclosure);
  if(node.owner==='participant'){
   const handle=control('⠿','group-handle',async()=>{},'u2-drag-handle');handle.dataset.elementId=node.groupId;
   const selected=source('group',node.groupId,node.label,handle);controller.handle(handle,selected);head.append(handle);
   const menu=element('details','',{class:'row-menu'});menu.append(element('summary','···',{'aria-label':'Actions for '+node.label,'data-testid':'group-actions'}),
    control('Create group with this item','create-with-item',async()=>picker(selected),'row-action'),
    control('Rename…','group-rename',async()=>renameGroup(selected),'row-action'),
    control('Move to…','group-move-picker',async()=>moveGroupPicker(selected),'row-action'),
    control('Remove…','group-delete',async()=>removeGroup(selected),'row-action danger'));
   head.append(title,menu);
  }else head.append(title);
  if(newlyCreated)head.append(element('span','New',{class:'u2-new-marker'}));
  head.append(element('span',(node.owner==='participant'?'':'🔒 Fixed · ')+orderedCards(node.groupId).length+' cards',{class:'muted u2-group-count','aria-label':orderedCards(node.groupId).length+' direct cards · level '+depth(node,view.groups)}));
  item.append(head,body);const members=cardList(node.groupId,body,true);if(members.length>2)moreCards(body,members.length,'group-more');
  const children=groupOrder(node.groupId);for(const [index,child]of children.entries()){reorderTarget(node.groupId,index,'group',body);appendGroup(child,body);}
  if(view.mode!=='closed')creationTarget(node.groupId,'New subgroup in '+node.label+' · level '+(depth(node,view.groups)+1),body);
  parent.append(item);
 }
 function moveGroupPicker(item){
  for(const menu of root.querySelectorAll('.row-menu[open]'))menu.open=false;
  controller.pick(item);controller.busy=true;
  openParentPicker({root,source:item,groups:view.groups,ranks:view.groupOrder,mode:view.mode,action:'move',eligible,
   choose:async target=>{try{await operate('move-group',{groupId:item.id,targetParentGroupId:target.parent});controller.clear();}catch(error){announce(error.message,true);controller.busy=false;}},cancel:()=>controller.cancel()});
 }
 function renameGroup(item){
  for(const menu of root.querySelectorAll('.row-menu[open]'))menu.open=false;
  controller.pick(item);controller.busy=true;
  openNaming({root,source:item,target:{node:root.querySelector('[data-group-id="'+item.id+'"]'),label:'Rename “'+item.label+'”'},
   controller,pending,dialogName:'Rename group',confirmText:'Save name',raw:item.label,
   commit:async(source,target,label)=>{await operate('rename-group',{groupId:item.id,label});focusCard(item.id);}});
 }
 function removeGroup(item){
  const dialog=element('dialog','',{'aria-label':'Remove owned group',class:'editor-sheet'});
  dialog.append(element('h2','Remove '+item.label+'?'),element('p','Cards are never deleted. Child groups move to the parent. Choose what happens to directly contained cards.'),
   control('Move contents to parent','sort-delete-parent',async()=>{await operate('delete-group',{groupId:item.id,disposition:'move-to-parent'});dialog.close();},'danger'),
   control('Unassign cards and remove','sort-delete-unassign',async()=>{await operate('delete-group',{groupId:item.id,disposition:'unassign'});dialog.close();},'danger'),
   control('Cancel','sort-delete-cancel',async()=>dialog.close()));dialog.onclose=()=>dialog.remove();root.append(dialog);dialog.showModal();
 }
 for(const [index,node]of groupOrder(null).entries()){reorderTarget(null,index,'group',groups);appendGroup(node,groups);}
 const rootCreation=view.mode!=='closed'?creationTarget(null,'New group · drop a card here, or tap to choose an item',groups):null;
 if(view.undoOperationId){
  const undo=control(lastChange?'Undo':'Undo last saved change','sort-undo',async()=>operate('undo',{operationId:view.undoOperationId}),lastChange?'primary':'text-button u2-undo');
  if(lastChange){const message=lastChange.operationKind.startsWith('create-')?
   'Created “'+lastChange.payload.label+'”'+(lastChange.parentLabel?' in “'+lastChange.parentLabel+'”':'')+' with “'+lastChange.sourceLabel+'”.':
   lastChange.operationKind==='rename-group'?'Saved new group name “'+lastChange.payload.label+'”.':'Saved '+lastChange.operationKind.replaceAll('-',' ')+'.';
   const toast=element('section','',{class:'u2-change-toast','data-testid':'last-change-toast','aria-label':'Last saved change'});toast.append(element('span',message),undo);groups.insertBefore(toast,rootCreation);
  }else groups.append(undo);
 }
 const legend=element('details','',{class:'muted u2-group-legend'});legend.append(element('summary','Counts are direct cards · sorting limits'),element('p','Nested cards remain in their subgroup.100 total fixed and owned groups per response; five levels.'));groups.append(legend);
 if(lastChange?.operationKind.startsWith('create-')){
  root.classList.add('u2-confirmation');const reveal=control('Show unsorted cards','reveal-unsorted',async()=>{root.classList.remove('u2-confirmation');reveal.remove();},'text-button u2-reveal-unsorted');groups.prepend(reveal);
 }
 root.append(unsorted,groups);return root;
}
