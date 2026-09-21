from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUIDANCE = ROOT / "testamur" / "web" / "supply-chain-guidance.js"
ACTIONS = ROOT / "testamur" / "web" / "supply-chain-actions.js"


def test_transition_rows_expose_mechanical_component_context():
    text = GUIDANCE.read_text(encoding="utf-8")
    for marker in (
        "data-dependency-name",
        "data-component-id",
        "data-before-version",
        "data-after-version",
        "data-transition",
    ):
        assert marker in text


def test_row_review_carries_context_without_asserting_affectedness():
    text = ACTIONS.read_text(encoding="utf-8")
    assert "Review impact" in text
    assert "testamur.reviewContext" in text
    assert "testamur:review-context" in text
    assert "source: 'supply-chain-compare'" in text
    assert "does not establish affectedness" in text
    assert "carries component context only" in text
