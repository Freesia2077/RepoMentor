"""Regressions for lost research explanations and incomplete source reads; no inference."""

import copy
import json
from types import SimpleNamespace

from agents.testing import assistant_message
from test_sdk_agent import ANSWER, build, final_step, read_step

from repomentor.agent_tools import AgentLimits, RepositoryEnvironment
from repomentor.contracts import ToolCall
from repomentor.sdk_agent import research_notes_payload


def test_stop_response_with_dangling_value_is_reviewed_without_rewriting():
    from repomentor.answer_quality import inspect_answer

    for text in ("name() 返回 `", "不相同。结果为字符串 "):
        answer = {"answer": text, "citations": [], "insufficient_evidence": True}
        original = copy.deepcopy(answer)
        quality = inspect_answer(answer, [], "success")
        assert {i["code"] for i in quality["issues"]} == {"possibly_incomplete_text"}
        assert answer == original and quality["semantic_correctness"] == "not_scored"
    for text in ("返回类型是字符串。", "`0` 是结果。", "```python\nx = '`'\n```\n以上是示例。"):
        quality = inspect_answer({"answer": text, "citations": [], "insufficient_evidence": True}, [], "success")
        assert quality["issues"] == []


def environment(lines=220, **limits):
    source = "\n".join(f"value_{i} = 'source padding for an exact bounded read'" for i in range(1, lines + 1))
    reader = SimpleNamespace(snapshot=SimpleNamespace(snapshot_id="fixture"), paths=["main.py"],
                             read=lambda path, maximum: (source, False))
    return RepositoryEnvironment(reader, None, AgentLimits(**limits))


def read(env, start, end, call_id):
    return env.step(ToolCall(call_id=call_id, tool="read_code", arguments={
        "path": "main.py", "start_line": start, "end_line": end}))


def test_larger_read_budget_reaches_line_172_and_does_not_overread():
    env = environment()
    row = read(env, 80, 200, "target")
    assert row["source_spans"][0]["end_line"] >= 172
    assert len(row["visible_json"].encode()) <= env.limits.max_observation_bytes
    assert not row["observation"]["truncated"] and not env.pending_reads()


def test_continuation_fills_unread_gaps_without_repeating_visible_islands():
    env = environment(max_read_lines=10)
    read(env, 21, 30, "island")
    first = read(env, 1, 40, "prefix")
    cursor = first["observation"]["continuation"]["arguments"]
    assert (cursor["start_line"], cursor["end_line"]) == (11, 40)
    second = env.step(ToolCall(call_id="gap", tool="read_code", arguments=cursor))
    assert [(s["start_line"], s["end_line"]) for s in second["source_spans"]] == [(11, 20)]
    assert second["observation"]["continuation"]["arguments"]["start_line"] == 31
    restored = environment(max_read_lines=10)
    restored.restore(copy.deepcopy(env.state))
    assert restored.pending_reads() == env.pending_reads()
    final = env.step(ToolCall(call_id="last", tool="read_code",
        arguments=second["observation"]["continuation"]["arguments"]))
    assert final["observation"]["continuation"] is None and not env.pending_reads()


def test_wire_trim_updates_exact_continuation_instead_of_skipping_omitted_lines():
    env = environment(max_observation_bytes=1400)
    row = read(env, 1, 40, "bounded")
    assert row["observation"]["status"] == "ok", row["observation"]
    last = row["source_spans"][0]["end_line"]
    assert row["observation"]["continuation"]["arguments"] == {
        "path": "main.py", "start_line": last + 1, "end_line": 40, "expand": "none"}
    assert len(row["visible_json"].encode()) <= 1400


async def test_finalizer_receives_completed_notes_and_raw_source_as_separate_inputs(reader, tmp_path):
    note = "测试的输入是 FOO/foo；应分别解释两个断言。src/unread.ts 不是已读来源。"
    engine, model = build(reader, tmp_path, [read_step(), [assistant_message(note)], final_step()])
    row = await engine.answer("入口和测试如何验证？")
    payload = json.loads(model.calls[-1].input[-1]["content"])
    assert payload["research_notes"]["text"] == note
    assert payload["research_notes"]["is_evidence"] is False
    assert payload["context"][0]["path"] == "src/index.ts"
    assert all(s["path"] != "src/unread.ts" for s in row["final_context"])
    assert row["completion"]["status"] == "success"
    assert row["research_notes"] == row["checkpoint"]["research_notes"]
    replay = await engine.answer("入口和测试如何验证？")
    assert replay["new_model_requests"] == 0 and replay["attempt_id"] == row["attempt_id"]
    model.assert_complete()


