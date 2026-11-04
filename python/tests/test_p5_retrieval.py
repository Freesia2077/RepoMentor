import hashlib
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("tree_sitter")

from repomentor.chunking import ChunkSettings, build_structural_index, structural_chunks
from repomentor.hybrid_retrieval import HybridIndex, reciprocal_rank_fusion
from repomentor.repository import RepositoryError
from repomentor.retrieval import BM25Index


class CharacterTokenizer:
    def encode(self, text, **kwargs):
        return list(text)


def test_structural_index_skips_unreadable_link_without_failing_repository(reader, tmp_path):
    original = reader.read

    def read(path, limit):
        if path == "linked.py":
            raise RepositoryError("unreadable", "symlink cannot be read")
        return original(path, limit)

    view = SimpleNamespace(snapshot=reader.snapshot, paths=[*reader.paths, "linked.py"], read=read)
    index = build_structural_index(view, tmp_path, CharacterTokenizer(), {"test": "characters"})
    assert {"path": "linked.py", "reason": "unreadable"} in index.record["omissions"]
    assert all(c.path != "linked.py" for c in index.chunks)
    assert index.search("helper")["candidates"]


@pytest.mark.parametrize(
    "path,source",
    [
        (
            "x.py",
            "@decorate\ndef hello(x):\n    return x + 1\n\nclass Store:\n    def get(self):\n        return 2\n",
        ),
        (
            "x.ts",
            "export function hello(x: number) {\n return x + 1;\n}\nconst twice = (x: number) => x * 2;\n",
        ),
        ("x.js", "describe('events', () => {\n it('adds', () => {\n  assert(true);\n });\n});\n"),
        ("README.md", "# Guide\nIntroduction\n```py\n# not a heading\n```\n## Usage\nUse hello.\n"),
    ],
)
def test_structural_chunks_preserve_source_and_boundaries(path, source):
    chunks, omitted, meta = structural_chunks(
        "snapshot", path, source, CharacterTokenizer(), ChunkSettings(max_tokens=128, min_split_tokens=50)
    )
    assert not omitted
    covered = set()
    for chunk in chunks:
        assert chunk.content == "\n".join(source.splitlines()[chunk.start_line - 1 : chunk.end_line])
        assert chunk.content_hash == hashlib.sha256(chunk.content.encode()).hexdigest()
        assert meta[chunk.chunk_id]["embedding_tokens"] <= 128
        covered.update(range(chunk.start_line, chunk.end_line + 1))
    assert all(i in covered for i, line in enumerate(source.splitlines(), 1) if line.strip())
    if path == "x.py":
        assert next(c for c in chunks if c.symbol == "hello").start_line == 1
    if path.endswith(".md"):
        assert not any(c.symbol == "not a heading" for c in chunks)


def test_parse_failure_and_oversized_line_are_explicit():
    chunks, omitted, meta = structural_chunks(
        "s",
        "bad.py",
        "def broken(:\n" + "x" * 200 + "\nprint(1)",
        CharacterTokenizer(),
        ChunkSettings(max_tokens=128),
    )
    assert omitted[0]["start_line"] == 2
    assert all(v["parser"] == "parse_error_text_fallback" for v in meta.values())
    assert chunks[-1].end_line == 3


def test_fusion_uses_ranks_and_deduplicates():
    a = {"chunk": {"chunk_id": "a", "path": "a", "start_line": 1}, "score": 1000}
    b = {"chunk": {"chunk_id": "b", "path": "b", "start_line": 1}, "score": 0.001}
    ranked = reciprocal_rank_fusion({"bm25": [a, b], "vector": [b, b]})
    assert [r["chunk"]["chunk_id"] for r in ranked] == ["b", "a"]
    assert ranked[0]["ranks"] == {"bm25": 2, "vector": 1}
    assert ranked[0]["score"] == pytest.approx(1 / 62 + 1 / 61)


def test_search_cache_binds_index_and_settings(reader, tmp_path):
    index = BM25Index.build(reader, tmp_path / "lexical")
    hybrid = HybridIndex(index, None, tmp_path / "cache", strategy="bm25")
    first, second = hybrid.search("helper"), hybrid.search("helper")
    assert not first["cache_hit"] and second["cache_hit"]
    assert [r["chunk"] for r in first["candidates"]] == [r["chunk"] for r in second["candidates"]]
    path = next((tmp_path / "cache/search").glob("*.json"))
    record = json.loads(path.read_text(encoding="utf-8"))
    record["cache_key"] = "tampered"
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="mismatch"):
        hybrid.search("helper")


