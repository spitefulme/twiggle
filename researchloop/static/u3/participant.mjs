import {element} from '../u1/api.mjs';

export function renderTree({area, session, header, control, act, review, recover, pending}) {
  const view = session.state ?? session.participantView;
  const index = Math.min(view.completedTasks + 1, view.taskCount);
  const panel = element('section', '', {class:'tree-panel','aria-label':'Tree task'});
  area.append(header('Find a destination', `Task ${index} of ${view.taskCount}`), panel);
  if (pending) {
    panel.append(element('p', 'The last action may already be saved. Confirm it before another change.', {role:'status'}),
      control('Retry the exact action','tree-retry',recover,'primary'));
    return;
  }
  if (!view.activeTaskId) {
    panel.append(element('h2','Tasks completed',{tabindex:'-1'}),control('Review and submit','participant-review',review,'primary'));
    return;
  }
  panel.append(element('p',view.instructionText,{class:'muted'}),
    element('h2',view.prompt,{tabindex:'-1'}));
  if (!view.taskStarted) {
    panel.append(control('Start task','task-start',()=>act('task-start',{}),'primary'));
    return;
  }
  const breadcrumbs=element('nav','',{class:'tree-navigation','aria-label':'Current path'});
  if(view.path.length) breadcrumbs.append(control('Root','tree-root',()=>act('ancestor-return',{occurrenceId:null}),'text-button'));
  view.path.forEach((node,i)=>{
    if(i===view.path.length-1) breadcrumbs.append(element('span',node.label,{'aria-current':'location'}));
    else breadcrumbs.append(control(node.label,'tree-ancestor',()=>act('ancestor-return',{occurrenceId:node.occurrenceId}),'text-button'));
  });
  if(!view.path.length) breadcrumbs.append(element('span','Root',{'aria-current':'location'}));
  panel.append(breadcrumbs);
  const list=element('ul','',{class:'placement-tree','aria-label':'Available branches'});
  for(const child of view.children){
    const item=element('li','');
    item.append(control('Open '+child.label,'tree-enter',()=>act('child-entry',{occurrenceId:child.occurrenceId}),'secondary'));
    list.append(item);
  }
  panel.append(list);
  if(!view.children.length) panel.append(element('p','No further branches',{class:'muted'}));
  const current=view.path.at(-1), parent=view.path.at(-2);
  const actions=element('div','',{class:'tree-navigation'});
  if(current) actions.append(control('Back','tree-back',()=>act('ancestor-return',{occurrenceId:parent?.occurrenceId??null}),'text-button'));
  if (current?.selectable) actions.append(control('Choose this destination','tree-select',()=>act('destination-selected',{occurrenceId:current.occurrenceId}),'primary'));
  actions.append(control('Give up this task','tree-give-up',()=>act('give-up',{}),'text-button'));
  panel.append(actions);
}
