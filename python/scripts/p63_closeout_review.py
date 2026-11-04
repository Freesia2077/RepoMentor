"""Offline closeout exports, source-bound manual model reviews, and delivery audit."""

import argparse
import hashlib
import json
import sqlite3
from collections import Counter

from p63_closeout import OUT, ROOT, load

from repomentor.paths import data_root, runtime_dir
from repomentor.rag import citation_validity, verify_spans
from repomentor.records import write
from repomentor.repository import SnapshotStore
from repomentor.runtimes.chat import fingerprint
from repomentor.semantic_review import candidate_digest, validate_judgment


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def candidates():
    rows = []
    for phase in ("development", "diagnostic", "confirmation", "reports", "dialogue", "demo-cpu"):
        for path in sorted((OUT / phase / "cases").glob("*.json")):
            case = load(path)
            for record in case.get("stages") or [case["result"]]:
                stage = record.get("stage", "answer")
                published = case.get("report", {}).get("outputs", {}).get(stage)
                rows.append({"candidate_id": phase + "/" + path.stem + "/" + stage,
                    "phase": phase, "mode": case.get("mode", "sdk"), "stage": stage,
                    "task_id": case.get("task_id", path.stem), "question": record.get("question", case.get("question")),
                    "snapshot_id": case["snapshot_id"], "source_file": path.relative_to(ROOT).as_posix(),
                    "source_sha256": sha(path), "record_sha256": fingerprint(record),
                    "completion_status": record["completion"]["status"],
                    "answer": published if published is not None else record["answer"],
                    "published": published is not None if phase == "reports" else record["completion"]["status"] == "success",
                    "observed_context": record.get("final_context", record["context"])})
    return rows


def export():
    rows = candidates()
    write(OUT / "review-candidates.json", rows)
    print(json.dumps({"candidates": len(rows), "generation_requests": 0}))


def finalize():
    rows = candidates()
    assert rows == load(OUT / "review-candidates.json"), "export differs from current recorded outputs"
    decisions = {r["candidate_id"]: r for r in load(OUT / "review-decisions.json")}
    assert set(decisions) == {r["candidate_id"] for r in rows}, "every actual output needs an explicit review"
    store = SnapshotStore(data_root() / "corpus")
    judgments = []
    for row in rows:
        judgment = validate_judgment(decisions[row["candidate_id"]], row, store.open(row["snapshot_id"]))
        judgment["solved"] = (row["completion_status"] == "success" and row["published"]
            and judgment["correctness"] == "correct" and judgment["citation_support"] in ("supported", "not_applicable")
            and judgment["completeness"] == "adequate" and judgment["consistency"] == "consistent"
            and judgment["abstention"] != "inappropriate")
        judgments.append(judgment)
    write(OUT / "judgments.json", judgments)
    lookup = {r["candidate_id"]: r for r in rows}
    qa = {mode: [j for j in judgments if lookup[j["candidate_id"]]["phase"] == "confirmation"
                 and lookup[j["candidate_id"]]["mode"] == mode] for mode in ("fixed", "sdk")}
    tasks = load(OUT / "confirmation.json")
    paired = []
    for task in tasks:
        hits = {mode: next(j for j in qa[mode] if lookup[j["candidate_id"]]["task_id"] == task["task_id"]) for mode in qa}
        paired.append({"task_id": task["task_id"], "kind": task["kind"],
                       **{mode + "_solved": j["solved"] for mode, j in hits.items()},
                       "both_generated": all(lookup[j["candidate_id"]]["completion_status"] == "success" for j in hits.values())})
    efficiency = {}
    for mode, js in qa.items():
        records = [load(ROOT / lookup[j["candidate_id"]]["source_file"])["result"] for j in js]
        phases = Counter(d["phase"] for r in records for d in r.get("decisions", []) if d["request_sent"])
        efficiency[mode] = {"requests": sum(r["new_model_requests"] for r in records),
            "mean_requests": sum(r["new_model_requests"] for r in records) / len(records),
            "requests_by_phase": dict(phases), "tool_calls": sum(r.get("tool_calls", 0) for r in records),
            "cases_using_tools": sum(bool(r.get("tool_calls")) for r in records),
            "model_usage": {key: {"known_sum": sum((r["completion"]["usage"].get(key) or 0) for r in records),
                                  "unknown_cases": sum(r["completion"]["usage"].get(key) is None for r in records)}
                            for key in ("prompt_tokens", "completion_tokens")}}
    write(OUT / "summary.json", {"cohorts": {p: {"outputs": len(js := [j for j in judgments if lookup[j["candidate_id"]]["phase"] == p]),
        "solved": sum(j["solved"] for j in js)} for p in ("development", "diagnostic", "confirmation", "reports", "dialogue", "demo-cpu")},
        "confirmation": {mode: {"tasks": len(js), "solved": sum(j["solved"] for j in js),
            "correctness": dict(Counter(j["correctness"] for j in js)),
            "citation_support": dict(Counter(j["citation_support"] for j in js)),
            "completeness": dict(Counter(j["completeness"] for j in js))} for mode, js in qa.items()},
        "paired": paired, "efficiency": efficiency,
        "sdk_gains": sum(r["sdk_solved"] and not r["fixed_solved"] for r in paired),
        "sdk_regressions": sum(r["fixed_solved"] and not r["sdk_solved"] for r in paired),
        "semantic_gains_both_generated": sum(r["both_generated"] and r["sdk_solved"] and not r["fixed_solved"] for r in paired),
        "semantic_regressions_both_generated": sum(r["both_generated"] and r["fixed_solved"] and not r["sdk_solved"] for r in paired),
        "reviewer": "Codex nonblind model review", "human_approved": False,
        "training_eligible": False, "same_repository_confirmation": True})
    audit()


