"""Targeted follow-up after preserved P6.2 C failures; larger report budget authorized by owner."""

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path

from run_rag_quality import profile, sha

from repomentor.agent_tools import AgentLimits
from repomentor.harness import HarnessError
from repomentor.paths import data_root, runtime_dir
from repomentor.rag import citation_validity, verify_spans
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
BASE = ROOT / "experiments/p6-rag-context"
OUT = ROOT / "experiments/p6-rag-context-fix"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def prepare():
    old = load(BASE / "protocol.json")
    cases = [c for c in old["cases"] if c["case_id"] in ("chat-nanoid-2", "report-mitt-isolated", "report-mitt-shared")]
    protected = {**old["protected_files"], **{p.relative_to(ROOT).as_posix(): sha(p)
                 for p in BASE.rglob("*") if p.is_file()}}
    protocol = {"schema_version": "repomentor.rag.context.fix.v1", "sdk": sdk_version(),
        "runner_sha256": sha(Path(__file__)), "cases": cases, "protected_files": protected,
        "qa_config": OpenAISdkRuntime(profile()).settings.public(),
        "report_config": OpenAISdkRuntime(profile().model_copy(update={"max_tokens": 2048})).settings.public(),
        "qa_limits": old["qa_limits"],
        "report_limits": AgentLimits(max_tool_calls=6, max_decisions=8, timeout_seconds=240).model_dump(),
        "retrieval": old["retrieval"], "max_generation_requests": 54, "automatic_retry": False,
        "human_approved": False, "training_eligible": False,
        "comparison": "Targeted regression, not a controlled ablation. Reuses the saved first chat turn as immutable "
                      "input instead of paying to regenerate it. Report budget raised with owner authorization; "
                      "QA budget and 8K window unchanged. Source retention and report grammar also changed."}
    path = OUT / "protocol.json"
    if path.exists():
        assert load(path) == protocol, "Frozen protocol differs; create a new version."
    else:
        write(path, protocol)
    return protocol


def protect(protocol):
    for name, digest in protocol["protected_files"].items():
        assert sha(ROOT / name) == digest, name


class Runtime(OpenAISdkRuntime):
    def __init__(self, settings, protocol, replay=False):
        super().__init__(settings)
        self.protocol, self.replay = protocol, replay

    async def verify_backend(self):
        assert not self.replay, "replay cannot call metadata endpoints"
        return await super().verify_backend()

    async def _request(self, request):
        assert not self.replay, "replay cannot generate"
        path = OUT / "dispatches.json"
        ledger = load(path) if path.exists() else []
        if len(ledger) >= self.protocol["max_generation_requests"]:
            raise HarnessError("suite_budget", "targeted local request cap")
        await super()._request(request)
        ledger.append({"ordinal": len(ledger) + 1, "request_hash": self.active["request_hash"], "actor": self.active["actor"]})
        write(path, ledger)


