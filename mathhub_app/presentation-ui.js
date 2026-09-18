/* Deterministic presentation layer. No AI-generated prose enters this surface. */
const presentationState={cache:new Map(),loading:new Set()};

async function loadPresentations(claimId,{force=false}={}){
  if(!live(claimId))return null;
  if(!force&&presentationState.cache.has(claimId))return presentationState.cache.get(claimId);
  if(presentationState.loading.has(claimId))return null;
  presentationState.loading.add(claimId);
  try{
    const data=await apiJson(`/api/claims/${claimId}/presentations`);
    presentationState.cache.set(claimId,data);
    return data;
  }finally{
    presentationState.loading.delete(claimId);
    if(state.view==='claim'&&state.selected===claimId)render();
  }
}

function presentationCards(data){
  const curated=data?.curated||[];
  if(!curated.length)return '<div class="presentation-empty">No human presentation has been added. The deterministic canonical rendering remains available.</div>';
  return curated.map(p=>`<article class="presentation-card"><div class="presentation-card-head"><span>${esc(p.kind)}</span><span>${esc(p.language||'und')}</span></div>${p.text?`<p>${esc(p.text)}</p>`:''}${p.latex?`<div class="presentation-latex">\\[${p.latex}\\]</div>`:''}<div class="presentation-byline">${p.author?`by ${esc(p.author)} · `:''}${esc(p.created_at||'')}</div></article>`).join('');
}

function addPresentationHtml(){
  return `<details class="add-presentation"><summary>Add a human presentation</summary><form id="presentation-form"><div class="presentation-form-grid"><div class="field"><label>Kind</label><select id="presentation-kind"><option value="concise">Concise</option><option value="textbook">Textbook</option><option value="intuitive">Intuitive</option><option value="translation">Translation</option></select></div><div class="field"><label>Language</label><input id="presentation-language" value="und" placeholder="en / zh / und"/></div></div><div class="field"><label>Text</label><textarea id="presentation-text" placeholder="Human-written mathematical wording"></textarea></div><div class="field"><label>LaTeX (optional)</label><textarea id="presentation-latex" placeholder="Optional display formula"></textarea></div><div class="field"><label>Author</label><input id="presentation-author" placeholder="name / handle"/></div><button class="btn" type="submit">Add presentation</button><p class="section-note">This changes presentation only. It does not create a new Claim revision or affect Lean verification.</p></form></details>`;
}

function canonicalBasis(canonical){
  const basis=canonical?.ir?.input_basis||'';
  if(basis==='lean-pretty-type')return {label:'Lean-elaborated',className:'lean',detail:'rendered from the elaborated Lean type'};
  return {label:'source fallback',className:'fallback',detail:'rendered deterministically from the formal declaration source'};
}

function decoratePresentationClaim(){
  if(state.view!=='claim'||!live(state.selected))return;
  const claimId=state.selected;
  const data=presentationState.cache.get(claimId);
  if(!data){if(!presentationState.loading.has(claimId))loadPresentations(claimId).catch(()=>{});return;}
  const canonical=data.canonical;
  const preferred=data.preferred;
  const surface=document.querySelector('.claim-statement-surface');
  const header=document.querySelector('.argument-claim-header p');
  if(header&&preferred?.text)header.textContent=preferred.text;
  if(surface&&canonical){
    const label=surface.querySelector('.surface-label');
    if(label)label.textContent='Canonical mathematical rendering';
    const math=surface.querySelector('.formal-math');
    if(math){
      if(canonical.latex){
        math.innerHTML=`\\[${canonical.latex}\\]`;
        math.classList.remove('canonical-text-only');
        math.classList.add('canonical-latex');
        math.setAttribute('aria-label',canonical.text||'Canonical mathematical rendering');
      }else{
        math.textContent=canonical.text||'—';
        math.classList.add('canonical-text-only');
        math.classList.remove('canonical-latex');
      }
    }
    let meta=surface.querySelector('.canonical-render-meta');
    if(!meta){
      meta=document.createElement('div');
      meta.className='canonical-render-meta';
      surface.querySelector('.formal-declaration')?.before(meta);
    }
    const basis=canonicalBasis(canonical);
    const environmentBound=Boolean(canonical.ir?.environment_hash||canonical.ir?.formal_fingerprint);
    meta.innerHTML=`<span>deterministic</span><span>${esc(canonical.renderer_version||'renderer')}</span><span class="canonical-basis ${basis.className}" title="${esc(basis.detail)}">${esc(basis.label)}</span>${environmentBound?'<span>environment-bound</span>':''}`;
  }
  if(surface&&!document.querySelector('.presentation-section')){
    const section=document.createElement('section');
    section.className='presentation-section';
    section.innerHTML=`<div class="route-section-head"><div><div class="eyebrow">Presentations</div><h2>Ways to read this Claim</h2></div></div><p class="presentation-note">The canonical rendering is deterministic. Human presentations can be concise, textbook-style, intuitive, or translated; none of them changes the formal Claim.</p><div class="presentation-list">${presentationCards(data)}</div>${addPresentationHtml()}`;
    surface.after(section);
    const form=section.querySelector('#presentation-form');
    form?.addEventListener('submit',async event=>{
      event.preventDefault();
      const button=form.querySelector('button[type="submit"]');
      if(button){button.disabled=true;button.textContent='Adding…';}
      try{
        await apiJson(`/api/claims/${claimId}/presentations`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:section.querySelector('#presentation-kind')?.value||'concise',language:section.querySelector('#presentation-language')?.value||'und',text:section.querySelector('#presentation-text')?.value||'',latex:section.querySelector('#presentation-latex')?.value||'',author:section.querySelector('#presentation-author')?.value||''})});
        presentationState.cache.delete(claimId);
        await loadPresentations(claimId,{force:true});
      }catch(err){alert(`Could not add presentation: ${err.message}`);}
      finally{if(button){button.disabled=false;button.textContent='Add presentation';}}
    });
  }
  if(window.MathJax?.typesetPromise){window.MathJax.typesetPromise().catch(()=>{});}
}

window.addEventListener('mathhub:presentation-stale',event=>{
  const claimId=event.detail?.claimId;
  if(!claimId)return;
  presentationState.cache.delete(claimId);
  if(state.view==='claim'&&state.selected===claimId){
    loadPresentations(claimId,{force:true}).catch(()=>{});
  }
});

// argument-ui supplies the public Claim surface; extend its post-render decorator.
const presentationBaseDecorate=decorateRegistryClaim;
decorateRegistryClaim=function(){presentationBaseDecorate();decoratePresentationClaim();};

// Human wording and title are optional on registration. The formal theorem/lemma
// is enough to create a Claim; the server deterministically fills display fallbacks.
const presentationBaseRenderSubmit=renderSubmit;
renderSubmit=function(){
  let html=presentationBaseRenderSubmit();
  html=html.replace('<label>Title</label><input id="draft-title" required/>','<label>Human title <span class="optional-label">optional</span></label><input id="draft-title" placeholder="Leave blank to derive from the Lean declaration name"/>');
  html=html.replace('<label>Statement</label><textarea id="draft-human" required></textarea>','<label>Human statement <span class="optional-label">optional</span></label><textarea id="draft-human" placeholder="Leave blank to use the deterministic canonical rendering"></textarea>');
  html=html.replace('Register a mathematical statement.','Register a formal mathematical Claim.');
  html=html.replace('The Claim owns the formal statement. An optional Proof owns its source.','A theorem/lemma declaration is sufficient. Human wording is presentation data and can be added later.');
  return html;
};
