"""Frozen local regression for symbol navigation and SDK context recovery; no automatic retries."""

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
from repomentor.rag import citation_validity, verify_spans
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
OUT = ROOT / "experiments/p6-rag-context"
BASE = ROOT / "experiments/p6-sdk-collaboration"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def prepare():
    old = load(BASE / "protocol.json")
    selected = {"off--ai--nanoid-p4-cross_negative", "off--developit--mitt-p4-cross_case",
                "chat-nanoid-1", "chat-nanoid-2", "chat-nanoid-3",
                "report-mitt-isolated", "report-mitt-shared"}
    cases = [c for c in old["cases"] if c["case_id"] in selected]
    protected = dict(old["protected_files"])
    protected.update({p.relative_to(ROOT).as_posix(): sha(p) for p in BASE.rglob("*") if p.is_file()})
    protocol = {"schema_version": "repomentor.rag.context.protocol.v1", "sdk": sdk_version(),
        "runner_sha256": sha(Path(__file__)), "cases": cases,
        "qa_config": OpenAISdkRuntime(profile()).settings.public(),
        "report_config": OpenAISdkRuntime(profile().model_copy(update={"max_tokens": 2048})).settings.public(),
        "qa_limits": old["qa_limits"], "report_limits": old["report_limits"], "retrieval": old["retrieval"],
        "protected_files": protected, "max_generation_requests": 60,
        "automatic_retry": False, "human_approved": False, "training_eligible": False,
        "comparison": "Historical before/after diagnosis, not a single-variable ablation or independent test set. "
                      "Questions, snapshots, model, tools and limits reused; navigation, packing and recovery changed."}
    path = OUT / "protocol.json"
    if path.exists():
        assert load(path) == protocol, "Frozen protocol differs; use a new experiment version."
    else:
        write(path, protocol)
    return protocol


def protect(protocol):
    for name, digest in protocol["protected_files"].items():
        assert sha(ROOT / name) == digest, name


class Runtime(OpenAISdkRuntime):
    def __init__(self, settings, protocol, replay):
        super().__init__(settings)
        self.protocol, self.replay = protocol, replay

    async def verify_backend(self):
        assert not self.replay, "Replay forbids metadata requests."
        return await super().verify_backend()

    async def _request(self, request):
        assert not self.replay, "Replay forbids inference."
        path = OUT / "dispatches.json"
        ledger = load(path) if path.exists() else []
        if len(ledger) >= self.protocol["max_generation_requests"]:
            raise HarnessError("suite_budget", "local regression budget exhausted")
        await super()._request(request)
        ledger.append({"ordinal": len(ledger) + 1, "request_hash": self.active["request_hash"],
                       "actor": self.active["actor"]})
        write(path, ledger)


