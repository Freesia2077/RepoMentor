"""Offline source, budget, accounting and coverage checks for the expanded SDK suite."""

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from p6_archive import sha, verify_archive
from run_sdk_e2e import OUT, ROOT, load

from repomentor.paths import data_root
from repomentor.rag import citation_validity, verify_spans
from repomentor.rag_evaluation import coverage
from repomentor.records import write
from repomentor.repository import SnapshotStore


def main(out=OUT):
    OUT = out
    protocol, references = load(OUT / "protocol.json"), load(OUT / "review-reference.json")
    previous = verify_archive()
    for name, digest in protocol["protected_files"].items():
        assert sha(ROOT / name) == digest, name
    assert sha(ROOT / "experiments/p4/tasks.jsonl") == protocol["tasks_sha256"]
    corpus = SnapshotStore(data_root() / "corpus")
    attempts, summaries, packets = {}, [], []
    for case in protocol["cases"]:
        record = load(OUT / "cases" / (case["case_id"] + ".json"))
        for key, value in case.items():
            # The first frozen suite used `turn` for the complete conversation record too.
            if key == "turn" and isinstance(record[key], dict):
                assert record[key]["request_id"] == case["case_id"]
            else:
                assert record[key] == value
        reader = corpus.open(case["snapshot_id"])
        if record.get("status") == "blocked_by_failed_turn":
            summaries.append({"case_id": case["case_id"], "kind": case["kind"],
                              "status": record["status"], "new_model_requests": 0})
            continue
        rows = record["stages"] if case["kind"] == "report" else [record["result"]]
        for row in rows:
            assert row["runtime"] == protocol["sdk"]
            assert row["snapshot_id"] == case["snapshot_id"] and row["commit_sha"] == case["commit_sha"]
            assert row["config"]["context_window"] == 8192
            assert urlparse(row["config"]["baseUrl"]).hostname == "127.0.0.1"
            assert "apiKey" not in row["config"]
            assert row["tool_calls"] <= row["limits"]["max_tool_calls"]
            assert len(row["decisions"]) <= row["limits"]["max_decisions"]
            assert row["source_bytes"] <= row["limits"]["max_source_bytes"]
            assert not row["resumed_checkpoint"]  # This initial suite never silently reruns failures.
            verify_spans(reader, row["context"])
            assert row["citation_ranges_valid"] == citation_validity(row["answer"], row["context"])
            if row["completion"]["status"] == "success":
                assert row["citation_ranges_valid"] is True
            assert row["total_model_requests"] == sum(bool(d["request_sent"]) for d in row["decisions"])
            context = []
            for trace in row["tool_trace"]:
                assert json.loads(trace["visible_json"]) == trace["observation"]
                assert len(trace["visible_json"].encode()) <= row["limits"]["max_observation_bytes"]
                context.extend(trace["source_spans"])
            assert context == row["context"]
            for decision in row["decisions"]:
                if not decision["request_sent"]:
                    continue
                assert decision.get("transport") != "scripted"
                assert decision["input_budget"]["fits"] and decision["http_status"] == 200
                body = decision["request"]
                assert not body.get("stream", False) and body["reasoning_effort"] == "none"
                usage = decision["usage"]
                assert usage["prompt_tokens"] <= decision["input_budget"]["input_tokens"]
                assert usage["prompt_tokens"] + usage["completion_tokens"] <= 8192
            old = attempts.get(row["attempt_id"])
            if old is None or row["new_model_requests"] > old["new_model_requests"]:
                attempts[row["attempt_id"]] = row
        status = record["report"]["status"] if case["kind"] == "report" else rows[0]["completion"]["status"]
        summary = {"case_id": case["case_id"], "kind": case["kind"], "status": status,
                   "new_model_requests": sum(r["new_model_requests"] for r in rows),
                   "tool_calls": sum(r["tool_calls"] for r in rows),
                   "cache_hits": sum(r["cache_hit"] for r in rows),
                   "elapsed_seconds": sum(r["elapsed_seconds"] for r in rows),
                   "citation_ranges_valid": all(r["citation_ranges_valid"] is True for r in rows)}
        if case["kind"] != "report":
            summary["gold_span_coverage"] = coverage(references[case["case_id"]]["gold"]["evidence"],
                                                      rows[0]["context"])
            summary["insufficient_evidence"] = bool((rows[0]["answer"] or {}).get("insufficient_evidence"))
        summaries.append(summary)
        packets.append({**case, "reference": references[case["case_id"]],
                        "answer": record["report"]["outputs"] if case["kind"] == "report" else rows[0]["answer"],
                        "source_context": {r.get("stage", "qa"): r["context"] for r in rows},
                        "workflow_status": status, "human_approved": False, "task_success": None})
    sent = [d for row in attempts.values() for d in row["decisions"] if d["request_sent"]]
    dispatches = load(OUT / "dispatches.json")
    assert Counter(d["request_hash"] for d in sent) == Counter(d["request_hash"] for d in dispatches)
    assert len(sent) <= protocol["max_local_requests"]
    grouping = {}
    for kind in ("qa", "chat", "report"):
        selected = [s for s in summaries if s["kind"] == kind]
        spans = [s["gold_span_coverage"] for s in selected if s.get("gold_span_coverage") is not None]
        grouping[kind] = {"cases": len(selected), "statuses": dict(Counter(s["status"] for s in selected)),
                          "new_model_requests": sum(s["new_model_requests"] for s in selected),
                          "cache_hits": sum(s.get("cache_hits", 0) for s in selected),
                          "mean_gold_span_coverage": round(statistics.mean(spans), 4) if spans else None}
    replay = load(OUT / "replay.json") if (OUT / "replay.json").exists() else None
    if replay:
        assert len(replay["cases"]) == len(protocol["cases"]) and replay["new_model_requests"] == 0
        assert replay["network_disabled"]
    value = {
        "schema_version": "repomentor.sdk.e2e.audit.v1", "cases": summaries, "groups": grouping,
        "unique_attempts": len(attempts), "new_local_requests": len(sent), "paid_requests": 0,
        "usage": {k: sum(d["usage"][k] for d in sent) for k in ("prompt_tokens", "completion_tokens", "total_tokens")},
        "model_latency_seconds": round(sum(d["elapsed_seconds"] for d in sent), 3),
        "replay_cases": len(replay["cases"]) if replay else 0, "replay_new_model_requests": 0 if replay else None,
        "protected_p2_p5_files": previous["protected_files"], "protected_p6_files": previous["archived_files"],
        "protected_p6_and_sdk_files": len(protocol["protected_files"]),
        "human_approved": False, "semantic_accuracy": None,
        "limits": ["Development validation suite; no blind test or human approval.",
                   "Citation ranges and span coverage do not measure entailment or answer accuracy.",
                   "Cache-hit latency is historical and excluded from new model latency totals.",
                   "Two reports use fixed stage order; chat does not delegate roles autonomously."]}
    write(OUT / "audit.json", value)
    write(OUT / "review-packet.json", packets)
    write(OUT / "artifact-hashes.json", {p.relative_to(OUT).as_posix(): sha(p)
          for p in sorted(OUT.rglob("*.json")) if p.name != "artifact-hashes.json"})
    print(json.dumps({k: v for k, v in value.items() if k not in ("cases", "limits")}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT)
    main(parser.parse_args().out)