def test_dense_and_reranker_change_rank_and_replay_without_inference(reader, tmp_path):
    np = pytest.importorskip("numpy")
    pytest.importorskip("faiss")
    lexical = BM25Index.build(reader, tmp_path / "lexical")

    class FakeNeural:
        stats = {"embedding_new": 0, "rerank_new": 0}

        def identity(self, role):
            return {"fixture": role}

        def embed(self, texts, *, query=False):
            self.stats["embedding_new"] += len(texts)
            matrix = np.zeros((len(texts), 1024), dtype="float32")
            matrix[:, 0] = 1
            return matrix

        def rerank(self, query, documents):
            self.stats["rerank_new"] += len(documents)
            return list(range(len(documents)))

    neural = FakeNeural()
    index = HybridIndex(lexical, neural, tmp_path / "cache")
    first = index.search("helper")
    before = dict(neural.stats)
    second = index.search("helper")
    assert neural.stats == before and all(v == 0 for v in second["neural_work"].values())
    assert len(first["component_ids"]) == 2
    assert [r["score"] for r in first["candidates"]] == sorted(
        [r["score"] for r in first["candidates"]], reverse=True
    )
    assert all("fusion_score" in r for r in first["candidates"])


def test_navigation_memory_uses_only_old_user_intentions():
    from repomentor.context_memory import navigation_summary, resolve_query

    history = []
    for question in [
        "customAlphabet 如何定义？",
        "nanoid 的长度？",
        "urlAlphabet？",
        "random？",
        "customRandom？",
    ]:
        history.extend(
            [{"role": "user", "content": question}, {"role": "assistant", "content": "invented fact"}]
        )
    memory = navigation_summary(history)
    assert memory["past_user_questions"] == ["customAlphabet 如何定义？"]
    assert "invented" not in json.dumps(memory) and memory["source_facts"] is False
    query = "最开始那个函数的返回值是什么？"
    assert resolve_query(query, "customRandom？", "customRandom", memory).endswith("customAlphabet")
    assert resolve_query(query, "customRandom？", "customRandom", memory, enabled=False) == query


def test_parser_native_regression_in_subprocess():
    import subprocess
    import sys

    from repomentor.paths import data_root
    from repomentor.repository import SnapshotStore

    try:
        reader = SnapshotStore(data_root() / "corpus").open("30e277e52d238ad871cb5684")
    except FileNotFoundError:
        pytest.skip("pinned nanoid fixture is not cached")
    source = reader.read("test/index.test.js", 500_000)[0]
    result = subprocess.run(
        [
            sys.executable,
            "-s",
            "-X",
            "utf8",
            "-X",
            "faulthandler",
            "-c",
            "import sys; from repomentor.chunking import syntax_units; s=sys.stdin.read(); "
            "assert all(len(syntax_units(s, '.js')[0]) == 39 for _ in range(20))",
        ],
        input=source,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


async def test_base_index_does_not_load_generation_profile(reader, tmp_path, monkeypatch):
    from repomentor import cli

    monkeypatch.setattr(cli, "open_reader", lambda *args: reader)

    def unavailable(*args):
        raise AssertionError("BM25 indexing must not load model settings")

    monkeypatch.setattr(cli, "load_chat_settings", unavailable)
    args = cli.parser().parse_args(
        ["--data-dir", str(tmp_path), "index", "--snapshot-id", reader.snapshot.snapshot_id]
    )
    assert await cli.async_main(args) == 0


@pytest.mark.parametrize("override", [("--retriever", "vector"), ("--history-mode", "summary")])
async def test_existing_conversation_rejects_changed_policy(reader, tmp_path, monkeypatch, override):
    from repomentor import cli
    from repomentor.conversation import ConversationStore

    state = ConversationStore(tmp_path / "conversations").create(reader.snapshot.snapshot_id)
    monkeypatch.setattr(cli, "open_reader", lambda *args: reader)
    args = cli.parser().parse_args(
        [
            "--data-dir",
            str(tmp_path),
            "chat",
            "--session",
            state["session_id"],
            "--message",
            "helper",
            *override,
        ]
    )
    with pytest.raises(ValueError, match="pinned"):
        await cli.async_main(args)


@pytest.mark.parametrize("configured_loaded", [True, False])
def test_gpu_sharing_unloads_only_the_configured_model(monkeypatch, configured_loaded):
    import httpx
    from test_chat import settings

    from repomentor.retrieval_factory import unload_local_generator

    profile = settings().model_copy(update={"backend": "ollama"})
    posts = []

    def respond(request):
        if request.method == "GET":
            names = ["unrelated-model"] + ([profile.model] if configured_loaded else [])
            return httpx.Response(200, json={"models": [{"name": name} for name in names]})
        posts.append(json.loads(request.content))
        return httpx.Response(200, json={"done": True})

    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(respond))
    )
    unload_local_generator(profile)
    assert posts == ([{"model": profile.model, "keep_alive": 0}] if configured_loaded else [])