async def run(protocol, replay=False):
    receipts = []
    for case in protocol["cases"]:
        path = OUT / "cases" / (case["case_id"] + ".json")
        if path.exists() and not replay:
            continue
        reader = SnapshotStore(data_root() / "corpus").open(case["snapshot_id"])
        assert reader.snapshot.commit_sha == case["commit_sha"] and case["split"] == "validation"
        index = build_index(reader, profile=profile(), **protocol["retrieval"])
        report_case = case["kind"] == "report"
        runtime = Runtime(profile().model_copy(update={"max_tokens": 2048}) if report_case else profile(), protocol, replay)
        limits = AgentLimits(**protocol["report_limits" if report_case else "qa_limits"])
        cache = runtime_dir() / "sdk/cache"
        record = dict(case)
        if report_case:
            report = SdkReport(reader, index, runtime, cache, limits=limits, share_sources=case["sharing"])
            run_id = "p62cfix-" + fingerprint(protocol)[:10] + "-" + case["case_id"]
            ledger = None if replay else RunStore(runtime_dir(), run_id)
            value = await report.run(run_id=run_id, emit=ledger.emit if ledger else lambda *a: None)
            if ledger:
                ledger.save(value)
                write(ledger.events_path.parent / "sdk-stages.json", report.records)
            rows = report.records
            record.update(report=value.model_dump(), stages=rows)
        else:
            first = load(BASE / "cases/chat-nanoid-1.json")["result"]
            second = load(BASE / "cases/chat-nanoid-2.json")["result"]
            payload = json.loads(second["input"][-1]["content"])
            engine = SdkAgentEngine(reader, index, runtime, cache, limits,
                                   collaboration=CollaborationSettings(mode=case["mode"]))
            value = await engine.answer(case["question"], history=second["input"][:-1],
                query=payload["resolved_query"], memory=payload["navigation_memory"], task_context=payload["task_context"],
                shared_context={"snapshot_id": case["snapshot_id"], "spans": first["final_context"],
                                "preferred_ranges": first["answer"]["citations"]})
            record.update(result=value, source_case="p6-rag-context/chat-nanoid-1", handling="saved-turn continuation fixture")
            rows = [value]
        new = sum(r["new_model_requests"] for r in rows)
        if replay:
            old = load(path)
            before = old["stages"] if report_case else [old["result"]]
            assert [r["attempt_id"] for r in rows] == [r["attempt_id"] for r in before]
            assert new == 0
        else:
            write(path, record)
        receipt = {"case_id": case["case_id"], "statuses": [r["completion"]["status"] for r in rows],
                   "new_model_requests": new, "research_stops": [r["research_stop"] for r in rows]}
        print(json.dumps(receipt), flush=True)
        receipts.append(receipt)
    protect(protocol)
    if replay:
        write(OUT / "replay.json", {"receipts": receipts, "new_model_requests": 0, "metadata_requests": 0})


def audit(protocol):
    cases, hashes, attempts = [], [], set()
    for case in protocol["cases"]:
        r = load(OUT / "cases" / (case["case_id"] + ".json"))
        rows = r["stages"] if case["kind"] == "report" else [r["result"]]
        reader = SnapshotStore(data_root() / "corpus").open(case["snapshot_id"])
        for row in rows:
            assert row["runtime"] == protocol["sdk"]
            assert len(row["decisions"]) <= row["limits"]["max_decisions"]
            assert row["tool_calls"] <= row["limits"]["max_tool_calls"]
            assert row["source_bytes"] <= row["limits"]["max_source_bytes"]
            for spans in (row["context"], row["final_context"], row["shared_input_context"]):
                verify_spans(reader, spans)
            if row["completion"]["status"] == "success":
                assert row["report_validation"]["passed"] if case["kind"] == "report" else citation_validity(row["answer"], row["final_context"])
            if row["attempt_id"] not in attempts:
                hashes += [d["request_hash"] for d in row["decisions"] if d["request_sent"]]
                attempts.add(row["attempt_id"])
        cases.append({"case_id": case["case_id"], "statuses": [row["completion"]["status"] for row in rows],
                      "new_model_requests": sum(row["new_model_requests"] for row in rows),
                      "research_stops": [row["research_stop"] for row in rows],
                      "tool_calls": [row["tool_calls"] for row in rows]})
    ledger = load(OUT / "dispatches.json")
    assert Counter(hashes) == Counter(d["request_hash"] for d in ledger)
    protect(protocol)
    record = {"cases": cases, "local_requests": len(ledger), "paid_requests": 0,
              "protected_files_unchanged": len(protocol["protected_files"]),
              "semantic_correctness": "not_scored", "human_approved": False, "training_eligible": False}
    write(OUT / "audit.json", record)
    print(json.dumps(record, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("run", "replay", "audit", "prepare"))
    action = parser.parse_args().action
    protocol = prepare()
    if action in ("run", "replay"):
        asyncio.run(run(protocol, action == "replay"))
    elif action == "audit":
        audit(protocol)
