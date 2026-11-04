"""Comparison wiring and split isolation; inference stays offline on the actual SDK Runner."""

import json

import pytest
from agents.testing import ScriptedModel, assistant_message, function_call
from test_chat import settings

from repomentor.agent_tools import AgentLimits
from repomentor.benchmark import DEFAULT_CONDITIONS, case_plan, nonblank_reference_complete, policy_for
from repomentor.cli import parser
from repomentor.rag import RagEngine
from repomentor.retrieval_factory import build_index
from repomentor.retrieval_policy import RetrievalPolicy
from repomentor.runtimes.openai_sdk import OpenAISdkRuntime
from repomentor.sdk_agent import SdkAgentEngine


@pytest.mark.parametrize("sdk_requests", [3, 8])
def test_declared_followup_freezes_gold_separately_and_obeys_remaining_cap(reader, tmp_path, monkeypatch, sdk_requests):
    from types import SimpleNamespace

    from repomentor import benchmark
    from repomentor.question_bank import question_id

    profile = settings(context_window=16384)
    monkeypatch.setattr(benchmark, "python_home", lambda: tmp_path)
    monkeypatch.setattr(benchmark, "private_path", lambda _: tmp_path / "private")
    monkeypatch.setattr(benchmark, "load_chat_settings", lambda: profile)
    monkeypatch.setattr(benchmark, "reader_for", lambda *a, **kw: reader)
    monkeypatch.setattr(benchmark, "prior_ledgers", lambda _: {"old.sqlite": 1007})
    # Inspect the real orchestration without loading optional neural weights.
    built_policies = []
    def offline_index(reader, **kwargs):
        built_policies.append((kwargs["retriever"], kwargs["code_aware"]))
        return build_index(reader, **{**kwargs, "retriever": "bm25"})
    monkeypatch.setattr(benchmark, "build_index", offline_index)
    task = {"task_id": "old", "snapshot_id": reader.snapshot.snapshot_id, "commit_sha": "a"*40,
            "repository": "fixture/repo", "split": "validation", "cohort": "development", "answerable": True,
            "input": {"question": "入口？", "history": []}}
    source = {"tasks": [task], "evaluation_only": {"old": {"sources": [], "answerable": True}}, "excluded_paths": {}}
    (tmp_path / "source.json").write_text(json.dumps(source), encoding="utf-8")
    new = {**task, "input": {"question": "怎样阅读入口？", "history": [
        {"role": "user", "content": "HISTORY_MUST_BE_SENT"},
        {"role": "assistant", "content": "我会按当前问题核对源码。"}], "query": "entry helper"}, "cohort": "new-business"}
    new["task_id"] = question_id(new["repository"], new["commit_sha"], new["input"]["question"])
    plan = {"source": "source.json", "development_task_ids": [], "new_tasks": [new],
            "new_references": {new["task_id"]: {"sources": [], "required_points": ["GOLD_NEVER_SENT"], "answerable": True}},
            "retrieval_policy": {"top_k": 2, "source_bytes": 2000},
            "cases": [{"case_id": "new--agentic", "task_id": new["task_id"], "condition": "agentic", "kind": "main", "max_requests": sdk_requests},
                      {"case_id": "new--grep_read", "task_id": new["task_id"], "condition": "grep_read", "kind": "main", "max_requests": 1}],
            "combined_authorized_limit": 1186, "evaluation_date": "2026-10-22", "scope": "test"}
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    output = tmp_path / "suite"
    benchmark.prepare_declared(SimpleNamespace(output=output, plan=path))
    frozen = json.loads((output / "dataset.json").read_text(encoding="utf-8"))
    assert frozen["protocol"]["max_new_requests"] == sdk_requests + 1
    assert frozen["protocol"]["combined_limit"] == 1007 + sdk_requests + 1
    assert "GOLD_NEVER_SENT" not in json.dumps(frozen["prepared_inputs"])
    assert "HISTORY_MUST_BE_SENT" in json.dumps(frozen["prepared_inputs"])
    assert frozen["prepared_inputs"]["new--agentic"]["retrieval"]["query"] == "entry helper"
    grep = frozen["prepared_inputs"]["new--grep_read"]
    assert grep["initial_retrieval"]["retrieval"]["candidates"] == []
    assert len(grep["context"]) <= 2
    assert sum(len(s["content"].encode()) for s in grep["context"]) <= 2000
    assert frozen["declared_plan"]["cases"] == plan["cases"]
    assert frozen["policies"]["agentic"]["top_k"] == 2
    assert frozen["policies"]["agentic"]["source_bytes"] == 2000
    assert ("hybrid-rerank", True) in built_policies
    assert frozen["policies"]["agentic"]["retriever"] == "hybrid-rerank"
    plan["condition_options"] = {"agentic": {"sampling": {"temperature": 0.7}}}
    path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(ValueError, match="removed"):
        benchmark.prepare_declared(SimpleNamespace(output=tmp_path / "removed", plan=path))
    del plan["condition_options"]
    plan["cases"][0]["max_requests"] = 9
    path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(ValueError, match="per-case"):
        benchmark.prepare_declared(SimpleNamespace(output=tmp_path / "too-many", plan=path))
    plan["cases"][0]["max_requests"] = sdk_requests
    plan["combined_authorized_limit"] = 1007 + sdk_requests
    path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(ValueError, match="remaining"):
        benchmark.prepare_declared(SimpleNamespace(output=tmp_path / "over", plan=path))

    plan["cases"] = [{"case_id": "new--" + condition, "task_id": new["task_id"],
                      "condition": condition, "kind": "main", "max_requests": 1}
                     for condition in ("closed_book", "bm25", "bm25_code", "vector", "hybrid", "hybrid_rerank")]
    plan["combined_authorized_limit"] = 1013
    path.write_text(json.dumps(plan), encoding="utf-8")
    benchmark.prepare_declared(SimpleNamespace(output=tmp_path / "public", plan=path))
    public = json.loads((tmp_path / "public/dataset.json").read_text(encoding="utf-8"))
    assert not public["policies"]["bm25"]["code_aware"]
    assert public["policies"]["bm25_code"]["code_aware"]
    assert public["policies"]["vector"]["retriever"] == "vector"
    assert public["policies"]["hybrid"]["retriever"] == "hybrid"
    assert public["policies"]["hybrid_rerank"]["retriever"] == "hybrid-rerank"
    assert {("bm25", False), ("bm25", True), ("vector", False), ("hybrid", False),
            ("hybrid-rerank", True)} <= set(built_policies)
    closed = public["prepared_inputs"]["new--closed_book"]
    assert closed["context"] == [] and "允许依据已有知识" in closed["input_messages"][0]["content"]
    assert "GOLD_NEVER_SENT" not in json.dumps(public["prepared_inputs"])


