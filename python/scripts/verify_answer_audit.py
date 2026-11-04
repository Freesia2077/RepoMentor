"""Recompute saved-answer diagnostics and assert known regression boundaries offline."""

import json

from audit_saved_answers import DEFAULT_OUTPUT, ROOT, load, sha

from repomentor.answer_audit import audit_answer
from repomentor.paths import data_root
from repomentor.repository import SnapshotStore


def main():
    protocol = load(DEFAULT_OUTPUT / "protocol.json")
    for name, digest in protocol["source_files"].items():
        assert sha(ROOT / name) == digest, name
    assert protocol["implementation_sha256"] == sha(ROOT / "src/repomentor/answer_audit.py")
    assert protocol["runner_sha256"] == sha(ROOT / "scripts/audit_saved_answers.py")
    store = SnapshotStore(data_root() / "corpus")
    rows = load(DEFAULT_OUTPUT / "review-packet.json")
    for row in rows:
        assert row["split"] == "validation" and sha(ROOT / row["source_file"]) == row["source_sha256"]
        reader = store.open(row["snapshot_id"])
        assert reader.snapshot.commit_sha == row["commit_sha"]
        case = load(ROOT / row["source_file"])
        stages = case["stages"] if case["kind"] == "report" else [case["result"]]
        stage = next(s for s in stages if s["attempt_id"] == row["attempt_id"])
        assert row["observed_context"] == stage.get("final_context", stage.get("context", []))
        assert row["audit"] == audit_answer(reader, stage.get("answer"), row["observed_context"])
    by_id = {row["review_id"]: row for row in rows}
    mentor = by_id["p6-rag-context-fix/report-mitt-isolated/mentor"]["audit"]
    assert mentor["dependency_edges"][0]["status"] == "not_a_static_import"
    # A contradictory answer can have perfectly valid citation ranges: do not score it correct.
    qa = by_id["p6-rag-context/off--developit--mitt-p4-cross_case/qa"]["audit"]
    assert all(r["status"] == "visible_range" for r in qa["claims"][0]["references"])
    assert qa["claims"][0]["factual_correctness"] == "needs_review"
    commands = [c for row in rows for c in row["audit"]["commands"]]
    assert len(commands) == 6 and all(c["status"] == "declared_script" and c["execution"] == "not_run" for c in commands)
    failures = sum(r["stop_reason"] == "report_grounding" for r in rows)
    assert failures == 2  # Rejected parsed outputs remain available for diagnosis, not counted as success.
    print(json.dumps({"recomputed_stage_appearances": len(rows), "preserved_rejected_outputs": failures,
                      "historical_files_unchanged": len(protocol["source_files"]),
                      "regression_checks": "passed", "new_model_requests": 0, "semantic_accuracy": "not_scored"}))


if __name__ == "__main__":
    main()
