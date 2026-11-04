"""Offline P6.3 acceptance; missing work stays pending, failed model outputs stay failed."""

import hashlib
import json
import statistics
from collections import Counter, defaultdict

from p63_dataset import OUT, ROOT, prepare

from repomentor.paths import data_root
from repomentor.rag import citation_validity, verify_spans
from repomentor.records import write
from repomentor.repository import SnapshotStore
from repomentor.runtimes.chat import fingerprint
from repomentor.semantic_review import candidate_digest


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify():
    # Imported lazily so the unified entry can delegate here too.
    from p63 import ledger, verify_frozen

    protocol = prepare()
    verify_frozen(protocol)
    final_manifest = OUT / "completed-files.json"
    if final_manifest.exists():
        for path, digest in load(final_manifest)["sha256"].items():
            assert sha(OUT / path) == digest, f"completed result changed: {path}"
    supplement = load(OUT / "supplement-protocol.json")
    assert sha(OUT / "dialogues.json") == supplement["dialogues_sha256"]
    assert sha(OUT / "evaluation-only/development.json") == supplement["development_refs_sha256"]
    historical_batches = ({p.split("/")[1] for p in load(final_manifest)["sha256"] if p.startswith("runs/")}
                          if final_manifest.exists() else None)

    def historical_dispatches():
        rows = ledger(protocol).rows()
        assert len(rows) <= protocol["max_local_requests"]
        return [r for r in rows if historical_batches is None or r["case"].split("/")[0] in historical_batches]

    dispatches = historical_dispatches()
    assert len(dispatches) <= protocol["max_local_requests"]
    for i, row in enumerate(dispatches, 1):
        b = row["budget"]
        assert row["ordinal"] == i and row["paid"] is False
        assert b["fits"] and b["total"] <= b["context_window"] == 8192
        assert b["output_allowance"] in (1024, 1536)
    readers, attempts, batches = {}, {}, {}
    invalid_published = []
    for folder in sorted((OUT / "runs").iterdir()):
        if not folder.is_dir():
            continue
        cases = [load(p) for p in sorted((folder / "cases").glob("*.json"))]
        counts, modes, questions = Counter(), Counter(), []
        for case in cases:
            records = case.get("stages") or ([case["result"]] if case.get("result") else [])
            modes[case.get("mode", "sdk")] += 1
            if not records:
                counts["blocked"] += 1
                continue
            sid = case["snapshot_id"]
            if sid not in readers:
                readers[sid] = SnapshotStore(data_root() / "corpus").open(sid)
            for record in records:
                status = record["completion"]["status"]
                counts[status] += 1
                context = record.get("final_context", record["context"])
                verify_spans(readers[sid], context)
                verify_spans(readers[sid], record["context"])
                verify_spans(readers[sid], (record.get("initial_retrieval") or {}).get("sources", []))
                if status == "success" and citation_validity(record["answer"], context) is not True:
                    invalid_published.append([folder.name, case.get("task_id"), record.get("stage")])
                calls = [d for d in record.get("decisions", []) if d["request_sent"]]
                if not calls and record["completion"].get("request_sent"):
                    calls = [record["completion"]]
                for call in calls:
                    b = call["input_budget"]
                    assert b["fits"] and b["total"] <= b["context_window"] == 8192
                attempts[record["attempt_id"]] = len(calls)
                questions.append(record)
        candidates = load(folder / "review-candidates.json") if (folder / "review-candidates.json").exists() else []
        judgments = load(folder / "judgments.json") if (folder / "judgments.json").exists() else []
        bound = {c["candidate_id"]: c for c in candidates}
        for candidate in candidates:
            case = load(ROOT / candidate["source_file"])
            record = case["result"] if candidate["stage"] == "answer" else next(s for s in case["stages"] if s["stage"] == candidate["stage"])
            assert fingerprint(record) == candidate["record_sha256"]
        for judgment in judgments:
            candidate = bound[judgment["candidate_id"]]
            assert judgment["candidate_sha256"] == candidate_digest({**candidate, "observed_context": candidate["visible_context"]})
            assert judgment["human_approved"] is False and judgment["training_eligible"] is False
        replay = load(folder / "replay.json") if (folder / "replay.json").exists() else None
        if replay:
            assert replay["generation_requests"] == replay["metadata_requests"] == 0
        batches[folder.name] = {"case_files": len(cases), "modes": dict(modes), "statuses": dict(counts),
            "records": len(questions), "reviewed": len(judgments), "solved": sum(j["solved"] for j in judgments),
            "replay_cases": len(replay["cases"]) if replay else 0}
    assert not invalid_published, invalid_published
    retrieval, chunk_ids = {}, defaultdict(set)
    for mode in ("bm25", "vector", "hybrid", "hybrid-rerank"):
        rows = [load(p) for p in (OUT / "retrieval/current" / mode).glob("*.json")]
        for row in rows:
            assert row["generation_requests"] == 0
            chunk_ids[row["task_id"]].add(row["chunk_ids_sha256"])
        retrieval[mode] = {"tasks": len(rows), **{
            metric: statistics.mean(values) if (values := [r["metrics"][metric] for r in rows if r["metrics"][metric] is not None]) else None
            for metric in ("mrr", "recall_at_6", "target_overlap_recall_at_6", "line_coverage_at_6")},
            "first_observed_seconds": sum(r["first_query_seconds"] for r in rows),
            "warm_query_seconds": sum(r["warm_query_seconds"] for r in rows)}
    assert all(len(ids) == 1 for ids in chunk_ids.values())
    pending = []
    for name, expected in {"confirmation-v1": 48, "dialogues-v1": 16, "reports-v1": 2}.items():
        batch = batches.get(name, {})
        if batch.get("case_files") != expected:
            pending.append(f"{name}: expected {expected} case files")
        if not batch or batch.get("reviewed") != batch.get("records"):
            pending.append(f"{name}: semantic review incomplete")
    if any(r["tasks"] != 72 for r in retrieval.values()):
        pending.append("retrieval: expected 72 questions in all four conditions")
    if batches.get("confirmation-v1", {}).get("replay_cases") != 48:
        pending.append("confirmation: actual engine zero-call replay incomplete")
    for name in ("dialogues-v1", "reports-v1"):
        if not (OUT / "runs" / name / "replay.json").exists():
            pending.append(f"{name}: zero-call replay receipt missing")
    if len(list((OUT / "retrieval/cache-probe").glob("*.json"))) != 4:
        pending.append("cold/warm retrieval probe incomplete")
    conversations = [load(p) for p in (OUT / "runs/dialogues-v1").glob("*-session.json")]
    reports = [load(p) for p in (OUT / "runs/reports-v1/cases").glob("*.json")]
    workflow_targets = {
        "four_turn_dialogues_completed": sum(len(c["turns"]) == 4 and not c["pending"] for c in conversations),
        "four_turn_dialogues_planned": 4,
        "three_stage_reports_completed": sum(r["report"]["status"] == "success" and len(r["stages"]) == 3 for r in reports),
        "three_stage_reports_planned": 2,
        "report_stages_attempted": sum(len(r["stages"]) for r in reports),
        "report_stages_planned": 6,
    }
    goals = {"evaluated": False}
    comparison = OUT / "runs/confirmation-v1/comparison.json"
    if comparison.exists():
        result = load(comparison)
        fixed, sdk = (result["modes"][m] for m in ("fixed", "sdk"))
        assert len(result["pairs"]) == 24 and all(p["initial_retrieval_equal"] for p in result["pairs"])
        goals = {"evaluated": fixed["reviewed"] == sdk["reviewed"] == 24,
            "sdk_not_worse": sdk["solved"] >= fixed["solved"],
            "cross_function_two_net_extra": result["cross_function_net_gain"] >= 2,
            "mean_requests_at_most_four": sdk["mean_model_requests"] <= 4,
            "fixed_solved": fixed["solved"], "sdk_solved": sdk["solved"],
            "cross_function_extra_solved": result["cross_function_extra_solved"],
            "cross_function_regressions": result["cross_function_regressions"],
            "cross_function_net_gain": result["cross_function_net_gain"],
            "target_interpretation": "Count gains and regressions together; three new successes and three losses do not meet a net two-question improvement.",
            "sdk_mean_requests": sdk["mean_model_requests"]}
    # Runs can finish while this read-only audit walks files. Refresh the ledger
    # after records, so a newly saved attempt cannot appear ahead of its reservation.
    dispatches = historical_dispatches()
    assert len(dispatches) <= protocol["max_local_requests"]
    assert sum(attempts.values()) <= len(dispatches)
    for row in dispatches:
        b = row["budget"]
        assert row["paid"] is False and b["fits"] and b["total"] <= b["context_window"] == 8192
    if len(dispatches) != sum(attempts.values()):
        pending.append("request ledger includes in-flight or unrecorded dispatches; reconcile after running batches stop")
    result = {"schema_version": "p63-acceptance-v1", "protected_files_verified": len(protocol["protected_files"]),
        "frozen_splits_verified": True, "reserved_local_requests": len(dispatches), "local_cap": 600,
        "paid_requests": 0, "unique_recorded_attempt_requests": sum(attempts.values()),
        "reservation_minus_recorded_requests": len(dispatches) - sum(attempts.values()),
        "budget_and_source_checks_passed": True, "batches": batches, "retrieval": retrieval,
        "pending": pending, "delivery_complete": not pending, "semantic_goals": goals,
        "delivery_definition": "All scheduled attempts, including retained failures and blocked successors, are collected and reviewed. This does not mean all workflow or semantic targets passed.",
        "maximum_reserved_total_tokens": max((r["budget"]["total"] for r in dispatches), default=0),
        "completed_results_frozen": final_manifest.exists(),
        "workflow_targets": workflow_targets,
        "all_acceptance_targets_met": bool(not pending and goals.get("evaluated") and goals.get("sdk_not_worse")
            and goals.get("cross_function_two_net_extra") and goals.get("mean_requests_at_most_four")
            and workflow_targets["four_turn_dialogues_completed"] == 4
            and workflow_targets["three_stage_reports_completed"] == 2),
        "reviewer": "Codex nonblind model review", "human_approved": False, "training_eligible": False,
        "limitations": ["First observed retrieval timings reuse existing caches; not fully cold startup.",
            "Blocked dialogue turns count as failures, not generated answers.",
            "24 confirmation questions use the same six development repositories.",
            "Exact-once cache replay is checked; arbitrary process termination inside a dispatch is not guaranteed exactly-once."]}
    write(OUT / "dispatches.json", {"experiment": fingerprint(protocol), "cap": protocol["max_local_requests"],
        "reservations": dispatches, "reserved_requests": len(dispatches), "paid_requests": 0,
        "note": "Reservations include ambiguous interrupted dispatches, never reused or refunded."})
    write(OUT / "acceptance.json", result)
    print(json.dumps({k: v for k, v in result.items() if k not in {"batches", "retrieval", "limitations"}}, ensure_ascii=False))
    return result


if __name__ == "__main__":
    verify()
