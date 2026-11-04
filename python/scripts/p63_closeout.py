"""Frozen 16K closeout: bounded development, fresh same-repository checks and demos."""

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path

from p63_dataset import ROOT

from repomentor.agent_tools import AgentLimits
from repomentor.config import load_chat_settings
from repomentor.conversation import ConversationStore
from repomentor.experiment_ledger import RequestLedger
from repomentor.paths import data_root, runtime_dir
from repomentor.presentation import render_run
from repomentor.rag import RagEngine
from repomentor.records import write
from repomentor.repository import SnapshotStore
from repomentor.retrieval_factory import build_index
from repomentor.runtimes.chat import ChatCompletionsRuntime, fingerprint
from repomentor.runtimes.openai_sdk import OpenAISdkRuntime
from repomentor.sdk_agent import SdkAgentEngine, sdk_version
from repomentor.sdk_report import SdkReport

OUT = ROOT / "experiments/p63-closeout"
PRIVATE = runtime_dir() / "p63-closeout"
CAPS = {"development": 24, "diagnostic": 12, "confirmation": 72, "reports": 24, "dialogue": 16}
DEV = ["history-dev-03", "history-dev-04", "history-dev-05", "history-dev-06",
       "sweqa-requests-001", "sweqa-flask-002"]
LIMITS = AgentLimits(max_tool_calls=4, max_decisions=4, timeout_seconds=240)


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def freeze(path, value):
    if path.exists():
        assert load(path) == value, f"frozen identity changed: {path}"
    else:
        write(path, value)


def settings():
    value = load_chat_settings(ROOT / "config/model-settings.qwen-local.json")
    assert value.backend == "ollama" and value.context_window == 16384 and value.max_tokens == 1536
    return value


def prepare():
    if (OUT / "protocol.json").exists():
        # The original development identity remains immutable; later phases freeze their own identity.
        protocol = load(OUT / "protocol.json")
        assert settings().public() == protocol["profile"]
        return protocol
    freeze(OUT / "protocol.json", {"version": "p63-closeout-v1", "sdk": sdk_version(),
        "profile": settings().public(), "limits": LIMITS.model_dump(),
        "retriever": "hybrid-rerank", "device": "cuda", "chunker": "syntax",
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
        "global_cap": 600, "previous_requests": 300, "batch_caps": CAPS,
        "development_tasks": DEV, "confirmation_count": 12,
        "same_repository_confirmation": True, "unseen_repository_claim": False,
        "automatic_retry": False, "paid_requests": 0, "training_eligible": False,
        "policy": "One frozen development batch, then confirmation without tuning; keep all failures."})
    return load(OUT / "protocol.json")


def ledger(batch):
    return RequestLedger(runtime_dir() / "p63/dispatches.sqlite", limit=600,
        experiment=fingerprint(load(ROOT / "experiments/p63/protocol.json")), context_windows=(16384,),
        batch_prefix="closeout-" + batch, batch_limit=CAPS[batch])


def runtime(batch, name, mode, replay):
    profile = settings()

    def before(body, budget):
        assert not replay, "cache replay must not send requests"
        ledger(batch).reserve(profile, "closeout-" + batch + "/" + name, body, budget)

    class Fixed(ChatCompletionsRuntime):
        async def verify_backend(self):
            assert not replay, "cache replay must not query backend metadata"
            return await super().verify_backend()

    class Sdk(OpenAISdkRuntime):
        async def verify_backend(self):
            assert not replay, "cache replay must not query backend metadata"
            return await super().verify_backend()

    return (Fixed if mode == "fixed" else Sdk)(profile, before_dispatch=before)


def open_index(task):
    reader = SnapshotStore(data_root() / "corpus").open(task["snapshot_id"])
    assert reader.snapshot.commit_sha == task["commit_sha"]
    index = build_index(reader, chunker="syntax", retriever="hybrid-rerank", device="cuda",
                        storage=runtime_dir(), profile=settings())
    return reader, index


def export(name, row):
    folder = OUT / "markdown"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (name + ".md")).write_text(render_run(row), encoding="utf-8")


async def qa(args):
    tasks = load(OUT / "confirmation.json") if args.batch == "confirmation" else [
        t for t in load(ROOT / "experiments/p63/development.json") if t["task_id"] in DEV]
    if args.task:
        tasks = [t for t in tasks if t["task_id"] == args.task]
        assert tasks
    replayed = []
    for task in tasks:
        reader, index = open_index(task)
        pair = []
        for mode in args.modes:
            name = task["task_id"] + "--" + mode
            path = OUT / args.batch / "cases" / (name + ".json")
            if path.exists() and not args.replay:
                pair.append(load(path))
                print(json.dumps({"case": name, "saved": True}), flush=True)
                continue
            started = time.perf_counter()
            rt = runtime(args.batch, name, mode, args.replay)
            engine = (RagEngine(reader, index, rt, PRIVATE / "rag-cache") if mode == "fixed" else
                      SdkAgentEngine(reader, index, rt, PRIVATE / "sdk-cache", LIMITS))
            result = await engine.answer(task["input"]["question"])
            row = {"task_id": task["task_id"], "mode": mode, "snapshot_id": task["snapshot_id"],
                   "repository": task["repository"], "split": args.batch, "training_eligible": False,
                   "wall_seconds_after_index": round(time.perf_counter() - started, 6), "result": result}
            if args.replay:
                old = load(path)["result"]
                assert result["new_model_requests"] == 0 and result["attempt_id"] == old["attempt_id"]
                replayed.append(name)
            else:
                write(path, row)
                export(args.batch + "-" + name, row)
                print(json.dumps({"case": name, "status": result["completion"]["status"],
                                  "requests": result["new_model_requests"]}), flush=True)
            pair.append(row)
        if len(pair) == 2:
            assert pair[0]["result"]["initial_retrieval"]["sources"] == pair[1]["result"]["initial_retrieval"]["sources"]
    if args.replay:
        write(OUT / args.batch / "replay.json", {"cases": replayed, "generation_requests": 0, "metadata_requests": 0})


