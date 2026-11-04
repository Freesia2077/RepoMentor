"""Controlled six-turn navigation experiments; each condition uses its own actual answers."""

import argparse
import asyncio
import json
from pathlib import Path

from repomentor.config import load_chat_settings
from repomentor.conversation import ConversationStore
from repomentor.evaluation import atomic_write
from repomentor.paths import data_root, runtime_dir
from repomentor.rag import RagEngine
from repomentor.rag_evaluation import retrieval_metrics
from repomentor.repository import SnapshotStore
from repomentor.retrieval import BM25Index
from repomentor.runtimes.chat import ChatCompletionsRuntime

ROOT = Path(__file__).resolve().parents[1]


def prepare():
    scenarios = [
        {
            "repository": "developit/mitt",
            "snapshot_id": "dfdc0816797bf578d0363591",
            "symbol": "on",
            "path": "src/index.ts",
            "questions": [
                "on 如何注册监听器？",
                "它对已经存在的监听列表怎么处理？",
                "off 不传处理器时会做什么？",
                "emit 按什么顺序调用通配监听器？",
                "EventHandlerMap 保存什么键和值？",
                "最开始讨论的那个方法，它如何保存监听器？",
            ],
            "gold_ranges": [[[66, 73]], [[66, 73]], [[82, 91]], [[103, 121]], [[18, 21]], [[66, 73]]],
        },
        {
            "repository": "ai/nanoid",
            "snapshot_id": "30e277e52d238ad871cb5684",
            "symbol": "customAlphabet",
            "path": "index.js",
            "questions": [
                "customAlphabet 的返回值是什么？",
                "它返回的生成器对负数长度怎么处理？",
                "nanoid 是怎样定义的？",
                "random 如何产生随机字节？",
                "customRandom 如何处理不能均匀映射到字母表的字节？",
                "最开始讨论的那个函数，它的返回值和默认长度是什么？",
            ],
            "gold_ranges": [
                [[96, 110], [133, 138], [169, 175]],
                [[133, 138]],
                [[177, 177]],
                [[18, 27]],
                [[29, 40], [70, 86]],
                [[96, 110], [133, 138], [169, 175]],
            ],
        },
    ]
    frozen = {
        "schema": "repomentor.p5.history.v1",
        "split": "validation",
        "human_approved": False,
        "scenarios": scenarios,
        "conditions": [["none", "recent"], ["rules", "recent"], ["rules", "summary"]],
        "retriever": "line_bm25",
        "note": "Designed pronoun/old-topic stress cases, not an independent dialogue benchmark; same current question across conditions, previous assistant turns are actual outputs of that condition. Gold never enters the model prompt.",
    }
    target = ROOT / "experiments/p5/history-protocol.json"
    if target.exists() and json.loads(target.read_text(encoding="utf-8")) != frozen:
        raise ValueError("history protocol changed")
    if not target.exists():
        atomic_write(target, json.dumps(frozen, ensure_ascii=False, indent=2))
    return frozen


async def main(args):
    frozen = prepare()
    if args.prepare_only:
        print("Two six-turn scenarios and three conditions frozen; no model calls.")
        return
    runtime = ChatCompletionsRuntime(load_chat_settings(ROOT / "config/model-settings.qwen-local.json"))
    sessions = ConversationStore(runtime_dir() / "p5-history-sessions")
    manifest_path = runtime_dir() / "p5-history-session-ids.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    rows = []
    for scenario in frozen["scenarios"]:
        reader = SnapshotStore(data_root() / "corpus").open(scenario["snapshot_id"])
        engine = RagEngine(
            reader, BM25Index.build(reader, runtime_dir() / "indexes"), runtime, runtime_dir() / "rag/cache"
        )
        for rewrite, history_mode in frozen["conditions"]:
            key = f"{scenario['snapshot_id']}/{rewrite}/{history_mode}"
            if key not in manifest:
                manifest[key] = sessions.create(
                    scenario["snapshot_id"], history_mode=history_mode, query_rewrite=rewrite
                )["session_id"]
                atomic_write(manifest_path, json.dumps(manifest))
            for i, (question, ranges) in enumerate(
                zip(scenario["questions"], scenario["gold_ranges"], strict=True), 1
            ):
                row = await sessions.turn(manifest[key], engine, question, request_id=f"turn-{i}")
                evidence = [{"path": scenario["path"], "start_line": a, "end_line": b} for a, b in ranges]
                row.update(
                    repository=scenario["repository"],
                    split="validation",
                    condition=f"{rewrite}/{history_mode}",
                    turn=i,
                    metrics=retrieval_metrics(evidence, row["result"]),
                    gold_reference=evidence,
                )
                result = row["result"]
                result["retrieval"]["candidates"] = [
                    {**r, "chunk": {k: v for k, v in r["chunk"].items() if k != "content"}}
                    for r in result["retrieval"]["candidates"]
                ]
                rows.append(row)
                atomic_write(args.output, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
                print(
                    json.dumps(
                        {
                            "repository": row["repository"],
                            "condition": row["condition"],
                            "turn": i,
                            "status": result["completion"]["status"],
                            "new_requests": row["new_model_requests"],
                        }
                    ),
                    flush=True,
                )
                if result["completion"]["status"] != "success":
                    break  # Preserve failure; never invent history or silently retry.


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "experiments/p5/history.jsonl")
    asyncio.run(main(parser.parse_args()))
