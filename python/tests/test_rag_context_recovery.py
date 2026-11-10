import hashlib
import json
from types import SimpleNamespace

import jsonschema
import pytest
from test_rag_quality import mentor
from test_sdk_agent import ANSWER, build, final_step, http_engine, response

from repomentor.contracts import MentorOutput
from repomentor.grounded_schema import grounded_report_schema
from repomentor.shared_sources import select_sources
from repomentor.source_context import pack_observed_context
from repomentor.symbol_navigation import declarations, symbol_navigation


def nav_reader(files):
    return SimpleNamespace(paths=list(files), read=lambda path, maximum: (files[path], False))


def test_factory_navigation_resolves_exact_export_not_similar_helper():
    reader = nav_reader(
        {
            "index.js": "export function random(n) { throw Error(); }\n"
            "export function factory(n) { return size => { if (size < 0) throw Error(); return n; }; }\n"
            "export const generate = factory(21);",
            "browser.js": "export function generate() { return ''; }",
        }
    )
    focus = {"entry_paths": ["index.js"], "excluded_paths": ["browser.js"]}
    result = symbol_navigation(reader, "Node generate(-10) 的行为？", focus)
    assert result["current_symbol"] == "generate" and not result["source_facts"]
    assert len(result["targets"]) == 1
    target = result["targets"][0]
    assert target["start_line"] == 3 and target["factory_candidates"][0]["start_line"] == 2
    assert "content" not in target and "random" not in str(result)
    follow = symbol_navigation(reader, "那么它呢？", focus, "generate")
    assert follow["current_symbol"] == "generate"


def test_ast_binding_navigation_ignores_comments_strings_and_nested_variables():
    text = (
        "const documentation = `\nexport const fake = factory();\n`;\n"
        "function outer() { const inner = factory(); }\n"
        "// export const commented = factory();\n"
        "export const real =\n factory(1);"
    )
    rows = declarations(text, "module.js")
    assert {r["symbol"] for r in rows} == {"outer", "real"}
    assert next(r for r in rows if r["symbol"] == "real")["end_line"] == 7


def test_symbol_navigation_keeps_ambiguity_and_bounded_scan():
    reader = nav_reader(
        {"a.py": "class A:\n def run(self): return 1", "b.py": "class B:\n def run(self): return 2"}
    )
    result = symbol_navigation(reader, "run 如何实现？", {})
    assert result["current_symbol"] is None and len(result["targets"]) == 2
    limited = symbol_navigation(reader, "run 如何实现？", {}, max_files=1)
    assert limited["search_incomplete"] and limited["scanned_files"] == 1
    assert not declarations("def broken(\n", "bad.py")


def test_observed_partition_preserves_relevant_tail_without_fabricating_lines():
    lines = ["irrelevant"] * 45 + ["needle target assertion"] * 15
    text = "\n".join(lines)
    span = {
        "path": "a.py",
        "start_line": 10,
        "end_line": 69,
        "content": text,
        "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
    }
    chosen, omitted = pack_observed_context(
        [span], "needle", lambda rows: sum(len(s["content"].encode()) for s in rows) < 450
    )
    assert len(chosen) == 1 and chosen[0]["start_line"] == 50
    assert chosen[0]["content"] == "\n".join(lines[40:])
    assert omitted[0]["start_line"] == 10 and omitted[0]["end_line"] == 49
    assert chosen[0]["content_sha256"] == hashlib.sha256(chosen[0]["content"].encode()).hexdigest()


async def test_navigation_alone_never_authorizes_citations(reader, tmp_path):
    engine, _ = build(reader, tmp_path, [final_step(ANSWER)])
    result = await engine.answer("src/index.ts 的 helper 如何实现？")
    assert result["stop_reason"] == "invalid_citations" and not result["context"]


