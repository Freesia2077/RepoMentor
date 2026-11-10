"""Exercise the real SDK Runner without inference, plus the actual HTTP adapter."""

import asyncio
import hashlib
import json

import httpx2
import pytest
from agents.testing import ModelStep, ScriptedModel, assistant_message, function_call
from test_chat import settings

from repomentor.agent_tools import AgentLimits
from repomentor.conversation import ConversationStore
from repomentor.harness import CancellationToken
from repomentor.interview_baselines import FrozenSourcesIndex
from repomentor.retrieval import BM25Index
from repomentor.runtimes.openai_sdk import OpenAISdkRuntime
from repomentor.sdk_agent import SdkAgentEngine

ANSWER = {
    "answer": "入口见已读取的源码。",
    "insufficient_evidence": False,
    "citations": [{"path": "src/index.ts", "start_line": 1, "end_line": 2}],
}


def read_step():
    return [
        function_call("read_code", {"path": "src/index.ts", "start_line": 1, "end_line": 4}, call_id="r1")
    ]


def final_step(answer=ANSWER):
    return [assistant_message(json.dumps(answer, ensure_ascii=False))]


def build(reader, tmp_path, steps, **limits):
    model = ScriptedModel(steps)
    runtime = OpenAISdkRuntime(settings(context_window=32768), scripted_model=model)
    return SdkAgentEngine(
        reader,
        BM25Index.build(reader, tmp_path / "index"),
        runtime,
        tmp_path / "cache",
        AgentLimits(**limits),
    ), model


async def test_sdk_real_tool_round_and_cache(reader, tmp_path):
    engine, model = build(reader, tmp_path, [read_step(), final_step()])
    row = await engine.answer("入口？")
    assert row["completion"]["status"] == "success", row["stop_reason"]
    assert row["new_model_requests"] == 2 and row["tool_calls"] == 1
    assert row["citation_ranges_valid"] and row["runtime"]["loop"] == "agents.Runner.run"
    assert any(v.get("type") == "function_call_output" for v in model.calls[1].input)
    model.assert_complete()
    cached = await engine.answer("入口？")
    assert cached["cache_hit"] and cached["new_model_requests"] == 0
    assert len(model.calls) == 2


async def test_sdk_resume_at_model_boundary_preserves_tools(reader, tmp_path):
    engine, model = build(reader, tmp_path, [read_step(), ConnectionError("offline"), final_step()])
    failed = await engine.answer("入口？")
    assert failed["completion"]["status"] == "failed" and failed["new_model_requests"] == 2
    assert failed["checkpoint"]["resumable"] and failed["tool_calls"] == 1
    replay = await engine.answer("入口？")
    assert replay["new_model_requests"] == 0
    recovered = await engine.answer("入口？", refresh=True)
    assert recovered["resumed_checkpoint"] and recovered["new_model_requests"] == 1
    assert recovered["total_model_requests"] == 3 and recovered["tool_calls"] == 1
    assert recovered["completion"]["status"] == "success"
    assert model.calls[1].input == model.calls[2].input
    old = engine.cache_dir / engine.runtime.settings.config_id / "attempts" / (failed["attempt_id"] + ".json")
    assert json.loads(old.read_text(encoding="utf-8"))["completion"]["status"] == "failed"


async def test_sdk_limits_and_invalid_citations(reader, tmp_path):
    engine, model = build(reader, tmp_path, [read_step(), final_step()], max_decisions=2, max_tool_calls=1)
    row = await engine.answer("入口？")
    assert row["stop_reason"] == "answered" and row["new_model_requests"] == 2
    assert row["tool_calls"] == 1 and model.calls[1].model_settings.tool_choice == "none"
    bad, _ = build(reader, tmp_path / "bad", [final_step()])
    result = await bad.answer("无需读取直接编造引用")
    assert result["stop_reason"] == "invalid_citations" and not result["citation_ranges_valid"]


