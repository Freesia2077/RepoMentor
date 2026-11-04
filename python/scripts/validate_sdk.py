"""Bounded validation-only SDK smoke tests; cached replays issue no model requests."""

import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse

from repomentor.agent_tools import AgentLimits
from repomentor.config import load_chat_settings
from repomentor.conversation import ConversationStore
from repomentor.paths import data_root, runtime_dir
from repomentor.records import write
from repomentor.repository import SnapshotStore
from repomentor.retrieval_factory import build_index
from repomentor.runtimes.openai_sdk import OpenAISdkRuntime
from repomentor.sdk_agent import SdkAgentEngine
from repomentor.sdk_report import SdkReport

ROOT = Path(__file__).resolve().parents[1]


async def main(args):
    if args.output.exists():
        raise ValueError("choose a new output to preserve earlier attempts")
    profile = load_chat_settings(ROOT / "config/model-settings.qwen-local.json")
    if profile.backend != "ollama" or urlparse(profile.baseUrl).hostname not in ("localhost", "127.0.0.1"):
        raise ValueError("validation permits only the existing local Ollama endpoint")
    tasks = json.loads((ROOT / "experiments/p6/tasks.json").read_text(encoding="utf-8"))
    task = next(t for t in tasks if t["task_id"] == "developit--mitt-p4-cross_case")
    assert task["split"] == "validation"
    reader = SnapshotStore(data_root() / "corpus").open(task["snapshot_id"])
    index = build_index(reader, storage=runtime_dir(), profile=profile,
                        chunker="syntax", retriever="bm25", device="cpu", query_expansion=True)
    if args.mode == "report":
        profile = profile.model_copy(update={"max_tokens": 2048})
    runtime = OpenAISdkRuntime(profile)
    if args.replay:
        async def no_model():
            raise AssertionError("cache-only replay cannot request metadata or inference")
        runtime.verify_backend = no_model
    if args.mode == "report":
        report = SdkReport(reader, index, runtime, runtime_dir() / "sdk/cache",
                           limits=AgentLimits(max_tool_calls=3, max_decisions=4))
        result = await report.run()
        record = {"split": "validation", "report": result.model_dump(), "stages": report.records}
        rows = report.records
    else:
        engine = SdkAgentEngine(reader, index, runtime, runtime_dir() / "sdk/cache",
                                AgentLimits(max_tool_calls=5, max_decisions=6))
        if args.mode == "qa":
            row = await engine.answer(task["input"]["question"])
            record = {"split": "validation", "task_id": task["task_id"], "result": row}
            rows = [row]
        else:
            store = ConversationStore(runtime_dir() / "sdk-validation-conversations")
            state = store.create(reader.snapshot.snapshot_id, "SDK compatibility validation", query_rewrite="none")
            # First turn deliberately reuses the QA cache above; only the follow-up needs inference.
            first = await store.turn(state["session_id"], engine, task["input"]["question"], request_id="first")
            if first["result"]["completion"]["status"] != "success":
                raise ValueError("QA compatibility must pass before testing follow-up")
            second = await store.turn(state["session_id"], engine,
                                      "刚才讨论的事件名可以用 Symbol 吗？请核对实现和对应测试。", request_id="second")
            replay = None
            if second["result"]["completion"]["status"] == "success":
                replay = await store.turn(state["session_id"], engine,
                                          "刚才讨论的事件名可以用 Symbol 吗？请核对实现和对应测试。", request_id="second")
            rows = [first["result"], second["result"]]
            record = {"split": "validation", "session_id": state["session_id"], "turns": [first, second],
                      "request_id_replay_new_requests": replay["new_model_requests"] if replay else None}
    record.update(new_local_requests=sum(r["new_model_requests"] for r in rows), paid_requests=0,
                  acceptance="protocol smoke only; no semantic benchmark or human approval")
    write(args.output, record)
    print(json.dumps({"output": str(args.output), "new_local_requests": record["new_local_requests"],
                      "stops": [r["stop_reason"] for r in rows]}, ensure_ascii=False))


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--mode", choices=("qa", "conversation", "report"), required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--replay", action="store_true")
asyncio.run(main(parser.parse_args()))
