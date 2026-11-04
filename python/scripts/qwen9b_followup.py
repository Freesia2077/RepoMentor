"""Separate development regression for current-question focus and lossless source lines."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from qwen9b_reliability import DIALOGUE, LIMITS, PREFIX, ROOT, freeze, index_for, load, profile

from repomentor.config import write_json
from repomentor.conversation import ConversationStore
from repomentor.experiment_ledger import RequestLedger
from repomentor.harness import HarnessError
from repomentor.paths import data_root, runtime_dir
from repomentor.presentation import render_run
from repomentor.rag import verify_spans
from repomentor.repository import SnapshotStore
from repomentor.runtimes.chat import fingerprint
from repomentor.runtimes.openai_sdk import OpenAISdkRuntime
from repomentor.sdk_agent import SdkAgentEngine, sdk_version
from repomentor.sdk_context import source_items
from repomentor.semantic_review import validate_judgment
from repomentor.source_windows import uncovered

OUT = ROOT / "experiments/qwen9b-followup"
PRIVATE = runtime_dir() / "qwen9b-followup"
BASE = ROOT / "experiments/qwen9b-reliability"
CAP = 24


def book():
    # The supplement shares the original aggregate 144 cap, not another 144 allowance.
    return RequestLedger(runtime_dir() / "p63/dispatches.sqlite", limit=600,
        experiment=fingerprint(load(ROOT / "experiments/p63/protocol.json")), context_windows=(16384,),
        batch_prefix=PREFIX, batch_limit=144)


def verify_baseline():
    for name, digest in load(BASE / "completed-files.json")["sha256"].items():
        assert hashlib.sha256((BASE / name).read_bytes()).hexdigest() == digest, name


def prepare():
    verify_baseline()
    task = next(t for t in load(BASE / "confirmation.json") if t["repository"] == "developit/mitt")
    freeze(OUT / "protocol.json", {"version": "qwen9b-followup-development-v1", "date": "2026-10-20",
        "profile": profile().public(), "sdk": sdk_version(), "limits": LIMITS.model_dump(),
        "task": {k: task[k] for k in ("repository", "snapshot_id", "commit_sha")}, "questions": DIALOGUE,
        "baseline_manifest_sha256": hashlib.sha256((BASE / "completed-files.json").read_bytes()).hexdigest(),
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
        "new_cap": CAP, "aggregate_reliability_cap": 144, "global_cap": 600,
        "purpose": "Development recheck after an observed failure; not fresh confirmation or a re-run of the frozen 12 questions.",
        "changes": ["Explicit new calls do not inherit previous questions", "Finalizer question is the current user question; retrieval navigation stays separate", "Trailing blank source lines retain exact numbering"],
        "automatic_retry": False, "human_approved": False, "training_eligible": False})


def check():
    verify_baseline()
    p = load(OUT / "protocol.json")
    assert p["profile"] == profile().public() and p["sdk"] == sdk_version()
    assert p["runner_sha256"] == hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    return p


async def run(replay=False):
    p = check()
    task = p["task"]
    reader = SnapshotStore(data_root() / "corpus").open(task["snapshot_id"])
    assert reader.snapshot.commit_sha == task["commit_sha"]
    store = ConversationStore(PRIVATE / "conversations")
    session_file = PRIVATE / "session.json"
    if session_file.exists():
        state = load(session_file)
    else:
        assert not replay
        state = store.create(task["snapshot_id"], "核对 mitt 实现、测试与不可验证信息")
        write_json(session_file, state)

    def before(body, budget):
        assert not replay, "replay cannot generate"
        rows = book().rows()
        if sum(r["case"].startswith(PREFIX + "/followup/") for r in rows) >= CAP:
            raise HarnessError("suite_budget", "followup cap reached")
        book().reserve(profile(), PREFIX + "/followup/mitt", body, budget)

    class Runtime(OpenAISdkRuntime):
        async def verify_backend(self):
            assert not replay, "replay cannot query model metadata"
            return await super().verify_backend()

    engine = SdkAgentEngine(reader, index_for(reader), Runtime(profile(), before_dispatch=before),
                            PRIVATE / "sdk-cache", LIMITS)
    receipts = []
    for i, question in enumerate(p["questions"], 1):
        name = f"mitt-turn-{i}"
        path = OUT / "cases" / (name + ".json")
        turn = await store.turn(state["session_id"], engine, question, request_id=name)
        result = turn["result"]
        if replay:
            assert turn.get("replayed") and turn.get("new_model_requests", 0) == 0
            assert result["attempt_id"] == load(path)["result"]["attempt_id"]
            receipts.append({"turn": i, "attempt_id": result["attempt_id"]})
        elif not path.exists():
            freeze(path, {**task, "task_id": name, "question": question, "linked_turn": i,
                "session_id": state["session_id"], "result": result, "training_eligible": False})
            target = OUT / "markdown" / (name + ".md")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(render_run({"result": result}), encoding="utf-8")
            print(json.dumps({"turn": i, "status": result["completion"]["status"],
                "requests": result["new_model_requests"]}), flush=True)
        if result["completion"]["status"] != "success":
            break
    if replay:
        write_json(OUT / "replay.json", {"receipts": receipts, "generation_requests": 0, "metadata_requests": 0})
    book().export(OUT / "dispatches.json")


async def replay():
    from unittest.mock import patch

    import httpx
    import httpx2

    attempts = []

    def no_http(*a, **kw):
        attempts.append(True)
        raise AssertionError("offline replay forbids HTTP")

    async def no_http_async(*a, **kw):
        no_http()

    with (patch.object(httpx.Client, "send", no_http), patch.object(httpx.AsyncClient, "send", no_http_async),
          patch.object(httpx2.Client, "send", no_http), patch.object(httpx2.AsyncClient, "send", no_http_async)):
        await run(True)
    assert not attempts
    write_json(OUT / "network-free-replay.json", {"http_send_attempts": 0, "generation_requests": 0, "metadata_requests": 0})


def candidates():
    return [{"candidate_id": path.stem + "/answer", "question": (case := load(path))["question"],
        "snapshot_id": case["snapshot_id"], "answer": (r := case["result"])["answer"],
        "completion_status": r["completion"]["status"], "record_sha256": fingerprint(r),
        "observed_context": r["final_context"], "source_file": path.relative_to(ROOT).as_posix()}
        for path in sorted((OUT / "cases").glob("*.json"))]


def summarize():
    rows = candidates()
    assert rows == load(OUT / "review-candidates.json")
    decisions = {r["candidate_id"]: r for r in load(OUT / "review-decisions.json")}
    assert set(decisions) == {r["candidate_id"] for r in rows}
    reader = SnapshotStore(data_root() / "corpus").open(rows[0]["snapshot_id"])
    judgments = []
    for row in rows:
        j = validate_judgment(decisions[row["candidate_id"]], row, reader)
        j["solved"] = (row["completion_status"] == "success" and j["correctness"] == "correct"
            and j["citation_support"] in {"supported", "not_applicable"} and j["completeness"] == "adequate"
            and j["abstention"] != "inappropriate" and j["consistency"] == "consistent")
        judgments.append(j)
    write_json(OUT / "judgments.json", judgments)
    write_json(OUT / "summary.json", {"outputs": len(rows), "solved": sum(j["solved"] for j in judgments),
        "requests": sum(load(ROOT / r["source_file"])["result"]["new_model_requests"] for r in rows),
        "baseline_solved": load(BASE / "summary.json")["groups"]["dialogue/sdk"]["solved"],
        "development_recheck_only": True, "human_approved": False, "training_eligible": False})


def audit():
    check()
    records = [load(p)["result"] for p in sorted((OUT / "cases").glob("*.json"))]
    reader = SnapshotStore(data_root() / "corpus").open(load(OUT / "protocol.json")["task"]["snapshot_id"])
    for record in records:
        verify_spans(reader, record["final_context"])
        actual = source_items(record["decisions"][-1]["input"])
        for s in record["final_context"]:
            assert not uncovered(s["start_line"], s["end_line"], [p for p in actual if p["path"] == s["path"]])
        for call in record["decisions"]:
            if call.get("request_sent"):
                assert call["input_budget"]["fits"] and call["input_budget"]["total"] <= 16384
        final = next((d for d in reversed(record["decisions"]) if d.get("phase") == "finalize"), None)
        if final:
            assert json.loads(final["input"][-1]["content"])["question"] == record["question"]
    rows = book().rows()
    count = sum(r["case"].startswith(PREFIX + "/followup/") for r in rows)
    aggregate = sum(r["case"].startswith(PREFIX + "/") for r in rows)
    assert count <= CAP and aggregate <= 144 and len(rows) <= 600
    write_json(OUT / "audit.json", {"turns": len(records), "current_question_isolated": True,
        "all_final_source_ranges_visible_including_blank_lines": True, "baseline_files_unchanged": True,
        "reserved_requests": count, "aggregate_reliability_requests": aggregate,
        "global_reserved_requests": len(rows), "paid_requests": 0})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "run", "replay", "export-review", "summarize", "audit"])
    args = parser.parse_args()
    if args.action in {"run", "replay"}:
        asyncio.run(run() if args.action == "run" else replay())
    elif args.action == "export-review":
        write_json(OUT / "review-candidates.json", candidates())
    else:
        {"prepare": prepare, "summarize": summarize, "audit": audit}[args.action]()


if __name__ == "__main__":
    main()