async def test_wire_context_limit_preserves_rejection_and_uses_one_sdk_finalizer(
    reader, tmp_path, monkeypatch
):
    import repomentor.runtimes.openai_sdk as transport

    original = transport.sdk_input_budget

    def budget(settings, body):
        actual = original(settings, body)
        if any(m.get("role") == "tool" for m in body["messages"]):
            return {**actual, "fits": False, "total": settings.context_window + 1}
        return actual

    monkeypatch.setattr(transport, "sdk_input_budget", budget)
    sent = []

    def handler(request):
        body = json.loads(request.content)
        sent.append(body)
        if len(sent) == 1:
            return response(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "read",
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
        assert "response_format" in body and body["messages"][-1]["role"] == "user"
        return response()

    engine = await http_engine(reader, tmp_path, handler)
    row = await engine.answer("入口？")
    assert row["completion"]["status"] == "success", row["stop_reason"]
    assert row["research_stop"] == "research_context_limit"
    assert row["new_model_requests"] == len(sent) == 2
    assert len(row["decisions"]) == 3
    assert not row["decisions"][1]["request_sent"] and row["decisions"][1]["status"] == "failed"
    assert row["decisions"][-1]["phase"] == "finalize" and row["tool_calls"] == 1
    assert (await engine.answer("入口？"))["new_model_requests"] == 0


def test_previous_citation_source_survives_budget_without_importing_past_claims():
    text = "\n".join(["needle header"] * 45 + ["if size < 0: raise ValueError()"] * 15)
    reader = nav_reader({"a.py": text})
    reader.snapshot = SimpleNamespace(snapshot_id="s")
    span = {
        "path": "a.py",
        "start_line": 1,
        "end_line": 60,
        "content": text,
        "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
    }
    envelope = {
        "snapshot_id": "s",
        "spans": [span],
        "preferred_ranges": [{"path": "a.py", "start_line": 50, "end_line": 51}],
    }
    chosen = select_sources(reader, envelope, "needle", max_bytes=450)
    assert any(s["start_line"] <= 50 <= 51 <= s["end_line"] for s in chosen)
    assert sum(len(s["content"].encode()) for s in chosen) <= 450
    assert not select_sources(reader, envelope, "needle", max_bytes=450, excluded=["a.py"])
    envelope["preferred_ranges"] = [{"path": "a.py", "start_line": 70, "end_line": 80}]
    assert all(s["end_line"] <= 60 for s in select_sources(reader, envelope, "needle", max_bytes=450))


def test_report_schema_binds_file_and_graph_keys_without_claiming_semantic_correctness():
    original = MentorOutput.model_json_schema()
    schema = grounded_report_schema(original, [{"path": "src/index.ts"}])
    value = mentor(["src/index.ts"])
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(value, schema)
    assert "enum" not in original["$defs"]["ReadingStep"]["properties"]["file"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(mentor(["unread.ts"]), schema)
    value["dependencyGraph"] = {"dependencies": []}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(value, schema)
    value["dependencyGraph"] = {"src": ["src/index.ts"]}
    jsonschema.validate(value, schema)  # Legal paths alone do not establish a true dependency edge.
    empty = grounded_report_schema(original, [])
    jsonschema.Draft202012Validator.check_schema(empty)
    jsonschema.validate(mentor(), empty)


async def test_actual_sdk_transmits_observed_path_schema_to_formatter(reader, tmp_path):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return response(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "read",
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
        schema = body["response_format"]["json_schema"]["schema"]
        assert schema["$defs"]["ReadingStep"]["properties"]["file"]["enum"] == ["src/index.ts"]
        assert not schema["properties"]["dependencyGraph"]["additionalProperties"]
        return response({"role": "assistant", "content": json.dumps(mentor(["src/index.ts"]))})

    engine = await http_engine(reader, tmp_path, handler)
    from repomentor.agent_tools import AgentLimits

    engine.limits = AgentLimits(max_tool_calls=1)
    result = await engine.answer("报告", output_type=MentorOutput)
    assert result["completion"]["status"] == "success", result["stop_reason"]
    assert len(requests) == 2 and result["report_validation"]["passed"]