async def test_prompt_only_schema_ablation_retains_invalid_raw_output(reader, tmp_path):
    import httpx

    from repomentor.retrieval import BM25Index
    from repomentor.runtimes.chat import ChatCompletionsRuntime

    bodies = []
    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "not valid JSON"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4}})
    runtime = ChatCompletionsRuntime(settings(stream=False), transport=httpx.MockTransport(handler))
    engine = RagEngine(reader, BM25Index.build(reader, tmp_path / "index"), runtime, tmp_path / "cache")
    row = await engine.answer("入口？", constrain_output=False)
    assert "response_format" not in bodies[0]
    assert row["answer"] is None and row["completion"]["content"] == "not valid JSON"
    assert row["completion"]["error_code"] == "invalid_answer_contract"


def test_closed_book_fact_score_cannot_be_published_as_grounded_resolution(tmp_path, monkeypatch):
    from repomentor import benchmark

    answer = {"answer": "来自模型已有知识。", "citations": [], "insufficient_evidence": False}
    data = {"tasks": [{"task_id": "q", "cohort": "new-business", "answerable": True}], "cases": ["c"],
            "evaluation_only": {"q": {"sources": []}}, "prepared_inputs": {"c": {"context": []}},
            "retrieval_variants": {}, "protocol": {"prior_requests": 1007, "scope": "test"}}
    row = {"case_id": "c", "task_id": "q", "condition": "closed_book", "result": {"answer": answer,
           "completion": {"status": "success", "usage": {}}, "new_model_requests": 1, "context": []}}
    judgment = {"candidate_id": "c", "correctness": "correct", "citation_support": "not_applicable", "completeness": "adequate",
                "abstention": "not_applicable", "entry_scope": "correct", "consistency": "consistent", "claims": [], "notes": "test"}
    monkeypatch.setattr(benchmark, "checked_data", lambda _: data)
    monkeypatch.setattr(benchmark, "records", lambda _: [row])
    monkeypatch.setattr(benchmark, "reader_for", lambda _: None)
    monkeypatch.setattr(benchmark, "validate_judgment", lambda value, *a: value)
    monkeypatch.setattr(benchmark, "count_requests", lambda _: 1)
    monkeypatch.setattr(benchmark, "private_path", lambda _: tmp_path)
    benchmark.summarize(tmp_path, {"c": judgment})
    group = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))["groups"]["all/closed_book"]
    assert group["factually_correct"] == 1
    assert "core_supported" not in group and "fully_resolved" not in group


