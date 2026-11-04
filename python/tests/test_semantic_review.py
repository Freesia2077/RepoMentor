from types import SimpleNamespace

import pytest

from repomentor.semantic_review import quality_tiers, summarize_reviews, validate_judgment


def fixture():
    source = "export const x = 1"
    reader = SimpleNamespace(paths=["a.js"], read=lambda path, limit: (source, False))
    candidate = {"candidate_id": "r1", "answer": {"answer": "x is 1"}, "observed_context": []}
    value = {"candidate_id": "r1", "correctness": "correct", "citation_support": "unsupported",
        "completeness": "adequate", "abstention": "not_applicable", "entry_scope": "correct",
        "consistency": "consistent", "notes": "Correct fact but no citation.", "claims": [
            {"claim": "x is 1", "status": "supported", "rationale": "constant declaration",
             "evidence": [{"path": "a.js", "start_line": 1, "end_line": 1}]}]}
    return reader, candidate, value


def test_correct_but_incomplete_answer_is_useful_without_becoming_fully_resolved():
    _, _, value = fixture()
    value.update(citation_support="supported", completeness="partial")
    assert quality_tiers(value, "success") == {"core_supported": True, "fully_resolved": False}
    for change in ({"correctness": "partially_correct"}, {"citation_support": "unsupported"},
                   {"entry_scope": "mixed"}, {"consistency": "contradictory"}, {"abstention": "inappropriate"}):
        assert not quality_tiers({**value, **change}, "success")["core_supported"]
    assert not quality_tiers(value, "truncated")["core_supported"]


def test_partial_citation_can_be_useful_only_with_actual_visible_support():
    reader, candidate, value = fixture()
    candidate["observed_context"] = [{"path": "a.js", "start_line": 1, "end_line": 1}]
    value["citation_support"] = "partial"
    reviewed = validate_judgment(value, candidate, reader)
    assert quality_tiers(reviewed, "success") == {"core_supported": True, "fully_resolved": False}
    candidate["observed_context"] = []
    reviewed = validate_judgment(value, candidate, reader)
    assert not quality_tiers(reviewed, "success")["core_supported"]


def test_review_evidence_does_not_become_generator_evidence_or_human_approval():
    reader, candidate, value = fixture()
    result = validate_judgment(value, candidate, reader)
    assert not result["claims"][0]["verified_evidence"][0]["visible_to_generator"]
    assert result["correctness"] == "correct" and result["citation_support"] == "unsupported"
    assert not result["human_approved"] and not result["training_eligible"]


@pytest.mark.parametrize("change", ["id", "range", "path", "missing_evidence", "contradiction", "invalid_label", "missing_answer"])
def test_review_rejects_unverifiable_provenance_and_inconsistent_labels(change):
    reader, candidate, value = fixture()
    if change == "id":
        value["candidate_id"] = "wrong"
    elif change == "range":
        value["claims"][0]["evidence"][0]["end_line"] = 9
    elif change == "path":
        value["claims"][0]["evidence"][0]["path"] = "missing.js"
    elif change == "missing_evidence":
        value["claims"][0]["evidence"] = []
    elif change == "contradiction":
        value["claims"][0]["status"] = "contradicted"
    elif change == "invalid_label":
        value["abstention"] = "100%"
    else:
        candidate["answer"] = None
    with pytest.raises(ValueError):
        validate_judgment(value, candidate, reader)


def test_unknown_judgments_remain_unknown_and_cohorts_are_separate():
    reader, candidate, value = fixture()
    result = validate_judgment(value, candidate, reader)
    unknown = {**result, "candidate_id": "r2", "correctness": "unverifiable"}
    summary = summarize_reviews([result, unknown], {"r1": {"cohort": "history"}, "r2": {"cohort": "external"}})
    assert summary["cohorts"]["external"]["correctness"] == {"unverifiable": 1}
    assert summary["overall_accuracy"].startswith("not_estimated")