async def reports(args):
    tasks = load(ROOT / "experiments/p63/development.json")
    replayed = []
    for repo in ("developit/mitt", "dbader/schedule"):
        task = next(t for t in tasks if t["repository"] == repo)
        name = repo.replace("/", "--")
        path = OUT / "reports/cases" / (name + ".json")
        if path.exists() and not args.replay:
            continue
        reader, index = open_index(task)
        report = SdkReport(reader, index, runtime("reports", name, "sdk", args.replay),
                           PRIVATE / "sdk-cache", limits=LIMITS)
        value = await report.run(total_timeout=900)
        row = {"repository": repo, "snapshot_id": task["snapshot_id"], "report": value.model_dump(mode="json"),
               "stages": report.records, "training_eligible": False}
        if args.replay:
            assert all(s["new_model_requests"] == 0 for s in report.records)
            assert value.outputs == load(path)["report"]["outputs"]
            replayed.append(name)
        else:
            write(path, row)
            print(json.dumps({"report": repo, "status": value.status, "stages": len(report.records)}), flush=True)
    if args.replay:
        write(OUT / "reports/replay.json", {"cases": replayed, "generation_requests": 0, "metadata_requests": 0})


async def dialogue(args):
    task = next(t for t in load(ROOT / "experiments/p63/development.json") if t["task_id"] == "history-dev-04")
    reader, index = open_index(task)
    engine = SdkAgentEngine(reader, index, runtime("dialogue", "mitt", "sdk", args.replay), PRIVATE / "sdk-cache", LIMITS)
    store = ConversationStore(PRIVATE / "conversations")
    path = OUT / "dialogue/session.json"
    if path.exists():
        session = load(path)["session_id"]
    else:
        assert not args.replay
        state = store.create(task["snapshot_id"], "理解 mitt 事件注册与移除")
        session = state["session_id"]
        write(path, {"session_id": session, "snapshot_id": task["snapshot_id"]})
    questions = ["mitt 的 on 会统一转换事件名大小写吗？", "那么测试如何验证大小写保持不变？",
                 "off 不传 handler 会怎样处理，是否删除 Map 的键？", "那么 emit 遍历时为什么调用 slice？"]
    replayed = []
    for i, question in enumerate(questions, 1):
        dest = OUT / f"dialogue/cases/turn-{i}.json"
        if dest.exists() and not args.replay:
            continue
        value = await store.turn(session, engine, question, request_id=f"closeout-{i}")
        if args.replay:
            assert value["replayed"] and value["new_model_requests"] == 0
            replayed.append(i)
        else:
            write(dest, {**value, "snapshot_id": task["snapshot_id"], "training_eligible": False})
            export(f"dialogue-{i}", value)
            print(json.dumps({"turn": i, "status": value["result"]["completion"]["status"],
                              "requests": value["new_model_requests"]}), flush=True)
            if value["result"]["completion"]["status"] != "success":
                break
    if args.replay:
        write(OUT / "dialogue/replay.json", {"turns": replayed, "generation_requests": 0, "metadata_requests": 0})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "qa", "reports", "dialogue"])
    parser.add_argument("--batch", choices=["development", "diagnostic", "confirmation"], default="development")
    parser.add_argument("--modes", nargs="+", choices=["fixed", "sdk"], default=["fixed", "sdk"])
    parser.add_argument("--task")
    parser.add_argument("--replay", action="store_true")
    args = parser.parse_args()
    protocol = prepare()
    batch = args.batch if args.action == "qa" else args.action
    if args.action != "prepare":
        if batch == "development":
            assert sdk_version() == protocol["sdk"], "original development is closed; use the bounded diagnostic phase"
        freeze(OUT / batch / "manifest.json", {"sdk": sdk_version(), "profile": settings().public(),
            "limits": LIMITS.model_dump(), "retriever": "hybrid-rerank", "device": "cuda",
            "batch_cap": CAPS[batch], "global_cap": 600,
            "runner_sha256": hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest()})
    if args.action == "qa":
        asyncio.run(qa(args))
    elif args.action == "reports":
        asyncio.run(reports(args))
    elif args.action == "dialogue":
        asyncio.run(dialogue(args))


if __name__ == "__main__":
    main()
