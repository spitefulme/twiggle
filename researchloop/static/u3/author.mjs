import {element} from '../u1/api.mjs';

export const blankTree = {instructionText:'',resources:[],occurrences:[],tasks:[]};

export function definitionChanges(before, after) {
  const changes={};
  if((before.instructionText??'')!==after.instructionText)changes.instructionText=after.instructionText;
  for(const [field,idField] of [['resources','resourceId'],['occurrences','occurrenceId'],['tasks','taskId']]){
    const prior=before[field]??[];
    const previous=new Map(prior.map(row=>[row[idField],JSON.stringify(row)]));
    const current=new Set(after[field].map(row=>row[idField]));
    const upsert=after[field].filter(row=>previous.get(row[idField])!==JSON.stringify(row));
    const remove=[...previous.keys()].filter(id=>!current.has(id));
    const order=after[field].map(row=>row[idField]);
    const orderChanged=field==='tasks'&&JSON.stringify([...previous.keys()])!==JSON.stringify(order);
    if(upsert.length||remove.length||orderChanged)changes[field]={upsert,remove,...(orderChanged?{order}:{})};
  }
  return changes;
}
const id=()=>crypto.randomUUID().slice(0,24);
const byOrder=(left,right)=>left.order-right.order;
const parentOf=node=>node.parentOccurrenceId??null;
const children=(content,parent)=>content.occurrences.filter(node=>parentOf(node)===parent).sort(byOrder);
const previousMoves=new WeakMap();
const sectionState=new WeakMap();

export function movePlacement(content, node, parent, index) {
  let cursor=parent,depth=1;
  while(cursor!==null){
    if(cursor===node.occurrenceId)throw new Error('A placement cannot move into itself or its descendants');
    const target=content.occurrences.find(item=>item.occurrenceId===cursor);
    if(!target)throw new Error('Choose an available destination');
    cursor=parentOf(target);depth++;
  }
  function height(item){return 1+Math.max(0,...children(content,item.occurrenceId).map(height));}
  if(depth+height(node)-1>8)throw new Error('A move cannot exceed eight placement levels');
  const oldParent=parentOf(node);
  const destination=children(content,parent).filter(other=>other!==node);
  destination.splice(index,0,node);
  node.parentOccurrenceId=parent;
  destination.forEach((item,order)=>item.order=order);
  if(oldParent!==parent)children(content,oldParent).forEach((item,order)=>item.order=order);
}

