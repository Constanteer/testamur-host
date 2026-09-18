/* Web navigation state for the mathematical graph.
   Graph centers are shareable/bookmarkable without serializing the graph itself. */
const graphWebState={trail:[],restoring:false};

function graphWebLabel(id){
  const live=(argumentState.graph?.nodes||[]).find(node=>node.id===id);
  if(live)return live.title||live.lean_name||id;
  const cached=registryState?.claims?.get?.(id);
  return cached?.title||cached?.lean_name||id;
}

function graphTrailRecord(id){
  if(!id||graphWebState.restoring)return;
  const last=graphWebState.trail[graphWebState.trail.length-1];
  if(last?.id===id)return;
  graphWebState.trail.push({id,label:graphWebLabel(id)});
  if(graphWebState.trail.length>8)graphWebState.trail.shift();
}

function graphTrailRefreshLabel(id){
  const item=[...graphWebState.trail].reverse().find(entry=>entry.id===id);
  if(item)item.label=graphWebLabel(id);
}

function graphWebUrl(root=''){
  const url=new URL(window.location.href);
  url.searchParams.set('view','graph');
  if(root)url.searchParams.set('claim',root);else url.searchParams.delete('claim');
  url.searchParams.set('depth',String(argumentState.depth||1));
  return url;
}

function syncGraphUrl(root,{push=false}={}){
  if(graphWebState.restoring)return;
  const url=graphWebUrl(root),method=push?'pushState':'replaceState';
  history[method]({view:'graph',claim:root,depth:argumentState.depth||1},'',url);
}

const graphWebBaseLoadGraph=loadGraph;
loadGraph=async function webAwareLoadGraph(root=''){
  const previous=argumentState.graph?.root||'',changed=root!==previous;
  if(root&&changed)graphTrailRecord(root);
  if(!graphWebState.restoring)syncGraphUrl(root,{push:changed});
  const result=await graphWebBaseLoadGraph(root);
  if(root){
    graphTrailRefreshLabel(root);
    if(!graphWebState.restoring)syncGraphUrl(root,{push:false});
    if(state.view==='graph')render();
  }
  return result;
};

function graphTrailHtml(){
  if(graphWebState.trail.length<2)return '';
  return `<nav class="graph-trail" aria-label="Graph navigation path"><span>Path</span>${graphWebState.trail.map((item,index)=>`<button type="button" data-graph-trail="${esc(item.id)}" title="${esc(item.label)}"><small>${index+1}</small>${esc(item.label.length>34?item.label.slice(0,33)+'…':item.label)}</button>`).join('<i>›</i>')}</nav>`;
}

const graphWebBaseRenderGraph=renderGraph;
renderGraph=function webAwareRenderGraph(){
  const html=graphWebBaseRenderGraph(),trail=graphTrailHtml();
  if(!trail)return html;
  if(html.includes('<div class="graph-window-meta">'))return html.replace('<div class="graph-window-meta">',`${trail}<div class="graph-window-meta">`);
  return html;
};

const graphWebBaseBind=bind;
bind=function webAwareGraphBind(){
  graphWebBaseBind();
  if(state.view!=='graph')return;
  document.querySelectorAll('[data-graph-trail]').forEach(button=>button.addEventListener('click',()=>{const id=button.dataset.graphTrail;if(id){state.graphSelected=id;loadGraph(id);}}));
};

function graphStateFromLocation(){
  const params=new URL(window.location.href).searchParams;
  if(params.get('view')!=='graph')return null;
  const claim=params.get('claim')||'',depth=Math.max(1,Math.min(2,Number(params.get('depth')||1)||1));
  return {claim,depth};
}

async function restoreGraphLocation(){
  const target=graphStateFromLocation();if(!target)return;
  graphWebState.restoring=true;
  try{
    state.view='graph';argumentState.depth=target.depth;
    if(target.claim){state.graphSelected=target.claim;graphWebState.trail=[{id:target.claim,label:target.claim}];}
    render();await graphWebBaseLoadGraph(target.claim);if(target.claim)graphTrailRefreshLabel(target.claim);
  }finally{graphWebState.restoring=false;render();}
}

window.addEventListener('popstate',()=>restoreGraphLocation());
window.addEventListener('DOMContentLoaded',()=>{
  restoreGraphLocation();
  document.addEventListener('click',event=>{
    const view=event.target.closest?.('[data-view]')?.dataset?.view;
    if(!view)return;
    if(view==='graph'){
      queueMicrotask(()=>syncGraphUrl(argumentState.graph?.root||'',{push:false}));
      return;
    }
    const url=new URL(window.location.href);url.searchParams.delete('view');url.searchParams.delete('claim');url.searchParams.delete('depth');history.replaceState({},'',url);
  });
});
