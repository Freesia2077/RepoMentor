"""Read-only repository tools shared by SDKs and training environments."""

import json

import pytest

from repomentor.agent_tools import AgentLimits, RepositoryEnvironment, native_tools, sdk_tools
from repomentor.contracts import ToolCall
from repomentor.retrieval import BM25Index


def action(name, args, ident="call1"):
    return ToolCall(call_id=ident, tool=name, arguments=args)


@pytest.fixture
def env(reader, tmp_path):
    return RepositoryEnvironment(reader, BM25Index.build(reader, tmp_path / "idx"))


def test_tool_boundaries_and_reset(env):
    with pytest.raises(ValueError, match="gold"):
        env.reset({"question": "q", "gold": "leak"})
    bad = env.step(action("read_code", {"path": "../secrets", "start_line": 1, "end_line": 2}))
    assert bad["observation"]["status"] == "error" and not env.state["context"]
    bad2 = env.step(action("read_code", {"path": "src/index.ts", "start_line": "1", "end_line": 2}, "bad2"))
    assert bad2["observation"]["error_code"] == "invalid_arguments"
    unknown = env.step(action("Bash", {"command": "echo nope"}, "bad3"))
    assert unknown["observation"]["error_code"] == "unknown_tool"
    env.reset({"question": "new"})
    assert not env.state["observations"] and env.state["tool_calls"] == 0


def test_search_rewrite_read_tests_and_idempotence(env):
    miss = env.step(action("search_code", {"query": "nonexistent_unique_symbol", "match": "literal"}))
    assert miss["observation"]["status"] == "empty"
    hit = env.step(action("search_code", {"query": "helper", "match": "literal"}, "hit"))
    assert hit["observation"]["spans"]
    read = env.step(
        action("read_code", {"path": "src/utils/helper.ts", "start_line": 1, "end_line": 20}, "read")
    )
    assert read["observation"]["spans"][0]["start_line"] == 1
    replay = env.step(
        action("read_code", {"path": "src/utils/helper.ts", "start_line": 1, "end_line": 20}, "read")
    )
    assert replay["replayed"] and env.state["tool_calls"] == 3
    duplicate = env.step(
        action("read_code", {"path": "src/utils/helper.ts", "start_line": 1, "end_line": 20}, "dup")
    )
    assert duplicate["observation"]["error_code"] == "duplicate_action"
    tests = env.step(action("find_tests", {"path": "src/utils/helper.ts", "query": "helper"}, "tests"))
    assert "tests/helper.test.ts" in tests["observation"]["paths"]
    assert all(json.loads(o["visible_json"]) == o["observation"] for o in env.state["observations"])


def test_budget_cancel_and_native_mcp_schema(env):
    env.limits = AgentLimits(max_tool_calls=1)
    env.step(action("search_docs", {"query": "example"}))
    assert (
        env.step(action("search_docs", {"query": "readme"}, "2"))["observation"]["error_code"]
        == "tool_budget"
    )
    assert env.state["tool_calls"] == 1
    env.reset({"question": "new"})
    env.token.cancel()
    assert env.step(action("search_docs", {"query": "readme"}))["observation"]["error_code"] == "cancelled"
    native = {t["function"]["name"]: t["function"]["parameters"] for t in native_tools()}
    assert native == {t.name: t.input_schema for t in sdk_tools(env)}


@pytest.mark.parametrize("directory", ["src/utils", "src/utils/"])
def test_search_directory_mistake_is_scoped_and_recorded(env, directory):
    canonical = env.step(action("search_code", {"query": "helper", "path_prefix": "src/utils"}))
    env.reset({"question": "helper"})
    corrected = env.step(action("search_code", {"query": "helper", "path": directory}))
    assert corrected["observation"]["status"] == "ok"
    assert corrected["source_spans"] == canonical["source_spans"]
    assert corrected["observation"]["search_scope"] == canonical["observation"]["search_scope"]
    assert corrected["action"]["arguments"]["path"] == directory
    assert corrected["observation"]["scope_correction"] == {
        "supplied_path": directory, "used_path_prefix": "src/utils"}
    assert all(s["path"].startswith("src/utils/") for s in corrected["source_spans"])
    env.reset({"question": "excluded"})
    env.excluded_paths = ["src/utils/helper.ts"]
    excluded = env.step(action("search_code", {"query": "helper", "path": directory}))
    assert not excluded["source_spans"]
    for i, path in enumerate(("src/missing", "../src/utils", "/src/utils")):
        bad = env.step(action("search_code", {"query": "helper", "path": path}, f"bad-{i}"))
        assert bad["observation"]["status"] == "error" and not bad["source_spans"]


async def test_native_mcp_same_observation(env):
    native = env.step(action("read_code", {"path": "src/index.ts", "start_line": 1, "end_line": 4}))
    env.reset({"question": "new"})
    tool = next(t for t in sdk_tools(env) if t.name == "read_code")
    mcp = await tool.handler({"path": "src/index.ts", "start_line": 1, "end_line": 4})
    assert mcp["content"][0]["text"] == native["visible_json"]


def test_bounded_source_numbering_and_overlapping_read(env):
    first = env.step(action("read_code", {"path": "src/index.ts", "start_line": 1, "end_line": 2}))
    assert first["observation"]["spans"][0]["lines"][0].startswith("L1: ")
    assert "L1: " not in first["source_spans"][0]["content"]
    overlap = env.step(action("read_code", {"path": "src/index.ts", "start_line": 2, "end_line": 2}, "2"))
    assert overlap["observation"]["error_code"] == "already_read"
    assert env.state["source_bytes"] == len(first["source_spans"][0]["content"].encode())


def test_environment_deadline_and_isolation(env):
    other = RepositoryEnvironment(env.reader, env.index)
    env.clock = lambda: env.started + env.limits.timeout_seconds + 1
    assert env.step(action("search_code", {"query": "helper"}))["observation"]["error_code"] == "deadline"
    assert other.state["tool_calls"] == 0 and other.state["observations"] == []


def test_static_dependencies_and_schema_do_not_execute_repository(env):
    result = env.step(action("trace_dependencies", {"path": "src/index.ts"}))
    assert result["observation"]["status"] == "ok"
    assert result["observation"]["paths"] and not result["source_spans"]
    assert "static" in result["observation"]["scope"]
