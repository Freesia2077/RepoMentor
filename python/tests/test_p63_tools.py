from types import SimpleNamespace

import pytest

from repomentor.agent_tools import RepositoryEnvironment
from repomentor.contracts import ToolCall


def environment(source):
    reader = SimpleNamespace(snapshot=SimpleNamespace(snapshot_id="fixture"), paths=["src/main.py"],
                             read=lambda path, maximum: (source, False))
    return RepositoryEnvironment(reader, None)


def test_long_function_match_survives_and_continuation_advances():
    source = "def outer():\n" + "".join(f'    value_{i} = "padding_for_bounded_source_window"\n' for i in range(80))
    source += "    return should_bypass_proxies()\n"
    env = environment(source)
    env.limits.max_read_bytes = 1800  # Exercise continuation even with the larger normal allowance.
    chunk = {"path": "src/main.py", "start_line": 1, "end_line": 82,
             "content": source, "chunk_id": "long"}
    result = env._excerpts([{"chunk": chunk, "score": 1}], "should_bypass_proxies", 18000)
    assert "should_bypass_proxies" in result["spans"][0]["content"]
    assert result["hits"][0]["match_line"] == 82
    first = env.step(ToolCall(call_id="first", tool="read_code",
        arguments={"path": "src/main.py", "start_line": 1, "end_line": 82, "expand": "symbol"}))
    last = first["source_spans"][0]["end_line"]
    following = env.step(ToolCall(call_id="next", tool="read_code",
        arguments={"path": "src/main.py", "start_line": last + 1, "end_line": 82, "expand": "symbol"}))
    assert following["source_spans"][0]["start_line"] == last + 1
    assert following["source_spans"][0]["end_line"] > last


def test_duplicate_units_do_not_displace_other_functions():
    source = "def one():\n    return 1\ndef two():\n    return 2\ndef three():\n    return 3\n"
    env = environment(source)
    rows = []
    for i, (start, end) in enumerate([(1, 2), (1, 2), (1, 2), (3, 4), (5, 6)]):
        rows.append({"score": 5-i, "chunk": {"path": "src/main.py", "start_line": start,
            "end_line": end, "content": "\n".join(source.splitlines()[start-1:end]), "chunk_id": str(i)}})
    found = env._excerpts(rows, "return", 18000)
    assert len(found["hits"]) == 3
    assert {h["unit"]["symbol"] for h in found["hits"]} == {"one", "two", "three"}


def test_wire_trimming_preserves_match_and_recomputes_uncovered():
    source = "def outer():\n" + "".join(f'    x{i} = "' + 'x'*45 + '"\n' for i in range(30))
    source += "    return NEEDLE()\n"
    env = environment(source)
    env.limits.max_observation_bytes = 1200
    chunk = {"path": "src/main.py", "start_line": 1, "end_line": 32,
             "content": source, "chunk_id": "long"}
    # Force the wire boundary to trim an initially generous source selection.
    payload = env._excerpts([{"chunk": chunk, "score": 1}], "NEEDLE", 18000)
    payload["padding"] = "x" * 150
    env._execute = lambda call: payload
    result = env.step(ToolCall(call_id="wire", tool="search_code", arguments={"query": "NEEDLE"}))
    assert result["observation"]["status"] == "ok"
    assert "NEEDLE" in result["source_spans"][0]["content"]
    assert len(result["visible_json"].encode()) <= 1200


@pytest.mark.parametrize("anchor,priority,channels,child", [
    (None, None, [], False), (3, None, [], True), (3, 2, [], False),
    (3, 10, [], True), (3, 2, ["static_callee"], True),
])
def test_retrieved_test_keeps_assertion_but_explicit_callback_stays_local(anchor, priority, channels, child):
    source = ("def test_cleanup():\n"
              "    seen = []\n"
              "    def receiver(value):\n"
              "        seen.append(value)\n"
              "    with connection(receiver):\n"
              "        emit(2)\n"
              "    assert seen == [2]\n"
              "    assert not connected(receiver)\n")
    env = environment(source)
    candidate = {"score": 1, "chunk": {"path": "src/main.py", "start_line": 1, "end_line": 8,
                 "content": source, "chunk_id": "test", "symbol": "test_cleanup"}}
    if anchor is not None:
        candidate["anchor_line"] = anchor
    if priority is not None:
        candidate["symbol_priority"] = priority
    candidate["channels"] = channels
    result = env._excerpts([candidate], "receiver", 8000, expand_selected=True)
    span, hit = result["spans"][0], result["hits"][0]
    if not child:
        assert span["content"] == source.rstrip("\n")
        assert hit["unit"]["symbol"] == "test_cleanup"
        assert "assert not connected(receiver)" in span["content"]
    else:
        assert (span["start_line"], span["end_line"]) == (3, 4)
        assert hit["unit"]["symbol"] == "test_cleanup.receiver"
    assert not hit["truncated"]


def test_structural_test_expansion_still_respects_source_allowance():
    source = ("def test_cleanup():\n    def receiver(value):\n        seen.append(value)\n"
              + "".join(f'    padding_{i} = "额外源码"\n' for i in range(30))
              + "    assert not connected(receiver)\n")
    env = environment(source)
    lines = source.splitlines()
    candidate = {"score": 1, "chunk": {"path": "src/main.py", "start_line": 1, "end_line": len(lines),
                 "content": source, "chunk_id": "test", "symbol": "test_cleanup"}}
    result = env._excerpts([candidate], "seen.append", 256, expand_selected=True)
    assert sum(len(span["content"].encode()) for span in result["spans"]) <= 256
    assert "seen.append(value)" in result["spans"][0]["content"]
    hit = result["hits"][0]
    assert hit["unit"]["symbol"] == "test_cleanup"
    assert hit["truncated"] and hit["uncovered_ranges"]
