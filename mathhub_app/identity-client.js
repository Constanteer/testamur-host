/* Public identity adapter.
   The canonical server returns opaque public IDs in claim_id/proof_id/build_id.
   Keep those available to the presentation layer while titles and Lean names
   remain the primary visible identity. */

const identityBaseRegistryToUi = registryToUi;
registryToUi = function publicRegistryToUi(item) {
  const ui = identityBaseRegistryToUi(item);
  ui.publicId = item.public_id || item.claim_id || '';
  ui.slug = item.slug || '';
  ui.canonicalPath = ui.slug && ui.publicId
    ? `/claim/${ui.slug}--${ui.publicId}`
    : (ui.publicId ? `/claim/${ui.publicId}` : '');
  return ui;
};

function copyCanonicalRef(value) {
  if (!value) return;
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(value).catch(() => {});
  }
}

function decorateCanonicalReference() {
  if (state.view !== 'claim') return;
  const c = claims.find(x => x.id === state.selected);
  if (!c?.registry || !c.publicId) return;
  const rail = document.querySelector('.meta-rail');
  if (!rail || rail.querySelector('[data-canonical-ref]')) return;
  const row = document.createElement('div');
  row.className = 'meta-row canonical-ref-row';
  row.innerHTML = `<div class="meta-label">Canonical reference</div><div class="canonical-ref-line"><code data-canonical-ref>${esc(c.publicId)}</code><button class="copy-ref" type="button" title="Copy canonical reference">copy</button></div>${c.slug ? `<div class="canonical-slug">${esc(c.slug)}</div>` : ''}`;
  rail.prepend(row);
  row.querySelector('.copy-ref')?.addEventListener('click', event => {
    copyCanonicalRef(c.publicId);
    const button = event.currentTarget;
    button.textContent = 'copied';
    setTimeout(() => { button.textContent = 'copy'; }, 900);
  });
}

function decorateIdentityExamples() {
  const schema = document.querySelector('.registry-schema');
  if (!schema) return;
  schema.textContent = `Claim  CLM-7Q2M-K9F4-D8VN-3S1R\n  ├─ version history\n  ├─ Proof  PRF-4AXT-P9N7-W2KC-H8ME\n  │    └─ Build  BLD-9T6A-P2RE-7XKV-M3JD\n  └─ additional proofs / builds…`;
}

const identityBaseRender = render;
render = function identityRender() {
  identityBaseRender();
  decorateCanonicalReference();
  decorateIdentityExamples();
};
