"""One bounded CPU-retrieval dialogue after a recorded CUDA initialization failure."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from p63_closeout import LIMITS, OUT, PRIVATE, ROOT, freeze, load, settings

from repomentor.conversation import ConversationStore
from repomentor.experiment_ledger import RequestLedger
from repomentor.paths import data_root, runtime_dir
from repomentor.presentation import render_run
from repomentor.records import write
from repomentor.repository import SnapshotStore
from repomentor.retrieval_factory import build_index
from repomentor.runtimes.chat import fingerprint
from repomentor.runtimes.openai_sdk import OpenAISdkRuntime
from repomentor.sdk_agent import SdkAgentEngine, sdk_version


async def main(replay):
    folder = OUT / "demo-cpu"
    freeze(folder / "manifest.json", {"sdk": sdk_version(), "profile": settings().public(),
        "retriever": "bm25", "device": "cpu", "limits": LIMITS.model_dump(), "cap": 16,
        "purpose": "One actual four-turn operational demo, not a new semantic confirmation",
        "reason": "Hybrid CUDA retrieval unload/reload was followed by local CUDA initialization failure",
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
        "training_eligible": False, "automatic_retry": False})
    profile = settings()
    book = RequestLedger(runtime_dir() / "p63/dispatches.sqlite", limit=600,
        experiment=fingerprint(load(ROOT / "experiments/p63/protocol.json")), context_windows=(16384,),
        batch_prefix="closeout-demo-cpu", batch_limit=16)

    def before(body, budget):
        assert not replay, "replay forbids generation"
        book.reserve(profile, "closeout-demo-cpu/mitt", body, budget)

    class Runtime(OpenAISdkRuntime):
        async def verify_backend(self):
            assert not replay, "replay forbids metadata requests"
            return await super().verify_backend()

    task = next(t for t in load(ROOT / "experiments/p63/development.json") if t["task_id"] == "history-dev-04")
    reader = SnapshotStore(data_root() / "corpus").open(task["snapshot_id"])
    index = build_index(reader, chunker="syntax", retriever="bm25", device="cpu", storage=runtime_dir(), profile=profile)
    engine = SdkAgentEngine(reader, index, Runtime(profile, before_dispatch=before), PRIVATE / "cpu-demo-cache", LIMITS)
    store = ConversationStore(PRIVATE / "cpu-demo-conversations")
    state_path = folder / "session.json"
    if not state_path.exists():
        assert not replay
        state = store.create(task["snapshot_id"], "理解 mitt 事件注册与移除")
        write(state_path, {"session_id": state["session_id"]})
    session = load(state_path)["session_id"]
    questions = ["mitt 的 on 会统一转换事件名大小写吗？", "那么测试如何验证大小写保持不变？",
                 "off 不传 handler 会怎样处理，是否删除 Map 的键？", "那么 emit 遍历时为什么调用 slice？"]
    replayed = []
    for i, question in enumerate(questions, 1):
        path = folder / f"cases/turn-{i}.json"
        if path.exists() and not replay:
            continue
        result = await store.turn(session, engine, question, request_id=f"cpu-demo-{i}")
        if replay:
            assert result["replayed"] and result["new_model_requests"] == 0
            replayed.append(i)
        else:
            write(path, {**result, "snapshot_id": task["snapshot_id"], "training_eligible": False})
            (OUT / "markdown" / f"demo-cpu-{i}.md").write_text(render_run(result), encoding="utf-8")
            print(json.dumps({"turn": i, "status": result["result"]["completion"]["status"],
                              "requests": result["new_model_requests"]}), flush=True)
            if result["result"]["completion"]["status"] != "success":
                break
    if replay:
        write(folder / "replay.json", {"turns": replayed, "generation_requests": 0, "metadata_requests": 0})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", action="store_true")
    asyncio.run(main(parser.parse_args().replay))