async def test_sdk_tool_budget_stops_before_another_research_request(reader, tmp_path):
    # There is ample model budget; stopping must be the SDK tool_use_behavior callback.
    engine, model = build(reader, tmp_path, [read_step(), final_step()], max_decisions=8, max_tool_calls=1)
    row = await engine.answer("入口？")
    assert row["completion"]["status"] == "success" and row["research_stop"] == "tool_budget"
    assert [d["phase"] for d in row["decisions"]] == ["research", "finalize"]
    assert model.calls[1].tools == [] and model.calls[1].output_schema is not None
    model.assert_complete()


async def test_sdk_zero_tool_budget_uses_only_schema_agent(reader, tmp_path):
    value = {"answer": "需要源码依据。", "citations": [], "insufficient_evidence": True}
    engine, model = build(reader, tmp_path, [final_step(value)], max_tool_calls=0)
    row = await engine.answer("入口？")
    assert row["completion"]["status"] == "success" and row["new_model_requests"] == 1
    assert row["tool_calls"] == 0 and model.calls[0].tools == []


async def test_sdk_cancel_in_flight_and_timeout(reader, tmp_path):
    async def wait(call):
        await asyncio.sleep(5)
        return final_step()

    engine, _ = build(reader, tmp_path, [ModelStep.respond(wait)])
    token = CancellationToken()

    async def cancel():
        await asyncio.sleep(0.2)
        token.cancel()

    timer = asyncio.create_task(cancel())
    row = await engine.answer("入口？", token=token)
    await timer
    assert row["completion"]["status"] == "cancelled" and row["checkpoint"]["resumable"]
    engine2, _ = build(reader, tmp_path / "timeout", [ModelStep.respond(wait)], timeout_seconds=0.05)
    row2 = await engine2.answer("入口？")
    assert row2["completion"]["status"] == "timeout"


async def test_sdk_conversation_request_replay_and_history(reader, tmp_path):
    engine, model = build(reader, tmp_path, [read_step(), final_step(), read_step(), final_step()])
    store = ConversationStore(tmp_path / "conversations")
    state = store.create(reader.snapshot.snapshot_id, "学习")
    first = await store.turn(state["session_id"], engine, "入口？", request_id="first")
    replay = await store.turn(state["session_id"], engine, "入口？", request_id="first")
    assert first["result"]["completion"]["status"] == "success" and replay["new_model_requests"] == 0
    second = await store.turn(state["session_id"], engine, "再解释 src/index.ts", request_id="second")
    assert second["result"]["completion"]["status"] == "success"
    assert model.calls[2].input[0]["content"] == "入口？"


def response(message=None, finish="stop", status=200):
    return httpx2.Response(
        status,
        json={
            "id": "fixture",
            "object": "chat.completion",
            "created": 1,
            "model": "fixture",
            "choices": [
                {
                    "index": 0,
                    "message": message or {"role": "assistant", "content": json.dumps(ANSWER)},
                    "finish_reason": finish,
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
        },
    )


async def http_engine(reader, tmp_path, handler, **changes):
    async def verified():
        return {"verification": "mock-http"}

    runtime = OpenAISdkRuntime(
        settings(**{"context_window": 32768, **changes}),
        transport=httpx2.MockTransport(handler),
        verifier=verified,
    )
    return SdkAgentEngine(reader, BM25Index.build(reader, tmp_path / "idx"), runtime, tmp_path / "cache")


@pytest.mark.parametrize("backend", ["generic", "ollama"])
async def test_research_tools_then_typed_finalizer_protocol(reader, tmp_path, backend):
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        assert body["presence_penalty"] == 1.5
        assert ("response_format" in body) == (len(calls) == 3)
        assert bool(body.get("tools")) == (len(calls) < 3)
        if len(calls) == 1:
            return response(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "read-native",
                            "type": "function",
                            "function": {
                                "name": "read_code",
                                "arguments": json.dumps(
                                    {"path": "src/index.ts", "start_line": 1, "end_line": 4}
                                ),
                            },
                        }
                    ],
                },
                finish="tool_calls",
            )
        if len(calls) == 2:
            assert any(m.get("role") == "tool" for m in body["messages"])
            return response({"role": "assistant", "content": "入口已核验，整理时仍需对照源码。"})
        return response()

    engine = await http_engine(reader, tmp_path, handler, backend=backend, presence_penalty=1.5)
    row = await engine.answer("入口在哪里？解释其行为。")
    assert row["completion"]["status"] == "success", row["stop_reason"]
    assert row["new_model_requests"] == 3 and row["tool_calls"] == 1
    assert [d["phase"] for d in row["decisions"]] == ["research", "research", "finalize"]
    payload = json.loads(calls[-1]["messages"][-1]["content"])
    assert payload["research_notes"]["is_evidence"] is False
    assert row["answer"] == ANSWER