async def test_research_notes_cannot_authorize_citing_an_unread_file(reader, tmp_path):
    bad = {**ANSWER, "citations": [{"path": "unread.ts", "start_line": 1, "end_line": 2}]}
    engine, model = build(reader, tmp_path, [read_step(), [assistant_message("unread.ts:1-2 提供支持")], final_step(bad)])
    row = await engine.answer("入口？")
    assert row["completion"]["status"] != "success" and row["stop_reason"] == "invalid_citations"
    model.assert_complete()


def test_notes_are_bounded_utf8_and_provider_reasoning_is_not_carried_forward():
    note = research_notes_payload("源码要点。" * 100, maximum=70)
    assert note["truncated"] and note["kept_bytes"] <= 70
    assert research_notes_payload("<think>provider reasoning</think>") is None


def test_sdk_filter_exposes_read_progress_without_authorizing_unread_lines(reader):
    from agents import Agent
    from agents.run_config import ModelInputData
    from test_chat import settings

    from repomentor.runtimes.openai_sdk import OpenAISdkRuntime
    from repomentor.sdk_context import ContextFilter, source_items

    env = RepositoryEnvironment(reader, None, AgentLimits(max_read_lines=1))
    row = env.step(ToolCall(call_id="read", tool="read_code", arguments={
        "path": "src/index.ts", "start_line": 1, "end_line": 4}))
    runtime = OpenAISdkRuntime(settings(context_window=32768))
    items = [{"role": "user", "content": "入口？"},
             {"type": "function_call", "name": "read_code", "call_id": "read", "arguments": "{}"},
             {"type": "function_call_output", "call_id": "read", "output": row["visible_json"]}]
    agent = Agent(name="progress", model_settings=runtime.model_settings)
    data = SimpleNamespace(agent=agent, model_data=ModelInputData(input=items, instructions="read source"))
    result = ContextFilter(env, runtime, "入口？", [])(data)
    payload = json.loads(result.input[-1]["content"])
    assert payload["reading_progress"][0]["start_line"] == 2
    assert all(s["end_line"] == 1 for s in source_items(result.input))


def test_explicit_new_call_does_not_inherit_the_old_question():
    from repomentor.conversation import followup_query

    question = "那么 off('FOO') 不传 handler，是删除键还是清空数组？"
    assert followup_query(question, "on 会转小写吗？") == question
    assert "on" in followup_query("那么它的参数呢？", "previous", "on")


async def test_finalizer_current_question_stays_separate_from_retrieval_navigation(reader, tmp_path):
    engine, model = build(reader, tmp_path, [read_step(), [assistant_message("当前应解释 off。")], final_step()])
    question = "那么 off('FOO') 呢？"
    query = question + "\n追问对象：on 是否转换大小写？"
    result = await engine.answer(question, query=query)
    assert result["completion"]["status"] == "success"
    payload = json.loads(model.calls[-1].input[-1]["content"])
    assert payload["question"] == question
    assert payload["navigation_memory"]["resolved_query"] == query


def test_blank_last_source_line_survives_render_merge_share_and_wire_recovery():
    from repomentor.rag import render_sources
    from repomentor.sdk_agent import merged_context
    from repomentor.sdk_context import source_items
    from repomentor.shared_sources import select_sources
    from repomentor.source_windows import source_span

    text = "def f():\n    return 1\n\n"
    reader = SimpleNamespace(snapshot=SimpleNamespace(snapshot_id="fixture"), paths=["main.py"],
                             read=lambda *args: (text, False))
    span = source_span("main.py", text.splitlines(), 1, 3)
    assert render_sources([span])[0]["content"].endswith("L3: ")
    assert source_items(render_sources([span])) == [span]
    assert merged_context([span]) == [span]
    selected = select_sources(reader, {"snapshot_id": "fixture", "spans": [span],
        "preferred_ranges": [{"path": "main.py", "start_line": 3, "end_line": 3}]}, "f")
    assert selected == [span]
    env = RepositoryEnvironment(reader, None)
    row = read(env, 1, 3, "blank")
    assert row["observation"]["spans"][0]["lines"][-1] == "L3: "
    assert source_items(json.loads(row["visible_json"])) == row["source_spans"]