export function authorContent({content,changed,button,field,redraw,gotoTasks}) {
  const layout=element('div','',{class:'u3-author-content'});
  const resources=element('section','',{'aria-label':'Resources'});
  const placements=element('section','',{'aria-label':'Placements'});
  const tools=sectionState.get(content)??{resources:!content.resources.length,placements:!content.occurrences.length};
  sectionState.set(content,tools);
  const sheet=element('dialog','',{id:'u3-placement-sheet',class:'editor-sheet u3-placement-sheet','aria-labelledby':'u3-placement-sheet-title'});
  const sheetTitle=element('h2','',{id:'u3-placement-sheet-title',tabindex:'-1'});
  const sheetBody=element('div','');
  let activeSheet=null;
  function restoreSheet({focus=true}={}){
    const prior=activeSheet;
    if(!prior)return;
    activeSheet=null;
    if(sheet.open)sheet.close();
    prior.menu.append(prior.actions);
    delete prior.line.dataset.placementActive;
    prior.trigger.removeAttribute('aria-expanded');
    prior.reset();
    if(focus&&prior.trigger.isConnected)prior.trigger.focus();
  }
  sheet.addEventListener('cancel',event=>{event.preventDefault();restoreSheet();});
  sheet.addEventListener('close',()=>{if(!sheet.open)restoreSheet();});
  sheet.addEventListener('click',event=>{
    if(event.target!==sheet)return;
    const bounds=sheet.getBoundingClientRect();
    if(event.clientX<bounds.left||event.clientX>bounds.right||event.clientY<bounds.top||event.clientY>bounds.bottom)
      restoreSheet();
  });
  sheet.append(sheetTitle,sheetBody,button('Cancel','u3-cancel-placement',async()=>restoreSheet()));
  const commit=(focusId=null)=>{
    restoreSheet({focus:false});changed();redraw();
    if(focusId)document.querySelector('[data-tree-placement="'+CSS.escape(focusId)+'"] summary')?.focus();
  };
  function place(node,parent,index){
    const before=structuredClone(content.occurrences);
    movePlacement(content,node,parent,index);
    previousMoves.set(content,{before,after:JSON.stringify(content.occurrences)});
    commit(node.occurrenceId);
  }
  const labels=new Map(content.resources.map(row=>[row.resourceId,row.label]));
  const labelFor=node=>node.displayLabel??labels.get(node.resourceId)??'';
  function heading(title,count){
    const titleNode=element('h2',title);
    titleNode.append(element('span',count,{class:'u3-count'}));return titleNode;
  }
  const resourceTools=element('details','',{class:'u3-tools'});
  resourceTools.open=tools.resources;
  resourceTools.ontoggle=()=>{tools.resources=resourceTools.open;};
  resourceTools.append(element('summary','+ Resource'));
  resources.append(heading('Resources',content.resources.length+' / 500'),resourceTools);
  const addResource=element('input','',{'aria-label':'Resource name'});
  resourceTools.append(addResource,button('Add resource','u3-add-resource',async()=>{
    if(!addResource.value.trim()||content.resources.length>=500)throw new Error('Enter a resource name within the approved limit');
    content.resources.push({resourceId:id(),label:addResource.value});commit();
  }));
  for(const resource of content.resources){
    const row=element('div','',{class:'object-row'});
    row.append(field('Rename resource',resource.label,value=>resource.label=value,{name:'Rename resource '+resource.label}));
    const remove=button('Remove resource','u3-remove-resource',async()=>{
      if(content.occurrences.some(item=>item.resourceId===resource.resourceId)||
          content.tasks.some(task=>task.acceptedResourceIds.includes(resource.resourceId)))throw new Error('Remove placement and task references first');
      content.resources=content.resources.filter(item=>item!==resource);commit();
    });
    row.append(remove);resourceTools.append(row);
  }
  const placementTools=element('details','',{class:'u3-tools'});
  placementTools.open=tools.placements;
  placementTools.ontoggle=()=>{tools.placements=placementTools.open;};
  placementTools.append(element('summary','+ Place'));
  placements.append(heading('Placements',content.occurrences.length+' / 1,000'),placementTools);
  const lastMove=previousMoves.get(content);
  if(lastMove?.after===JSON.stringify(content.occurrences)){
    placements.append(button('Undo placement move','u3-undo-move',async()=>{
      if(lastMove.after!==JSON.stringify(content.occurrences))throw new Error('Placement edits changed since the move');
      content.occurrences=lastMove.before;previousMoves.delete(content);commit();
    }));
  }
  const moveStatus=element('p','',{'role':'status','aria-live':'polite'});
  const headingInput=element('input','',{'aria-label':'Heading label'});
  function add(row){
    if(content.occurrences.length>=1000)throw new Error('Placement capacity reached');
    content.occurrences.push({occurrenceId:id(),parentOccurrenceId:null,
      order:children(content,null).length,...row});commit();
  }
  const resourcePicker=element('select','',{'aria-label':'Resource to link'});
  for(const item of content.resources)resourcePicker.append(element('option',item.label,{value:item.resourceId}));
  placementTools.append(headingInput,button('Add heading','u3-add-heading',async()=>{
    if(!headingInput.value.trim())throw new Error('Enter a heading label');
    add({kind:'heading',displayLabel:headingInput.value});
  }),resourcePicker,button('Link existing resource','u3-link-resource',async()=>{
    if(!resourcePicker.value)throw new Error('Add a resource to link first');
    add({kind:'resource',resourceId:resourcePicker.value});
  }));
  function tree(parent){
    const list=element('ul','',{class:'placement-tree'});
    for(const [index,node] of children(content,parent).entries()){
      const item=element('li',''),line=element('div','',{class:'u3-placement-line'}),menu=element('details','',{class:'u3-placement-actions'});
      const trigger=element('summary',labelFor(node),{'data-testid':'u3-placement'});
      menu.append(trigger);
      const actions=element('div','',{class:'u3-placement-controls'});
      const placementGroup=element('section','',{'aria-label':'This placement'});
      const resourceGroup=element('section','',{'aria-label':'Linked resource'});
      actions.append(placementGroup);
      menu.append(actions);
      let labelControl,target;
      const reset=()=>{labelControl.value=node.displayLabel??'';};
      function openActions(source,focusMove=false){
        if(!matchMedia('(max-width: 767px)').matches){
          menu.open=true;
          if(focusMove)target.focus();
          return;
        }
        restoreSheet({focus:false});reset();
        activeSheet={trigger:source,menu,line,actions,reset};
        source.setAttribute('aria-haspopup','dialog');
        source.setAttribute('aria-controls','u3-placement-sheet');
        source.setAttribute('aria-expanded','true');
        line.dataset.placementActive='true';
        sheetTitle.textContent='Placement actions · '+(placementPaths(content).get(node.occurrenceId)??labelFor(node));
        sheetBody.replaceChildren(actions);
        sheet.showModal();
        (focusMove?target:sheetTitle).focus();
      }
      trigger.addEventListener('click',event=>{
        if(matchMedia('(max-width: 767px)').matches){
          event.preventDefault();openActions(trigger);
        }
      });
      item.dataset.treePlacement=node.occurrenceId;
      line.dataset.treePlacementDrop=node.occurrenceId;
      let pointer=null;
      const handle=button('','u3-placement-drag',async()=>openActions(handle,true),'tree-placement-drag');
      handle.setAttribute('aria-label','Drag to move '+labelFor(node)+' or choose a parent');
      handle.append(element('span','⋮⋮',{'aria-hidden':'true'}));
      handle.onpointerdown=event=>{
        if(event.button!==0||!event.isPrimary)return;
        pointer={id:event.pointerId,x:event.clientX,y:event.clientY,lastX:event.clientX,
          lastY:event.clientY,dragging:false};
        handle.setPointerCapture(event.pointerId);
      };
      handle.onpointermove=event=>{
        if(pointer?.id!==event.pointerId)return;
        pointer.lastX=event.clientX;pointer.lastY=event.clientY;
        if(Math.hypot(event.clientX-pointer.x,event.clientY-pointer.y)>8)pointer.dragging=true;
        if(pointer.dragging)moveStatus.textContent='Moving '+labelFor(node)+'; drop onto a placement to nest';
      };
      handle.onpointercancel=()=>{pointer=null;moveStatus.textContent='Move cancelled; placement unchanged';};
      handle.onpointerup=event=>{
        if(pointer?.id!==event.pointerId)return;
        const drag=pointer.dragging;
        // A touch-end packet need not contain a contact point. The last
        // delivered touch move remains the user's actual drop location.
        const x=event.pointerType==='touch'?pointer.lastX:event.clientX;
        const y=event.pointerType==='touch'?pointer.lastY:event.clientY;
        pointer=null;
        if(!drag)return;
        const drop=document.elementFromPoint(x,y)?.closest('[data-tree-placement-drop]');
        const target=drop?.dataset.treePlacementDrop;
        if(!target){moveStatus.textContent='Move cancelled; placement unchanged';return;}
        try{
          const destination=content.occurrences.find(item=>item.occurrenceId===target);
          const bounds=drop.getBoundingClientRect();
          const fraction=(y-bounds.top)/Math.max(1,bounds.height);
          if(fraction<=.2||fraction>=.8){
            const siblingParent=parentOf(destination);
            const siblings=children(content,siblingParent).filter(item=>item!==node);
            const insert=siblings.indexOf(destination);
            if(insert<0)throw new Error('Choose a distinct placement');
            place(node,siblingParent,insert+(fraction>=.8?1:0));
          }else place(node,target,children(content,target).length);
        }catch(error){moveStatus.textContent=error.message+'; placement unchanged';}
      };
      if(node.resourceId&&content.occurrences.filter(other=>other.resourceId===node.resourceId).length>1)
        placementGroup.append(element('span','Repeated resource',{class:'pill'}));
      const labelEditor=element('label','Placement-only label');
      labelControl=element('input','');
      labelControl.value=node.displayLabel??'';
      labelEditor.append(labelControl);
      placementGroup.append(labelEditor,button('Apply placement label','u3-apply-placement-label',async()=>{
        if(node.kind==='heading'&&!labelControl.value.trim())throw new Error('Enter a heading label');
        if(labelControl.value)node.displayLabel=labelControl.value;else delete node.displayLabel;
        commit(node.occurrenceId);
      },'text-button'));
      target=element('select','',{'aria-label':'New parent for '+labelFor(node)});
      target.append(element('option','Root',{value:''}));
      for(const other of content.occurrences){
        if(other!==node)target.append(element('option',labelFor(other),{value:other.occurrenceId}));
      }
      target.value=parent??'';
      placementGroup.append(target,button('Move placement','u3-move-placement',async()=>{
        const destination=target.value||null;place(node,destination,children(content,destination).length);
      }),button('Move earlier','u3-earlier',async()=>place(node,parent,Math.max(0,index-1))),
      button('Move later','u3-later',async()=>place(node,parent,index+1)),
      button('Remove only this placement','u3-remove-placement',async()=>{
        if(children(content,node.occurrenceId).length||content.tasks.some(task=>task.acceptedOccurrenceIds.includes(node.occurrenceId)))
          throw new Error('Move child placements and remove task references first');
        content.occurrences=content.occurrences.filter(other=>other!==node);
        children(content,parent).forEach((other,order)=>other.order=order);
        commit();
      }));
      if(node.resourceId){
        const linked=content.occurrences.filter(item=>item.resourceId===node.resourceId).length;
        actions.append(resourceGroup);
        resourceGroup.append(element('p','Resource-wide: '+linked+' linked '+(linked===1?'placement':'placements')+
          '. A resource rename changes only inherited labels.',{class:'u3-results-muted'}),
          button('Edit resource across placements','u3-edit-linked-resource',async()=>{
            restoreSheet({focus:false});
            resourceTools.open=true;
            const resourceName=labels.get(node.resourceId);
            resources.querySelectorAll('[data-testid=editor-field]')[
              content.resources.findIndex(item=>item.resourceId===node.resourceId)
            ]?.focus();
            moveStatus.textContent='Editing resource '+resourceName;
          },'text-button'));
      }
      line.append(menu,handle);item.append(line);
      if(children(content,node.occurrenceId).length)item.append(tree(node.occurrenceId));
      list.append(item);
    }
    return list;
  }
  placements.append(tree(null),moveStatus);layout.append(resources,placements);
  const tasks=element('section','',{'aria-label':'Researcher tasks'});
  tasks.append(heading('Tasks',content.tasks.length+' / 20'),
    button('Tasks & rules','u3-content-tasks',gotoTasks));
  const sample=content.tasks[0];
  if(sample){
    const card=element('div','',{class:'read-panel'});
    card.append(element('p','Task 1 · Participant prompt',{class:'eyebrow'}),
      element('strong',sample.prompt),
      element('p',sample.acceptedOccurrenceIds.length?'Researcher only · Restricted accepted placements':
        'Researcher only · All placements of correct resources',{class:'muted'}));
    tasks.append(card);
  }
  layout.append(tasks,sheet);
  return layout;
}