async def test_sdk_prefetch_uses_delivered_fixed_context_after_overlap_dedup(reader, tmp_path):
    import hashlib
    from types import SimpleNamespace

    from repomentor.retrieval import RetrievalSettings

    text, _ = reader.read("src/index.ts", 500000)

    def span(a, b):
        content = "\n".join(text.splitlines()[a - 1 : b])
        return dict(
            path="src/index.ts",
            start_line=a,
            end_line=b,
            content=content,
            content_sha256=hashlib.sha256(content.encode()).hexdigest(),
        )

    sources = [span(1, 3), span(2, 3)]
    index = SimpleNamespace(
        index_id="overlap",
        settings=RetrievalSettings(),
        initial_sources=lambda *a, **kw: {"sources": sources, "retrieval": {"candidates": []}, "hits": []},
    )
    model = ScriptedModel([final_step()])
    runtime = OpenAISdkRuntime(settings(context_window=32768), scripted_model=model)
    row = await SdkAgentEngine(reader, index, runtime, tmp_path / "sdk").answer("入口？")
    assert row["completion"]["status"] == "success"
    assert row["shared_input_context"] == sources[:1]
    assert row["initial_retrieval"]["source_omissions"][0]["reason"] == "overlap"
    model.assert_complete()


@pytest.mark.parametrize("backend", ["generic", "ollama"])
async def test_sdk_wire_protocol_usage_and_no_remote_traces(reader, tmp_path, backend):
    calls = []

    def handler(request):
        assert request.url.host == "127.0.0.1"
        body = json.loads(request.content)
        calls.append(body)
        assert body.get("stream", False) is False and body["parallel_tool_calls"] is False
        assert body["seed"] == 42 and body["reasoning_effort"] == "none"
        assert "response_format" not in body
        if len(calls) == 1:
            return response(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "real-call",
                            "type": "function",
                            "function": {
                                "name": "read_code",
                                "arguments": json.dumps(
                                    {"path": "src/index.ts", "start_line": 1, "end_line": 4}
                                ),
                            },
                        }
                    ],
                },
                "tool_calls",
            )
        assert calls[1]["messages"][-1]["role"] == "tool"
        assert calls[1]["messages"][-1]["tool_call_id"] == "real-call"
        return response()

    engine = await http_engine(reader, tmp_path, handler, backend=backend)
    row = await engine.answer("入口？")
    assert row["completion"]["status"] == "success", row["decisions"]
    assert row["new_model_requests"] == 2 and row["decisions"][1]["usage"]["total_tokens"] == 110


