from testamur.project_advisory_revalidation import project_advisory_revalidation


def test_review_required_becomes_explicit_revalidation_work():
    result = project_advisory_revalidation([
        {
            "event_revision_id": "adv-rev-1",
            "requires_review": True,
            "competing_subject_revision_ids": [],
        }
    ])

    assert result["requires_revalidation"] is True
    assert result["reasons"] == ["advisory-review-required"]
    assert result["semantics"]["candidate_is_affectedness_verdict"] is False
    assert result["semantics"]["recorded_assessment_is_verification"] is False


def test_mechanical_change_never_manufactures_affectedness_or_invalidity():
    result = project_advisory_revalidation(
        [],
        supply_chain_diff={
            "counts": {
                "dependencies_added": 1,
                "dependencies_removed": 1,
                "dependencies_changed": 1,
            }
        },
    )

    assert result["dependency_observation_changed"] is True
    assert "dependency-observation-changed" in result["reasons"]
    assert result["requires_revalidation"] is False
    assert result["semantics"]["changed_implies_invalid"] is False
    assert result["semantics"]["changed_implies_affected"] is False
    assert result["semantics"]["mechanical_diff_rewrites_assessment"] is False


def test_competing_heads_require_resolution_without_collapsing_them():
    result = project_advisory_revalidation([
        {
            "event_revision_id": "adv-rev-2",
            "requires_review": True,
            "competing_subject_revision_ids": ["component-a"],
        }
    ])

    assert result["requires_revalidation"] is True
    assert result["competing_count"] == 1
    assert "competing-assessment-heads" in result["reasons"]
    assert result["reviews"][0]["competing_subject_revision_ids"] == ["component-a"]
    assert result["semantics"]["generic_trust_score_used"] is False