export function placementPaths(content) {
  const resources=new Map(content.resources.map(row=>[row.resourceId,row.label]));
  const nodes=new Map(content.occurrences.map(row=>[row.occurrenceId,row]));
  const paths=new Map(),counts=new Map();
  for(const node of nodes.values()){
    const labels=[],seen=new Set();
    let current=node;
    while(current&&!seen.has(current.occurrenceId)){
      seen.add(current.occurrenceId);
      labels.push(current.displayLabel??resources.get(current.resourceId)??current.occurrenceId);
      current=nodes.get(parentOf(current));
    }
    const path=labels.reverse().join(' › ');
    paths.set(node.occurrenceId,path);
    counts.set(path,(counts.get(path)??0)+1);
  }
  return new Map([...paths].map(([id,path])=>[id,counts.get(path)>1?path+' · '+id:path]));
}

export function taskRules({content,changed,button,field,redraw}) {
  const panel=element('section','',{'aria-label':'Tasks and rules'});
  panel.append(field('Instructions for participants',content.instructionText,value=>content.instructionText=value,{tag:'textarea'}));
  const commit=()=>{changed();redraw();};
  const paths=placementPaths(content), labels=new Map();
  for(const resource of content.resources)labels.set(resource.label,(labels.get(resource.label)??0)+1);
  panel.append(button('Add task','u3-add-task',async()=>{
    if(content.tasks.length>=20)throw new Error('A version can have up to 20 tasks');
    content.tasks.push({taskId:id(),prompt:'New task',acceptedResourceIds:[],acceptedOccurrenceIds:[]});commit();
  }));
  for(const [index,task] of content.tasks.entries()){
    const row=element('details','',{class:'task-editor'});
    row.append(element('summary',`Task ${index+1} · ${task.prompt}`),
      field('Task prompt',task.prompt,value=>task.prompt=value,{tag:'textarea'}));
    for(const [name,items,fieldName,idName,limit] of [
      ['Correct resources — private',content.resources,'acceptedResourceIds','resourceId',20],
      ['Restrict accepted placements — private',content.occurrences.filter(item=>item.kind==='resource'),
        'acceptedOccurrenceIds','occurrenceId',100]
    ]){
      const group=element('fieldset','',{class:'target-list'});
      group.append(element('legend',name));
      for(const item of items){
        const nameText=idName==='occurrenceId'?paths.get(item[idName]):
          labels.get(item.label)>1?item.label+' · '+item[idName]:item.label;
        const label=element('label',nameText??item[idName]);
        const control=element('input','',{type:'checkbox'});
        control.checked=task[fieldName].includes(item[idName]);
        control.onchange=()=>{
          if(control.checked&&task[fieldName].length>=limit){control.checked=false;return;}
          task[fieldName]=control.checked?[...task[fieldName],item[idName]]:task[fieldName].filter(value=>value!==item[idName]);
          changed();
        };
        label.prepend(control);group.append(label);
      }
      row.append(group);
    }
    if(index>0)row.append(button('Move task earlier','u3-task-earlier',async()=>{
      [content.tasks[index-1],content.tasks[index]]=[content.tasks[index],content.tasks[index-1]];commit();
    }));
    if(index+1<content.tasks.length)row.append(button('Move task later','u3-task-later',async()=>{
      [content.tasks[index],content.tasks[index+1]]=[content.tasks[index+1],content.tasks[index]];commit();
    }));
    row.append(button('Remove task','u3-remove-task',async()=>{content.tasks.splice(index,1);commit();}));
    panel.append(row);
  }
  return panel;
}
