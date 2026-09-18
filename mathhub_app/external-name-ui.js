/* Reader-facing imported theorem names stay canonical; MathHub aliases remain provenance. */
const externalNameBaseRenderClaim=renderClaim;
renderClaim=function renderClaimWithExternalNameProvenance(){
  const html=externalNameBaseRenderClaim();
  const c=claims.find(x=>x.id===state.selected)||claims[0];
  if(!c)return html;
  const argument=argFor(c),claim=argument?.claim||{};
  if(claim.lean_name_source!=='imported-declaration'||!claim.formal_lean_name||!claim.lean_name)return html;
  const note=`<div class="external-lean-binding"><span>External Lean declaration</span><code>${esc(claim.lean_name)}</code><span class="external-binding-arrow">↔</span><span>MathHub verification alias</span><code>${esc(claim.formal_lean_name)}</code></div>`;
  return html.replace('<pre class="formal-declaration">',`${note}<pre class="formal-declaration">`);
};