@pytest.mark.parametrize("failure", ["oversize", "http500", "truncated", "bad_json"])
async def test_sdk_wire_failures_do_not_retry(reader, tmp_path, failure):
    calls = []

    def handler(request):
        calls.append(request)
        if failure == "http500":
            return response(status=500)
        if failure == "truncated":
            return response(finish="length")
        return response({"role": "assistant", "content": "not JSON"})

    engine = await http_engine(reader, tmp_path, handler)
    row = await engine.answer("入口？" + ("x" * 40000 if failure == "oversize" else ""))
    assert row["completion"]["status"] != "success"
    # Natural-language research output goes to the one reserved formatting stage.
    assert len(calls) == (0 if failure == "oversize" else 2 if failure == "bad_json" else 1)
    if failure == "bad_json":
        assert "response_format" not in json.loads(calls[0].content)
        assert "response_format" in json.loads(calls[1].content)
    if failure == "oversize":
        assert row["stop_reason"] == "context_limit" and row["new_model_requests"] == 0
    else:
        assert row["decisions"][0]["raw_response"]


async def test_sdk_disabled_tools_keep_observations_for_final_answer(reader, tmp_path):
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if len(seen) == 1:
            return response(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "last-tool",
                            "type": "function",
                            "function": {
                                "name": "read_code",
                                "arguments": json.dumps(
                                    {"path": "src/index.ts", "start_line": 1, "end_line": 3}
                                ),
                            },
                        }
                    ],
                },
                "tool_calls",
            )
        assert body.get("tool_choice") == "none"
        assert body["messages"][-1]["role"] == "user" and "response_format" in body
        return response()

    engine = await http_engine(reader, tmp_path, handler, backend="ollama")
    engine.limits = AgentLimits(max_decisions=2)
    row = await engine.answer("入口？")
    assert row["completion"]["status"] == "success" and row["new_model_requests"] == 2
    assert row["completion"]["usage"]["total_tokens"] == 220


async def test_sdk_truncated_research_uses_reserved_finalizer_without_partial_draft(reader, tmp_path):
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if len(seen) == 1:
            return response(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "source",
                            "type": "function",
                            "function": {
                                "name": "read_code",
                                "arguments": json.dumps(
                                    {"path": "src/index.ts", "start_line": 1, "end_line": 4}
                                ),
                            },
                        }
                    ],
                },
                "tool_calls",
            )
        if len(seen) == 2:
            return response({"role": "assistant", "content": "UNTRUSTED_PARTIAL_DRAFT"}, "length")
        assert "UNTRUSTED_PARTIAL_DRAFT" not in json.dumps(body)
        assert "response_format" in body and body.get("tool_choice") == "none"
        assert body["max_tokens"] == 1536
        return response()

    engine = await http_engine(reader, tmp_path, handler, max_tokens=1536)
    row = await engine.answer("入口？")
    assert row["completion"]["status"] == "success"
    assert row["research_stop"] == "research_truncated" and row["new_model_requests"] == 3
    assert [d["phase"] for d in row["decisions"]] == ["research", "research", "finalize"]
    assert row["decisions"][1]["finish_reason"] == "length"
    assert "UNTRUSTED_PARTIAL_DRAFT" in row["decisions"][1]["raw_response"]
    replay = await engine.answer("入口？")
    assert replay["new_model_requests"] == 0 and len(seen) == 3


async def test_sdk_report_roles_share_tools_pass_summaries_and_replay(reader, tmp_path):
    from repomentor.report import run_report
    from repomentor.runtimes.stub import StubRuntime
    from repomentor.sdk_report import SdkReport

    stub = await run_report(reader, StubRuntime())
    steps = []
    for stage in ("explorer", "mentor", "contributor"):
        value = stub.outputs[stage]
        value["evidenceClaims"] = [
            {
                "claim": "fixture",
                "confidence": "low",
                "evidence": [{"path": "src/index.ts", "supports": "fixture source"}],
            }
        ]
        # This SDK fixture reads one file per role; the separate stub report reads more.
        value["evidenceCoverage"]["examinedFiles"] = ["src/index.ts"]
        for field in ("entryPoints", "moduleMap", "readingPath", "entryFiles"):
            if field in value:
                value[field] = []
        if "dependencyGraph" in value:
            value["dependencyGraph"] = {}
        steps.extend([read_step(), final_step(value)])
    engine, model = build(reader, tmp_path, steps)
    report = SdkReport(reader, engine.index, engine.runtime, tmp_path / "report-cache")
    result = await report.run()
    assert result.status == "success", result.stop_reason
    assert len(result.outputs) == 3 and sum(r["new_model_requests"] for r in report.records) == 6
    payload = json.loads(model.calls[2].input[-1]["content"])
    assert payload["task_context"]["prior_roles"]["explorer"]["claims"]
    assert all(r["tool_calls"] == 1 for r in report.records)
    cached = await report.run()
    assert cached.status == "success" and all(r["new_model_requests"] == 0 for r in report.records)
    model.assert_complete()


