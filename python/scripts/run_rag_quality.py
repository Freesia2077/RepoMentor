"""P6.2 development diagnostics and a bounded, frozen local SDK retrieval comparison."""

import argparse
import asyncio
import hashlib
import json
import statistics
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from repomentor.agent_tools import AgentLimits, RepositoryEnvironment
from repomentor.config import load_chat_settings
from repomentor.harness import HarnessError
from repomentor.paths import data_root, runtime_dir
from repomentor.rag import verify_spans
from repomentor.rag_evaluation import coverage
from repomentor.records import write
from repomentor.report_validation import validate_report_paths
from repomentor.repository import SnapshotStore
from repomentor.retrieval_factory import build_index
from repomentor.runtimes.openai_sdk import OpenAISdkRuntime
from repomentor.sdk_agent import SdkAgentEngine, sdk_version

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments/p6-rag-quality"
SELECTED = ["developit--mitt-p4-cross_map", "developit--mitt-06", "ai--nanoid-p4-cross_negative",
            "ai--nanoid-12", "ai--nanoid-p4-unknown_business", "dbader--schedule-04"]
DIAGNOSTIC = SELECTED + ["dbader--schedule-08", "dbader--schedule-06", "ai--nanoid-p4-cross_custom",
                         "developit--mitt-p4-unknown_runtime", "more-itertools--more-itertools-14",
                         "more-itertools--more-itertools-03"]
MODES = ("bm25", "hybrid-rerank")


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def profile():
    value = load_chat_settings(ROOT / "config/model-settings.qwen-local.json")
    url = urlparse(value.baseUrl)
    if value.backend != "ollama" or url.hostname != "127.0.0.1" or url.port != 11435 or value.context_window != 8192:
        raise ValueError("only the pinned local Ollama 8K profile is allowed")
    return value


def frozen(path, value):
    if path.exists():
        if load(path) != value:
            raise ValueError(f"frozen record differs: {path.name}; use a new experiment directory")
    else:
        write(path, value)


def prepare():
    tasks_path = ROOT / "experiments/p4/tasks.jsonl"
    tasks = {r["task_id"]: r for r in map(json.loads, tasks_path.read_text(encoding="utf-8").splitlines())}
    cases, references = [], {}
    for task_id in DIAGNOSTIC:
        task = tasks[task_id]
        assert task["split"] == "validation"
        cases.append({k: task[k] for k in ("task_id", "repository", "snapshot_id", "commit_sha", "input", "split")})
        references[task_id] = task["gold"]
    protected = {p.relative_to(ROOT).as_posix(): sha(p)
                 for folder in ("p3", "p4", "p5", "p6", "p6-sdk", "p6-sdk-e2e", "p6-sdk-e2e-budget-fix")
                 for p in sorted((ROOT / "experiments" / folder).rglob("*")) if p.is_file()}
    value = {"schema_version": "repomentor.rag.quality.protocol.v1", "sdk": sdk_version(),
             "script_sha256": sha(Path(__file__)), "qa_config": OpenAISdkRuntime(profile()).settings.public(),
             "limits": AgentLimits(max_tool_calls=5, max_decisions=6, timeout_seconds=300).model_dump(),
             "retrieval": {"chunker": "syntax", "device": "cuda", "query_expansion": True},
             "modes": list(MODES), "cases": cases, "executed_task_ids": SELECTED,
             "max_local_generation_requests": 72, "automatic_retry": False, "paid_requests": 0,
             "tasks_sha256": sha(tasks_path), "protected_files": protected,
             "grading": "Validation development diagnostics; source inspection is nonblind, not human approval.",
             "human_approved": False, "training_eligible": False}
    frozen(OUT / "protocol.json", value)
    frozen(OUT / "review-reference.json", references)
    return value


def verify_protected(protocol):
    for name, digest in protocol["protected_files"].items():
        assert sha(ROOT / name) == digest, name


class LocalRuntime(OpenAISdkRuntime):
    def __init__(self, settings, protocol, replay=False):
        super().__init__(settings)
        self.protocol, self.replay = protocol, replay

    async def verify_backend(self):
        if self.replay:
            raise AssertionError("cache replay forbids metadata requests")
        return await super().verify_backend()

    async def _request(self, request):
        if self.replay:
            raise AssertionError("cache replay forbids inference")
        ledger = OUT / "dispatches.json"
        entries = load(ledger) if ledger.exists() else []
        if len(entries) >= self.protocol["max_local_generation_requests"]:
            raise HarnessError("suite_request_budget", "local comparison cap reached")
        await super()._request(request)
        entries.append({"ordinal": len(entries) + 1, "request_hash": self.active["request_hash"]})
        write(ledger, entries)


def selected(protocol):
    return [c for c in protocol["cases"] if c["task_id"] in protocol["executed_task_ids"]]


