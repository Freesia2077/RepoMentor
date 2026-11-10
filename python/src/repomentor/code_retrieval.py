"""Shared code-aware candidate protocol, symbol channel and bounded initial retrieval."""

import hashlib
import re
from pathlib import Path

from .code_navigation import SymbolIndex, explicit_source_paths, file_role
from .retrieval_scope import SearchScope
from .runtimes.chat import fingerprint
from .source_windows import source_span, uncovered


class CodeRetrievalIndex:
    def __init__(self, reader, base, cache_dir, *, code_aware=True):
        self.reader, self.base, self.code_aware = reader, base, code_aware
        self.settings, self.chunks, self.record = base.settings, base.chunks, base.record
        self.symbols = SymbolIndex.build(reader, {c.path for c in self.chunks}, cache_dir)
        implementation = hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        self.index_id = fingerprint(
            ["code-retrieval-v2", base.index_id, self.symbols.index_id, implementation, code_aware]
        )[:24]

    def __getattr__(self, name):
        return getattr(self.base, name)

    def search(self, question, *, k=None, scope=None):
        scope = scope or SearchScope()
        result = self.base.search(question, k=k, scope=scope)
        if not self.code_aware:
            return {
                **result,
                "index_id": self.index_id,
                "base_index_id": self.base.index_id,
                "policy": "plain-retrieval-v1",
            }
        excluded = [p for p in {d["path"] for d in self.symbols.definitions} if not scope.matches(p)]
        targets = self.symbols.lookup(question, excluded=excluded)["targets"]
        # Weak name matches can guide navigation, but must not displace ranked evidence.
        targets = [
            t
            for t in targets
            if t["match_priority"] >= 4
            or t["explicit_path"]
            or question.strip(" `") == t["symbol"].rsplit(".", 1)[-1]
        ]
        explicit = explicit_source_paths(question, {c.path for c in self.chunks if scope.matches(c.path)})
        exact = [
            c
            for c in self.chunks
            if scope.matches(c.path)
            and (file_role(c.path) != "documentation" or c.path in explicit)
            and any(t["path"] == c.path and c.start_line <= t["start_line"] <= c.end_line for t in targets)
        ]
        related_by_id = {}
        for target in targets:
            if target.get("role") != "implementation" or not scope.matches(target["path"]):
                continue
            for row in self.symbols.lookup(
                target["symbol"], path=target["path"], relation="callees", excluded=excluded
            )["targets"]:
                if scope.matches(row["path"]):
                    priority = max(1, target.get("match_priority", 1) - 1)
                    previous = related_by_id.get(row["id"], {})
                    related_by_id[row["id"]] = {
                        **row,
                        "dependency_priority": max(priority, previous.get("dependency_priority", 0)),
                    }
        related = sorted(
            related_by_id.values(), key=lambda t: (-t["dependency_priority"], t["path"], t["start_line"])
        )[:6]
        one_hop = [
            c
            for c in self.chunks
            if scope.matches(c.path)
            and any(
                t["path"] == c.path and c.start_line <= t["start_line"] <= c.end_line for t in related[:6]
            )
        ]
        rows = {r["chunk"]["chunk_id"]: {**r, "channels": ["retrieval"]} for r in result["candidates"]}
        # Retrieve inside explicitly named files as well as globally. File names
        # in a mixed implementation/test question must not recall only imports.
        for path in sorted(explicit):
            path_scope = SearchScope(**{**scope.model_dump(), "path": path})
            path_query = question + (" return value callback" if "返回值" in question else "")
            scoped = [
                r
                for r in self.base.search(path_query, k=k, scope=path_scope)["candidates"]
                if r["chunk"]["path"] == path
            ]
            for i, item in enumerate(scoped):
                row = rows.setdefault(item["chunk"]["chunk_id"], {**item, "channels": ["path_retrieval"]})
                row["path_rank"] = i

        def anchored_row(chunk, line):
            # One chunk can contain several method declarations. Keep each
            # anchor so _excerpts can select the distinct containing units.
            key = chunk.chunk_id
            base = rows.get(key)
            if base is not None and base.get("anchor_line", line) != line:
                key += "@" + str(line)
            row = rows.setdefault(key, {"chunk": chunk.model_dump(), "score": 0, "channels": []})
            row["anchor_line"] = line
            return row

        for c in exact:
            anchors = [
                t for t in targets if t["path"] == c.path and c.start_line <= t["start_line"] <= c.end_line
            ]
            for anchor in sorted(anchors, key=lambda t: -t.get("match_priority", 0)):
                row = anchored_row(c, anchor["start_line"])
                row["channels"].append("symbol")
                row["symbol_priority"] = max(row.get("symbol_priority", 0), anchor.get("match_priority", 0))
        for c in one_hop:
            for anchor in (
                t for t in related if t["path"] == c.path and c.start_line <= t["start_line"] <= c.end_line
            ):
                row = anchored_row(c, anchor["start_line"])
                row["channels"].append("static_callee")
                row["dependency_priority"] = max(
                    row.get("dependency_priority", 0), anchor["dependency_priority"]
                )
        for c in self.chunks:
            if c.path in explicit:
                if not any(r["chunk"]["chunk_id"] == c.chunk_id for r in rows.values()):
                    rows[c.chunk_id] = {"chunk": c.model_dump(), "score": 0, "channels": []}
            titles = re.findall(r"(?:test|it):['\"]([^'\"]+)['\"]", c.symbol or "")
            if scope.matches(c.path) and any(
                len(re.findall(r"[A-Za-z_]+", title)) >= 3 and title.casefold() in question.casefold()
                for title in titles
            ):
                row = rows.setdefault(c.chunk_id, {"chunk": c.model_dump(), "score": 0, "channels": []})
                row["named_test"] = True
                row["anchor_line"] = c.start_line
        for path in explicit:
            path_rows = [r for r in rows.values() if r["chunk"]["path"] == path]
            if path_rows and not any("path_rank" in r for r in path_rows):
                min(path_rows, key=lambda r: r["chunk"]["start_line"])["path_rank"] = 0
        for row in rows.values():
            row["explicit_path"] = row["chunk"]["path"] in explicit
        requested = (
            "example"
            if re.search(r"示例|例子|\bexamples?\b", question, re.I)
            else "test"
            if re.search(r"测试|断言|\btests?\b", question, re.I)
            else "implementation"
        )
        rank = {r["chunk"]["chunk_id"]: i for i, r in enumerate(result["candidates"])}
        for row in rows.values():
            cid = row["chunk"]["chunk_id"]
            role = file_role(row["chunk"]["path"])
            row["role"] = role
            # Exact symbols are a separate recall channel; scope restrictions still apply before selection.
            row["rank_score"] = 1 / (60 + rank[cid] + 1) if cid in rank else 0
            row["rank_score"] += max(row.get("symbol_priority", 0), row.get("dependency_priority", 0)) / 61
            row["rank_score"] *= 1.1 if role == requested else 0.8 if role == "example" else 1
        candidates = sorted(
            rows.values(),
            key=lambda r: (
                not (
                    r.get("path_rank") == 0 or r.get("named_test", False) or r.get("symbol_priority", 0) >= 5
                ),
                not r.get("named_test", False),
                r.get("path_rank") != 0,
                not r.get("explicit_path", False),
                r.get("path_rank", 100),
                -r["rank_score"],
                r["chunk"]["path"],
                r["chunk"]["start_line"],
            ),
        )
        return {
            **result,
            "index_id": self.index_id,
            "base_index_id": self.base.index_id,
            "policy": "code-retrieval-v1",
            "candidates": candidates[: k or self.settings.candidate_k],
            "symbol_channel_targets": targets,
            "static_callee_targets": related[:6],
            "preferred_role": requested,
        }

    def initial_sources(self, question, *, excluded=()):
        from .agent_tools import RepositoryEnvironment

        env = RepositoryEnvironment(self.reader, self)
        env.excluded_paths = list(excluded)
        try:
            search = self.search(question, scope=SearchScope(exclude_paths=list(excluded)))
            # Preserve an explicitly asked no-argument boundary in test recall;
            # a full prose query can rank ordinary argument-taking tests ahead.
            zero_calls = set(re.findall(r"(?<![A-Za-z_0-9])([A-Za-z_][A-Za-z_0-9]*)\s*\(\s*\)", question))
            targets = [
                t
                for t in search.get("symbol_channel_targets", [])
                if t["role"] == "implementation"
                and t.get("match_priority", 0) >= 4
                and t["path"].endswith(".py")
                and t["symbol"].rsplit(".", 1)[-1] in zero_calls
            ]
            if search.get("preferred_role") == "test" and len(targets) == 1:
                query = targets[0]["symbol"].rsplit(".", 1)[-1] + "()"
                tests = env._ranked(query, "tests", literal=True)
                candidates = search["candidates"]
                if tests and not any(
                    r["chunk"]["chunk_id"] == tests[0]["chunk"]["chunk_id"] for r in candidates
                ):
                    at = next(
                        (
                            i
                            for i, r in enumerate(candidates)
                            if r.get("role") == "test"
                            and not r.get("explicit_path")
                            and not r.get("named_test")
                        ),
                        len(candidates),
                    )
                    candidates = [
                        *candidates[:at],
                        {**tests[0], "channels": ["literal_test"], "role": "test"},
                        *candidates[at:],
                    ][: self.settings.candidate_k]
                    search = {**search, "candidates": candidates, "test_query": query}
            result = env._excerpts(
                search["candidates"],
                question,
                self.settings.initial_source_bytes,
                max_units=self.settings.top_k,
                expand_selected=True,
            )
            # An imported alias is evidence for the selected call, not another
            # retrieval unit. Keep the original hits and attach the complete raw
            # declaration only when its call is visible and spare bytes suffice.
            owners = {
                (t["path"], t["symbol"])
                for t in search.get("symbol_channel_targets", [])
                if t["role"] == "implementation" and t.get("match_priority", 0) >= 4
            }
            callees = {t["id"] for t in search.get("static_callee_targets", [])}
            selected = {(h["path"], h["unit"]["symbol"]): h for h in result["hits"]}
            remaining = self.settings.initial_source_bytes - sum(
                len(s["content"].encode()) for s in result["spans"]
            )
            for relation in self.symbols.relations:
                binding = relation.get("import_binding")
                owner = (relation["path"], relation["owner"])
                if (
                    not binding
                    or owner not in owners
                    or owner not in selected
                    or relation["target_id"] not in callees
                ):
                    continue
                visible = [s for s in result["spans"] if s["path"] == relation["path"]]
                if not any(s["start_line"] <= relation["line"] <= s["end_line"] for s in visible):
                    continue
                missing = uncovered(binding["start_line"], binding["end_line"], visible)
                if not missing:
                    continue
                text, truncated = self.reader.read(binding["path"], 500_000)
                if truncated:
                    continue
                parts = [
                    source_span(binding["path"], text.splitlines(), r["start_line"], r["end_line"])
                    for r in missing
                ]
                size = sum(len(s["content"].encode()) for s in parts)
                if size <= remaining:
                    result["spans"].extend(parts)
                    selected[owner].setdefault("dependency_sources", []).append(
                        {**binding, "kind": "import_binding"}
                    )
                    remaining -= size
        finally:
            if hasattr(self.base, "release_models"):
                self.base.release_models()
        return {"sources": result["spans"], "retrieval": search, "hits": result["hits"]}
