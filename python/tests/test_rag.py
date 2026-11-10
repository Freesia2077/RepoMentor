import json

import pytest
from test_chat import settings

from repomentor.conversation import ConversationStore, followup_query
from repomentor.harness import CancellationToken
from repomentor.rag import RagEngine, citation_validity
from repomentor.retrieval import BM25Index
from repomentor.runtimes.chat import CompletionResult, fingerprint


class FakeChat:
    def __init__(self):
        self.settings = settings(context_window=8192)
        self.calls = 0
        self.status = "success"

    async def verify_backend(self):
        return {"verification": "offline-fixture"}

    async def complete(self, messages, **kwargs):
        self.calls += 1
        context = json.loads(messages[-1]["content"])["context"]
        citations = [{k: context[0][k] for k in ("path", "start_line", "end_line")}] if context else []
        return CompletionResult(
            status=self.status,
            request_sent=True,
            request_hash=fingerprint(messages),
            content=json.dumps(
                {"answer": "fixture answer", "citations": citations, "insufficient_evidence": not context}
            ),
            finish_reason="stop",
        )


@pytest.fixture
def engine(reader, tmp_path):
    return RagEngine(reader, BM25Index.build(reader, tmp_path / "index"), FakeChat(), tmp_path / "cache")


async def test_rag_caches_with_trace_and_no_gold_input(engine):
    first = await engine.answer("helper")
    second = await engine.answer("helper")
    assert first["new_model_requests"] == 1 and second["new_model_requests"] == 0
    assert first["attempt_id"] == second["attempt_id"] and engine.runtime.calls == 1
    assert first["context"] and first["citation_ranges_valid"]
    assert "L1:" in str(first["input_messages"]) and "gold" not in str(first["input_messages"])
    from repomentor.contracts import ANSWER_SCHEMA, RepositoryAnswer

    visible = json.loads(first["input_messages"][-1]["content"])["response_schema"]
    assert visible == ANSWER_SCHEMA == RepositoryAnswer.model_json_schema()
    assert "完整回答" in visible["properties"]["answer"]["description"]
    none = await engine.answer("helper", mode="none")
    assert none["answer"]["insufficient_evidence"] and none["context"] == []


async def test_history_pairs_and_snapshot_tamper_are_rejected(engine):
    with pytest.raises(ValueError, match="complete"):
        await engine.answer("helper", history=[{"role": "user", "content": "half"}])
    span = {
        "path": "src/index.ts",
        "start_line": 1,
        "end_line": 1,
        "content": "fake",
        "content_sha256": "0" * 64,
    }
    with pytest.raises(ValueError, match="immutable"):
        await engine.answer("helper", mode="oracle", oracle_context=[span])
    assert engine.runtime.calls == 0


async def test_failed_answer_cached_until_explicit_refresh(engine):
    engine.runtime.status = "timeout"
    failed = await engine.answer("helper")
    engine.runtime.status = "success"
    assert (await engine.answer("helper"))["completion"]["status"] == "timeout"
    retried = await engine.answer("helper", refresh=True)
    assert retried["completion"]["status"] == "success" and failed["attempt_id"] != retried["attempt_id"]


async def test_shared_initial_retrieval_runs_once_and_none_never_searches(reader, tmp_path):
    from unittest.mock import Mock

    from repomentor.retrieval_factory import build_index

    index = build_index(reader, storage=tmp_path / "index", code_aware=False, top_k=5)
    index.base.search = Mock(wraps=index.base.search)
    engine = RagEngine(reader, index, FakeChat(), tmp_path / "cache")
    row = await engine.answer("helper")
    assert index.base.search.call_count == 1
    assert row["initial_retrieval"]["retrieval"] == row["retrieval"]
    assert row["budget"]["fits"] and row["context"]
    await engine.answer("helper", mode="none")
    assert index.base.search.call_count == 1


async def test_tampered_cached_source_cannot_bypass_snapshot_verification(engine):
    row = await engine.answer("helper")
    path = engine.cache_dir / engine.runtime.settings.config_id / (row["cache_key"] + ".json")
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["context"][0]["content"] = "changed source"
    path.write_text(json.dumps(saved), encoding="utf-8")
    with pytest.raises(ValueError, match="immutable"):
        await engine.answer("helper")
    assert engine.runtime.calls == 1


async def test_conversation_replays_completed_request_and_resumes_failure(engine, tmp_path):
    store = ConversationStore(tmp_path / "sessions")
    state = store.create(engine.reader.snapshot.snapshot_id)
    sid = state["session_id"]
    one = await store.turn(sid, engine, "helper 如何实现？", request_id="first")
    replay = await store.turn(sid, engine, "helper 如何实现？", request_id="first")
    assert replay["replayed"] and engine.runtime.calls == 1
    assert replay["result"]["attempt_id"] == one["result"]["attempt_id"]
    engine.runtime.status = "timeout"
    await store.turn(sid, engine, "它有哪些测试？", request_id="second")
    pending = store.load(sid)
    assert pending["pending"] and len(pending["turns"]) == 1
    engine.runtime.status = "success"
    resumed = await store.turn(sid, engine, resume=True)
    assert "helper" in resumed["retrieval_query"]
    assert len(store.load(sid)["turns"]) == 2 and store.load(sid)["pending"] is None
    with pytest.raises(ValueError, match="another question"):
        await store.turn(sid, engine, "different", request_id="first")


async def test_cancellation_does_not_start_request_and_session_path_safe(engine, tmp_path):
    token = CancellationToken()
    token.cancel()
    with pytest.raises(Exception, match="cancelled"):
        await engine.answer("helper", token=token)
    assert engine.runtime.calls == 0
    with pytest.raises(ValueError):
        ConversationStore(tmp_path / "sessions").load("../escape")


def test_followup_and_citation_checks():
    assert "run_pending" in followup_query("它的测试呢？", "previous", "run_pending")
    assert followup_query("新问题 helper", "previous") == "新问题 helper"
    assert not citation_validity({"insufficient_evidence": False, "citations": []}, [])
    assert not citation_validity(
        {"insufficient_evidence": False, "citations": [{"path": "a", "start_line": 5, "end_line": 2}]},
        [{"path": "a", "start_line": 1, "end_line": 10}],
    )


async def test_ambiguous_initial_followup_clarifies_without_call(engine, tmp_path):
    store = ConversationStore(tmp_path / "sessions")
    sid = store.create(engine.reader.snapshot.snapshot_id)["session_id"]
    row = await store.turn(sid, engine, "它怎么实现的？")
    assert row["result"]["handling"] == "deterministic_clarification"
    assert row["new_model_requests"] == 0 and engine.runtime.calls == 0
    assert store.load(sid)["pending"] is None


async def test_cancelled_conversation_keeps_pending_for_resume(engine, tmp_path):
    store = ConversationStore(tmp_path / "sessions")
    sid = store.create(engine.reader.snapshot.snapshot_id)["session_id"]
    token = CancellationToken()
    token.cancel()
    with pytest.raises(Exception, match="cancelled"):
        await store.turn(sid, engine, "helper", token=token)
    assert store.load(sid)["pending"] and engine.runtime.calls == 0
    row = await store.turn(sid, engine, resume=True)
    assert row["result"]["completion"]["status"] == "success"
    assert store.load(sid)["pending"] is None
