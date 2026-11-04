"""Bounded Qwen 8K comparison: fixed syntax BM25 versus SDK Agent, six external tasks."""

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path

from run_rag_quality import frozen, profile, sha

from repomentor.agent_tools import AgentLimits
from repomentor.harness import HarnessError
from repomentor.paths import data_root, runtime_dir
from repomentor.rag import RagEngine, verify_spans
from repomentor.records import write
from repomentor.repository import SnapshotStore
from repomentor.retrieval_factory import build_index
from repomentor.runtimes.chat import ChatCompletionsRuntime, fingerprint
from repomentor.runtimes.openai_sdk import OpenAISdkRuntime
from repomentor.sdk_agent import SdkAgentEngine, sdk_version

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments/p6-d-external"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def prepare():
    tasks = load(OUT / "tasks.json")
    assert len(tasks) == 6 and all(t["split"] == "external-development" for t in tasks)
    previous = load(OUT / "protocol.json") if (OUT / "protocol.json").exists() else None
    protocol = {"sdk": sdk_version(), "runner_sha256": sha(Path(__file__)), "tasks": tasks,
        "admission_sha256": sha(OUT / "admission.json"), "profile": profile().public(),
        "limits": AgentLimits(max_tool_calls=6, max_decisions=8, timeout_seconds=240).model_dump(),
        "retrieval": {"chunker": "syntax", "retriever": "bm25", "query_expansion": True, "device": "cpu"},
        "modes": ["fixed-bm25", "sdk-agent"], "max_local_requests": 54,
        "comparison": "Same snapshots/questions/model/index/output cap. Strategy includes prompt, tools and context policy; "
                      "fixed retrieval gets one request, SDK up to eight. Not a prompt-only or equal-cost ablation.",
        "reference_answers_in_generation": False, "automatic_retry": False, "training_eligible": False,
        "protected_files": previous["protected_files"] if previous else {
            p.relative_to(ROOT).as_posix(): sha(p) for p in (ROOT / "experiments").rglob("*")
            if p.is_file() and OUT not in p.parents}}
    frozen(OUT / "protocol.json", protocol)
    return protocol


def reserve(protocol, kind, request_hash):
    path = OUT / "dispatches.json"
    ledger = load(path) if path.exists() else []
    if len(ledger) >= protocol["max_local_requests"]:
        raise HarnessError("suite_budget", "external evaluation request cap")
    ledger.append({"ordinal": len(ledger) + 1, "kind": kind, "request_hash": request_hash})
    write(path, ledger)  # Reserve before dispatch, including interrupted/failed requests.


class LocalSdk(OpenAISdkRuntime):
    def __init__(self, protocol, replay):
        super().__init__(profile())
        self.protocol, self.replay = protocol, replay

    async def verify_backend(self):
        assert not self.replay, "replay cannot contact metadata endpoints"
        return await super().verify_backend()

    async def _request(self, request):
        assert not self.replay, "replay cannot generate"
        await super()._request(request)
        reserve(self.protocol, "sdk-agent", self.active["request_hash"])


class LocalFixed(ChatCompletionsRuntime):
    def __init__(self, protocol, replay):
        super().__init__(profile())
        self.protocol, self.replay = protocol, replay

    async def verify_backend(self):
        assert not self.replay, "replay cannot contact metadata endpoints"
        return await super().verify_backend()

    async def complete(self, messages, **kwargs):
        assert not self.replay, "replay cannot generate"
        reserve(self.protocol, "fixed-bm25", fingerprint(messages))
        return await super().complete(messages, **kwargs)


async def run(protocol, replay=False):
    receipts = []
    for task in protocol["tasks"]:
        reader = SnapshotStore(data_root() / "corpus").open(task["snapshot_id"])
        assert reader.snapshot.commit_sha == task["commit_sha"]
        index = build_index(reader, profile=profile(), **protocol["retrieval"])
        for mode in protocol["modes"]:
            path = OUT / "cases" / f"{task['task_id']}--{mode}.json"
            if path.exists() and not replay:
                continue
            if mode == "fixed-bm25":
                engine = RagEngine(reader, index, LocalFixed(protocol, replay), runtime_dir() / "rag/cache")
                result = await engine.answer(task["input"]["question"])
            else:
                engine = SdkAgentEngine(reader, index, LocalSdk(protocol, replay), runtime_dir() / "sdk/cache",
                                        AgentLimits(**protocol["limits"]))
                result = await engine.answer(task["input"]["question"])
            verify_spans(reader, result.get("final_context", result["context"]))
            if replay:
                original = load(path)["result"]
                assert result["answer"] == original["answer"] and result["new_model_requests"] == 0
                assert result["completion"]["status"] == original["completion"]["status"]
            else:
                write(path, {**task, "mode": mode, "result": result})
            receipt = {"task_id": task["task_id"], "mode": mode,
                       "status": result["completion"]["status"], "new_model_requests": result["new_model_requests"]}
            receipts.append(receipt)
            print(json.dumps(receipt), flush=True)
    for name, digest in protocol["protected_files"].items():
        assert sha(ROOT / name) == digest, name
    if replay:
        write(OUT / "replay.json", {"receipts": receipts, "new_model_requests": 0, "metadata_requests": 0})
    cases = [load(p) for p in sorted((OUT / "cases").glob("*.json"))]
    write(OUT / "workflow-summary.json", {"cases": len(cases),
        "statuses": dict(Counter(c["result"]["completion"]["status"] for c in cases)),
        "reserved_local_requests": len(load(OUT / "dispatches.json")), "paid_generation_requests": 0,
        "historical_files_unchanged": len(protocol["protected_files"]), "semantic_correctness": "separate_review"})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run", "replay"))
    action = parser.parse_args().action
    protocol = prepare()
    if action != "prepare":
        asyncio.run(run(protocol, action == "replay"))
