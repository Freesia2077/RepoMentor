"""Fixed source-selection baselines; no agent loop, gold input, or target-code execution."""

# 为固定检索对照提供选源适配器，沿用现有 RagEngine 的预算、缓存与恢复。
# 各条件通过明确的证据选择策略区分，评测参考答案留在生成输入之外。

from __future__ import annotations

import copy
import re
from types import SimpleNamespace

from .agent_tools import RepositoryEnvironment
from .contracts import ANSWER_SCHEMA
from .entry_scope import resolve_entry
from .rag import assemble_context, messages_for
from .retrieval import indexable, tokenize
from .retrieval_scope import SearchScope
from .source_windows import anchored_window, source_span, verify_spans
from .token_budget import input_budget

CONDITIONS = ("none", "grep_read", "located_file", "lexical", "code_rag")
SOURCE_BYTES = 4000


def lexical_locations(reader, paths, question, excluded=()):
    """Literal identifier matching in immutable indexed files, not a gold-aware locator."""
    terms = {t for t in tokenize(question) if re.fullmatch(r"[a-z_][a-z_0-9]*", t)}
    rows = []
    for path in sorted(set(paths) - set(excluded)):
        if not indexable(path):
            continue
        text, truncated = reader.read(path, 500_000)
        if truncated:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            hits = terms & {t.lower() for t in re.findall(r"[A-Za-z_][A-Za-z_0-9]*", line)}
            if hits:
                rows.append({"path": path, "line": number, "score": len(hits)})
    return sorted(rows, key=lambda r: (-r["score"], r["path"], r["line"]))


# 仅依据问题和仓库原文选择证据，各基线沿用相同源码格式及范围约束。
def select_sources(reader, index, question, condition, *, source_bytes=SOURCE_BYTES, max_units=3):
    if condition not in CONDITIONS:
        raise ValueError("unknown interview baseline")
    if condition == "none":
        return {"sources": [], "retrieval": {"candidates": []}, "hits": []}
    excluded = resolve_entry(reader, question)["excluded_paths"]
    if condition == "code_rag":
        return index.initial_sources(question, excluded=excluded)
    if condition == "lexical":
        base = index.base
        search = base.search(question, scope=SearchScope(exclude_paths=excluded))
        environment = RepositoryEnvironment(reader, base)
        result = environment._excerpts(search["candidates"], question, source_bytes, max_units=max_units)
        return {"sources": result["spans"], "retrieval": search, "hits": result["hits"]}
    locations = lexical_locations(reader, {c.path for c in index.chunks}, question, excluded)
    if condition == "located_file":
        if not locations:
            return {"sources": [], "retrieval": {"candidates": []}, "hits": [], "no_location": True}
        path = locations[0]["path"]
        text, truncated = reader.read(path, 500_000)
        if truncated:
            return {
                "sources": [],
                "retrieval": {"candidates": []},
                "hits": locations[:1],
                "infeasible": "whole_file_reader_limit",
            }
        spans = [source_span(path, text.splitlines(), 1, len(text.splitlines()))]
    else:
        spans, remaining = [], source_bytes
        for hit in locations:
            if any(
                s["path"] == hit["path"] and s["start_line"] <= hit["line"] <= s["end_line"] for s in spans
            ):
                continue
            lines = reader.read(hit["path"], 500_000)[0].splitlines()
            window = anchored_window(
                lines,
                max(1, hit["line"] - 20),
                min(len(lines), hit["line"] + 20),
                hit["line"],
                min(1600, remaining),
            )
            if window:
                span = source_span(hit["path"], lines, *window)
                spans.append(span)
                remaining -= len(span["content"].encode())
            if len(spans) == max_units or remaining <= 0:
                break
    verify_spans(reader, spans)
    return {"sources": spans, "retrieval": {"candidates": []}, "hits": locations[:20]}


def prepare_input(settings, question, selection, condition):
    if selection.get("infeasible"):
        return {"feasible": False, "reason": selection["infeasible"], "context": [], "input_messages": []}
    if condition == "located_file":
        messages = messages_for(question, [], selection["sources"])
        budget = input_budget(settings, messages, response_schema=ANSWER_SCHEMA)
        # A full-file baseline is never silently shortened into another retrieval baseline.
        return {
            "feasible": budget["fits"],
            "reason": None if budget["fits"] else "whole_file_context_limit",
            "context": selection["sources"],
            "input_messages": messages,
            "omissions": [],
            "budget": budget,
        }
    assembled = assemble_context(settings, question, [], selection["sources"], top_k=None)
    budget = input_budget(settings, assembled["input_messages"], response_schema=ANSWER_SCHEMA)
    return {
        **assembled,
        "feasible": budget["fits"],
        "reason": None if budget["fits"] else "context_limit",
        "budget": budget,
    }


# 把冻结证据适配成已有检索接口，保留 RagEngine 的统一输入、缓存和恢复流程。
class FrozenSourcesIndex:
    """Adapter for the existing fixed RagEngine, including its cache/recovery semantics."""

    def __init__(self, index_id, selection):
        self.index_id, self.selection = index_id, copy.deepcopy(selection)
        self.settings = SimpleNamespace(top_k=6)

    def search(self, question, **kwargs):
        return {"query": question, "index_id": self.index_id, "candidates": []}

    def initial_sources(self, question, *, excluded=()):
        if any(s["path"] in excluded for s in self.selection["sources"]):
            raise ValueError("baseline source violates entry scope")
        return copy.deepcopy(self.selection)


def percentile(values, fraction):
    """Linear sample percentile, descriptive only; six tasks do not establish an SLO."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    first = int(position)
    last = min(first + 1, len(ordered) - 1)
    return ordered[first] + (ordered[last] - ordered[first]) * (position - first)
