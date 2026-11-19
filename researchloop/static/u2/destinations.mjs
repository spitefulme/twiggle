// One hierarchy/path/depth contract for researcher and participant alternatives.
export function destinations(groups,{source,action='create',ranks=[],totalGroups=groups.length,mode='hybrid'}={}){
 const byId=new Map(groups.map(group=>[group.groupId,group]));
 const order=new Map(ranks.map(row=>[row.groupId,row.order]));
 const children=id=>groups.filter(group=>(group.parentGroupId??null)===id).sort((a,b)=>(order.get(a.groupId)??a.order??0)-(order.get(b.groupId)??b.order??0));
 const ordered=[],seen=new Set();
 function walk(id,path=[]){for(const group of children(id)){if(seen.has(group.groupId))continue;seen.add(group.groupId);const full=[...path,group.label];ordered.push({group,path:full});walk(group.groupId,full);}}
 walk(null);
 const pathCounts=new Map();
 for(const entry of ordered){const path=entry.path.join(' › ');pathCounts.set(path,(pathCounts.get(path)??0)+1);}
 function subtreeHeight(id,visited=new Set()){
  if(visited.has(id))return 6;const next=new Set(visited).add(id);
  return 1+Math.max(0,...children(id).map(child=>subtreeHeight(child.groupId,next)));
 }
 const height=source?.kind==='group'?subtreeHeight(source.id):1;
 return [null,...ordered].map(entry=>{
  const group=entry?.group,parent=group?.groupId??null,parentDepth=entry?.path.length??0;
  const resultingDepth=parentDepth+(action==='move'&&source?.kind==='card'?0:action==='create'&&source?.kind==='group'?height+1:height);
  let reason='';
  if(action==='create'&&mode==='closed')reason='Closed sort: fixed groups only';
  else if(action==='create'&&totalGroups>=100)reason='100 total groups reached';
  else if(source?.kind==='group'){
   let cursor=group;const visited=new Set();
   while(cursor&&!visited.has(cursor.groupId)){
    if(cursor.groupId===source.id){reason='Cannot put a group inside itself or its descendants';break;}
    visited.add(cursor.groupId);cursor=byId.get(cursor.parentGroupId);
   }
  }
  if(!reason&&resultingDepth>5)reason='Maximum five levels, including descendants';
  const ownership=group?(group.owner==='participant'?'Participant-owned':'Fixed researcher-owned'):'No parent';
  const path=entry?.path.join(' › ')??'Top level';
  const disambiguator=group&&pathCounts.get(path)>1?' [group '+group.groupId+']':'';
  return {parent,path,ownership,resultingDepth,reason,label:path+disambiguator+' · '+ownership+' · resulting depth '+resultingDepth};
 });
}
