/* Canonical Claim references in semantic-relation UI.
   Validation belongs to the public API boundary; the browser should not assume
   numeric CLM-000123 identifiers. */

function decoratePublicRelationInput() {
  const input = document.querySelector('#relation-target');
  if (!input) return;
  input.placeholder = 'CLM-7Q2M-K9F4-D8VN-3S1R';
  input.removeAttribute('pattern');
  input.autocomplete = 'off';
  input.title = 'Paste a MathHub canonical Claim reference';
}

const publicRelationBaseRender = render;
render = function publicRelationRender() {
  publicRelationBaseRender();
  decoratePublicRelationInput();
};