async def test_sdk_report_blocks_explicit_out_of_bounds_evidence_and_keeps_raw_answer(reader, tmp_path):
    from repomentor.contracts import ExplorerOutput
    from repomentor.report import run_report
    from repomentor.runtimes.stub import StubRuntime

    value = (await run_report(reader, StubRuntime())).outputs["explorer"]
    value["entryPoints"], value["moduleMap"] = [], []
    value["evidenceCoverage"] = {"examinedFiles": ["src/index.ts"], "gaps": []}
    value["evidenceClaims"] = [
        {
            "claim": "bad range",
            "confidence": "low",
            "evidence": [{"path": "src/index.ts", "supports": "L999-L1000"}],
        }
    ]
    engine, model = build(reader, tmp_path, [read_step(), final_step(value)])
    row = await engine.answer("介绍仓库", output_type=ExplorerOutput)
    assert row["completion"]["status"] == "failed" and row["stop_reason"] == "report_grounding"
    assert row["report_validation"]["locations"]["issues"][0]["reason"] == "out_of_bounds"
    assert row["answer"]["evidenceClaims"][0]["evidence"][0]["supports"] == "L999-L1000"
    cached = await engine.answer("介绍仓库", output_type=ExplorerOutput)
    assert cached["new_model_requests"] == 0 and cached["stop_reason"] == "report_grounding"
    model.assert_complete()


async def test_sdk_draft_and_finalizer_resume_without_repeating_research(reader, tmp_path):
    engine, model = build(
        reader,
        tmp_path,
        [
            read_step(),
            [assistant_message("查到入口，待整理。")],
            ConnectionError("format transport"),
            final_step(),
        ],
    )
    failed = await engine.answer("入口？")
    assert failed["checkpoint"]["phase"] == "finalize" and failed["new_model_requests"] == 3
    recovered = await engine.answer("入口？", refresh=True)
    assert recovered["completion"]["status"] == "success" and recovered["new_model_requests"] == 1
    assert recovered["tool_calls"] == 1 and model.calls[2].input == model.calls[3].input


def test_sdk_cli_defaults_and_removed_engine():
    from repomentor.cli import parser

    assert parser().parse_args(["report", "--repository", "developit/mitt"]).runtime == "sdk"
    with pytest.raises(SystemExit):
        parser().parse_args(["ask", "--repository", "developit/mitt", "--question", "q", "--engine", "agent"])


async def test_sdk_normalizes_default_limits_for_cache(reader, tmp_path):
    value = {"answer": "请提供函数名称。", "citations": [], "insufficient_evidence": True}
    engine, model = build(reader, tmp_path, [final_step(value)])
    assert type(engine.limits.timeout_seconds) is float
    row = await engine.answer("请解释")
    engine.limits = AgentLimits(**AgentLimits().model_dump())
    replay = await engine.answer("请解释")
    assert replay["attempt_id"] == row["attempt_id"] and replay["new_model_requests"] == 0
    model.assert_complete()


