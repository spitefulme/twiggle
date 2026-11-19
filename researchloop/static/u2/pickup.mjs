import {element} from '../u1/api.mjs';

// One proposal shared by real pointer, touch and keyboard. Nothing here saves.
export class PickupController{
 constructor(root,{commit,create,eligible,cancelAllowed=()=>true}){
  this.root=root;this.commit=commit;this.create=create;this.eligible=eligible;this.cancelAllowed=cancelAllowed;
  this.targets=[];this.source=null;this.pending=null;this.current=null;this.busy=false;this.frame=null;this.velocity=0;
  this.abort=new AbortController();const signal=this.abort.signal;
  this.bar=element('div','',{class:'u2-pickup-bar'});this.bar.hidden=true;
  this.status=element('span','',{'data-testid':'pickup-status',role:'status','aria-live':'polite'});
  const cancel=element('button','Cancel',{type:'button','data-testid':'pickup-cancel',class:'secondary'});
  cancel.onclick=()=>this.cancel();this.bar.append(this.status,cancel);root.prepend(this.bar);
  document.addEventListener('pointermove',event=>this.pointerMove(event),{signal});
  document.addEventListener('pointerup',event=>this.pointerUp(event),{signal});
  document.addEventListener('pointercancel',()=>this.cancel(),{signal});
  document.addEventListener('keydown',event=>this.key(event),{signal});
  this.observer=new MutationObserver(()=>{if(!root.isConnected)this.dispose();});
  queueMicrotask(()=>{if(root.isConnected)this.observer.observe(document.body,{childList:true,subtree:true});});
 }
 handle(node,source){
  node.classList.add('u2-drag-handle');node.setAttribute('aria-label','Pick up '+source.label);
  node.setAttribute('aria-pressed','false');
  node.onpointerdown=event=>{
   if(event.button!==0||this.busy)return;
   this.pending={source:{...source,handle:node},x:event.clientX,y:event.clientY,time:performance.now(),pointer:event.pointerId,type:event.pointerType};
   if(event.pointerType!=='touch'){event.preventDefault();this.pick(this.pending.source);}
  };
  node.onkeydown=event=>{
   if([' ','Enter'].includes(event.key)&&!this.source){event.preventDefault();event.stopPropagation();this.pick({...source,handle:node});}
  };
 }
 target(node,target){
  node.dataset.dropTarget=String(this.targets.length);
  const value={node,...target};this.targets.push(value);
  if(node.classList.contains('u2-reorder-target')){node.tabIndex=-1;node.setAttribute('aria-hidden','true');}
  node.onclick=()=>{if(this.source&&!this.busy){this.select(value);this.release();}};
 }
 pick(source){
  if(this.busy)return;
  this.source=source;this.current=null;this.bar.hidden=false;
  this.root.classList.add('u2-pickup-active');
  source.handle?.setAttribute('aria-pressed','true');source.handle?.closest('[data-sort-row]')?.classList.add('u2-reserved');
  this.status.textContent='Moving “'+source.label+'”. Choose a destination or Cancel.';
  this.ghost=element('div',source.label,{class:'u2-drag-ghost','aria-hidden':'true'});this.root.append(this.ghost);this.ghost.hidden=true;
  for(const target of this.targets){const reason=this.eligible(source,target);target.node.dataset.dropValid=String(!reason);target.node.setAttribute('aria-label',target.label+(reason?' — '+reason:''));if(target.node.classList.contains('u2-reorder-target')){target.node.tabIndex=0;target.node.setAttribute('aria-hidden','false');}}
 }
 select(target){
  this.current?.node.classList.remove('u2-drop-selected','u2-drop-invalid');this.current=target;
  const reason=this.eligible(this.source,target);target.node.classList.add(reason?'u2-drop-invalid':'u2-drop-selected');
  this.status.textContent=reason?'Cannot place “'+this.source.label+'”: '+reason:target.label+' — “'+this.source.label+'”.';
 }
 pointerMove(event){
  const pending=this.pending;if(!pending||event.pointerId!==pending.pointer||this.busy)return;
  if(!this.source){
   if(pending.type!=='touch'||performance.now()-pending.time<300||Math.hypot(event.clientX-pending.x,event.clientY-pending.y)<=8)return;
   this.pick(pending.source);
  }
  event.preventDefault();this.ghost.hidden=false;this.ghost.style.left=event.clientX+12+'px';this.ghost.style.top=event.clientY+12+'px';
  const hit=document.elementsFromPoint(event.clientX,event.clientY).map(node=>node.closest('[data-drop-target]')).find(node=>node&&this.root.contains(node));
  const target=hit?this.targets[Number(hit.dataset.dropTarget)]:null;
  if(target)this.select(target);else{this.current?.node.classList.remove('u2-drop-selected','u2-drop-invalid');this.current=null;this.status.textContent='Choose a valid destination for “'+this.source.label+'”, or Cancel.';}
  this.velocity=event.clientY<48?-0.4:event.clientY>innerHeight-48?0.4:0;
  if(this.velocity&&!this.frame){this.previous=performance.now();this.frame=requestAnimationFrame(now=>this.scroll(now));}
 }
 scroll(now){
  this.frame=null;if(!this.source||!this.pending||!this.velocity||this.busy)return;
  window.scrollBy(0,this.velocity*Math.min(24,now-this.previous));this.previous=now;
  this.frame=requestAnimationFrame(time=>this.scroll(time));
 }
 stopScroll(){if(this.frame)cancelAnimationFrame(this.frame);this.frame=null;this.velocity=0;}
 pointerUp(event){
  if(!this.pending||event.pointerId!==this.pending.pointer)return;
  this.pending=null;this.stopScroll();if(this.source&&!this.busy)this.release();
 }
 async release(){
  if(!this.source||this.busy)return;
  if(!this.current||this.eligible(this.source,this.current)){
   this.status.textContent='Choose a valid destination for “'+this.source.label+'”, or Cancel.';return;
  }
  this.busy=true;this.ghost.hidden=true;this.stopScroll();
  try{
   if(this.current.kind==='create')await this.create(this.source,this.current,this);
   else await this.commit(this.source,this.current);
  }catch(error){this.status.textContent=error.message;this.busy=false;}
 }
 key(event){
  if(!this.source||this.busy||['INPUT','TEXTAREA','SELECT'].includes(event.target.tagName))return;
  if(event.key==='Escape'){event.preventDefault();this.cancel();return;}
  if(['ArrowDown','ArrowRight','ArrowUp','ArrowLeft'].includes(event.key)){
   event.preventDefault();const direction=['ArrowDown','ArrowRight'].includes(event.key)?1:-1;
   const visible=this.targets.filter(t=>t.node.getClientRects().length),index=visible.indexOf(this.current);
   if(visible.length){const target=visible[(index+direction+visible.length)%visible.length];this.select(target);target.node.focus();}
  }else if(event.key==='Enter'||event.key===' '){event.preventDefault();this.release();}
 }
 cancel(){
  if(!this.cancelAllowed()){this.status.textContent='Outcome unknown. Retry the exact request or reload saved state.';return;}
  const handle=this.source?.handle;this.pending=null;this.stopScroll();this.clear();handle?.focus();
 }
 clear(){
  this.current?.node.classList.remove('u2-drop-selected','u2-drop-invalid');
  this.source?.handle?.setAttribute('aria-pressed','false');this.source?.handle?.closest('[data-sort-row]')?.classList.remove('u2-reserved');
  this.ghost?.remove();this.source=null;this.current=null;this.busy=false;this.bar.hidden=true;
  this.root.classList.remove('u2-pickup-active');
  for(const target of this.targets)if(target.node.classList.contains('u2-reorder-target')){target.node.tabIndex=-1;target.node.setAttribute('aria-hidden','true');}
 }
 dispose(){this.stopScroll();this.abort.abort();this.observer.disconnect();this.clear();this.bar.remove();}
}
