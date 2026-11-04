"""Versioned line chunks and deterministic BM25 over immutable Git snapshots."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath

from pydantic import Field, model_validator

from .contracts import CodeChunk, Contract
from .records import atomic_write
from .repository import MANIFEST_NAMES, RepositoryError, is_source
from .retrieval_scope import SearchScope
from .runtimes.chat import fingerprint

RETRIEVAL_VERSION = "line-bm25-v1"
EXPANSIONS = {
    "概览": "readme overview introduction usage package",
    "介绍": "readme introduction usage",
    "入口": "main index init exports entry",
    "测试": "test tests spec assert",
    "实现": "implementation src lib",
    "依赖": "import require dependencies",
    "安装": "install installation setup",
    "运行": "run usage start",
    "监听": "handler listener on off emit",
    "参数": "parameter argument default",
    "学习": "readme usage examples",
    "架构": "src lib core modules architecture",
}


class RetrievalSettings(Contract):
    max_lines: int = Field(default=40, ge=4, le=200)
    overlap_lines: int = Field(default=8, ge=0, le=32)
    max_chunk_chars: int = Field(default=2400, ge=200, le=10000)
    max_file_bytes: int = Field(default=500_000, ge=1000)
    max_index_bytes: int = Field(default=20_000_000, ge=1000)
    max_files: int = Field(default=2000, ge=1)
    top_k: int = Field(default=6, ge=1, le=30)
    candidate_k: int = Field(default=20, ge=1, le=100)
    initial_source_bytes: int = Field(default=8000, ge=1000, le=48000)
    query_expansion: bool = True
    k1: float = Field(default=1.5, gt=0)
    b: float = Field(default=0.75, ge=0, le=1)

    @model_validator(mode="after")
    def validate_candidate_limit(self):
        if self.candidate_k < self.top_k:
            raise ValueError("candidate_k must be at least top_k")
        return self


def tokenize(text):
    identifiers = re.findall(r"[A-Za-z_][A-Za-z_0-9./-]*|\d+|[\u4e00-\u9fff]+", text)
    result = []
    for word in identifiers:
        if re.fullmatch(r"[\u4e00-\u9fff]+", word):
            result.extend(word[i : i + 2] for i in range(max(1, len(word) - 1)))
            continue
        result.append(word.casefold())
        parts = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", word)
        parts = re.sub(r"([A-Z])([A-Z][a-z])", r"\1 \2", parts)
        result.extend(
            p.casefold() for p in re.split(r"[\W_]+", parts) if p and p.casefold() != word.casefold()
        )
    return result


def expand_query(question, enabled=True):
    extras = [words for phrase, words in EXPANSIONS.items() if enabled and phrase in question]
    return question + ("\n" + " ".join(extras) if extras else "")


def indexable(path):
    p = PurePosixPath(path)
    if any(
        part.lower() in {"node_modules", "vendor", "dist", "build", ".git", "coverage", "__pycache__"}
        for part in p.parts
    ):
        return False
    if p.name.lower().endswith((".lock", "-lock.json", ".min.js", ".map")):
        return False
    return (
        is_source(path)
        or p.suffix.lower() in {".md", ".rst", ".txt", ".json", ".toml", ".yaml", ".yml", ".cfg"}
        or p.name.lower() in MANIFEST_NAMES
    )


def chunk_file(snapshot_id, path, text, settings):
    lines = text.splitlines()
    rows, skipped = [], []
    start = 0
    while start < len(lines):
        end, size = start, 0
        while end < len(lines) and end - start < settings.max_lines:
            added = len(lines[end]) + (end > start)
            if size + added > settings.max_chunk_chars:
                break
            size += added
            end += 1
        if end == start:
            skipped.append({"path": path, "start_line": start + 1, "reason": "oversized_line"})
            start += 1
            continue
        content = "\n".join(lines[start:end])
        if content.strip():
            digest = hashlib.sha256(content.encode()).hexdigest()
            rows.append(
                CodeChunk(
                    snapshot_id=snapshot_id,
                    chunk_id=fingerprint([snapshot_id, path, start + 1, end, digest, RETRIEVAL_VERSION])[:24],
                    path=path,
                    start_line=start + 1,
                    end_line=end,
                    content_hash=digest,
                    content=content,
                )
            )
        if end == len(lines):
            break
        start = max(start + 1, end - settings.overlap_lines)
    return rows, skipped


class BM25Index:
    def __init__(self, record, settings):
        self.record, self.settings = record, settings
        self.chunks = [CodeChunk.model_validate(row) for row in record["chunks"]]
        self.postings = defaultdict(dict)
        self.lengths = []
        for i, row in enumerate(self.chunks):
            terms = Counter(tokenize(row.content) + tokenize(row.path) * 2)
            self.lengths.append(sum(terms.values()))
            for term, frequency in terms.items():
                self.postings[term][i] = frequency
        self.average_length = sum(self.lengths) / max(1, len(self.lengths))

    @property
    def index_id(self):
        return self.record["index_id"]

    @classmethod
    def build(cls, reader, cache_dir: Path, settings=None, token=None):
        settings = settings or RetrievalSettings()
        key = fingerprint(
            {
                "snapshot": reader.snapshot.model_dump(),
                "settings": settings.model_dump(),
                "version": RETRIEVAL_VERSION,
            }
        )[:24]
        path = cache_dir / (key + ".json")
        if path.exists():
            record = json.loads(path.read_text(encoding="utf-8"))
            if record["index_id"] != key or record["snapshot_id"] != reader.snapshot.snapshot_id:
                raise ValueError("index cache metadata mismatch")
            for row in record["chunks"]:
                if hashlib.sha256(row["content"].encode()).hexdigest() != row["content_hash"]:
                    raise ValueError("index chunk hash mismatch")
            return cls(record, settings)
        rows, omissions, used, files = [], [], 0, 0
        for relative in sorted(reader.paths):
            if token:
                token.check()
            if not indexable(relative):
                continue
            if files >= settings.max_files or used >= settings.max_index_bytes:
                omissions.append({"path": relative, "reason": "index_budget"})
                continue
            try:
                text, truncated = reader.read(relative, settings.max_file_bytes)
            except RepositoryError as exc:
                omissions.append({"path": relative, "reason": exc.code})
                continue
            if truncated or used + len(text.encode()) > settings.max_index_bytes:
                omissions.append({"path": relative, "reason": "file_or_index_budget"})
                continue
            chunks, skipped = chunk_file(reader.snapshot.snapshot_id, relative, text, settings)
            rows.extend(row.model_dump() for row in chunks)
            omissions.extend(skipped)
            files += 1
            used += len(text.encode())
        record = {
            "index_id": key,
            "version": RETRIEVAL_VERSION,
            "snapshot_id": reader.snapshot.snapshot_id,
            "commit_sha": reader.snapshot.commit_sha,
            "settings": settings.model_dump(),
            "files": files,
            "source_bytes": used,
            "omissions": omissions,
            "chunks": rows,
        }
        atomic_write(path, json.dumps(record, ensure_ascii=False))
        return cls(record, settings)

    def search(self, question, *, k=None, scope=None):
        scope = scope or SearchScope()
        eligible = {i for i, chunk in enumerate(self.chunks) if scope.matches(chunk.path)}
        query = expand_query(question, self.settings.query_expansion)
        scores = defaultdict(float)
        n = len(self.chunks)
        for term in set(tokenize(query)):
            posting = self.postings.get(term, {})
            idf = math.log(1 + (n - len(posting) + 0.5) / (len(posting) + 0.5))
            for i, tf in posting.items():
                if i not in eligible:
                    continue
                denom = tf + self.settings.k1 * (
                    1 - self.settings.b + self.settings.b * self.lengths[i] / max(1, self.average_length)
                )
                scores[i] += idf * tf * (self.settings.k1 + 1) / denom
        ranked = sorted(scores, key=lambda i: (-scores[i], self.chunks[i].path, self.chunks[i].start_line))[
            : k or self.settings.candidate_k
        ]
        return {
            "query": query,
            "index_id": self.index_id,
            "search_scope": scope.model_dump(),
            "eligible_chunks": len(eligible),
            "candidates": [
                {"score": round(scores[i], 6), "chunk": self.chunks[i].model_dump()} for i in ranked
            ],
        }