async def test_sdk_finalizer_keeps_followup_referent(reader, tmp_path):
    engine, model = build(reader, tmp_path, [read_step(), final_step()], max_decisions=2)
    history = [
        {"role": "user", "content": "src/index.ts 是入口吗？"},
        {"role": "assistant", "content": "需要核对源码。"},
    ]
    row = await engine.answer("那么它呢？", history=history, query="那么它呢？\n追问对象：src/index.ts")
    assert row["completion"]["status"] == "success"
    assert model.calls[-1].input[:2] == history
    payload = json.loads(model.calls[-1].input[-1]["content"])
    assert payload["question"] == "那么它呢？"
    assert "src/index.ts" in payload["navigation_memory"]["resolved_query"]


async def test_sdk_old_session_rejected_before_loading_models(tmp_path, monkeypatch):
    from repomentor import cli
    from repomentor.config import ConfigurationError

    store = ConversationStore(tmp_path / "conversations")
    state = store.create("archived", "keep history")
    state["engine"] = "agent"
    store.save(state)
    before = store.path(state["session_id"]).read_bytes()
    monkeypatch.setattr(cli, "open_reader", lambda *a: pytest.fail("must reject before loading repository"))
    args = cli.parser().parse_args(
        ["--data-dir", str(tmp_path), "chat", "--session", state["session_id"], "--message", "next"]
    )
    with pytest.raises(ConfigurationError, match="removed engine"):
        await cli.rag_command(args)
    assert store.path(state["session_id"]).read_bytes() == before


@pytest.mark.parametrize("engine_args", [[], ["--engine", "sdk"]])
async def test_sdk_cli_pins_model_limits_and_reuses_batch_cache(reader, tmp_path, monkeypatch, engine_args):
    from repomentor import cli, retrieval_factory
    from repomentor.runtimes import openai_sdk

    value = {"answer": "请提供函数名称。", "citations": [], "insufficient_evidence": True}
    engine, model = build(reader, tmp_path, [final_step(value)])
    monkeypatch.setattr(cli, "open_reader", lambda *a: reader)
    monkeypatch.setattr(cli, "load_chat_settings", lambda *a: engine.runtime.settings)
    monkeypatch.setattr(retrieval_factory, "build_index", lambda *a, **kw: engine.index)
    monkeypatch.setattr(openai_sdk, "OpenAISdkRuntime", lambda *a: engine.runtime)
    args = cli.parser().parse_args(
        [
            "--data-dir",
            str(tmp_path),
            "chat",
            "--snapshot-id",
            reader.snapshot.snapshot_id,
            "--message",
            "请解释",
            "--request-id",
            "first",
            *engine_args,
        ]
    )
    assert await cli.rag_command(args) == 0
    state = json.loads(next((tmp_path / "conversations").glob("*.json")).read_text(encoding="utf-8"))
    assert state["engine"] == "sdk" and type(state["agent_limits"]["timeout_seconds"]) is float
    assert state["agent_limits"] == AgentLimits().model_dump()
    assert state["retrieval_config"]["retriever"] == "bm25"
    assert state["retrieval_config"]["device"] == "cpu"
    batch = SdkAgentEngine(reader, engine.index, engine.runtime, tmp_path / "sdk/cache")
    initial_task_context = json.loads(state["turns"][0]["result"]["input"][-1]["content"])["task_context"]
    replay = await batch.answer("请解释", task_context=initial_task_context)
    assert replay["new_model_requests"] == 0 and replay["cache_hit"]
    for extra, match in (
        (["--engine", "rag"], "engine"),
        (["--agent-max-tools", "3"], "limits"),
        (["--retriever", "vector"], "retrieval"),
    ):
        parsed = cli.parser().parse_args(
            [
                "--data-dir",
                str(tmp_path),
                "chat",
                "--session",
                state["session_id"],
                "--message",
                "next",
                *extra,
            ]
        )
        with pytest.raises(ValueError, match=match):
            await cli.rag_command(parsed)
    model.assert_complete()