def audit():
    completed = OUT / "completed-files.json"
    if completed.exists():
        assert all(sha(OUT / path) == h for path, h in load(completed)["sha256"].items())
    rows = candidates()
    judgments = {j["candidate_id"]: j for j in load(OUT / "judgments.json")}
    assert set(judgments) == {r["candidate_id"] for r in rows}
    assert all(judgments[r["candidate_id"]]["candidate_sha256"] == candidate_digest(r) for r in rows)
    network = load(OUT / "network-free-replay.json")
    assert network["http_send_attempts"] == network["generation_requests"] == network["metadata_requests"] == 0
    store = SnapshotStore(data_root() / "corpus")
    final_identity = load(OUT / "selection.json")["sdk"]
    for phase in ("confirmation", "reports", "dialogue", "demo-cpu"):
        assert load(OUT / phase / "manifest.json")["sdk"] == final_identity
    for row in rows:
        reader = store.open(row["snapshot_id"])
        verify_spans(reader, row["observed_context"])
        case = load(ROOT / row["source_file"])
        record = (case["result"] if row["stage"] == "answer" else
                  next(s for s in case["stages"] if s["stage"] == row["stage"]))
        verify_spans(reader, record["context"])
        if row["phase"] in ("confirmation", "reports", "dialogue", "demo-cpu") and row["mode"] == "sdk":
            assert record["runtime"] == final_identity
        if row["completion_status"] == "success":
            assert citation_validity(row["answer"], row["observed_context"])
        for request in record.get("decisions", []):
            if request["request_sent"]:
                b = request["input_budget"]
                assert b["fits"] and b["total"] <= b["context_window"] == 16384
    with sqlite3.connect((runtime_dir() / "p63/dispatches.sqlite").as_uri() + "?mode=ro", uri=True) as db:
        dispatches = [json.loads(r[0]) for r in db.execute("SELECT payload FROM dispatches ORDER BY ordinal")]
    assert len(dispatches) <= 600 and all(not r["paid"] for r in dispatches)
    for row in dispatches:
        assert row["budget"]["fits"] and row["budget"]["total"] <= row["budget"]["context_window"]
    frozen = load(ROOT / "experiments/p63/completed-files.json")["sha256"]
    assert all(sha(ROOT / "experiments/p63" / p) == h for p, h in frozen.items())
    for phase in ("confirmation", "reports", "dialogue", "demo-cpu"):
        replay = load(OUT / phase / "replay.json")
        assert replay["generation_requests"] == replay["metadata_requests"] == 0
    for name, h in load(OUT / "selection.json")["files"].items():
        assert sha(OUT / name) == h
    write(OUT / "audit.json", {"generation_requests": 0, "budget_and_source_checks_passed": True,
        "baseline_frozen_files_unchanged": len(frozen), "global_reserved_requests": len(dispatches),
        "reviewed_actual_outputs": len(rows), "strict_network_free_replay": True,
        "closeout_reserved_requests": sum(r["case"].startswith("closeout-") for r in dispatches),
        "batch_requests": dict(Counter(r["case"].split("/")[0] for r in dispatches if r["case"].startswith("closeout-"))),
        "paid_requests": 0, "training_eligible": False, "human_approved": False,
        "implementation_frozen_through_confirmation_and_demos": True,
        "development_identity_deviation_disclosed": True})
    print(json.dumps({"audit": "passed", "global_requests": len(dispatches), "generation": 0}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["export", "finalize", "audit"])
    args = parser.parse_args()
    {"export": export, "finalize": finalize, "audit": audit}[args.action]()
