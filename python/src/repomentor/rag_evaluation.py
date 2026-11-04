"""Retrieval/format/abstention evaluation, explicitly separate from semantic correctness."""

import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from .rag import RagEngine
from .records import atomic_write
from .retrieval import BM25Index, RetrievalSettings


def coverage(gold, spans):
    if not gold:
        return None
    hits = []
    for reference in gold:
        needed = set(range(reference["start_line"], reference["end_line"] + 1))
        actual = set()
        for span in spans:
            if span["path"] == reference["path"]:
                actual.update(range(span["start_line"], span["end_line"] + 1))
        hits.append(needed <= actual)
    return sum(hits) / len(hits)


def nonblank_reference_complete(references, spans):
    """Complement strict range coverage: omitted blank lines are not missing source facts."""
    if not references:
        return None
    return all(any(s["path"] == ref["path"] and s["start_line"] <= number <= s["end_line"] for s in spans)
               for ref in references
               for number, line in enumerate(ref["content"].split("\n"), ref["start_line"]) if line.strip())


def retrieval_metrics(gold, result):
    candidates = [row["chunk"] for row in result["retrieval"]["candidates"]]
    rank = next(
        (
            i
            for i, chunk in enumerate(candidates, 1)
            if any(
                ref["path"] == chunk["path"]
                and max(ref["start_line"], chunk["start_line"]) <= min(ref["end_line"], chunk["end_line"])
                for ref in gold
            )
        ),
        None,
    )
    return {
        "gold_span_recall_at_6": coverage(gold, candidates[:6]),
        "gold_span_recall_at_20": coverage(gold, candidates[:20]),
        "gold_span_selected_coverage": coverage(gold, result["context"]),
        "mrr_first_overlapping_span": 1 / rank if rank else 0 if gold else None,
    }


def load_rag_tasks(path):
    rows = [
        json.loads(line) for line in Path(path).read_text(encoding="utf-8-sig").splitlines() if line.strip()
    ]
    if not rows or len({r["task_id"] for r in rows}) != len(rows):
        raise ValueError("empty task set or duplicate task IDs")
    if any(row["split"] not in ("train", "validation") for row in rows):
        raise ValueError("final test set remains sealed")
    return rows


def summarize(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["mode"]].append(row)
    result = {}
    for mode, group in grouped.items():
        metric_names = [
            "gold_span_recall_at_6",
            "gold_span_recall_at_20",
            "gold_span_selected_coverage",
            "mrr_first_overlapping_span",
        ]
        metrics = {}
        for name in metric_names:
            available = [r["metrics"][name] for r in group if r["metrics"][name] is not None]
            metrics[name] = round(statistics.mean(available), 4) if available else None
        result[mode] = {
            "tasks": len(group),
            "statuses": dict(Counter(r["result"]["completion"]["status"] for r in group)),
            "citation_ranges_valid": sum(r["result"]["citation_ranges_valid"] is True for r in group),
            "insufficient_evidence": sum(
                bool(r["result"]["answer"] and r["result"]["answer"].get("insufficient_evidence"))
                for r in group
            ),
            "new_model_requests": sum(r["result"]["new_model_requests"] for r in group),
            "cache_hits": sum(r["result"]["cache_hit"] for r in group),
            "latency_median_seconds": round(
                statistics.median(r["result"]["completion"]["elapsed_seconds"] for r in group), 3
            ),
            "retrieval": metrics,
            "semantic_correctness": "not_scored",
        }
    return {
        "modes": result,
        "notes": "Development set; gold stays out of BM25/none model input. Oracle context is a diagnostic. Recall requires full coverage of each labeled line span (union allowed); MRR uses first overlapping span, not semantic relevance. Missing alternative evidence may undercount. Latency of cache hits belongs to the original attempt. No human approval or answer accuracy claim.",
    }


async def run_rag_evaluation(
    tasks,
    runtime,
    snapshots,
    storage,
    output,
    *,
    modes=("none", "bm25", "oracle"),
    limit=None,
    token=None,
    emit=print,
):
    from .execution import CancellationToken
    from .source_windows import verify_spans

    token = token or CancellationToken()
    if limit is not None:
        if limit <= 0:
            raise ValueError("limit must be positive")
        tasks = tasks[:limit]
    rows, engines = [], {}
    for task in tasks:
        token.check()
        sid = task["snapshot_id"]
        if sid not in engines:
            reader = snapshots.open(sid)
            index = BM25Index.build(reader, storage / "indexes", RetrievalSettings(), token=token)
            engines[sid] = RagEngine(reader, index, runtime, storage / "rag/cache")
        engine = engines[sid]
        if task["commit_sha"] != engine.reader.snapshot.commit_sha:
            raise ValueError("task/snapshot SHA differs")
        references = task["gold"]["evidence"]
        context = []
        for ref in references:
            text, truncated = engine.reader.read(ref["path"], 500_000)
            if truncated:
                raise ValueError("gold source is truncated")
            content = "\n".join(text.splitlines()[ref["start_line"] - 1 : ref["end_line"]])
            context.append({**ref, "content": content})
        verify_spans(engine.reader, context)
        for mode in modes:
            result = await engine.answer(
                task["input"]["question"],
                history=task["input"].get("history", []),
                mode=mode,
                oracle_context=context if mode == "oracle" else (),
                token=token,
            )
            # Keep raw outputs and actual selected input; redundant unselected source bodies stay in private cache.
            public = {
                **result,
                "retrieval": {
                    **result["retrieval"],
                    "candidates": [
                        {
                            "score": item["score"],
                            "chunk": {k: v for k, v in item["chunk"].items() if k != "content"},
                        }
                        for item in result["retrieval"]["candidates"]
                    ],
                },
            }
            row = {
                "task_id": task["task_id"],
                "repository": task["repository"],
                "split": task["split"],
                "task_type": task["task_type"],
                "mode": mode,
                "metrics": retrieval_metrics(references, result),
                "result": public,
            }
            rows.append(row)
            atomic_write(output, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
            atomic_write(
                output.with_suffix(".summary.json"), json.dumps(summarize(rows), ensure_ascii=False, indent=2)
            )
            emit(
                json.dumps(
                    {
                        "task_id": task["task_id"],
                        "mode": mode,
                        "status": result["completion"]["status"],
                        "cache_hit": result["cache_hit"],
                    }
                )
            )
            if token.cancelled:
                return summarize(rows)
    return summarize(rows)