async def test_cli_explicit_fixed_rag_and_legacy_session_keep_shared_lexical_policy(
    reader, tmp_path, monkeypatch
):
    from test_rag import FakeChat

    from repomentor import cli, retrieval_factory
    from repomentor.runtimes import chat, openai_sdk

    runtime, selected = FakeChat(), []
    index = BM25Index.build(reader, tmp_path / "idx")

    def build_index(*a, **kw):
        selected.append(kw)
        return index

    monkeypatch.setattr(cli, "open_reader", lambda *a: reader)
    monkeypatch.setattr(cli, "load_chat_settings", lambda *a: runtime.settings)
    monkeypatch.setattr(retrieval_factory, "build_index", build_index)
    monkeypatch.setattr(chat, "ChatCompletionsRuntime", lambda *a: runtime)
    monkeypatch.setattr(
        openai_sdk, "OpenAISdkRuntime", lambda *a: pytest.fail("fixed session must not start SDK")
    )
    args = cli.parser().parse_args(
        [
            "--data-dir",
            str(tmp_path),
            "chat",
            "--snapshot-id",
            reader.snapshot.snapshot_id,
            "--message",
            "helper如何实现？",
            "--engine",
            "rag",
        ]
    )
    assert await cli.rag_command(args) == 0
    state = json.loads(next((tmp_path / "conversations").glob("*.json")).read_text(encoding="utf-8"))
    assert state.get("engine", "rag") == "rag" and runtime.calls == 1
    assert state["turns"][0]["result"]["mode"] == "bm25"
    assert selected[0]["retriever"] == state["retrieval_config"]["retriever"] == "bm25"
    assert selected[0]["device"] == "cpu"
    resumed = cli.parser().parse_args(
        ["--data-dir", str(tmp_path), "chat", "--session", state["session_id"], "--message", "再解释helper"]
    )
    assert await cli.rag_command(resumed) == 0
    assert runtime.calls == 2


async def test_sdk_backend_failure_and_precancel_send_nothing(reader, tmp_path):
    engine, model = build(reader, tmp_path, [])

    async def fail():
        raise ValueError("revision drift")

    engine.runtime.verify_backend = fail
    row = await engine.answer("version")
    assert row["completion"]["status"] == "failed" and row["new_model_requests"] == 0
    token = CancellationToken()
    token.cancel()
    cancelled = await engine.answer("cancel", token=token)
    assert cancelled["completion"]["status"] == "cancelled" and cancelled["new_model_requests"] == 0
    model.assert_complete()


def test_sdk_merge_does_not_fill_unread_gaps():
    from repomentor.sdk_agent import merged_context

    spans = [
        {"path": "x", "start_line": 1, "content": "a\nb"},
        {"path": "x", "start_line": 2, "content": "b\nc"},
        {"path": "x", "start_line": 5, "content": "e"},
    ]
    result = merged_context(spans)
    assert [(r["start_line"], r["end_line"]) for r in result] == [(1, 3), (5, 5)]


def seeded(reader):
    content = "\n".join(reader.read("src/index.ts", 500000)[0].splitlines()[:2])
    span = {
        "path": "src/index.ts",
        "start_line": 1,
        "end_line": 2,
        "content": content,
        "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
    }
    return FrozenSourcesIndex("frozen", {"sources": [span], "retrieval": {"candidates": []}, "hits": []})


async def test_permission_control_preserves_research_and_finalization(reader, tmp_path):
    answer = {
        "answer": "入口由源码支持。",
        "citations": [{"path": "src/index.ts", "start_line": 1, "end_line": 2}],
        "insufficient_evidence": False,
    }
    rows, models = [], []
    for permission in (True, False):
        model = ScriptedModel(
            [
                [assistant_message("已读取入口声明；整理时仍需核对源码。")],
                [assistant_message(json.dumps(answer))],
            ]
        )
        runtime = OpenAISdkRuntime(settings(context_window=32768), scripted_model=model)
        row = await SdkAgentEngine(
            reader,
            seeded(reader),
            runtime,
            tmp_path / "same-cache",
            AgentLimits(max_decisions=3),
            tools_enabled=permission,
        ).answer("入口在哪里？")
        rows.append(row)
        models.append(model)
        assert row["completion"]["status"] == "success", row["stop_reason"]
        assert [d["phase"] for d in row["decisions"]] == ["research", "finalize"]
        assert row["new_model_requests"] == 2 and not row["cache_hit"]
        assert row["tool_calls"] == 0
        model.assert_complete()
    assert rows[0]["input"] == rows[1]["input"]
    assert rows[0]["cache_key"] != rows[1]["cache_key"]
    assert {t.name for t in models[1].calls[0].tools} == {"submit_answer"}
    assert "read_code" in {t.name for t in models[0].calls[0].tools}
    assert models[1].calls[0].output_schema is None
    assert models[1].calls[0].model_settings.tool_choice != "none"
    assert models[0].calls[1].input == models[1].calls[1].input


