import {element} from '../u1/api.mjs';
import {graphemes} from './unicode.mjs';
import {PickupController} from './pickup.mjs';
import {destinations} from './destinations.mjs';

export function groupDepth(group,groups){
 let depth=1,cursor=group,seen=new Set();
 while(cursor.parentGroupId){
  if(seen.has(cursor.groupId))throw new Error('Cycle is not allowed');
  seen.add(cursor.groupId);cursor=groups.find(g=>g.groupId===cursor.parentGroupId);
  if(!cursor)throw new Error('Parent group is missing');depth++;
 }
 return depth;
}
export function authorGroups(panel,content,{changed,render,button,field}){
 const groups=content.fixedGroups;
 function refresh(){changed();render();}
 function reindex(){
  const parents=new Set(groups.map(g=>g.parentGroupId??null));
  for(const parent of parents)groups.filter(g=>(g.parentGroupId??null)===parent)
   .sort((a,b)=>a.order-b.order).forEach((g,i)=>g.order=i);
 }
 function relocate(group,parent){
  const previous=group.parentGroupId;group.parentGroupId=parent;
  try{for(const g of groups)if(groupDepth(g,groups)>5)throw new Error('Maximum five group levels');}
  catch(error){group.parentGroupId=previous;throw error;}
  group.order=groups.filter(g=>g!==group&&g.parentGroupId===parent).length;reindex();refresh();
 }
 function move(group){
  const dialog=element('dialog','',{'aria-label':'Move group',class:'editor-sheet','data-testid':'group-move-dialog'});
  const select=element('select','',{'aria-label':'Parent group','data-testid':'group-parent'});
  for(const target of destinations(groups,{source:{kind:'group',id:group.groupId},action:'move'})){
   const choice=element('option',target.label+(target.reason?' — '+target.reason:''),{value:target.parent??''});
   choice.disabled=!!target.reason;
   select.append(choice);
  }
  select.value=group.parentGroupId??'';
  dialog.append(element('h2','Move '+group.label),select,
   button('Move group','group-move-confirm',async()=>{relocate(group,select.value||null);dialog.close();document.querySelector('[data-author-element="'+group.groupId+'"]')?.focus();},'primary'),
   button('Cancel','group-move-cancel',async()=>dialog.close()));
  document.body.append(dialog);dialog.onclose=()=>dialog.remove();dialog.showModal();select.focus();
 }
 function remove(group){
  const dialog=element('dialog','',{'aria-label':'Remove group',class:'editor-sheet'});
  const children=groups.filter(g=>g.parentGroupId===group.groupId);
  dialog.append(element('h2','Remove '+group.label+'?'),element('p',children.length?
   'Its child groups will move to its parent. No cards or child groups will be deleted.':'Only this draft group will be removed; published evidence is unchanged.'),
   button(children.length?'Move children to parent and remove':'Remove group','group-remove-confirm',async()=>{
    for(const child of children)child.parentGroupId=group.parentGroupId??null;
    groups.splice(groups.indexOf(group),1);reindex();refresh();dialog.close();
   },'danger'),button('Cancel','group-remove-cancel',async()=>dialog.close()));
  document.body.append(dialog);dialog.onclose=()=>dialog.remove();dialog.showModal();
 }
 function append(group,parent){
  const depth=groupDepth(group,groups),row=element('div','',{class:'object-row u2-group-row','data-testid':'group-row','data-depth':depth});
  row.dataset.groupId=group.groupId;
  row.style.marginInlineStart='0';
  const children=groups.filter(g=>g.parentGroupId===group.groupId).sort((a,b)=>a.order-b.order);
  const subtree=element('div','',{class:'u2-author-subtree'}),disclosure=button(children.length?'▾':'·','group-disclosure',async()=>{subtree.hidden=!subtree.hidden;disclosure.textContent=subtree.hidden?'▸':'▾';disclosure.setAttribute('aria-expanded',String(!subtree.hidden));},'text-button');
  if(!children.length)disclosure.disabled=true;
  disclosure.setAttribute('aria-label','Expand '+group.label);disclosure.setAttribute('aria-expanded','true');
  const input=field('',group.label,value=>{group.label=value;counter.textContent=graphemes(value).length+'/120';},{name:'Group name '+group.label});
  input.querySelector('input').dataset.rowId=group.groupId;input.querySelector('input').dataset.rowType='groupId';
  const counter=element('span',graphemes(group.label).length+'/120',{class:'muted group-counter'});
  const menu=element('details','',{class:'row-menu'});menu.append(element('summary','···',{'aria-label':'Actions for '+group.label,'data-testid':'row-actions'}));
  const actions=element('div','',{class:'u2-group-inline-actions'});actions.append(button('Move to…','group-move',async()=>move(group),'row-action'),button('Remove…','row-remove',async()=>remove(group),'row-action danger'));
  row.append(disclosure,input,counter,element('span','Fixed · level '+depth+' of 5',{class:'muted'}),menu,actions);parent.append(row);
  for(const child of children)append(child,subtree);parent.append(subtree);
 }
 for(const group of groups.filter(g=>!g.parentGroupId).sort((a,b)=>a.order-b.order))append(group,panel);
}

