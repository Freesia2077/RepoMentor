"""Frozen six-way retrieval ablation; no answer generation and no final-test access."""

import argparse
import hashlib
import json
import statistics
import time
from collections import defaultdict
from pathlib import Path

from benchmark_local_qwen import MemorySampler, gpu_query

from repomentor.chunking import build_structural_index
from repomentor.config import load_chat_settings
from repomentor.evaluation import ANSWER_SCHEMA, atomic_write
from repomentor.hybrid_retrieval import HybridIndex
from repomentor.neural_retrieval import NeuralRetrieval, load_retrieval_models
from repomentor.paths import data_root, runtime_dir
from repomentor.rag import assemble_context, span_from_chunk, verify_spans
from repomentor.rag_evaluation import load_rag_tasks, retrieval_metrics
from repomentor.repository import SnapshotStore
from repomentor.retrieval import BM25Index, RetrievalSettings
from repomentor.retrieval_factory import unload_local_generator
from repomentor.token_budget import input_budget

ROOT = Path(__file__).resolve().parents[1]
VARIANTS = (
    "line_bm25",
    "syntax_bm25_literal",
    "syntax_bm25",
    "syntax_vector",
    "syntax_hybrid",
    "syntax_hybrid_rerank",
)


def write(path, value):
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2))


