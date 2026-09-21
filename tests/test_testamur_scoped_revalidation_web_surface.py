from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "testamur" / "web" / "advisory-revalidation-ui.js"


def test_dependency_compare_context_survives_into_revalidation_navigation():
    source = ASSET.read_text(encoding="utf-8")
    assert "testamur.reviewContext" in source
    assert "supply-chain-compare" in source
    assert "data-revalidation-context-match" in source
    assert "Open scoped revalidation" in source
    assert "params.set('dependency', context.dependency)" in source
    assert "params.set('component', context.component_id)" in source
    assert "does not establish affectedness, validity, safety, or reliance" in source
    assert "Changed ≠ invalid; stale ≠ false" in source
