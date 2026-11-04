"""Two four-turn local demonstrations with real prior answers, persistent request replay."""

import asyncio
import json
from pathlib import Path

from repomentor.config import load_chat_settings, write_json
from repomentor.conversation import ConversationStore
from repomentor.paths import data_root, runtime_dir
from repomentor.rag import RagEngine
from repomentor.repository import SnapshotStore
from repomentor.retrieval import BM25Index
from repomentor.runtimes.chat import ChatCompletionsRuntime

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = [
    (
        "mitt",
        "dfdc0816797bf578d0363591",
        [
            "介绍这个仓库的用途和主要功能。",
            "从哪个入口文件开始阅读？",
            "请解释 on 的实现。",
            "它对应的测试在哪里，验证了哪些行为？",
        ],
    ),
    (
        "nanoid",
        "30e277e52d238ad871cb5684",
        [
            "介绍仓库用途和主要功能。",
            "Node.js 入口文件在哪里？",
            "请解释 customAlphabet 的默认长度和参数含义。",
            "它对应的测试是否检查自定义长度？",
        ],
    ),
]


async def main():
    storage = runtime_dir()
    checkpoint = ROOT / "experiments/p4/conversation-sessions.json"
    sessions = json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else {}
    store = ConversationStore(storage / "conversations")
    runtime = ChatCompletionsRuntime(load_chat_settings(ROOT / "config/model-settings.qwen-local.json"))
    summary = []
    for name, sid, questions in SCENARIOS:
        reader = SnapshotStore(data_root() / "corpus").open(sid)
        engine = RagEngine(
            reader, BM25Index.build(reader, storage / "indexes"), runtime, storage / "rag/cache"
        )
        if name not in sessions:
            sessions[name] = store.create(sid)["session_id"]
            write_json(checkpoint, sessions)
        rows = []
        for i, question in enumerate(questions, 1):
            row = await store.turn(sessions[name], engine, question, request_id=f"{name}-turn-{i}")
            rows.append(row)
            write_json(ROOT / f"experiments/p4/{name}-conversation.json", rows)
            print(
                json.dumps(
                    {
                        "scenario": name,
                        "turn": i,
                        "status": row["result"]["completion"]["status"],
                        "replayed": row["replayed"],
                    }
                ),
                flush=True,
            )
            if row["result"]["completion"]["status"] != "success":
                raise RuntimeError("preserved failed turn; inspect before resuming")
        # Reconstruct the store object to prove replay survives process-level storage boundaries.
        replay = await ConversationStore(storage / "conversations").turn(
            sessions[name], engine, questions[-1], request_id=f"{name}-turn-4"
        )
        assert replay["replayed"] and replay["new_model_requests"] == 0
        summary.append(
            {
                "scenario": name,
                "session_id": sessions[name],
                "turns": len(rows),
                "new_model_requests": sum(r["new_model_requests"] for r in rows),
                "replay_new_requests": replay["new_model_requests"],
                "semantic_correctness": "not_scored",
            }
        )
    write_json(ROOT / "experiments/p4/conversations-summary.json", summary)


if __name__ == "__main__":
    asyncio.run(main())
