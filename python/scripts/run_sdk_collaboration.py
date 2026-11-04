"""Frozen local P6.2 B/C diagnostics; SDK owns all nested agent iteration."""

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path

from run_rag_quality import profile, sha

from repomentor.agent_tools import AgentLimits
from repomentor.conversation import ConversationStore
from repomentor.harness import HarnessError
from repomentor.paths import data_root, runtime_dir
from repomentor.rag import verify_spans
from repomentor.rag_evaluation import coverage
from repomentor.records import write
from repomentor.repository import SnapshotStore
from repomentor.retrieval_factory import build_index
from repomentor.runtimes.chat import fingerprint
from repomentor.runtimes.openai_sdk import OpenAISdkRuntime
from repomentor.sdk_agent import SdkAgentEngine, sdk_version
from repomentor.sdk_collaboration import CollaborationSettings
from repomentor.sdk_report import SdkReport
from repomentor.storage import RunStore

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments/p6-sdk-collaboration"
TASKS = ["ai--nanoid-p4-cross_negative", "developit--mitt-p4-cross_case", "dbader--schedule-08"]


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def freeze(path, value):
    if path.exists():
        if load(path) != value:
            raise ValueError(f"frozen protocol differs: {path.name}; use a new experiment version")
    else:
        write(path, value)


def prepare():
    all_tasks = {r["task_id"]: r for r in map(json.loads, (ROOT / "experiments/p4/tasks.jsonl").read_text(encoding="utf-8").splitlines())}
    cases, refs = [], {}

    def add(case_id, task_id, kind="qa", mode="off", question=None, **extra):
        task = all_tasks[task_id]
        assert task["split"] == "validation"
        cases.append({"case_id": case_id, "task_id": task_id, "kind": kind, "mode": mode,
            **{k: task[k] for k in ("snapshot_id", "commit_sha", "repository", "split")},
            "question": question or task["input"]["question"], **extra})
        refs[case_id] = task["gold"] if question is None else None

    for task_id in TASKS:
        for mode in ("off", "auto"):
            add(mode + "--" + task_id, task_id, mode=mode)
    add("expert-demo", TASKS[1], mode="auto", kind="demo",
        question="请委托测试专家核对 mitt 的事件名是否区分大小写，并对照 src/index.ts 的实现说明测试断言。")
    for i, question in enumerate([
        all_tasks[TASKS[0]]["input"]["question"],
        "那么它是返回空字符串还是抛错？请沿用刚才的入口，并说明实现依据。",
        "切换到 non-secure，它的负数长度行为有何不同？请读取该入口实现。",
    ], 1):
        add(f"chat-nanoid-{i}", TASKS[0], kind="chat", mode="auto", question=question, turn=i)
    for sharing in (False, True):
        add("report-mitt-" + ("shared" if sharing else "isolated"), TASKS[1], kind="report", sharing=sharing)
    protected = {p.relative_to(ROOT).as_posix(): sha(p)
        for folder in ("p3", "p4", "p5", "p6", "p6-sdk", "p6-sdk-e2e", "p6-sdk-e2e-budget-fix", "p6-rag-quality")
        for p in sorted((ROOT / "experiments" / folder).rglob("*")) if p.is_file()}
    protocol = {"schema_version": "repomentor.sdk.collaboration.protocol.v1", "sdk": sdk_version(),
        "script_sha256": sha(Path(__file__)), "qa_config": OpenAISdkRuntime(profile()).settings.public(),
        "report_config": OpenAISdkRuntime(profile().model_copy(update={"max_tokens": 2048})).settings.public(),
        "qa_limits": AgentLimits(max_tool_calls=5, max_decisions=6, timeout_seconds=240).model_dump(),
        "report_limits": AgentLimits(max_tool_calls=4, max_decisions=5, timeout_seconds=240).model_dump(),
        "retrieval": {"chunker": "syntax", "retriever": "bm25", "device": "cpu", "query_expansion": True},
        "collaboration": CollaborationSettings(mode="auto").model_dump(), "cases": cases,
        "max_generation_requests": 90, "automatic_retry": False, "protected_files": protected,
        "human_approved": False, "training_eligible": False,
        "notes": "Six paired QA outputs compare single-agent/expert-enabled SDK under the same global limits. Explicit delegation demo and three chat turns are capability checks, not paired quality scores. Two reports compare verified source sharing. No correctness claim from workflow success."}
    freeze(OUT / "protocol.json", protocol)
    freeze(OUT / "review-reference.json", refs)
    return protocol


def protected(protocol):
    for name, digest in protocol["protected_files"].items():
        assert sha(ROOT / name) == digest, name


class Runtime(OpenAISdkRuntime):
    def __init__(self, settings, protocol, replay=False):
        super().__init__(settings)
        self.protocol, self.replay = protocol, replay

    async def verify_backend(self):
        if self.replay:
            raise AssertionError("replay forbids metadata calls")
        return await super().verify_backend()

    async def _request(self, request):
        if self.replay:
            raise AssertionError("replay forbids inference")
        ledger = OUT / "dispatches.json"
        entries = load(ledger) if ledger.exists() else []
        if len(entries) >= self.protocol["max_generation_requests"]:
            raise HarnessError("suite_request_budget", "local comparison cap reached")
        await super()._request(request)
        entries.append({"ordinal": len(entries) + 1, "request_hash": self.active["request_hash"],
                        "actor": self.active["actor"]})
        write(ledger, entries)


