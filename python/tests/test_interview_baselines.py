import json

from test_chat import settings

from repomentor.interview_baselines import (
    FrozenSourcesIndex,
    lexical_locations,
    percentile,
    prepare_input,
    select_sources,
)
from repomentor.retrieval_factory import build_index
from repomentor.source_windows import source_span


def test_literal_baseline_respects_scope_and_returns_actual_snapshot_lines(reader, tmp_path):
    index = build_index(reader, storage=tmp_path)
    locations = lexical_locations(reader, reader.paths, "helper", excluded=["src/utils/helper.ts"])
    assert all(h["path"] != "src/utils/helper.ts" for h in locations)
    for condition in ("grep_read", "located_file", "lexical", "code_rag"):
        selected = select_sources(reader, index, "helper", condition)
        assert all(s["path"] in reader.paths for s in selected["sources"])
    assert select_sources(reader, index, "helper", "none")["sources"] == []


def test_full_file_overflow_is_infeasible_and_keeps_the_complete_source():
    text = "x = '" + "many bytes " * 2500 + "'"
    selection = {"sources": [source_span("main.py", [text], 1, 1)]}
    result = prepare_input(settings(context_window=1024), "x?", selection, "located_file")
    assert result["feasible"] is False and result["reason"] == "whole_file_context_limit"
    assert result["context"] == selection["sources"]
    assert text in json.loads(result["input_messages"][-1]["content"])["context"][0]["content"]


def test_model_input_contains_only_task_contract_and_observed_source(reader, tmp_path):
    index = build_index(reader, storage=tmp_path)
    selection = select_sources(reader, index, "helper", "code_rag")
    prepared = prepare_input(settings(context_window=32768), "helper", selection, "code_rag")
    payload = json.loads(prepared["input_messages"][-1]["content"])
    from repomentor.contracts import ANSWER_SCHEMA

    assert set(payload) == {"question", "context", "response_schema"}
    assert payload["response_schema"] == ANSWER_SCHEMA
    adapter = FrozenSourcesIndex("immutable", selection)
    assert adapter.initial_sources("helper") == selection
    if selection["sources"]:
        import pytest

        with pytest.raises(ValueError, match="entry scope"):
            adapter.initial_sources("helper", excluded=[selection["sources"][0]["path"]])


def test_latency_percentiles_do_not_invent_an_empty_cohort():
    assert percentile([], 0.95) is None
    assert percentile([1, 2, 3], 0.5) == 2
    assert percentile([1, 2, 3], 0.95) == 2.9


def test_literal_baseline_can_match_source_budget_without_changing_legacy_defaults():
    from types import SimpleNamespace

    files = {
        f"module{i}.py": "def helper():\n    # " + "padding " * 60 + "\n    return 1\n" for i in range(8)
    }
    reader = SimpleNamespace(paths=list(files), read=lambda path, maximum: (files[path], False))
    index = SimpleNamespace(chunks=[SimpleNamespace(path=path) for path in files])
    legacy = select_sources(reader, index, "helper", "grep_read")
    matched = select_sources(reader, index, "helper", "grep_read", source_bytes=8000, max_units=6)
    bounded = select_sources(reader, index, "helper", "grep_read", source_bytes=1000, max_units=6)
    assert len(legacy["sources"]) == 3
    assert len(matched["sources"]) == 6
    assert sum(len(s["content"].encode()) for s in bounded["sources"]) <= 1000
    assert sum(len(s["content"].encode()) for s in bounded["sources"]) < sum(
        len(s["content"].encode()) for s in matched["sources"]
    )
