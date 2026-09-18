/* Dependency coverage is a navigation/read-model signal, not proof verification. */
const coverageUiState={resolving:new Set(),importing:new Set(),error:''};
function coverageLabel(c){
  if(!c||c.status==='unavailable')return 'coverage unavailable';
  if(c.status==='complete')return 'dependency-complete';
  if(c.percent!==null&&c.percent!==undefined)return `${Number(c.percent)}% theorem deps mapped`;
  return 'dependency coverage partial';
}
function coverageTone(c){return c?.status==='complete'?'complete':c?.status==='partial'?'partial':'unavailable';}
function coverageStats(c){
  if(!c||c.status==='unavailable')return '<span>no complete argument audit</span>';
  const parts=[
    `<span><strong>${Number(c.mapped_current||0)}</strong> mapped</span>`,
    Number(c.unresolved_theorems||0)?`<span><strong>${Number(c.unresolved_theorems)}</strong> unresolved theorem${Number(c.unresolved_theorems)===1?'':'s'}</span>`:'',
    Number(c.stale_mapped||0)?`<span><strong>${Number(c.stale_mapped)}</strong> stale revision${Number(c.stale_mapped)===1?'':'s'}</span>`:'',
    Number(c.substrate_refs||0)?`<span><strong>${Number(c.substrate_refs)}</strong> substrate refs</span>`:'',
    Number(c.unclassified_refs||0)?`<span><strong>${Number(c.unclassified_refs)}</strong> unclassified</span>`:'',
    Number(c.missing_refs||0)?`<span><strong>${Number(c.missing_refs)}</strong> missing</span>`:'',
  ].filter(Boolean);
  return parts.join('');
}
function coverageSamples(c){
  if(!c||c.status!=='partial')return '';
  const groups=[
    ['Unresolved theorem dependencies',c.unresolved_theorem_sample],
    ['Mapped to stale Claim revisions',c.stale_mapped_sample],
    ['Unclassified proof constants',c.unclassified_sample],
    ['Missing in this import environment',c.missing_sample],
  ].filter(([,items])=>items?.length);
  if(!groups.length)return '';
  return `<details class="coverage-details"><summary>Coverage gaps</summary>${groups.map(([title,items])=>`<div class="coverage-gap"><strong>${esc(title)}</strong><div>${items.map(x=>`<code>${esc(x)}</code>`).join('')}</div></div>`).join('')}</details>`;
}
function claimCoverageHtml(c){
  if(!c)return '';
  return `<section class="claim-coverage ${coverageTone(c)}"><div class="coverage-head"><span class="coverage-dot"></span><strong>${esc(coverageLabel(c))}</strong>${c.best_proof_id?`<small>best route ${esc(c.best_proof_id)}</small>`:''}</div><div class="coverage-stats">${coverageStats(c)}</div><p>Coverage describes how much of the current Proof argument route is represented by registered theorem Claims. Formal substrate does not count as missing mathematics.</p>${coverageSamples(c)}</section>`;
}
function proofCoverageHtml(c,proofId){
  if(!c)return '';
  const resolving=coverageUiState.resolving.has(proofId),importing=coverageUiState.importing.has(proofId),unknown=Number(c.unclassified_refs||0),unresolved=Number(c.unresolved_theorems||0),shown=(c.unresolved_theorem_sample||[]).length;
  const actions=[
    unknown?`<button class="btn coverage-resolve" data-resolve-proof-coverage="${esc(proofId||'')}" ${resolving?'disabled':''}>${resolving?'Classifying…':`Classify up to ${Math.min(200,unknown)} gaps`}</button>`:'',
    unresolved&&shown?`<button class="btn coverage-import" data-import-proof-coverage="${esc(proofId||'')}" ${importing?'disabled':''}>${importing?'Creating task…':`Import next ${shown} theorem gap${shown===1?'':'s'}`}</button>`:'',
  ].filter(Boolean).join('');
  return `<div class="proof-coverage ${coverageTone(c)}"><div><span class="coverage-dot"></span><strong>${esc(coverageLabel(c))}</strong></div><div class="coverage-stats">${coverageStats(c)}</div>${actions?`<div class="coverage-actions">${actions}</div>`:''}${coverageSamples(c)}</div>`;
}

function currentProofCoverage(proofId){
  const c=claims.find(x=>x.id===state.selected)||claims[0],argument=c?argFor(c):null;
  return (argument?.proofs||[]).find(p=>p.proof_id===proofId)?.coverage||null;
}
async function resolveProofCoverage(proofId){
  if(!proofId||coverageUiState.resolving.has(proofId))return;
  coverageUiState.resolving.add(proofId);coverageUiState.error='';render();
  try{
    await apiJson(`/api/proofs/${encodeURIComponent(proofId)}/coverage/resolve`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({limit:200})});
    if(state.selected&&typeof loadArgument==='function')await loadArgument(state.selected,{force:true});
  }catch(error){coverageUiState.error=error.message||String(error);}
  finally{coverageUiState.resolving.delete(proofId);render();}
}
async function importProofCoverageGaps(proofId){
  if(!proofId||coverageUiState.importing.has(proofId))return;
  const coverage=currentProofCoverage(proofId),names=[...new Set((coverage?.unresolved_theorem_sample||[]).filter(Boolean))];
  if(!names.length)return;
  coverageUiState.importing.add(proofId);coverageUiState.error='';render();
  try{
    const task=await createImportTask({declaration_names:names,imports:proofImports(proofId),max_depth:2,max_claims:Math.max(160,names.length)},{open:true,run:true});
    importTaskState.selected=task.task_id;importTaskState.open=true;renderImportTasks();
  }catch(error){coverageUiState.error=error.message||String(error);}
  finally{coverageUiState.importing.delete(proofId);render();}
}

const coverageProofHtmlBase=proofHtml;
proofHtml=function proofHtmlWithCoverage(p,i){
  const html=coverageProofHtmlBase(p,i),block=proofCoverageHtml(p.coverage,p.proof_id);
  return block?html.replace('</header>',`</header>${block}`):html;
};

const coverageRenderClaimBase=renderClaim;
renderClaim=function renderClaimWithCoverage(){
  const html=coverageRenderClaimBase();
  const c=claims.find(x=>x.id===state.selected)||claims[0];
  if(!c)return html;
  const coverage=(argFor(c)||{}).coverage,block=claimCoverageHtml(coverage),error=coverageUiState.error?`<div class="callout warn coverage-error">${esc(coverageUiState.error)}</div>`:'';
  if(!block)return html;
  return html.replace('</header><section class="claim-statement-surface">',`</header>${block}${error}<section class="claim-statement-surface">`);
};

const coverageBindBase=bind;
bind=function bindCoverageActions(){
  coverageBindBase();
  document.querySelectorAll('[data-resolve-proof-coverage]').forEach(button=>button.addEventListener('click',()=>resolveProofCoverage(button.dataset.resolveProofCoverage||'')));
  document.querySelectorAll('[data-import-proof-coverage]').forEach(button=>button.addEventListener('click',()=>importProofCoverageGaps(button.dataset.importProofCoverage||'')));
};
