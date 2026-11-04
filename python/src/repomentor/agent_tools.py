"""One read-only tool registry for native calls, SDK MCP and training environments."""

from __future__ import annotations

import copy
import hashlib
import json
import time
from pathlib import PurePosixPath
from uuid import uuid4

import jsonschema
from pydantic import Field

from .contracts import Contract, ToolCall
from .execution import CancellationToken, HarnessError
from .repository import RepositoryError, safe_path
from .retrieval import tokenize
from .retrieval_scope import SearchScope
from .runtimes.chat import fingerprint
from .source_context import containing_unit
from .source_windows import anchored_window, source_span, uncovered, verify_spans
from .tools import find_tests, trace_dependencies


class AgentLimits(Contract):
    max_tool_calls: int = Field(default=6, ge=0, le=32)
    max_decisions: int = Field(default=8, ge=1, le=32)
    timeout_seconds: float = Field(default=300, gt=0)
    max_observation_bytes: int = Field(default=12000, ge=1000, le=20000)
    max_source_bytes: int = Field(default=36000, ge=1000, le=100000)
    max_read_bytes: int = Field(default=8000, ge=256, le=16000)
    max_read_lines: int = Field(default=200, ge=1, le=400)


def object_schema(properties, required):
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


QUERY = {"type": "string", "minLength": 1, "maxLength": 240, "pattern": r"\S"}
PATH = {"type": "string", "minLength": 1, "maxLength": 300}
SEARCH = object_schema(
    {"query": QUERY, "path": PATH, "path_prefix": PATH,
     "match": {"type": "string", "enum": ["ranked", "literal"]}}, ["query"]
)
DEFINITIONS = {
    "navigate_symbol": (
        "Find exact definitions or one-hop callees/callers of an identifier. Returns up to six locations; "
        "unresolved dynamic calls are explicit. No source evidence: read returned lines with read_code.",
        object_schema({"query": QUERY, "path": PATH,
                       "relation": {"type": "string", "enum": ["definitions", "callees", "callers"]}}, ["query"]),
    ),
    "search_code": (
        "Search source/tests; returns at most 3 source units, bounded by bytes. "
        "Use English identifiers; match=literal for an exact substring. Empty results: change query. "
        "Optional path is exact; path_prefix limits to a directory BEFORE retrieval. "
        "Truncated hits include next_line; read remaining relevant lines with read_code.",
        SEARCH,
    ),
    "search_docs": (
        "Search README/docs/config (not source). Same bounded locations/excerpts as search_code. "
        "Use English keywords; path/path_prefix filter BEFORE retrieval; empty results do not prove absence.",
        SEARCH,
    ),
    "read_code": (
        "Read exact source or documentation lines from the snapshot. Supply path, start_line, end_line. "
        "Optional expand=symbol includes the enclosing function/class/test. "
        "Bounded by the run's max_read_lines/max_read_bytes and serialized observation allowance. "
        "If truncated, continuation contains exact read_code arguments for the next unread interval. "
        "Continue only when that interval is relevant; only shown lines can be cited.",
        object_schema(
            {
                "path": PATH,
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
                "expand": {"type": "string", "enum": ["none", "symbol"]},
            },
            ["path", "start_line", "end_line"],
        ),
    ),
    "trace_dependencies": (
        "Resolve static local imports of one source path. Returns related paths, not a runtime call graph. "
        "External/dynamic imports may be unresolved; read returned paths before making claims.",
        object_schema({"path": PATH}, ["path"]),
    ),
    "find_tests": (
        "Find candidate tests for a source path, optionally with a symbol query. Returns short excerpts; "
        "read relevant test ranges to verify assertions. A match does not prove executed coverage.",
        object_schema({"path": PATH, "query": QUERY}, ["path"]),
    ),
}


def native_tools():
    return [
        {
            "type": "function",
            "function": {"name": name, "description": description, "parameters": copy.deepcopy(schema)},
        }
        for name, (description, schema) in DEFINITIONS.items()
    ]


