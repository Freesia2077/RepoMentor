"""Regression cases for recall filtering, source completeness and report provenance."""

import hashlib
from types import SimpleNamespace

import pytest
from agents.testing import assistant_message
from test_sdk_agent import build, final_step, read_step

from repomentor.agent_tools import AgentLimits, RepositoryEnvironment
from repomentor.contracts import CodeChunk, MentorOutput
from repomentor.hybrid_retrieval import HybridIndex
from repomentor.report_validation import validate_report_paths
from repomentor.retrieval import BM25Index, RetrievalSettings
from repomentor.retrieval_scope import SearchScope
from repomentor.source_context import containing_unit, pack_observed_context


def lexical_fixture():
    chunks = []
    for i in range(26):
        path = f"src/file{i}.py" if i < 24 else "docs/guide.md" if i == 24 else "docs-other/guide.md"
        content = "needle " * 20 if i < 24 else "needle " + "irrelevant " * 80
        chunks.append(
            CodeChunk(
                snapshot_id="s",
                chunk_id=str(i),
                path=path,
                start_line=1,
                end_line=1,
                content=content,
                content_hash=hashlib.sha256(content.encode()).hexdigest(),
            ).model_dump()
        )
    return BM25Index({"index_id": "fixture", "chunks": chunks}, RetrievalSettings())


def test_scoped_recall_finds_document_below_global_top_twenty():
    index = lexical_fixture()
    assert all(r["chunk"]["path"].startswith("src/") for r in index.search("needle")["candidates"])
    scope = SearchScope(kind="docs", path_prefix="docs/")
    result = index.search("needle", scope=scope, k=1)
    assert result["eligible_chunks"] == 1
    assert result["candidates"][0]["chunk"]["path"] == "docs/guide.md"
    assert not scope.matches("docs-other/guide.md")


def test_dense_scope_precedes_top_k_reranking_and_cache(tmp_path):
    np = pytest.importorskip("numpy")
    pytest.importorskip("faiss")

    class Neural:
        def __init__(self):
            self.stats = {"embedding_new": 0, "rerank_new": 0}
            self.documents = []

        def identity(self, role):
            return {"fixture": role}

        def embed(self, texts, *, query=False):
            self.stats["embedding_new"] += len(texts)
            matrix = np.zeros((len(texts), 1024), dtype="float32")
            matrix[:, 0] = 1 if query else np.linspace(1, 0.1, len(texts))
            return matrix

        def rerank(self, question, documents):
            self.stats["rerank_new"] += len(documents)
            self.documents = documents
            return [1.0] * len(documents)

    neural = Neural()
    index = HybridIndex(lexical_fixture(), neural, tmp_path)
    scope = SearchScope(path="docs/guide.md")
    result = index.search("needle", k=1, scope=scope)
    assert result["component_ids"] == {"bm25": ["24"], "vector": ["24"]}
    assert len(neural.documents) == 1 and "docs/guide.md" in neural.documents[0]
    before = dict(neural.stats)
    assert index.search("needle", k=1, scope=scope)["cache_hit"]
    empty = index.search("needle", k=1, scope=SearchScope(path_prefix="missing"))
    assert not empty["candidates"] and neural.stats == before
    assert index.search("needle", k=1)["candidates"][0]["chunk"]["path"].startswith("src/")


def test_symbol_expansion_selects_nested_unit_and_decorators():
    source = "class A:\n    @decorate\n    def run(self):\n        return 1\n\ndef other():\n    pass"
    unit = containing_unit(source, "a.py", 4, 4)
    assert unit["start_line"] == 2 and unit["end_line"] == 4 and unit["symbol"] == "A.run"


