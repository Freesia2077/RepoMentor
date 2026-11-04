"""Bounded static declaration navigation; locations are not observed source evidence."""

import hashlib
import re
from bisect import bisect_right
from pathlib import PurePosixPath

from .chunking import syntax_units
from .repository import is_source, is_test


def declarations(text, path):
    """Named declarations plus simple JS factory bindings; no evaluation or call-graph claims."""
    units, parser = syntax_units(text, PurePosixPath(path).suffix)
    if parser != "tree_sitter":
        return []
    result = [{"symbol": symbol, "start_line": start, "end_line": end, "kind": kind}
              for start, end, symbol, kind in units if kind != "call_expression"]
    if PurePosixPath(path).suffix in (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx"):
        from tree_sitter import Language, Parser

        if PurePosixPath(path).suffix in (".ts", ".tsx"):
            import tree_sitter_typescript as grammar

            language = grammar.language_tsx() if path.endswith(".tsx") else grammar.language_typescript()
        else:
            import tree_sitter_javascript as grammar

            language = grammar.language()
        source = text.encode()
        tree = Parser(Language(language)).parse(source)
        line_starts = [0] + [i + 1 for i, byte in enumerate(source) if byte == 10]
        for statement in tree.root_node.named_children:
            if statement.type == "export_statement":
                statement = statement.child_by_field_name("declaration")
            if statement is None or statement.type not in ("lexical_declaration", "variable_declaration"):
                continue
            for node in statement.named_children:
                name, value = node.child_by_field_name("name"), node.child_by_field_name("value")
                if not name or name.type != "identifier" or not value or value.type != "call_expression":
                    continue
                callee = value.child_by_field_name("function")
                if callee and callee.type == "identifier":
                    result.append({"symbol": source[name.start_byte:name.end_byte].decode(),
                        "start_line": bisect_right(line_starts, node.start_byte),
                        "end_line": bisect_right(line_starts, node.end_byte - 1),
                        "kind": "factory_binding_candidate",
                        "local_factory": source[callee.start_byte:callee.end_byte].decode()})
    return result


def symbol_navigation(reader, question, focus, previous_symbol=None, *, max_files=80, max_bytes=2_000_000):
    identifiers = set(re.findall(r"[A-Za-z_$][A-Za-z_0-9$]*(?:\.[A-Za-z_$][A-Za-z_0-9$]*)*", question))
    followup = bool(re.search(r"它|该函数|那么|继续|\b(?:it|that|those)\b", question, re.I))
    if followup and previous_symbol:
        identifiers.add(previous_symbol)
    preferred = focus.get("entry_paths", [])
    excluded = focus.get("excluded_paths", [])
    explicit_paths = [p for p in reader.paths if p in question]
    paths = sorted((p for p in reader.paths if is_source(p) and not is_test(p) and p not in excluded),
                   key=lambda p: (p not in explicit_paths, p not in preferred, p))
    matches, used, scanned = [], 0, 0
    for path in paths[:max_files]:
        if used >= max_bytes:
            break
        text, truncated = reader.read(path, min(500_000, max_bytes - used))
        used += len(text.encode())
        scanned += 1
        if truncated:
            continue
        rows = declarations(text, path)
        digest = hashlib.sha256(text.encode()).hexdigest()
        for row in rows:
            if row["symbol"] not in identifiers and row["symbol"].split(".")[-1] not in identifiers:
                continue
            match = {**row, "path": path, "file_sha256": digest}
            if row.get("local_factory"):
                match["factory_candidates"] = [
                    {"path": path, **r} for r in rows if r["symbol"] == row["local_factory"]
                    and r["kind"] in ("function_declaration", "variable_declarator")
                ][:2]
            matches.append(match)
    # Exact paths and entry roots win; retain ambiguity instead of asserting a unique runtime binding.
    matches.sort(key=lambda r: (r["path"] not in explicit_paths, r["path"] not in preferred,
                               r["symbol"] not in identifiers, r["path"], r["start_line"]))
    selected = matches[:6]
    symbols = {r["symbol"] for r in selected}
    return {"policy": "static-symbol-navigation-v1", "targets": selected,
            "current_symbol": next(iter(symbols)) if len(symbols) == 1 else None,
            "scanned_files": scanned, "scanned_bytes": used,
            "omitted_matches": max(0, len(matches) - len(selected)),
            "search_incomplete": scanned < len(paths), "source_facts": False,
            "scope": "Static locations only. Read declaration and factory candidates with read_code before claims. "
                     "Simple factory candidates are not a resolved runtime call graph."}
