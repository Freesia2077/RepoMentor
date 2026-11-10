import hashlib
import json
from types import SimpleNamespace

import pytest
from agents.testing import assistant_message, function_call
from test_sdk_agent import ANSWER, build, final_step, read_step

from repomentor.agent_tools import RepositoryEnvironment
from repomentor.conversation import ConversationStore
from repomentor.entry_scope import resolve_entry
from repomentor.retrieval import BM25Index
from repomentor.sdk_collaboration import CollaborationSettings
from repomentor.shared_sources import select_sources


def source(reader, path="src/index.ts", start=1, end=4):
    text = "\n".join(reader.read(path, 500_000)[0].splitlines()[start - 1 : end])
    return {
        "path": path,
        "start_line": start,
        "end_line": end,
        "content": text,
        "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
    }


def delegation():
    return [function_call("inspect_tests", {"task": "核对 helper 的测试断言"}, call_id="delegate")]


def expert_read():
    return [
        function_call(
            "read_code", {"path": "tests/helper.test.ts", "start_line": 1, "end_line": 2}, call_id="test-read"
        )
    ]


TEST_ANSWER = {
    "answer": "测试调用了 helper，但这里没有断言，不代表覆盖或执行通过。",
    "insufficient_evidence": False,
    "citations": [{"path": "tests/helper.test.ts", "start_line": 1, "end_line": 2}],
}


async def test_actual_as_tool_shares_source_tracks_actors_and_replays(reader, tmp_path):
    engine, model = build(
        reader,
        tmp_path,
        [
            read_step(),
            delegation(),
            expert_read(),
            [assistant_message("测试仅调用 helper，未见断言。")],
            final_step(TEST_ANSWER),
        ],
    )
    engine.collaboration = CollaborationSettings(mode="auto")
    row = await engine.answer("对照入口实现与 helper 测试")
    assert row["completion"]["status"] == "success", row["stop_reason"]
    assert [r["actor"] for r in row["decisions"]] == [
        "main",
        "main",
        "inspect_tests",
        "inspect_tests",
        "main",
    ]
    assert row["tool_calls"] == 3 and row["new_model_requests"] == 5
    assert len(row["delegations"]) == 1
    assert "verified_source" in str(model.calls[2].input) and "src/index.ts" in str(model.calls[2].input)
    assert any(s["path"] == "tests/helper.test.ts" for s in row["visible_context"]["main"])
    payload = json.loads(row["delegations"][0]["visible_json"])
    assert payload["notes_not_evidence"] and payload["spans"]
    assert any(s["actor"] == "inspect_tests" for s in row["source_transfers"])
    assert (await engine.answer("对照入口实现与 helper 测试"))["new_model_requests"] == 0
    model.assert_complete()


async def test_expert_and_manager_share_decision_budget_with_finalizer_reserved(reader, tmp_path):
    engine, model = build(
        reader,
        tmp_path,
        [delegation(), expert_read(), [assistant_message("已找到测试调用")], final_step(TEST_ANSWER)],
        max_decisions=4,
    )
    engine.collaboration = CollaborationSettings(mode="auto")
    row = await engine.answer("核对测试")
    assert row["completion"]["status"] == "success", row["stop_reason"]
    assert row["new_model_requests"] == 4 and row["decisions"][-1]["phase"] == "finalize"
    assert model.calls[-1].tools == []
    model.assert_complete()


async def test_expert_loop_cap_returns_partial_sources_without_extra_budget(reader, tmp_path):
    engine, model = build(reader, tmp_path, [delegation(), expert_read(), final_step(TEST_ANSWER)])
    engine.collaboration = CollaborationSettings(mode="auto", max_turns=1)
    row = await engine.answer("核对测试")
    assert row["completion"]["status"] == "success", row["stop_reason"]
    assert row["new_model_requests"] == 3
    assert json.loads(row["delegations"][0]["visible_json"])["status"] == "partial"
    model.assert_complete()


async def test_nested_transport_failure_is_preserved_not_resumed_as_manager(reader, tmp_path):
    engine, _ = build(reader, tmp_path, [delegation(), ConnectionError("offline nested transport")])
    engine.collaboration = CollaborationSettings(mode="auto")
    row = await engine.answer("核对测试")
    assert row["completion"]["status"] == "failed"
    assert not row["checkpoint"]["resumable"] and row["new_model_requests"] == 2
    assert row["decisions"][-1]["actor"] == "inspect_tests"


def test_shared_sources_reject_wrong_snapshot_and_tampering(reader):
    span = source(reader)
    with pytest.raises(ValueError, match="snapshot"):
        select_sources(reader, {"snapshot_id": "other", "spans": [span]}, "index")
    span["content"] = "invented"
    with pytest.raises((ValueError, AssertionError)):
        select_sources(reader, {"snapshot_id": reader.snapshot.snapshot_id, "spans": [span]}, "index")