def test_expanded_reads_and_search_preserve_source_and_explicit_truncation(tmp_path):
    source = (
        "def needle():\n" + "\n".join(f"    value_{i} = {i}" for i in range(80)) + "\n    return value_79"
    )
    reader = SimpleNamespace(
        snapshot=SimpleNamespace(snapshot_id="s"), paths=["a.py"], read=lambda path, size: (source, False)
    )
    chunk = CodeChunk(
        snapshot_id="s",
        chunk_id="a",
        path="a.py",
        start_line=1,
        end_line=82,
        content=source,
        content_hash=hashlib.sha256(source.encode()).hexdigest(),
    )
    index = BM25Index({"index_id": "i", "chunks": [chunk.model_dump()]}, RetrievalSettings())
    env = RepositoryEnvironment(reader, index, AgentLimits(max_observation_bytes=1200))
    result = env.step({"call_id": "s", "tool": "search_code", "arguments": {"query": "needle"}})
    assert result["observation"]["status"] == "ok"
    hit, span = result["observation"]["hits"][0], result["source_spans"][0]
    assert hit["truncated"] and hit["next_line"] == span["end_line"] + 1
    assert hit["unit"]["end_line"] == 82 and len(result["visible_json"].encode()) <= 1200
    assert span["content"] == "\n".join(source.splitlines()[: span["end_line"]])
    env.reset({"question": "needle"})
    read = env.step(
        {
            "call_id": "r",
            "tool": "read_code",
            "arguments": {"path": "a.py", "start_line": 5, "end_line": 5, "expand": "symbol"},
        }
    )
    assert read["source_spans"][0]["start_line"] == 1
    assert read["observation"]["truncated"] and read["observation"]["unit"]["end_line"] == 82


def test_packing_keeps_earlier_relevant_span_and_never_fills_unread_gaps():
    context = [
        {
            "path": "a.py",
            "start_line": i,
            "end_line": i,
            "content": text,
            "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
        }
        for i, text in ((1, "needle handler"), (5, "unrelated stuff"))
    ]
    selected, omitted = pack_observed_context(context, "needle", lambda spans: len(spans) <= 1)
    assert selected == context[:1] and omitted[0]["start_line"] == 5
    assert "content" not in omitted[0]


def mentor(paths=()):
    return {
        "architectureOverview": "信息不足，尚未核验。",
        "dependencyGraph": {},
        "readingPath": [{"step": i, "file": p, "why": "阅读"} for i, p in enumerate(paths, 1)],
        "keyPatterns": [],
        "codeConventions": [],
        "evidenceClaims": [],
        "evidenceCoverage": {
            "examinedFiles": [],
            "gaps": [
                {"kind": "missing_evidence", "subject": "实现", "summary": "尚未核验", "severity": "medium"}
            ],
        },
    }


@pytest.mark.parametrize("path,reason", [("fake.py", "not_in_snapshot"), ("README.md", "not_observed")])
def test_report_rejects_unread_or_fabricated_reading_path(reader, path, reason):
    result = validate_report_paths(mentor([path]), [], reader.paths)
    assert not result["passed"] and any(i["reason"] == reason for i in result["issues"])


@pytest.mark.parametrize("finalizer", [True, False])
async def test_sdk_guardrail_blocks_schema_valid_fabricated_paths(reader, tmp_path, finalizer):
    value = mentor(["src/nonexistent.py"])
    steps = [[assistant_message("已整理报告")], final_step(value)] if finalizer else [final_step(value)]
    engine, model = build(reader, tmp_path, steps)
    row = await engine.answer("生成阅读路径", output_type=MentorOutput)
    assert row["stop_reason"] == "report_grounding" and row["answer"] == value
    assert not row["report_validation"]["passed"] and row["semantic_correctness"] == "not_scored"
    assert row["new_model_requests"] == len(steps)
    assert (await engine.answer("生成阅读路径", output_type=MentorOutput))["new_model_requests"] == 0
    model.assert_complete()


async def test_sdk_guardrail_accepts_observed_path_or_explicit_gap(reader, tmp_path):
    engine, model = build(reader, tmp_path, [read_step(), final_step(mentor(["src/index.ts"]))])
    row = await engine.answer("生成阅读路径", output_type=MentorOutput)
    assert row["completion"]["status"] == "success" and row["report_validation"]["passed"]
    assert validate_report_paths(mentor(), [], reader.paths)["passed"]
    model.assert_complete()