async def run(protocol, replay=False):
    corpus = SnapshotStore(data_root() / "corpus")
    store = ConversationStore(runtime_dir() / "rag-context/conversations")
    session = fingerprint(protocol)[:32]
    receipts = []
    for case in protocol["cases"]:
        path = OUT / "cases" / (case["case_id"] + ".json")
        if not replay and path.exists():
            continue
        if replay and load(path).get("blocked_by_failed_turn"):
            receipts.append({"case_id": case["case_id"], "blocked_by_failed_turn": True, "new_model_requests": 0})
            continue
        reader = corpus.open(case["snapshot_id"])
        assert reader.snapshot.commit_sha == case["commit_sha"] and case["split"] == "validation"
        index = build_index(reader, profile=profile(), **protocol["retrieval"])
        report_case = case["kind"] == "report"
        runtime = Runtime(profile().model_copy(update={"max_tokens": 2048}) if report_case else profile(), protocol, replay)
        limits = AgentLimits(**protocol["report_limits" if report_case else "qa_limits"])
        engine = SdkAgentEngine(reader, index, runtime, runtime_dir() / "sdk/cache", limits,
                               collaboration=CollaborationSettings(mode=case["mode"]))
        record = dict(case)
        if report_case:
            report = SdkReport(reader, index, runtime, engine.cache_dir, limits=limits, share_sources=case["sharing"])
            run_id = "p62c-" + fingerprint(protocol)[:10] + "-" + case["case_id"]
            ledger = None if replay else RunStore(runtime_dir(), run_id)
            result = await report.run(run_id=run_id, emit=ledger.emit if ledger else lambda *a: None)
            if ledger:
                ledger.save(result)
                write(ledger.events_path.parent / "sdk-stages.json", report.records)
            rows = report.records
            record.update(report=result.model_dump(), stages=rows)
        elif case["kind"] == "chat":
            if not store.path(session).exists():
                assert not replay
                store.create(case["snapshot_id"], "区分 nanoid 的入口与边界行为", session_id=session)
            state = store.load(session)
            if state["pending"] and not replay:
                write(path, {**record, "blocked_by_failed_turn": True})
                continue
            turn = await store.turn(session, engine, case["question"], request_id=case["case_id"])
            record.update(result=turn["result"], turn_record=turn, session_id=session)
            rows = [turn["result"]]
        else:
            record["result"] = await engine.answer(case["question"])
            rows = [record["result"]]
        new = turn["new_model_requests"] if case["kind"] == "chat" else sum(r["new_model_requests"] for r in rows)
        if replay:
            saved = load(path)
            old_rows = saved["stages"] if report_case else [saved["result"]]
            assert [r["attempt_id"] for r in rows] == [r["attempt_id"] for r in old_rows]
            assert new == 0
        else:
            write(path, record)
        receipt = {"case_id": case["case_id"], "statuses": [r["completion"]["status"] for r in rows],
                   "research_stops": [r["research_stop"] for r in rows], "new_model_requests": new}
        print(json.dumps(receipt), flush=True)
        receipts.append(receipt)
    protect(protocol)
    if replay:
        write(OUT / "replay.json", {"receipts": receipts, "new_model_requests": 0, "metadata_requests": 0})


def audit(protocol):
    refs, corpus = load(BASE / "review-reference.json"), SnapshotStore(data_root() / "corpus")
    cases, requests, attempts = [], [], set()
    for case in protocol["cases"]:
        record = load(OUT / "cases" / (case["case_id"] + ".json"))
        if record.get("blocked_by_failed_turn"):
            cases.append({"case_id": case["case_id"], "blocked": True})
            continue
        rows = record["stages"] if case["kind"] == "report" else [record["result"]]
        reader = corpus.open(case["snapshot_id"])
        for row in rows:
            assert row["runtime"] == protocol["sdk"]
            assert len(row["decisions"]) <= row["limits"]["max_decisions"]
            assert row["tool_calls"] <= row["limits"]["max_tool_calls"]
            assert row["source_bytes"] <= row["limits"]["max_source_bytes"]
            for context in (row["context"], row["final_context"], row["shared_input_context"]):
                verify_spans(reader, context)
            if case["kind"] != "report" and row["completion"]["status"] == "success":
                assert citation_validity(row["answer"], row["final_context"])
            if row["attempt_id"] not in attempts:
                requests += [d["request_hash"] for d in row["decisions"] if d["request_sent"]]
                attempts.add(row["attempt_id"])
        gold = refs[case["case_id"]] if case["kind"] == "qa" else None
        cases.append({"case_id": case["case_id"], "statuses": [r["completion"]["status"] for r in rows],
                      "research_stops": [r["research_stop"] for r in rows],
                      "new_model_requests": sum(r["new_model_requests"] for r in rows),
                      "tool_calls": [r["tool_calls"] for r in rows],
                      "shared_input_spans": [len(r["shared_input_context"]) for r in rows],
                      "symbols": [r["symbol_navigation"]["current_symbol"] for r in rows],
                      "gold_final_coverage": coverage(gold["evidence"], rows[0]["final_context"]) if gold else None})
    dispatches = load(OUT / "dispatches.json")
    assert Counter(requests) == Counter(d["request_hash"] for d in dispatches)
    protect(protocol)
    value = {"cases": cases, "local_requests": len(dispatches), "paid_requests": 0,
             "protected_files_unchanged": len(protocol["protected_files"]),
             "semantic_correctness": "not_scored", "human_approved": False, "training_eligible": False}
    write(OUT / "audit.json", value)
    print(json.dumps(value, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "run", "replay", "audit"))
    action = parser.parse_args().action
    protocol = prepare()
    if action in ("run", "replay"):
        asyncio.run(run(protocol, action == "replay"))
    elif action == "audit":
        audit(protocol)