export function modeControl(content,{changed,button}){
 const area=element('div'),label=element('label','Card sort mode'),select=element('select','',{'aria-label':'Card sort mode','data-testid':'sort-mode'});
 for(const mode of ['open','closed','hybrid'])select.append(element('option',mode[0].toUpperCase()+mode.slice(1),{value:mode}));
 select.value=content.mode;label.append(select);area.append(label);
 select.onchange=()=>{
  const mode=select.value;
  if(mode==='open'&&content.fixedGroups.length){
   const dialog=element('dialog','',{'aria-label':'Change card sort mode',class:'editor-sheet'});
   dialog.append(element('h2','Change card sort mode'),element('p','Open sorts have no fixed groups. Remove these draft groups explicitly or cancel. Cards and published versions remain unchanged.'),
    button('Remove fixed draft groups and use open','mode-confirm',async()=>{content.fixedGroups=[];content.mode='open';changed();dialog.close();},'danger'),
    button('Cancel','mode-cancel',async()=>dialog.close()));
   select.value=content.mode;document.body.append(dialog);dialog.onclose=()=>{select.value=content.mode;dialog.remove();select.focus();};dialog.showModal();
  }else{content.mode=mode;changed();}
 };
 area.append(element('p','Open: participants create groups. Closed: cards go into fixed groups. Hybrid: participants may add owned subgroups, including beneath fixed groups; fixed nodes remain immutable.',{class:'help-panel'}));
 return area;
}

export function authorInteractions(root,content,{changed,render,button}){
 const cards=root.querySelector('[data-testid=card-inventory]'),groups=root.querySelector('[data-testid=fixed-groups]');
 function eligible(source,target){
  if(source.kind!==target.accepts)return 'Choose a '+source.kind+' destination';
  if(source.kind==='card')return '';
  const nodes=content.fixedGroups.map(g=>({...g})),node=nodes.find(g=>g.groupId===source.id);
  if(!node)return 'Group no longer exists';node.parentGroupId=target.parent;
  try{for(const group of nodes)if(groupDepth(group,nodes)>5)return 'Maximum five levels, including descendants';}
  catch(error){return error.message;}
  return '';
 }
 function reindexGroups(){
  for(const parent of new Set(content.fixedGroups.map(g=>g.parentGroupId??null)))
   content.fixedGroups.filter(g=>(g.parentGroupId??null)===parent).sort((a,b)=>a.order-b.order).forEach((g,i)=>g.order=i);
 }
 const controller=new PickupController(root,{eligible,
  commit:async(source,target)=>{
   if(source.kind==='card'){
    const old=content.cards.findIndex(c=>c.cardId===source.id),[card]=content.cards.splice(old,1);
    const index=target.index>old?target.index-1:target.index;content.cards.splice(index,0,card);
    content.cards.forEach((row,index)=>row.order=index);
   }else{
    const group=content.fixedGroups.find(g=>g.groupId===source.id),old=group.parentGroupId??null;
    const siblings=content.fixedGroups.filter(g=>g!==group&&(g.parentGroupId??null)===target.parent).sort((a,b)=>a.order-b.order);
    let index=target.index??siblings.length;if(old===target.parent&&target.index!==undefined&&group.order<index)index--;
    group.parentGroupId=target.parent;siblings.splice(index,0,group);siblings.forEach((g,i)=>g.order=i);reindexGroups();
   }
   changed();render();document.querySelector('[data-author-element="'+source.id+'"]')?.focus();
  },create:()=>{throw new Error('Use Add group to author a fixed group');},
 });
 function target(node,proposal){controller.target(node,proposal);}
 for(const [index,row]of [...cards.querySelectorAll('.object-row')].entries()){
  const card=content.cards[index],handle=button('⠿','author-card-handle',async()=>{},'u2-drag-handle');handle.dataset.authorElement=card.cardId;row.dataset.sortRow='';
  row.querySelector('.row-index')?.replaceWith(handle);
  const source={kind:'card',id:card.cardId,get label(){return card.label;}};controller.handle(handle,source);
  const line=button('Move card here · position'+(index+1),'author-card-target',async()=>{},'u2-reorder-target');
  if(index>=3)line.classList.add('overflow-row');if(index>=8)line.classList.add('overflow-wide');
  row.before(line);target(line,{kind:'move',parent:null,index,accepts:'card',label:'Move card here · position'+(index+1)});
 }
 const last=button('Move card last','author-card-target',async()=>{},'u2-reorder-target');cards.append(last);
 target(last,{kind:'move',parent:null,index:content.cards.length,accepts:'card',label:'Move card last'});
 if(content.mode==='open')return;
 for(const row of groups.querySelectorAll('[data-testid=group-row]')){
  const group=content.fixedGroups.find(g=>g.groupId===row.dataset.groupId),handle=button('⠿','author-group-handle',async()=>{},'u2-drag-handle');
  handle.dataset.authorElement=group.groupId;row.dataset.sortRow='';row.prepend(handle);
  controller.handle(handle,{kind:'group',id:group.groupId,get label(){return group.label;}});
  const destination=button('Move into '+group.label,'author-group-target',async()=>{},'u2-author-destination');row.append(destination);
  target(destination,{kind:'move',parent:group.groupId,accepts:'group',label:'Move into '+group.label});
  const line=button('Move group before '+group.label,'author-group-reorder',async()=>{},'u2-reorder-target');row.before(line);
  target(line,{kind:'move',parent:group.parentGroupId??null,index:group.order,accepts:'group',label:'Move group before '+group.label});
  row.querySelector('input').addEventListener('input',()=>{destination.textContent='Move into '+group.label;const entry=controller.targets.find(t=>t.node===destination);entry.label=destination.textContent;});
 }
 const top=button('Drop a group here to make it top-level · or use +Group','author-root-target',async()=>groups.querySelector('input[aria-label="Add group"]')?.focus(),'u2-create-target');
 const guidance=groups.querySelector('.u2-group-guidance');if(guidance)guidance.before(top);else groups.append(top);const idle=top.onclick;
 target(top,{kind:'move',parent:null,accepts:'group',label:'Move group to top level'});
 const drop=top.onclick;top.onclick=event=>controller.source?drop(event):idle(event);
}
