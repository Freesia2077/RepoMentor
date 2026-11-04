import hashlib
import json

import pytest

from repomentor.retrieval import BM25Index, RetrievalSettings, chunk_file, expand_query, tokenize


def test_line_chunks_preserve_exact_source_and_record_oversized_lines():
    text = "\n".join([f"def f_{i}(): return '中文'" for i in range(12)] + ["z" * 500, "final line"])
    settings = RetrievalSettings(max_lines=4, overlap_lines=1, max_chunk_chars=200)
    chunks, skipped = chunk_file("a" * 24, "src/a.py", text, settings)
    for row in chunks:
        assert row.content == "\n".join(text.splitlines()[row.start_line - 1 : row.end_line])
        assert row.content_hash == hashlib.sha256(row.content.encode()).hexdigest()
    assert skipped == [{"path": "src/a.py", "start_line": 13, "reason": "oversized_line"}]
    assert chunks[-1].end_line == 14


def test_identifier_tokenization_and_expansion_are_explicit():
    terms = tokenize("src/getUser_ID.py HTTPServer helper")
    assert {"src/getuser_id.py", "get", "user", "id", "http", "server"} <= set(terms)
    assert expand_query("入口", False) == "入口"
    assert "index" in expand_query("入口", True)


def test_index_is_reproducible_and_binds_retrieval_settings(reader, tmp_path):
    first = BM25Index.build(reader, tmp_path / "index")
    second = BM25Index.build(reader, tmp_path / "index")
    assert first.index_id == second.index_id
    found = first.search("helper")
    assert found["candidates"] and any("helper" in row["chunk"]["path"] for row in found["candidates"])
    assert not first.search("zzzzunknownidentifierzzzz")["candidates"]
    literal = BM25Index.build(reader, tmp_path / "index", RetrievalSettings(query_expansion=False))
    assert literal.index_id != first.index_id


def test_index_cache_corruption_rejected(reader, tmp_path):
    index = BM25Index.build(reader, tmp_path / "index")
    path = tmp_path / "index" / (index.index_id + ".json")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["chunks"][0]["content"] = "tampered"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        BM25Index.build(reader, tmp_path / "index")