def test_comparison_is_complete_and_oracle_stays_in_development():
    tasks = [{"task_id": "dev", "cohort": "retrieval-development", "answerable": True},
             {"task_id": "confirm", "cohort": "confirmation", "answerable": True},
             {"task_id": "private", "cohort": "retrieval-development", "answerable": False}]
    cases = case_plan(tasks, DEFAULT_CONDITIONS, 1, 6)
    for task in tasks:
        assert {c["condition"] for c in cases if c["task_id"] == task["task_id"] and c["kind"] == "main"} == set(DEFAULT_CONDITIONS)
    assert [c["task_id"] for c in cases if c["condition"] == "oracle"] == ["dev"]
    assert sum(c["max_requests"] for c in cases) == 31
    with pytest.raises(ValueError, match="development"):
        case_plan(tasks, DEFAULT_CONDITIONS, 2, 6)
    with pytest.raises(ValueError, match="distinct"):
        case_plan(tasks, ["none", "none"], 0, 6)


def test_parameters_validate_and_sdk_uses_matched_retrieval():
    fixed = policy_for("hybrid_rerank", "cuda", top_k=10, candidate_k=25, source_bytes=12000)
    assert fixed == policy_for("agentic", "cuda", top_k=10, candidate_k=25, source_bytes=12000)
    args = parser().parse_args(["ask", "--snapshot-id", "fixture", "--question", "entry?", "--no-code-aware",
                               "--retriever", "vector", "--top-k", "10", "--candidate-k", "25", "--chunk-size", "1024"])
    assert not args.code_aware and args.top_k == 10 and args.chunk_size == 1024
    with pytest.raises(ValueError, match="at least"):
        RetrievalPolicy(top_k=10, candidate_k=5)
    with pytest.raises(ValueError, match="overlap"):
        RetrievalPolicy(max_lines=4, overlap_lines=8)