@pytest.mark.parametrize("permission", [True, False])
async def test_sdk_native_answer_submission_finishes_without_format_call(reader, tmp_path, permission):
    model = ScriptedModel([[function_call("submit_answer", ANSWER, call_id="submit")]])
    runtime = OpenAISdkRuntime(settings(context_window=32768), scripted_model=model)
    engine = SdkAgentEngine(reader, seeded(reader), runtime, tmp_path / "cache", tools_enabled=permission)
    row = await engine.answer("入口？")
    assert row["completion"]["status"] == "success", row["stop_reason"]
    assert row["answer"] == ANSWER and row["new_model_requests"] == 1 and row["tool_calls"] == 0
    assert row["citation_ranges_valid"]
    assert any(e["event"] == "answer_submission_accepted" for e in row["local_trace"])
    model.assert_complete()


async def test_submission_recovery_requires_model_to_see_read_result(reader, tmp_path):
    value = {**ANSWER, "citations": [{"path": "src/index.ts", "start_line": 1, "end_line": 3}]}
    model = ScriptedModel(
        [
            [function_call("submit_answer", value, call_id="unseen")],
            [
                function_call(
                    "read_code", {"path": "src/index.ts", "start_line": 3, "end_line": 3}, call_id="read"
                ),
                function_call("submit_answer", value, call_id="same-batch"),
            ],
            [function_call("submit_answer", value, call_id="seen")],
        ]
    )
    runtime = OpenAISdkRuntime(settings(context_window=32768), scripted_model=model)
    row = await SdkAgentEngine(reader, seeded(reader), runtime, tmp_path / "cache").answer("入口？")
    assert row["completion"]["status"] == "success", row["stop_reason"]
    assert row["new_model_requests"] == 3 and row["tool_calls"] == 1 and row["answer"] == value
    rejected = [e for e in row["local_trace"] if e["event"] == "answer_submission_rejected"]
    assert len(rejected) == 2
    assert all(
        e["missing_ranges"] == [{"path": "src/index.ts", "start_line": 3, "end_line": 3}] for e in rejected
    )
    feedback = [
        json.loads(item["output"])
        for item in model.calls[1].input
        if item.get("type") == "function_call_output"
    ]
    assert feedback[0]["accepted"] is False and feedback[0]["error"] == "invalid_citations"
    model.assert_complete()


async def test_invalid_submissions_cannot_exceed_global_model_budget(reader, tmp_path):
    fallback = {"answer": "缺少实现，无法判断。", "citations": [], "insufficient_evidence": True}
    engine, model = build(
        reader,
        tmp_path,
        [
            [function_call("submit_answer", ANSWER, call_id="bad1")],
            [function_call("submit_answer", ANSWER, call_id="bad2")],
            final_step(fallback),
        ],
        max_decisions=3,
    )
    row = await engine.answer("入口？")
    assert row["completion"]["status"] == "success" and row["answer"] == fallback
    assert row["new_model_requests"] == 3 and row["tool_calls"] == 0
    assert [d["phase"] for d in row["decisions"]] == ["research", "research", "finalize"]
    assert model.calls[-1].tools == []
    model.assert_complete()
