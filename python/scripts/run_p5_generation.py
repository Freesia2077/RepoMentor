"""Generate the preregistered validation subset from frozen retrieval records (local Qwen only)."""

import argparse
import asyncio
import json
import signal
from pathlib import Path

from run_p5_retrieval import protocol, write

from repomentor.config import load_chat_settings
from repomentor.evaluation import atomic_write
from repomentor.harness import CancellationToken
from repomentor.paths import data_root, runtime_dir
from repomentor.rag import RagEngine
from repomentor.rag_evaluation import summarize
from repomentor.repository import SnapshotStore
from repomentor.retrieval import RetrievalSettings
from repomentor.runtimes.chat import ChatCompletionsRuntime

ROOT = Path(__file__).resolve().parents[1]


class RecordedIndex:
    """Return exact frozen candidates; reconstruct content only from the pinned Git snapshot."""

    def __init__(self, reader, record):
        self.reader, self.record = reader, record
        self.index_id = record["retrieval"]["index_id"]
        self.settings = RetrievalSettings()

    def search(self, question):
        if question != self.record["question"]:
            raise ValueError("recorded query differs")
        rows = []
        for row in self.record["retrieval"]["candidates"]:
            chunk = row["chunk"]
            text, truncated = self.reader.read(chunk["path"], 500_000)
            if truncated:
                raise ValueError("source truncated")
            content = "\n".join(text.splitlines()[chunk["start_line"] - 1 : chunk["end_line"]])
            import hashlib

            if hashlib.sha256(content.encode()).hexdigest() != chunk["content_hash"]:
                raise ValueError("recorded source hash differs")
            rows.append({**row, "chunk": {**chunk, "content": content}})
        return {**self.record["retrieval"], "candidates": rows, "reused_frozen_retrieval": True}


async def main(args):
    _, frozen = protocol()
    records = [
        json.loads(line)
        for line in (ROOT / "experiments/p5/retrieval.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    if len(records) != 396:
        raise ValueError("finish the frozen retrieval matrix first")
    selected = [
        r
        for r in records
        if r["task_id"] in frozen["generation_task_ids"] and r["variant"] in frozen["generation_variants"]
    ]
    if len(selected) != 72:
        raise ValueError("unexpected generation subset")
    token = CancellationToken()
    signal.signal(signal.SIGINT, lambda *_: token.cancel())
    profile = load_chat_settings(ROOT / "config/model-settings.qwen-local.json")
    runtime = ChatCompletionsRuntime(profile)
    backend = None
    rows = []
    for record in selected:
        token.check()
        reader = SnapshotStore(data_root() / "corpus").open(record["snapshot_id"])
        engine = RagEngine(reader, RecordedIndex(reader, record), runtime, runtime_dir() / "rag/cache")
        engine.backend = backend
        result = await engine.answer(record["question"], token=token)
        backend = engine.backend or backend
        if result["context"] != record["context"] or result["input_messages"] != record["input_messages"]:
            raise ValueError("generation input changed from frozen retrieval assembly")
        result["retrieval"] = {
            **result["retrieval"],
            "candidates": [
                {**r, "chunk": {k: v for k, v in r["chunk"].items() if k != "content"}}
                for r in result["retrieval"]["candidates"]
            ],
        }
        row = {k: record[k] for k in ("task_id", "repository", "split", "task_type", "metrics")}
        row.update(mode=record["variant"], result=result)
        rows.append(row)
        atomic_write(args.output, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        write(args.output.with_suffix(".summary.json"), summarize(rows))
        print(
            json.dumps(
                {
                    "completed": len(rows),
                    "task_id": row["task_id"],
                    "variant": row["mode"],
                    "status": result["completion"]["status"],
                    "cache_hit": result["cache_hit"],
                }
            ),
            flush=True,
        )
    old = [
        json.loads(line)
        for line in (ROOT / "experiments/p4/rag-baseline.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    old = [r for r in old if r["mode"] == "bm25" and r["task_id"] in frozen["generation_task_ids"]]
    write(
        ROOT / "experiments/p5/historical-line-baseline.json",
        {
            "source": "../p4/rag-baseline.jsonl",
            "source_sha256": frozen["protected_files"]["experiments/p4/rag-baseline.jsonl"],
            "reuse_note": "Historical P4 outputs, not regenerated under P5 code",
            "summary": summarize(old),
            "attempt_ids": [r["result"]["attempt_id"] for r in old],
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "experiments/p5/generation.jsonl")
    asyncio.run(main(parser.parse_args()))
