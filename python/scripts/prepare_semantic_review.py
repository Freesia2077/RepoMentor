"""Anonymize selected saved candidates; retain identity mapping outside reviewer packets."""

import json
from pathlib import Path

from run_rag_quality import frozen, sha

from repomentor.semantic_review import RUBRIC, candidate_digest

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments/p6-d-review"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def prepare():
    candidates, identities, seen = [], {}, {}
    paths = []
    for folder in ("p6-rag-context", "p6-rag-context-fix", "p6-d-external"):
        paths.extend(sorted((ROOT / "experiments" / folder / "cases").glob("*.json")))
    paths.extend(sorted(p for p in (ROOT / "experiments/p6-rag-quality/cases").glob("*.json")
                        if "unknown_business" in p.name or "schedule-04" in p.name))
    for path in paths:
        record = load(path)
        question = record.get("question", record.get("input", {}).get("question"))
        stages = record["stages"] if record.get("kind") == "report" else [record["result"]]
        for stage in stages:
            appearance = {"source_file": path.relative_to(ROOT).as_posix(), "sha256": sha(path),
                          "stage": stage.get("stage", "qa"), "mode": record.get("mode"),
                          "attempt_id": stage.get("attempt_id"), "stop_reason": stage.get("stop_reason"),
                          "workflow_status": stage["completion"]["status"]}
            attempt = stage.get("attempt_id") or path.relative_to(ROOT).as_posix()
            cid = "candidate-" + candidate_digest(attempt)[:12]
            candidate = {"candidate_id": cid, "question": question, "answer": stage.get("answer"),
                         "observed_context": stage.get("final_context", stage["context"])}
            if attempt in seen:
                assert seen[attempt] == candidate, "same attempt has different candidate content"
                identities[cid]["appearances"].append(appearance)
                continue
            seen[attempt] = candidate
            candidates.append(candidate)
            external = record["split"] == "external-development"
            cohort = "external-" + record["mode"] if external else (
                "historical-report" if record.get("kind") == "report" else "historical-qa")
            identities[cid] = {"cohort": cohort, "snapshot_id": record["snapshot_id"],
                               "commit_sha": record["commit_sha"], "repository": record["repository"],
                               "split": record["split"], "task_id": record.get("task_id"),
                               "appearances": [appearance]}
    assert len([c for c in identities.values() if c["split"] == "external-development"]) == 12
    candidates.sort(key=lambda c: c["candidate_id"])
    frozen(OUT / "rubric.json", RUBRIC)
    frozen(OUT / "candidates.json", candidates)
    frozen(OUT / "identity-map.json", identities)
    frozen(OUT / "protocol.json", {"reviewer": "Codex (current session), explicitly chosen by owner",
        "blinding": "Identity-stripped packets available, actual reviewer has prior context: nonblind",
        "selection": "All C/C-fix stage outputs, four saved abstention/contradiction controls, twelve external outputs",
        "unique_candidates": len(candidates), "appearances": sum(len(r["appearances"]) for r in identities.values()),
        "rubric_sha256": sha(OUT / "rubric.json"), "candidates_sha256": sha(OUT / "candidates.json"),
        "identity_sha256": sha(OUT / "identity-map.json"), "paid_judge_calls": 0,
        "human_approved": False, "training_eligible": False,
        "automatic_score": False, "reference_policy": "Snapshot source overrides erroneous dataset reference claims."})
    print(json.dumps({"unique_candidates": len(candidates), "cohorts": sorted({i['cohort'] for i in identities.values()})}))


if __name__ == "__main__":
    prepare()
