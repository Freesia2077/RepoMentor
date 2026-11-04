"""Build a small fixed-context validation baseline; gold answers never enter tasks."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from repomentor.config import write_json
from repomentor.evaluation import EvaluationTask, atomic_write, verify_task_context
from repomentor.paths import data_root
from repomentor.repository import SnapshotStore

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--restore-snapshots",
        action="store_true",
        help="explicitly fetch missing validation snapshots at their frozen SHAs",
    )
    args = parser.parse_args()
    seeds = [
        json.loads(line)
        for line in (ROOT / "experiments/p0/validation-seed.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    store = SnapshotStore(data_root() / "corpus")
    counts = Counter()
    tasks = []
    for seed in seeds:
        if seed["split"] != "validation" or counts[seed["repository"]] >= 3:
            continue
        try:
            reader = store.open(seed["snapshot_id"])
        except FileNotFoundError:
            if not args.restore_snapshots:
                raise ValueError(
                    "P0 validation snapshot missing; use --restore-snapshots to fetch the frozen commit"
                ) from None
            reader = store.import_repository(seed["repository"], seed["commit_sha"])
            assert reader.snapshot.snapshot_id == seed["snapshot_id"]
        context = []
        for evidence in seed["gold"]["evidence"]:
            text, truncated = reader.read(evidence["path"], 500_000)
            assert not truncated
            content = "\n".join(text.splitlines()[evidence["start_line"] - 1 : evidence["end_line"]])
            assert hashlib.sha256(content.encode()).hexdigest() == evidence["content_sha256"]
            context.append({**evidence, "content": content})
        if sum(len(row["content"].encode()) for row in context) > 1700:
            continue
        task = EvaluationTask(
            **{k: seed[k] for k in ("task_id", "repository", "snapshot_id", "commit_sha", "split", "input")},
            context_mode="oracle",
            context=context,
        )
        verify_task_context(task, store)
        tasks.append(task)
        counts[seed["repository"]] += 1
    assert len(tasks) == 12 and len(counts) == 4
    atomic_write(
        ROOT / "experiments/p3/tasks.validation-oracle.jsonl",
        "".join(t.model_dump_json() + "\n" for t in tasks),
    )
    write_json(
        ROOT / "experiments/p3/task-selection.json",
        {
            "source": "p0/validation-seed.jsonl",
            "selection": "First three tasks per validation repository with gold-selected source spans <=1700 UTF-8 bytes",
            "context_mode": "oracle",
            "tasks": [t.task_id for t in tasks],
            "counts": dict(counts),
            "gold_answers_in_model_input": False,
            "human_approved": False,
            "limitations": "Source locations are oracle supplied; this tests generation and infrastructure, not retrieval or general task accuracy. Short-context selection bias is explicit. Final test split remains sealed.",
        },
    )
    print(json.dumps({"tasks": len(tasks), "repositories": dict(counts), "model_calls": 0}))


if __name__ == "__main__":
    main()