async def test_shared_context_is_in_cache_identity_and_can_answer_without_rereading(reader, tmp_path):
    engine, model = build(reader, tmp_path, [final_step()], max_tool_calls=0)
    shared = {"snapshot_id": reader.snapshot.snapshot_id, "spans": [source(reader)]}
    row = await engine.answer("入口", shared_context=shared)
    assert row["completion"]["status"] == "success" and row["tool_calls"] == 0
    assert row["shared_input_context"] == shared["spans"]
    assert row["source_transfers"][0]["origin"] == "revalidated-input"
    assert (await engine.answer("入口", shared_context=shared))["cache_hit"]
    model.assert_complete()


def entry_reader():
    files = {
        "package.json": json.dumps(
            {
                "exports": {
                    ".": {"browser": "./browser.js", "default": "./index.js"},
                    "./non-secure": "./non-secure/index.js",
                    "./package.json": "./package.json",
                },
                "bin": "./bin/tool.js",
            }
        ),
        "index.js": "export const run = () => 1",
        "browser.js": "export const run = () => 2",
        "non-secure/index.js": "export const run = () => 3",
        "bin/tool.js": "console.log(1)",
        "test/index.test.js": "import { run } from '../index.js';\nrun();",
        "test/browser.test.js": "import { run } from '../browser.js';\nrun();",
    }
    return SimpleNamespace(
        paths=list(files),
        snapshot=SimpleNamespace(snapshot_id="s"),
        read=lambda p, maximum: (files[p], False),
    )


def test_entry_selection_is_metadata_based_follows_user_and_allows_comparisons():
    reader = entry_reader()
    node = resolve_entry(reader, "Node run 如何实现？")
    assert node["entry_paths"] == ["index.js"] and "browser.js" in node["excluded_paths"]
    assert "test/browser.test.js" in node["excluded_paths"]
    assert node["related_test_candidates"] == ["test/index.test.js"]
    assert "package.json" not in node["excluded_paths"]
    assert resolve_entry(reader, "那么它的边界条件呢？", "node")["label"] == "node"
    assert resolve_entry(reader, "切换到 browser 的实现", "node")["label"] == "browser"
    assert resolve_entry(reader, "比较 Node 与 browser 的实现", "node")["excluded_paths"] == []
    assert resolve_entry(reader, "换到另一个话题", "node")["label"] is None
    assert resolve_entry(reader, "Node run 的 test/index.test.js", "node")["explicit_test_paths"] == [
        "test/index.test.js"
    ]


def test_entry_scope_filters_search_and_blocks_direct_conflicting_read(reader, tmp_path):
    env = RepositoryEnvironment(reader, BM25Index.build(reader, tmp_path))
    env.excluded_paths = ["src/index.ts"]
    result = env.step(
        {
            "call_id": "x",
            "tool": "read_code",
            "arguments": {"path": "src/index.ts", "start_line": 1, "end_line": 2},
        }
    )
    assert result["observation"]["error_code"] == "entry_scope_conflict"
    result = env.step({"call_id": "y", "tool": "search_code", "arguments": {"query": "index"}})
    assert all(s["path"] != "src/index.ts" for s in result["source_spans"])


async def test_conversation_revalidates_old_source_and_persists_task_state(reader, tmp_path):
    engine, model = build(reader, tmp_path, [read_step(), final_step(), final_step()])
    store = ConversationStore(tmp_path / "chat")
    state = store.create(reader.snapshot.snapshot_id, "学习入口")
    await store.turn(state["session_id"], engine, "入口 src/index.ts？", request_id="1")
    turn = await store.turn(state["session_id"], engine, "那么它的调用呢？", request_id="2")
    assert turn["result"]["tool_calls"] == 0 and turn["result"]["shared_input_context"]
    saved = store.load(state["session_id"])["task_state"]
    assert saved["goal"] == "学习入口" and saved["source_refs"] and not saved["source_facts"]
    assert (await store.turn(state["session_id"], engine, "那么它的调用呢？", request_id="2"))[
        "new_model_requests"
    ] == 0
    model.assert_complete()


async def test_unshared_expert_notes_do_not_authorize_citations(reader, tmp_path):
    engine, _ = build(
        reader, tmp_path, [delegation(), [assistant_message("src/index.ts 说明入口")], final_step(ANSWER)]
    )
    engine.collaboration = CollaborationSettings(mode="auto")
    row = await engine.answer("核对测试")
    assert row["stop_reason"] == "invalid_citations" and not row["context"]