def offline(protocol):
    """No generator calls. Learned retrieval uses cached local weights and caches only."""
    corpus = SnapshotStore(data_root() / "corpus")
    references = load(OUT / "review-reference.json")
    rows = []
    for case in selected(protocol):
        reader = corpus.open(case["snapshot_id"])
        assert reader.snapshot.commit_sha == case["commit_sha"]
        for mode in MODES:
            path = OUT / "retrieval" / (mode + "--" + case["task_id"] + ".json")
            if path.exists():
                rows.append(load(path))
                continue
            index = build_index(reader, profile=profile(), retriever=mode, **protocol["retrieval"])
            env = RepositoryEnvironment(reader, index, AgentLimits(**protocol["limits"]))
            question = case["input"]["question"]
            result = env.step({"call_id": "diagnostic", "tool": "search_code", "arguments": {"query": question}})
            verify_spans(reader, result["source_spans"])
            row = {"task_id": case["task_id"], "mode": mode, "index_id": index.index_id,
                   "observation": result, "gold_observed_coverage": coverage(
                       references[case["task_id"]]["evidence"], result["source_spans"]),
                   "neural_work": dict(index.neural.stats), "new_generation_requests": 0}
            write(path, row)
            rows.append(row)
            print(json.dumps({"retrieval": case["task_id"], "mode": mode,
                              "status": result["observation"]["status"]}), flush=True)
    reports = []
    for path in sorted((ROOT / "experiments/p6-sdk-e2e-budget-fix/cases").glob("report-*.json")):
        record = load(path)
        reader = corpus.open(record["snapshot_id"])
        for stage in record["stages"]:
            if stage["answer"]:
                reports.append({"source": path.relative_to(ROOT).as_posix(), "stage": stage["stage"],
                                "validation": validate_report_paths(stage["answer"], stage["context"], reader.paths)})
    write(OUT / "historical-report-audit.json", {"new_generation_requests": 0, "rows": reports,
                                              "original_records_modified": False})
    verify_protected(protocol)
    write(OUT / "offline-check.json", {"cases": len(rows), "source_spans_verified": True,
                                      "new_generation_requests": 0, "protected_files": len(protocol["protected_files"])})


async def run(protocol, replay=False):
    if not (OUT / "offline-check.json").exists():
        raise ValueError("run the offline checks before generation")
    corpus = SnapshotStore(data_root() / "corpus")
    receipts = []
    for case in selected(protocol):
        reader = corpus.open(case["snapshot_id"])
        for mode in MODES:
            path = OUT / "cases" / (mode + "--" + case["task_id"] + ".json")
            if path.exists() and not replay:
                continue
            index = build_index(reader, profile=profile(), retriever=mode, **protocol["retrieval"])
            runtime = LocalRuntime(profile(), protocol, replay)
            engine = SdkAgentEngine(reader, index, runtime, runtime_dir() / "sdk/cache",
                                    AgentLimits(**protocol["limits"]))
            row = await engine.answer(case["input"]["question"], history=case["input"].get("history", []))
            record = {**case, "mode": mode, "result": row}
            verify_spans(reader, row["context"])
            if replay:
                original = load(path)["result"]
                assert original["attempt_id"] == row["attempt_id"] and row["new_model_requests"] == 0
            else:
                write(path, record)
            receipt = {"task_id": case["task_id"], "mode": mode, "status": row["completion"]["status"],
                       "new_model_requests": row["new_model_requests"]}
            receipts.append(receipt)
            print(json.dumps(receipt), flush=True)
    verify_protected(protocol)
    if replay:
        write(OUT / "replay.json", {"receipts": receipts, "new_model_requests": 0,
                                  "metadata_requests": 0, "cache_replay_verified": True})


def audit(protocol):
    references = load(OUT / "review-reference.json")
    corpus = SnapshotStore(data_root() / "corpus")
    groups, details = {mode: [] for mode in MODES}, []
    for path in sorted((OUT / "cases").glob("*.json")):
        record = load(path)
        row = record["result"]
        reader = corpus.open(record["snapshot_id"])
        verify_spans(reader, row["context"])
        verify_spans(reader, row["final_context"])
        refs = references[record["task_id"]]["evidence"]
        value = {"task_id": record["task_id"], "mode": record["mode"],
                 "status": row["completion"]["status"], "stop_reason": row["stop_reason"],
                 "gold_observed_coverage": coverage(refs, row["context"]),
                 "gold_final_coverage": coverage(refs, row["final_context"]),
                 "citation_ranges_valid": row["citation_ranges_valid"],
                 "new_model_requests": row["new_model_requests"], "tool_calls": row["tool_calls"],
                 "elapsed_seconds": row["elapsed_seconds"], "semantic_correctness": "not_scored"}
        groups[record["mode"]].append(value)
        details.append(value)
    summary = {}
    for mode, rows in groups.items():
        if not rows:
            continue
        summary[mode] = {"cases": len(rows), "statuses": dict(Counter(r["status"] for r in rows)),
                         "new_model_requests": sum(r["new_model_requests"] for r in rows),
                         "median_seconds": statistics.median(r["elapsed_seconds"] for r in rows)}
        for field in ("gold_observed_coverage", "gold_final_coverage"):
            available = [r[field] for r in rows if r[field] is not None]
            summary[mode][field] = statistics.mean(available) if available else None
    verify_protected(protocol)
    requests = len(load(OUT / "dispatches.json")) if (OUT / "dispatches.json").exists() else 0
    assert requests <= protocol["max_local_generation_requests"]
    report = {"modes": summary, "cases": details, "local_dispatches": requests, "paid_dispatches": 0,
              "protected_files_unchanged": len(protocol["protected_files"]),
              "human_approved": False, "training_eligible": False, "aggregate_answer_accuracy": None,
              "notes": "Fixed local 8K generator; syntax chunker, prompts and budgets shared. Coverage is not correctness; alternate supporting sources may be unlabelled. Latency includes cold/warm local retrieval, not a controlled throughput benchmark."}
    write(OUT / "audit.json", report)
    print(json.dumps({k: v for k, v in report.items() if k != "cases"}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "offline", "run", "replay", "audit"))
    args = parser.parse_args()
    protocol = prepare()
    if args.action == "offline":
        offline(protocol)
    elif args.action in ("run", "replay"):
        asyncio.run(run(protocol, replay=args.action == "replay"))
    elif args.action == "audit":
        audit(protocol)
