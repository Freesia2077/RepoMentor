"""FAISS cosine search, reciprocal-rank fusion, and optional learned reranking."""

# 组合词项召回、FAISS 向量召回、RRF 排名融合与可选神经重排。
# 缓存绑定块顺序和模型身份，路径范围在召回前生效，保证受限搜索仍有完整候选空间。

import hashlib
import json
import time
from pathlib import Path

from .neural_retrieval import document_text
from .records import atomic_write
from .retrieval_scope import SearchScope
from .runtimes.chat import fingerprint


# 按 sum(1 / (k + rank)) 融合各路排名，避免直接相加尺度不同的 BM25 与向量分数。
def reciprocal_rank_fusion(rankings, *, constant=60):
    values = {}
    for source, ranking in rankings.items():
        seen = set()
        for rank, row in enumerate(ranking, 1):
            cid = row["chunk"]["chunk_id"]
            if cid in seen:
                continue
            seen.add(cid)
            merged = values.setdefault(cid, {"chunk": row["chunk"], "score": 0.0, "ranks": {}})
            merged["score"] += 1 / (constant + rank)
            merged["ranks"][source] = rank
    return sorted(values.values(), key=lambda r: (-r["score"], r["chunk"]["path"], r["chunk"]["start_line"]))


class HybridIndex:
    def __init__(self, lexical, neural, cache_dir, *, strategy="hybrid-rerank", rrf_k=60):
        if strategy not in ("bm25", "vector", "hybrid", "hybrid-rerank"):
            raise ValueError("unknown retrieval strategy")
        self.lexical, self.neural, self.cache_dir, self.strategy = lexical, neural, Path(cache_dir), strategy
        self.settings, self.chunks, self.record = lexical.settings, lexical.chunks, lexical.record
        self.rrf_k, self.vector_index = rrf_k, None
        self.vector_matrix = None
        self.identity = {
            "lexical_index": lexical.index_id,
            "strategy": strategy,
            "rrf_k": rrf_k,
            "code_sha256": hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
            "scope_sha256": hashlib.sha256(
                Path(__file__).with_name("retrieval_scope.py").read_bytes().replace(b"\r\n", b"\n")
            ).hexdigest(),
            "embedding": neural.identity("embedding") if strategy != "bm25" else None,
            "reranker": neural.identity("reranker") if strategy == "hybrid-rerank" else None,
        }
        self.index_id = fingerprint(self.identity)[:24]

    def release_models(self):
        if self.neural:
            self.neural.close()

    # 向量缓存同时核对模型身份、块顺序和文件哈希；L2 归一化后用内积表达余弦相似度。
    def build_vectors(self):
        import faiss
        import numpy as np

        if self.vector_index is not None:
            return
        identity = {
            "index": self.lexical.index_id,
            "embedding": self.neural.identity("embedding"),
            "faiss": faiss.__version__,
        }
        key = fingerprint(identity)
        path = self.cache_dir / "vectors" / (key + ".npy")
        meta = path.with_suffix(".json")
        ids = [chunk.chunk_id for chunk in self.chunks]
        if path.exists() and meta.exists():
            record = json.loads(meta.read_text(encoding="utf-8"))
            if (
                record["identity"] != identity
                or record["chunk_ids"] != ids
                or record["sha256"] != hashlib.sha256(path.read_bytes()).hexdigest()
            ):
                raise ValueError("vector index provenance differs")
            matrix = np.load(path, allow_pickle=False)
        else:
            matrix = (
                self.neural.embed([document_text(c) for c in self.chunks])
                if self.chunks
                else np.zeros((0, 1024), dtype="float32")
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            np.save(path, matrix, allow_pickle=False)
            atomic_write(
                meta,
                json.dumps(
                    {
                        "identity": identity,
                        "chunk_ids": ids,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                ),
            )
        if matrix.shape != (len(ids), 1024) or not np.isfinite(matrix).all():
            raise ValueError("vector matrix shape or values invalid")
        # embedding 已归一化，精确内积检索的排序等价于余弦相似度排序。
        self.vector_index = faiss.IndexFlatIP(1024)
        self.vector_matrix = np.ascontiguousarray(matrix, dtype="float32")
        self.vector_index.add(self.vector_matrix)

    # 先限定可检索集合，再做双路召回、RRF 与可选重排，防止全局 top-k 后过滤漏掉范围内命中。
    def search(self, question, *, k=None, scope=None):
        started = time.perf_counter()
        scope = scope or SearchScope()
        eligible = [i for i, chunk in enumerate(self.chunks) if scope.matches(chunk.path)]
        limit = k or self.settings.candidate_k
        key = fingerprint([self.identity, question, limit, scope.model_dump()])
        path = self.cache_dir / "search" / (key + ".json")
        if path.exists():
            value = json.loads(path.read_text(encoding="utf-8"))
            if value["cache_key"] != key:
                raise ValueError("retrieval cache key mismatch")
            by_id = {c.chunk_id: c for c in self.chunks}
            candidates = [
                {**row, "chunk": by_id[row["chunk_id"]].model_dump()} for row in value["candidates"]
            ]
            return {
                **value,
                "candidates": candidates,
                "cache_hit": True,
                "original_neural_work": value["neural_work"],
                "neural_work": {name: 0 for name in value["neural_work"]},
                "elapsed_seconds": time.perf_counter() - started,
            }
        before = dict(self.neural.stats) if self.neural else {}
        lexical = self.lexical.search(question, k=limit, scope=scope)
        rankings = {"bm25": lexical["candidates"]}
        if self.strategy != "bm25" and eligible:
            import faiss
            import numpy as np

            self.build_vectors()
            vector = self.neural.embed([question], query=True)
            # Search only eligible vectors: global top-k followed by filtering loses matches.
            scoped_index = self.vector_index
            if len(eligible) != len(self.chunks):
                scoped_index = faiss.IndexFlatIP(1024)
                scoped_index.add(np.ascontiguousarray(self.vector_matrix[eligible]))
            scores, positions = scoped_index.search(vector, min(limit, len(eligible)))
            rankings["vector"] = [
                {"score": float(score), "chunk": self.chunks[eligible[int(i)]].model_dump()}
                for score, i in zip(scores[0], positions[0], strict=True)
                if i >= 0
            ]
        elif self.strategy != "bm25":
            rankings["vector"] = []
        candidates = (
            rankings["bm25"]
            if self.strategy == "bm25"
            else rankings["vector"]
            if self.strategy == "vector"
            else reciprocal_rank_fusion(rankings, constant=self.rrf_k)[:limit]
        )
        if self.strategy == "hybrid-rerank" and candidates:
            by_id = {c.chunk_id: c for c in self.chunks}
            # 重排只处理融合后的有限候选；保留 fusion_score 便于追查重排前后的顺序变化。
            scores = self.neural.rerank(
                question, [document_text(by_id[r["chunk"]["chunk_id"]]) for r in candidates]
            )
            candidates = [
                {**row, "fusion_score": row["score"], "score": score}
                for row, score in zip(candidates, scores, strict=True)
            ]
            candidates.sort(key=lambda r: (-r["score"], r["chunk"]["path"], r["chunk"]["start_line"]))
        record = {
            "query": question,
            "lexical_query": lexical["query"],
            "index_id": self.index_id,
            "strategy": self.strategy,
            "search_scope": scope.model_dump(),
            "eligible_chunks": len(eligible),
            "cache_key": key,
            "cache_hit": False,
            "identity": self.identity,
            "elapsed_seconds": time.perf_counter() - started,
            "neural_work": {key: n - before[key] for key, n in self.neural.stats.items()}
            if self.neural
            else {},
            "component_ids": {
                source: [r["chunk"]["chunk_id"] for r in ranking] for source, ranking in rankings.items()
            },
            "candidates": [
                {**{k: v for k, v in row.items() if k != "chunk"}, "chunk_id": row["chunk"]["chunk_id"]}
                for row in candidates
            ],
        }
        atomic_write(path, json.dumps(record, ensure_ascii=False))
        return {**record, "candidates": candidates}