def sdk_tools(environment):
    """The SDK drives its own loop; these adapters invoke precisely the same step boundary."""
    import claude_agent_sdk as sdk

    definitions = []
    for name, (description, schema) in DEFINITIONS.items():

        async def handler(arguments, tool_name=name):
            result = environment.step(ToolCall(call_id=uuid4().hex, tool=tool_name, arguments=arguments))
            return {
                "content": [{"type": "text", "text": result["visible_json"]}],
                "isError": result["observation"]["status"] == "error",
            }

        definitions.append(sdk.tool(name, description, copy.deepcopy(schema))(handler))
    return definitions


def mcp_server(environment):
    import claude_agent_sdk as sdk

    return sdk.create_sdk_mcp_server(name="repomentor_agent", version="1.0.0", tools=sdk_tools(environment))


class RepositoryEnvironment:
    """Independent reset/step state. Task accepts only a question: gold/rewards stay outside."""

    def __init__(self, reader, index, limits=None, *, token=None, clock=time.monotonic):
        self.reader, self.index = reader, index
        self.limits = limits or AgentLimits()
        self.token, self.clock = token or CancellationToken(), clock
        self.reset({"question": ""})

    def reset(self, task):
        if set(task) != {"question"} or not isinstance(task["question"], str):
            raise ValueError("environment input permits question only; no gold/reward fields")
        self.state = {
            "question": task["question"],
            "tool_calls": 0,
            "source_bytes": 0,
            "seen": {},
            "call_ids": {},
            "context": [],
            "observations": [],
            "visible_context": {},
            "source_transfers": [],
            "delegations": [],
        }
        self.actor, self.excluded_paths = "main", []
        self.started = self.clock()
        return {
            "snapshot_id": self.reader.snapshot.snapshot_id,
            "question": task["question"],
            "tools": native_tools(),
            "limits": self.limits.model_dump(),
        }

    def restore(self, state):
        self.state = copy.deepcopy(state)
        verify_spans(self.reader, self.state["context"])
        self.started = self.clock()

    def check(self):
        self.token.check()
        if self.clock() - self.started >= self.limits.timeout_seconds:
            raise HarnessError("deadline", "episode deadline reached")

    def visible_context(self, actor="main"):
        return self.state.get("visible_context", {}).get(actor, [])

    def pending_reads(self, actor="main"):
        """Navigation only: derive remaining requested intervals from currently visible raw lines."""
        pending, seen = [], set()
        for record in reversed(self.state["observations"]):
            if record.get("actor") != actor or record["action"]["tool"] != "read_code":
                continue
            request = record["observation"].get("requested_range")
            if not request:
                continue
            path = record["action"]["arguments"]["path"]
            visible = [s for s in self.visible_context(actor) if s["path"] == path]
            for gap in uncovered(request["start_line"], request["end_line"], visible):
                key = (path, gap["start_line"], gap["end_line"])
                if key in seen:
                    continue
                seen.add(key)
                pending.append({"path": path, **gap, "arguments": {"path": path, **gap, "expand": "none"}})
                if len(pending) == 3:
                    return pending
        return pending

    def share(self, spans, actor, origin):
        verify_spans(self.reader, spans)
        visible = self.state.setdefault("visible_context", {}).setdefault(actor, [])
        admitted = []
        for span in spans:
            if span["path"] in self.excluded_paths:
                continue
            if span in visible:
                admitted.append(span)
                continue
            size = len(span["content"].encode())
            if self.state["source_bytes"] + size > self.limits.max_source_bytes:
                continue
            visible.append(copy.deepcopy(span))
            if span not in self.state["context"]:
                self.state["context"].append(copy.deepcopy(span))
            self.state["source_bytes"] += size
            self.state["source_transfers"].append({"actor": actor, "origin": origin, "bytes": size,
                **{k: span[k] for k in ("path", "start_line", "end_line", "content_sha256")}})
            admitted.append(span)
        return admitted

    def _span(self, path, start, end, maximum):
        self.check()
        text, truncated = self.reader.read(path, 500_000)
        if truncated:
            raise RepositoryError("oversized", "file exceeds bounded source reader")
        lines = text.splitlines()
        if start > end or start < 1 or start > len(lines):
            raise RepositoryError("invalid_range", "use the returned file/range and positive lines")
        last = min(end, len(lines), start + self.limits.max_read_lines - 1)
        selected, used = [], 0
        for line in lines[start - 1 : last]:
            size = len(line.encode()) + bool(selected)
            if used + size > maximum:
                break
            selected.append(line)
            used += size
        if not selected:
            raise RepositoryError("source_budget", "line exceeds remaining observation/source allowance")
        content = "\n".join(selected)
        span = {
            "path": path,
            "start_line": start,
            "end_line": start + len(selected) - 1,
            "content": content,
            "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
        }
        return span, (span["end_line"] + 1 if span["end_line"] < min(end, len(lines)) else None)

    def _ranked(self, query, kind, path=None, literal=False, path_prefix=None):
        if path:
            safe_path(path)
            if path not in self.reader.paths:
                raise RepositoryError("not_tracked", "path not present in snapshot")
        scope = SearchScope(kind=kind, path=path, path_prefix=path_prefix, exclude_paths=self.excluded_paths)
        # Scope is applied before every top-k operation, including vector recall and reranking.
        if literal:
            candidates = [
                {"chunk": c.model_dump(), "score": None}
                for c in self.index.chunks
                if scope.matches(c.path) and query.casefold() in c.content.casefold()
            ]
        else:
            try:
                candidates = self.index.search(query, k=self.index.settings.candidate_k, scope=scope)["candidates"]
            finally:
                if hasattr(self.index, "release_models"):
                    self.index.release_models()
        self.check()

        return candidates[:self.index.settings.candidate_k]

    def _excerpts(self, candidates, query, remaining, *, max_units=3, expand_selected=False):
        spans, hits, units_seen = [], [], set()
        terms = set(tokenize(query))
        for item in candidates:
            if remaining <= 0 or len(hits) >= max_units:
                break
            chunk = item["chunk"]
            lines = chunk["content"].splitlines()
            if not lines:
                continue
            offset = next(
                (
                    i
                    for i, line in enumerate(lines)
                    if query.casefold() in line.casefold()
                ),
                max(range(len(lines)), key=lambda i: len(terms & set(tokenize(lines[i])))),
            )
            start = item.get("anchor_line", chunk["start_line"] + offset)
            if not chunk["start_line"] <= start <= chunk["end_line"]:
                raise ValueError("source anchor must lie in its candidate chunk")
            text, _ = self.reader.read(chunk["path"], 500_000)
            # A lexical/leaf-name hit must not shrink its retrieved declaration
            # into a nested callback. Exact symbols and callees retain their anchor.
            targeted = "anchor_line" in item and (
                item.get("symbol_priority", 5) >= 5 or "static_callee" in item.get("channels", []))
            bounds = ((chunk["start_line"], chunk["end_line"])
                      if chunk.get("symbol") and not targeted else (start, start))
            unit = containing_unit(text, chunk["path"], *bounds)
            if unit["symbol"] is None:
                unit.update(start_line=chunk["start_line"], end_line=chunk["end_line"])
            unit_key = (chunk["path"], unit["start_line"], unit["end_line"])
            if unit_key in units_seen:
                continue
            full_lines = text.splitlines()
            window = anchored_window(full_lines, unit["start_line"], unit["end_line"], start,
                                     min(600 if expand_selected and chunk["path"].endswith(".d.ts") else 2400,
                                         remaining, self.limits.max_observation_bytes // 6))
            if window is None:
                continue
            units_seen.add(unit_key)
            span = source_span(chunk["path"], full_lines, *window)
            missing = uncovered(unit["start_line"], unit["end_line"], [span])
            next_line = span["end_line"] + 1 if span["end_line"] < unit["end_line"] else None
            remaining -= len(span["content"].encode())
            spans.append(span)
            hits.append(
                {
                    "path": chunk["path"],
                    "start_line": chunk["start_line"],
                    "end_line": chunk["end_line"],
                    "score": item["score"],
                    "chunk_id": chunk["chunk_id"],
                    "unit": unit,
                    "match_line": start,
                    "uncovered_ranges": missing,
                    "truncated": bool(missing),
                    "next_line": next_line,
                }
            )
        # Initial retrieval has a shared source budget. After every selected
        # unit has a window, spend unused bytes on complete units rather than
        # leave a return/call tail missing behind a per-unit soft cap.
        if expand_selected:
            for i in sorted(range(len(hits)), key=lambda j: "class" in hits[j]["unit"].get("kind", "")):
                hit = hits[i]
                unit = hit["unit"]
                if not hit["truncated"] or unit["end_line"]-unit["start_line"]+1 > self.limits.max_read_lines:
                    continue
                text, _ = self.reader.read(hit["path"], 500_000)
                whole = source_span(hit["path"], text.splitlines(), unit["start_line"], unit["end_line"])
                extra = len(whole["content"].encode()) - len(spans[i]["content"].encode())
                if extra <= remaining and len(whole["content"].encode()) <= self.limits.max_read_bytes:
                    spans[i] = whole
                    remaining -= extra
                    hit.update(uncovered_ranges=[], truncated=False, next_line=None)
                elif (remaining >= 256 and "class" not in unit.get("kind", "")
                      and spans[i]["end_line"] < unit["end_line"]):
                    tail = anchored_window(text.splitlines(), spans[i]["end_line"]+1, unit["end_line"],
                                           unit["end_line"], min(1200, remaining))
                    if tail is not None:
                        span = source_span(hit["path"], text.splitlines(), *tail)
                        spans.append(span)
                        remaining -= len(span["content"].encode())
                        missing = uncovered(unit["start_line"], unit["end_line"], [spans[i], span])
                        hit.update(uncovered_ranges=missing, truncated=bool(missing),
                                   next_line=missing[0]["start_line"] if missing else None)
        return {
            "schema_version": "repomentor.tool.sources.v2",
            "hits": hits,
            "spans": spans,
            "candidate_limit": self.index.settings.candidate_k if self.index is not None else len(candidates),
            "scope": "indexed candidates only; no match does not establish absence",
        }

    def _execute(self, call):
        args, name = call.arguments, call.tool
        remaining = self.limits.max_source_bytes - self.state["source_bytes"]
        if name == "navigate_symbol":
            symbols = getattr(self.index, "symbols", None)
            if symbols is None:
                from .code_navigation import SymbolIndex
                symbols = SymbolIndex.build(self.reader, {c.path for c in self.index.chunks})
            result = symbols.lookup(args["query"], path=args.get("path"),
                                    relation=args.get("relation", "definitions"), excluded=self.excluded_paths)
            return {**result, "paths": list(dict.fromkeys(t["path"] for t in result["targets"])), "spans": []}
        if name in ("search_code", "search_docs"):
            path, prefix = args.get("path"), args.get("path_prefix")
            corrected = False
            if path and prefix is None and path not in self.reader.paths:
                directory = safe_path(path.rstrip("/"))
                if any(p.startswith(directory + "/") for p in self.reader.paths):
                    path, prefix, corrected = None, directory, True
            rows = self._ranked(
                args["query"],
                "code" if name == "search_code" else "docs",
                path,
                args.get("match") == "literal",
                prefix,
            )
            result = self._excerpts(rows, args["query"], remaining)
            result["search_scope"] = SearchScope(
                kind="code" if name == "search_code" else "docs", path=path,
                path_prefix=prefix,
                exclude_paths=self.excluded_paths,
            ).model_dump()
            if corrected:
                result["scope_correction"] = {"supplied_path": args["path"], "used_path_prefix": prefix}
            return result
        path = safe_path(args["path"])
        if path in self.excluded_paths:
            raise RepositoryError("entry_scope_conflict", "path belongs to another entry; respect the user's selected entry")
        if path not in self.reader.paths:
            raise RepositoryError("not_tracked", "path not present in snapshot")
        if name == "read_code":
            start, end = args["start_line"], args["end_line"]
            text, truncated = self.reader.read(path, 500_000)
            if truncated:
                raise RepositoryError("oversized", "file exceeds bounded source reader")
            if start > end or start > len(text.splitlines()):
                raise RepositoryError("invalid_range", "use a valid source interval")
            unit = None
            visible = [s for s in self.visible_context(self.actor) if s["path"] == path]
            if args.get("expand") == "symbol" and start <= end:
                unit = containing_unit(text, path, start, end)
                # An explicit continuation must not jump back to a previously returned prefix.
                start = start if any(s["start_line"] <= start and s["end_line"] >= unit["start_line"]
                                     for s in visible) else unit["start_line"]
                end = unit["end_line"]
            end = min(end, len(text.splitlines()))
            gaps = uncovered(start, end, visible)
            if not gaps:
                raise RepositoryError(
                    "already_read",
                    "requested range is already visible; use that source or investigate another range/test",
                )
            span, next_line = self._span(
                path,
                gaps[0]["start_line"],
                gaps[0]["end_line"],
                min(self.limits.max_read_bytes, remaining, self.limits.max_observation_bytes * 2 // 3),
            )
            return {"spans": [span], "next_line": next_line, "truncated": next_line is not None,
                    "unit": unit, "requested_end_line": args["end_line"],
                    "requested_range": {"start_line": start, "end_line": end},
                    "uncovered_ranges": uncovered(start, end, [*visible, span]),
                    "schema_version": "repomentor.tool.sources.v3"}
        if name == "trace_dependencies":
            paths, metadata = trace_dependencies(self.reader, self.reader.paths, [path])
            paths = [p for p in paths if p not in self.excluded_paths]
            return {
                "paths": paths,
                "metadata": metadata,
                "spans": [],
                "scope": "bounded static import heuristic, not runtime calls; unresolved imports omitted",
            }
        paths, metadata = find_tests(self.reader.paths, [path])
        paths = [p for p in paths if p not in self.excluded_paths]
        query = args.get("query") or PurePosixPath(path).stem
        candidates = self._ranked(query, "tests")
        result = self._excerpts(candidates, query, remaining)
        result.update(
            paths=list(dict.fromkeys([h["path"] for h in result["hits"]] + paths))[:12],
            metadata=metadata,
            scope="candidate test discovery; not executed coverage",
        )
        return result

    def step(self, action, *, actor="main"):
        self.actor = actor
        call = ToolCall.model_validate(action)
        started = self.clock()
        key = fingerprint([actor, call.tool, call.arguments])
        prior = self.state["call_ids"].get(call.call_id)
        if prior:
            if prior["action_hash"] != key:
                raise ValueError("call ID reused with different action")
            return {**copy.deepcopy(prior), "replayed": True}
        error, payload, duplicate = None, {}, None
        try:
            self.check()
            if self.state["tool_calls"] >= self.limits.max_tool_calls:
                raise HarnessError(
                    "tool_budget", "tool limit reached; answer with available evidence and gaps"
                )
            self.state["tool_calls"] += 1  # Rejected/duplicate attempts still consume the call allowance.
            if call.tool not in DEFINITIONS:
                raise HarnessError("unknown_tool", "choose a registered repository tool")
            schema = DEFINITIONS[call.tool][1]
            errors = list(jsonschema.Draft7Validator(schema).iter_errors(call.arguments))
            if errors:
                raise HarnessError("invalid_arguments", "arguments must match the declared schema")
            if key in self.state["seen"]:
                duplicate = self.state["seen"][key]
                prior_sources = self.state["call_ids"][duplicate].get("source_spans", [])
                still_visible = bool(prior_sources) and all(not uncovered(s["start_line"], s["end_line"],
                    [v for v in self.visible_context(actor) if v["path"] == s["path"]]) for s in prior_sources)
                if not prior_sources or still_visible:
                    raise HarnessError("duplicate_action", "identical action is already visible; change query/range")
                duplicate = None  # Source was compacted out: allow a bounded re-read.
            self.state["seen"][key] = call.call_id
            payload = self._execute(call)
            self.check()
        except (HarnessError, RepositoryError) as exc:
            error, payload = exc.code, {"hint": str(exc), "spans": []}
        if duplicate:
            payload["previous_call_id"] = duplicate
        observation = {
            "status": "error"
            if error
            else "empty"
            if not any(payload.get(k) for k in ("spans", "hits", "paths"))
            else "ok",
            "error_code": error,
            **payload,
        }
        source_spans = list(observation.get("spans", []))

        def render():
            # Recompute after wire trimming; never offer a cursor that skips omitted lines.
            requested = observation.get("requested_range")
            if requested is not None and source_spans:
                path = source_spans[0]["path"]
                earlier = [s for s in self.visible_context(actor) if s["path"] == path]
                gaps = uncovered(requested["start_line"], requested["end_line"], [*earlier, *source_spans])
                observation.update(uncovered_ranges=gaps, truncated=bool(gaps),
                    next_line=gaps[0]["start_line"] if gaps else None,
                    continuation={"tool": "read_code", "arguments": {"path": path,
                        "start_line": gaps[0]["start_line"], "end_line": requested["end_line"], "expand": "none"}}
                    if gaps else None)
            observation["spans"] = [
                {**{k: v for k, v in s.items() if k != "content"},
                 "lines": [f"L{i}: {line}" for i, line in enumerate(s["content"].split("\n"), s["start_line"])]}
                for s in source_spans
            ]
            return json.dumps(observation, ensure_ascii=False, separators=(",", ":"))

        visible = render()
        # Preserve useful observed source under the wire budget instead of discarding all hits.
        while len(visible.encode()) > self.limits.max_observation_bytes and source_spans:
            observation["observation_truncated"] = True
            if len(source_spans) > 1:
                source_spans.pop()
                if observation.get("hits"):
                    observation["hits"].pop()
            else:
                span = source_spans[0]
                lines = span["content"].split("\n")
                if len(lines) <= 1:
                    break
                target = observation["hits"][0] if observation.get("hits") else observation
                anchor = target.get("match_line", span["start_line"])
                trim_start = anchor - span["start_line"] > span["end_line"] - anchor
                span = {**span, "content": "\n".join(lines[1:] if trim_start else lines[:-1]),
                        "start_line": span["start_line"] + int(trim_start),
                        "end_line": span["end_line"] - int(not trim_start)}
                span["content_sha256"] = hashlib.sha256(span["content"].encode()).hexdigest()
                source_spans[0] = span
                target.update(truncated=True, next_line=span["end_line"] + 1)
                unit = target.get("unit")
                if unit:
                    target["uncovered_ranges"] = uncovered(unit["start_line"], unit["end_line"], [span])
            visible = render()
        if len(visible.encode()) > self.limits.max_observation_bytes:
            observation = {
                "status": "error",
                "error_code": "observation_budget",
                "spans": [],
                "hint": "use a smaller range or narrower query",
            }
            visible = json.dumps(observation, ensure_ascii=False, separators=(",", ":"))
            source_spans = []
        for span in source_spans:
            self.state["source_bytes"] += len(span["content"].encode())
            self.state["context"].append(span)
            self.state["visible_context"].setdefault(actor, []).append(span)
        result = {
            "action": call.model_dump(),
            "actor": actor,
            "action_hash": key,
            "observation": observation,
            "visible_json": visible,
            "source_spans": source_spans,
            "elapsed_seconds": round(self.clock() - started, 6),
            "tool_calls_used": self.state["tool_calls"],
            "source_bytes": self.state["source_bytes"],
            "replayed": False,
        }
        self.state["observations"].append(result)
        self.state["call_ids"][call.call_id] = result
        return copy.deepcopy(result)