def test_index_cache_preserves_exclusions_and_checks_each_commit(reader, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from repomentor import benchmark

    monkeypatch.setattr(benchmark, "SnapshotStore", lambda _: SimpleNamespace(open=lambda _: reader))
    monkeypatch.setattr(benchmark, "private_path", lambda _: tmp_path / "cache")
    task = {"snapshot_id": reader.snapshot.snapshot_id, "commit_sha": reader.snapshot.commit_sha}
    policy = RetrievalPolicy().model_dump()
    cache = {}
    full = benchmark.index_for(task, policy, tmp_path, [], cache)
    excluded = ["src/core.ts", "src/utils/helper.ts"]
    scoped = benchmark.index_for(task, policy, tmp_path, excluded, cache)
    assert not set(excluded) & set(scoped[0].paths)
    assert not set(excluded) & {c.path for c in scoped[1].chunks}
    assert scoped is benchmark.index_for(task, policy, tmp_path, list(reversed(excluded)), cache)
    assert full is benchmark.index_for(task, policy, tmp_path, [], cache)
    with pytest.raises(ValueError, match="commit differs"):
        benchmark.index_for({**task, "commit_sha": "f" * 40}, policy, tmp_path, [], cache)


def test_disjoint_function_sources_are_complete_even_when_blank_separator_is_omitted():
    references = [{"path": "keys.py", "start_line": 1, "end_line": 5,
                   "content": "def a():\n    return 1\n\ndef b():\n    return a()"}]
    spans = [{"path": "keys.py", "start_line": 1, "end_line": 2},
             {"path": "keys.py", "start_line": 4, "end_line": 5}]
    assert nonblank_reference_complete(references, spans)
    assert not nonblank_reference_complete(references, spans[:1])


def test_any_implementation_drift_rejects_frozen_generation(tmp_path, monkeypatch):
    import hashlib
    from types import SimpleNamespace

    from repomentor import benchmark

    original = "CAP = 492\nasync def run(): return 'fixed'\ndef summarize(): return 1\n"
    module = tmp_path / "benchmark.py"
    module.write_text(original.replace("return 1", "return 2"), encoding="utf-8")
    (tmp_path / "source.json").write_text("{}", encoding="utf-8")
    def identity():
        return {"rag": "fixed", "sdk": "fixed", "benchmark": hashlib.sha256(module.read_bytes().replace(b"\r\n", b"\n")).hexdigest()}
    monkeypatch.setattr(benchmark, "__file__", str(module))
    monkeypatch.setattr(benchmark, "implementation", identity)
    monkeypatch.setattr(benchmark, "python_home", lambda: tmp_path)
    monkeypatch.setattr(benchmark, "load_chat_settings", lambda: SimpleNamespace(public=lambda: {}))
    protocol = {"implementation": {**identity(), "benchmark": hashlib.sha256(original.encode()).hexdigest()},
                "settings": {}, "source": "source.json", "source_sha256": hashlib.sha256(b"{}").hexdigest(), "prior_ledgers": {}}
    (tmp_path / "dataset.json").write_text(json.dumps({"protocol": protocol}), encoding="utf-8")
    with pytest.raises(ValueError, match="implementation"):
        benchmark.checked_data(tmp_path)
    module.write_text(original, encoding="utf-8")
    assert benchmark.checked_data(tmp_path)["protocol"] == protocol


def test_timeout_usage_stays_missing_in_raw_record_but_summarizes_as_zero(tmp_path, monkeypatch):
    from repomentor import benchmark

    data = {"tasks": [{"task_id": "q", "cohort": "development", "answerable": False}], "cases": ["case"],
            "retrieval_variants": {}, "protocol": {"prior_requests": 694, "scope": "development"}}
    raw = {"case_id": "case", "task_id": "q", "condition": "bm25", "result": {"answer": None,
           "completion": {"status": "timeout", "usage": {"prompt_tokens": None, "completion_tokens": None}},
           "new_model_requests": 1, "context": []}}
    monkeypatch.setattr(benchmark, "checked_data", lambda _: data)
    monkeypatch.setattr(benchmark, "records", lambda _: [raw])
    monkeypatch.setattr(benchmark, "count_requests", lambda _: 1)
    monkeypatch.setattr(benchmark, "private_path", lambda _: tmp_path)
    benchmark.summarize(tmp_path)
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["groups"]["all/bm25"]["prompt_tokens"] == 0
    assert summary["groups"]["all/bm25"]["timeout"] == 1
    assert raw["result"]["completion"]["usage"]["prompt_tokens"] is None
    frozen_bytes = (tmp_path / "summary.json").read_bytes()
    (tmp_path / "completed-files.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(benchmark, "count_requests", lambda _: 2)
    with pytest.raises(ValueError, match="sealed"):
        benchmark.summarize(tmp_path)
    assert (tmp_path / "summary.json").read_bytes() == frozen_bytes


async def test_sdk_and_fixed_share_sources_and_sdk_can_read_missing_evidence(reader, tmp_path):
    index = build_index(reader, storage=tmp_path / "index", code_aware=True, top_k=6, candidate_k=25)
    model = ScriptedModel([
        [function_call("search_code", {"query": "helper"}, call_id="search")],
        [function_call("read_code", {"path": "src/index.ts", "start_line": 1, "end_line": 4}, call_id="read")],
        [assistant_message(json.dumps({"answer": "入口在源码中。", "insufficient_evidence": False,
            "citations": [{"path": "src/index.ts", "start_line": 1, "end_line": 2}]}))],
    ])
    runtime = OpenAISdkRuntime(settings(context_window=32768), scripted_model=model)
    fixed = RagEngine(reader, index, runtime, tmp_path / "fixed").prepare("helper 如何实现？")
    sdk = await SdkAgentEngine(reader, index, runtime, tmp_path / "sdk", AgentLimits()).answer("helper 如何实现？")
    assert sdk["shared_input_context"] == fixed["context"]
    assert sdk["new_model_requests"] == 3 and sdk["tool_calls"] == 2
    assert sdk["tool_trace"][0]["observation"]["candidate_limit"] == 25
    assert sdk["completion"]["status"] == "success" and sdk["citation_ranges_valid"]
    model.assert_complete()
