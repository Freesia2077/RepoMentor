"""One small-repository cache-cold/warm retrieval probe, without LLM generation."""

import time

from p63 import load, open_task, settings
from p63_dataset import OUT

from repomentor.paths import runtime_dir
from repomentor.records import write
from repomentor.retrieval_factory import build_index


def main():
    task = next(t for t in load(OUT / "development.json") if t["repository"] == "developit/mitt")
    reader = open_task(task)
    question = task["input"]["question"]
    for mode in ("bm25", "vector", "hybrid", "hybrid-rerank"):
        destination = OUT / "retrieval/cache-probe" / (mode + ".json")
        if destination.exists():
            continue
        storage = runtime_dir() / "p63/cache-probe" / mode
        assert not storage.exists(), "partial cache probe must be retained, not silently called cold"
        before = time.perf_counter()
        index = build_index(reader, storage=storage, retriever=mode, device="cuda", profile=settings())
        built = time.perf_counter()
        first = index.search(question, k=20)
        cold = time.perf_counter()
        second = index.search(question, k=20)
        warm = time.perf_counter()
        assert [c["chunk"]["chunk_id"] for c in first["candidates"]] == [c["chunk"]["chunk_id"] for c in second["candidates"]]
        row = {"repository": task["repository"], "snapshot_id": task["snapshot_id"],
            "question": question, "retriever": mode, "chunks": len(index.chunks),
            "cache_initially_absent": True, "index_build_seconds": built-before,
            "cold_first_query_seconds": cold-built, "warm_query_seconds": warm-cold,
            "neural_work": dict(index.neural.stats) if hasattr(index, "neural") else {},
            "generation_requests": 0,
            "scope": "One 91-chunk repository; empty index/vector/query caches. Installed weights reused. OS disk cache and Python process startup not controlled. Not a six-repository latency average."}
        write(destination, row)
        if hasattr(index, "release_models"):
            index.release_models()
        print(mode, row["cold_first_query_seconds"], row["warm_query_seconds"], flush=True)


if __name__ == "__main__":
    main()