async def run(protocol, replay=False):
    corpus = SnapshotStore(data_root() / "corpus")
    store = ConversationStore(runtime_dir() / "sdk-collaboration/conversations")
    session_id = fingerprint(protocol)[:32]
    receipts = []
    for case in protocol["cases"]:
        path = OUT / "cases" / (case["case_id"] + ".json")
        if path.exists() and not replay:
            continue
        reader = corpus.open(case["snapshot_id"])
        assert reader.snapshot.commit_sha == case["commit_sha"]
        index = build_index(reader, profile=profile(), **protocol["retrieval"])
        is_report = case["kind"] == "report"
        runtime = Runtime(profile().model_copy(update={"max_tokens": 2048}) if is_report else profile(), protocol, replay)
        limits = AgentLimits(**protocol["report_limits" if is_report else "qa_limits"])
        engine = SdkAgentEngine(reader, index, runtime, runtime_dir() / "sdk/cache", limits,
                               collaboration=CollaborationSettings(mode=case["mode"]))
        record = dict(case)
        if is_report:
            report = SdkReport(reader, index, runtime, engine.cache_dir, limits=limits, share_sources=case["sharing"])
            run_id = "p62b-" + fingerprint(protocol)[:10] + "-" + case["case_id"]
            ledger = None if replay else RunStore(runtime_dir(), run_id)
            result = await report.run(run_id=run_id, emit=ledger.emit if ledger else lambda *a: None)
            if ledger:
                ledger.save(result)
                write(ledger.events_path.parent / "sdk-stages.json", report.records)
            record.update(report=result.model_dump(), stages=report.records)
            rows = report.records
        elif case["kind"] == "chat":
            if not store.path(session_id).exists():
                if replay:
                    raise ValueError("missing saved conversation")
                store.create(case["snapshot_id"], "区分 nanoid 的入口与边界行为", session_id=session_id)
            if store.load(session_id)["pending"] and not replay:
                write(path, {**record, "blocked_by_failed_turn": True})
                continue
            turn = await store.turn(session_id, engine, case["question"], request_id=case["case_id"])
            record.update(result=turn["result"], session_id=session_id, turn_record=turn)
            rows = [turn["result"]]
        else:
            result = await engine.answer(case["question"])
            record["result"] = result
            rows = [result]
        for row in rows:
            verify_spans(reader, row["context"])
        if replay:
            previous = load(path)
            old_rows = previous["stages"] if is_report else [previous["result"]]
            assert [r["attempt_id"] for r in rows] == [r["attempt_id"] for r in old_rows]
            new = turn["new_model_requests"] if case["kind"] == "chat" else sum(r["new_model_requests"] for r in rows)
            assert new == 0
        else:
            write(path, record)
            new = sum(r["new_model_requests"] for r in rows)
        receipt = {"case_id": case["case_id"], "statuses": [r["completion"]["status"] for r in rows],
                   "new_model_requests": new, "delegations": sum(len(r["delegations"]) for r in rows)}
        receipts.append(receipt)
        print(json.dumps(receipt), flush=True)
    protected(protocol)
    if replay:
        write(OUT / "replay.json", {"receipts": receipts, "new_model_requests": 0, "metadata_requests": 0})


def audit(protocol):
    refs = load(OUT / "review-reference.json")
    corpus = SnapshotStore(data_root() / "corpus")
    rows, hashes, attempts = [], [], set()
    for path in sorted((OUT / "cases").glob("*.json")):
        record = load(path)
        if record.get("blocked_by_failed_turn"):
            rows.append({"case_id": record["case_id"], "blocked": True})
            continue
        reader = corpus.open(record["snapshot_id"])
        records = record["stages"] if record["kind"] == "report" else [record["result"]]
        for row in records:
            assert len(row["decisions"]) <= row["limits"]["max_decisions"]
            assert row["tool_calls"] <= row["limits"]["max_tool_calls"]
            for context in (row["context"], row["final_context"], row["shared_input_context"]):
                verify_spans(reader, context)
            if row["attempt_id"] not in attempts:
                hashes += [d["request_hash"] for d in row["decisions"] if d["request_sent"]]
                attempts.add(row["attempt_id"])
        reference = refs[record["case_id"]]
        rows.append({"case_id": record["case_id"], "kind": record["kind"],
                     "statuses": [r["completion"]["status"] for r in records],
                     "stop_reasons": [r["stop_reason"] for r in records],
                     "new_model_requests": sum(r["new_model_requests"] for r in records),
                     "tool_calls": sum(r["tool_calls"] for r in records),
                     "delegations": sum(len(r["delegations"]) for r in records),
                     "shared_input_spans": [len(r["shared_input_context"]) for r in records],
                     "entry_labels": [r["entry_focus"]["label"] for r in records],
                     "gold_final_coverage": coverage(reference["evidence"], records[0]["final_context"]) if reference else None})
    dispatches = load(OUT / "dispatches.json")
    assert Counter(hashes) == Counter(d["request_hash"] for d in dispatches)
    protected(protocol)
    value = {"cases": rows, "local_requests": len(dispatches), "paid_requests": 0,
             "actor_requests": dict(Counter(d["actor"] for d in dispatches)),
             "protected_files_unchanged": len(protocol["protected_files"]),
             "semantic_correctness": "not_scored", "human_approved": False, "training_eligible": False}
    write(OUT / "audit.json", value)
    print(json.dumps(value, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "run", "replay", "audit"))
    args = parser.parse_args()
    protocol = prepare()
    if args.action in ("run", "replay"):
        asyncio.run(run(protocol, args.action == "replay"))
    elif args.action == "audit":
        audit(protocol)