def protocol():
    tasks = load_rag_tasks(ROOT / "experiments/p4/tasks.jsonl")
    frozen = {
        "schema": "repomentor.p5.protocol.v1",
        "split": "validation",
        "human_approved": False,
        "variants": list(VARIANTS),
        "candidate_k": 20,
        "top_k": 6,
        "rrf_k": 60,
        "chunk_tokens": {"maximum": 768, "split_target_minimum": 256},
        "generation_task_ids": [
            t["task_id"]
            for t in tasks
            if t["task_id"].endswith(("-01", "-05", "-10")) or "-p4-" in t["task_id"]
        ],
        "generation_variants": ["syntax_bm25", "syntax_vector", "syntax_hybrid", "syntax_hybrid_rerank"],
        "history_variants": ["none/recent", "rules/recent", "rules/summary"],
        "protected_files": {
            str(p.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in ("experiments/p2", "experiments/p4")
            for p in sorted((ROOT / folder).rglob("*"))
            if p.is_file()
        },
        "metrics_note": "Full labeled span coverage by union; MRR is first overlap, not semantic accuracy. Alternative evidence may be unlabeled. Summary stores only older user intentions. No gold fed to retrieval/generation.",
    }
    target = ROOT / "experiments/p5/protocol.json"
    if target.exists() and json.loads(target.read_text(encoding="utf-8")) != frozen:
        raise ValueError("frozen protocol or protected P2/P4 artifacts changed")
    if not target.exists():
        write(target, frozen)
    return tasks, frozen


def summarize(rows):
    result = {}
    for variant in VARIANTS:
        group = [r for r in rows if r["variant"] == variant]
        if not group:
            continue

        def metrics(items):
            return {
                k: round(statistics.mean(r["metrics"][k] for r in items if r["metrics"][k] is not None), 4)
                for k in group[0]["metrics"]
                if any(r["metrics"][k] is not None for r in items)
            }

        result[variant] = {
            "tasks": len(group),
            "metrics": metrics(group),
            "by_repository": {
                repo: metrics([r for r in group if r["repository"] == repo])
                for repo in sorted({r["repository"] for r in group})
            },
            "cross_file_complete": sum(
                r["metrics"]["gold_span_selected_coverage"] == 1
                for r in group
                if "-p4-cross_" in r["task_id"]
            ),
            "search_median_seconds": round(
                statistics.median(
                    r["retrieval"]["elapsed_seconds"] for r in group if "elapsed_seconds" in r["retrieval"]
                ),
                4,
            )
            if any("elapsed_seconds" in r["retrieval"] for r in group)
            else None,
            "cache_hits": sum(r["retrieval"].get("cache_hit", False) for r in group),
        }
    return result


def main(args):
    tasks, frozen = protocol()
    if args.freeze_only:
        print(
            json.dumps(
                {"tasks": len(tasks), "generation_tasks": frozen["generation_task_ids"]}, ensure_ascii=False
            )
        )
        return
    profile = load_chat_settings(ROOT / "config/model-settings.qwen-local.json")
    unload_local_generator(profile)
    specs = load_retrieval_models()
    storage = runtime_dir()
    neural = NeuralRetrieval(specs, storage / "neural-cache", device=args.device)
    sampler = MemorySampler()
    sampler.thread.start()
    begun = time.perf_counter()
    rows, builds = [], []
    groups = defaultdict(list)
    for task in tasks:
        groups[task["snapshot_id"]].append(task)
    try:
        for sid, group in groups.items():
            reader = SnapshotStore(data_root() / "corpus").open(sid)
            before = time.perf_counter()
            line = BM25Index.build(reader, storage / "indexes")
            syntax = build_structural_index(
                reader, storage / "syntax-indexes", neural.tokenizer(), neural.identity("embedding")
            )
            literal = build_structural_index(
                reader,
                storage / "syntax-indexes",
                neural.tokenizer(),
                neural.identity("embedding"),
                retrieval=RetrievalSettings(query_expansion=False),
            )
            vector = HybridIndex(syntax, neural, storage / "hybrid", strategy="vector")
            vector.build_vectors()
            neural.embed([t["input"]["question"] for t in group], query=True)
            builds.append(
                {
                    "repository": group[0]["repository"],
                    "snapshot_id": sid,
                    "commit_sha": reader.snapshot.commit_sha,
                    "line_chunks": len(line.chunks),
                    "syntax_chunks": len(syntax.chunks),
                    "syntax_index_id": syntax.index_id,
                    "elapsed_seconds": time.perf_counter() - before,
                    "omissions": syntax.record["omissions"],
                }
            )
            print(json.dumps({"indexed": builds[-1], "neural": neural.stats}), flush=True)
            indexes = [
                line,
                HybridIndex(literal, None, storage / "hybrid", strategy="bm25"),
                HybridIndex(syntax, None, storage / "hybrid", strategy="bm25"),
                vector,
                HybridIndex(syntax, neural, storage / "hybrid", strategy="hybrid"),
                HybridIndex(syntax, neural, storage / "hybrid", strategy="hybrid-rerank"),
            ]
            for variant, index in zip(VARIANTS, indexes, strict=True):
                for task in group:
                    question = task["input"]["question"]
                    searched_at = time.perf_counter()
                    search = index.search(question)
                    search.setdefault("elapsed_seconds", time.perf_counter() - searched_at)
                    prepared = assemble_context(
                        profile, question, [], [span_from_chunk(r["chunk"]) for r in search["candidates"]]
                    )
                    verify_spans(reader, prepared["context"])
                    budget = input_budget(profile, prepared["input_messages"], response_schema=ANSWER_SCHEMA)
                    if not budget["fits"]:
                        raise ValueError("context does not fit generation budget")
                    row = {
                        k: task[k]
                        for k in ("task_id", "repository", "split", "snapshot_id", "commit_sha", "task_type")
                    }
                    row.update(
                        variant=variant,
                        question=question,
                        retrieval=search,
                        **prepared,
                        metrics=retrieval_metrics(
                            task["gold"]["evidence"], {"retrieval": search, **prepared}
                        ),
                        budget=budget,
                    )
                    row["retrieval"] = {
                        **search,
                        "candidates": [
                            {**r, "chunk": {k: v for k, v in r["chunk"].items() if k != "content"}}
                            for r in search["candidates"]
                        ],
                    }
                    rows.append(row)
                    atomic_write(args.output, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
                print(
                    json.dumps(
                        {
                            "repository": group[0]["repository"],
                            "variant": variant,
                            "rows": len(rows),
                            "neural": neural.stats,
                        }
                    ),
                    flush=True,
                )
    finally:
        neural.close()
        sampler.stop.set()
        sampler.thread.join(10)
        write(
            args.output.with_suffix(".resources.json"),
            {
                "elapsed_seconds": time.perf_counter() - begun,
                "hardware": gpu_query("name,driver_version,memory.total"),
                "peak_gpu_mib": max((s["device_memory_mib"] for s in sampler.samples), default=None),
                "sample_count": len(sampler.samples),
                "sampling_errors": sampler.errors,
                "neural_work": neural.stats,
                "builds": builds,
            },
        )
    write(args.output.with_suffix(".summary.json"), summarize(rows))
    print(json.dumps(summarize(rows), ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    parser.add_argument("--output", type=Path, default=ROOT / "experiments/p5/retrieval.jsonl")
    parser.add_argument("--freeze-only", action="store_true")
    main(parser.parse_args())
