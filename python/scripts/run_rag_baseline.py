"""Explicit, resumable local generation benchmark; reuse all existing per-task results."""

import argparse
import asyncio
import json
import signal
from pathlib import Path

from repomentor.config import load_chat_settings
from repomentor.harness import CancellationToken
from repomentor.paths import data_root, runtime_dir
from repomentor.rag_evaluation import load_rag_tasks, run_rag_evaluation
from repomentor.repository import SnapshotStore
from repomentor.runtimes.chat import ChatCompletionsRuntime

ROOT = Path(__file__).resolve().parents[1]


async def main(args):
    token = CancellationToken()
    signal.signal(signal.SIGINT, lambda *_: token.cancel())
    profile = load_chat_settings(ROOT / "config/model-settings.qwen-local.json")
    tasks = load_rag_tasks(args.tasks)
    # Round robin across repositories gives --limit a balanced smoke subset.
    groups = {
        repo: [t for t in tasks if t["repository"] == repo]
        for repo in sorted({t["repository"] for t in tasks})
    }
    tasks = [
        group[i] for i in range(max(map(len, groups.values()))) for group in groups.values() if i < len(group)
    ]
    summary = await run_rag_evaluation(
        tasks,
        ChatCompletionsRuntime(profile),
        SnapshotStore(data_root() / "corpus"),
        runtime_dir(),
        args.output,
        limit=args.limit,
        token=token,
        emit=lambda s: print(s, flush=True),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, default=ROOT / "experiments/p4/tasks.jsonl")
    parser.add_argument("--output", type=Path, default=ROOT / "experiments/p4/rag-baseline.jsonl")
    parser.add_argument("--limit", type=int)
    asyncio.run(main(parser.parse_args()))
