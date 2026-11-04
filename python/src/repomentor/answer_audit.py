"""Offline source checks and review packets, never an automatic entailment judge.

Snapshot truth and source visible to the model are deliberately separate. This
module neither runs an agent nor executes commands from a generated answer.
"""

import hashlib
import json
import posixpath
import re
from bisect import bisect_right
from pathlib import PurePosixPath

from .repository import RepositoryError
from .source_windows import verify_spans

POLICY = "repomentor.answer-audit.v1"
MAX_FILE_BYTES = 500_000
LINE_REF = re.compile(r"L([1-9]\d*)(?:\s*[-–]\s*L?([1-9]\d*))?")


def line_hints(text):
    """Accept only an entire line-reference list; prose numbers are not citations."""
    parts = re.split(r"\s*[,，;；]\s*", text.strip())
    matches = [LINE_REF.fullmatch(part) for part in parts]
    if not matches or not all(matches):
        return None
    ranges = [(int(m[1]), int(m[2] or m[1])) for m in matches]
    return ranges if all(start <= end for start, end in ranges) else None


def visible(context, path, start, end):
    cursor = start
    for span in sorted((s for s in context if s["path"] == path), key=lambda s: s["start_line"]):
        if span["start_line"] > cursor:
            break
        cursor = max(cursor, span["end_line"] + 1)
        if cursor > end:
            return True
    return False


def reference_audit(reader, context, ref):
    path = ref["path"]
    result = {"reference": ref, "entailment": "needs_review", "excerpts": []}
    if path not in reader.paths:
        return {**result, "status": "path_not_in_snapshot"}
    ranges = ([(ref["start_line"], ref["end_line"])] if "start_line" in ref
              else line_hints(ref.get("supports", "")))
    if ranges is None:
        return {**result, "status": "unparsed_locator"}
    content, truncated = reader.read(path, MAX_FILE_BYTES)
    if truncated:
        return {**result, "status": "source_too_large"}
    lines = content.splitlines()
    if any(not 1 <= start <= end <= len(lines) for start, end in ranges):
        return {**result, "status": "invalid_line_range"}
    for start, end in ranges:
        selected = "\n".join(lines[start - 1:end])
        result["excerpts"].append({"path": path, "start_line": start, "end_line": end,
            "content": selected, "content_sha256": hashlib.sha256(selected.encode()).hexdigest(),
            "visible_to_model": visible(context, path, start, end)})
    return {**result, "status": "visible_range" if all(s["visible_to_model"] for s in result["excerpts"])
            else "range_not_visible"}


def _esm_imports(content, path):
    """AST-based literal ESM imports/re-exports only; incomplete forms stay unknown."""
    from tree_sitter import Language, Parser

    suffix = PurePosixPath(path).suffix
    if suffix in (".ts", ".tsx"):
        import tree_sitter_typescript as grammar

        language = grammar.language_tsx() if suffix == ".tsx" else grammar.language_typescript()
    elif suffix in (".js", ".jsx", ".mjs", ".cjs"):
        import tree_sitter_javascript as grammar

        language = grammar.language()
    else:
        return [], False
    source = content.encode()
    root = Parser(Language(language)).parse(source).root_node
    if root.has_error:
        return [], False
    imports, complete, stack = [], True, [root]
    while stack:
        node = stack.pop()
        if node.type in ("import_statement", "export_statement"):
            spec = node.child_by_field_name("source")
            if spec:
                value = source[spec.start_byte + 1:spec.end_byte - 1].decode()
                # Escaped strings, packages and aliases need a resolver beyond this audit.
                if "\\" in value or not value.startswith("."):
                    complete = False
                else:
                    starts = [0] + [i + 1 for i, byte in enumerate(source) if byte == 10]
                    imports.append({"specifier": value, "start_line": bisect_right(starts, node.start_byte),
                                    "end_line": bisect_right(starts, node.end_byte - 1)})
        if node.type == "call_expression":
            callee = node.child_by_field_name("function")
            name = source[callee.start_byte:callee.end_byte].decode() if callee else ""
            if name in ("require", "import", "module.require", "require.resolve"):
                complete = False
        if node.type == "import_require_clause":
            complete = False
        stack.extend(node.named_children)
    return imports, complete


def dependency_audit(reader, context, graph):
    """Graph means direct static local imports, not calls or type references.

    Absence is reported only for fully parsed files without unresolved import
    forms. Empty graphs are not evidence of dependency completeness.
    """
    rows = []
    tracked, cache = set(reader.paths), {}
    for source, targets in graph.items():
        for target in targets:
            row = {"from": source, "to": target, "relation": "direct_static_local_import"}
            if source not in tracked or target not in tracked:
                rows.append({**row, "status": "unknown", "reason": "directory_or_missing_file"})
                continue
            if source not in cache:
                content, truncated = reader.read(source, MAX_FILE_BYTES)
                imports, complete = _esm_imports(content, source) if not truncated else ([], False)
                resolved = []
                for item in imports:
                    base = posixpath.normpath(posixpath.join(posixpath.dirname(source), item["specifier"]))
                    candidates = {base}
                    stems = {base}
                    if base.endswith((".js", ".mjs", ".cjs")):
                        stems.add(base.rsplit(".", 1)[0])
                    for stem in stems:
                        for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
                            candidates.update((stem + ext, stem + "/index" + ext))
                    found = sorted(candidates & tracked)
                    if len(found) != 1:
                        complete = False
                    else:
                        resolved.append({**item, "target": found[0]})
                cache[source] = resolved, complete
            resolved, complete = cache[source]
            evidence = [s for s in resolved if s["target"] == target]
            rows.append({**row, "status": "supported_static_import" if evidence else
                         "not_a_static_import" if complete else "unknown",
                         "evidence": evidence,
                         "source_visible_to_model": any(visible(context, source, s["start_line"], s["end_line"])
                                                        for s in evidence)})
    return rows


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _object_entries(text, offset):
    """Offsets into already validated JSON, used to locate exact script entries."""
    decoder = json.JSONDecoder()
    offset += 1
    while True:
        while text[offset].isspace() or text[offset] == ",":
            offset += 1
        if text[offset] == "}":
            return
        start = offset
        key, offset = decoder.raw_decode(text, offset)
        while text[offset].isspace() or text[offset] == ":":
            offset += 1
        value_start = offset
        _, offset = decoder.raw_decode(text, offset)
        yield key, start, value_start, offset


def command_audit(reader, context, setup, package_path="package.json"):
    """Check simple root npm script declarations. No shell evaluation or execution."""
    scripts, locations, package_status = {}, {}, "missing_package"
    if package_path in reader.paths:
        text, truncated = reader.read(package_path, MAX_FILE_BYTES)
        package_status = "source_too_large" if truncated else "invalid_package"
        if not truncated:
            try:
                package = json.loads(text, object_pairs_hook=_unique_object)
                scripts = package.get("scripts", {})
                if not isinstance(scripts, dict) or any(not isinstance(v, str) for v in scripts.values()):
                    raise ValueError("invalid scripts")
                for key, _, start, _ in _object_entries(text, len(text) - len(text.lstrip())):
                    if key == "scripts":
                        for name, begin, _, end in _object_entries(text, start):
                            locations[name] = (text.count("\n", 0, begin) + 1, text.count("\n", 0, end - 1) + 1)
                package_status = "parsed"
            except (ValueError, AttributeError):
                scripts, locations = {}, {}
    result = []
    for field in ("build", "test", "lint"):
        command = setup.get(field)
        if not command:
            continue
        match = re.fullmatch(r"npm\s+(?:(?:run|run-script)\s+([A-Za-z0-9_.:-]+)|(test|start|stop|restart))", command.strip())
        row = {"field": field, "command": command, "execution": "not_run", "package_path": package_path}
        if not match:
            result.append({**row, "status": "unknown", "reason": "unsupported_command_form"})
            continue
        name = match[1] or match[2]
        status = "declared_script" if name in scripts else "not_declared" if package_status == "parsed" else "unknown"
        # npm start/test defaults and lifecycle behavior are outside this declaration check.
        row.update(status=status, script=name, package_status=package_status, source_visible_to_model=False)
        if name in locations:
            start, end = locations[name]
            row.update(start_line=start, end_line=end, script_body=scripts[name],
                       source_visible_to_model=visible(context, package_path, start, end))
        result.append(row)
    return result


def audit_answer(reader, answer, context):
    """Fail closed on altered evidence before preparing any apparent source checks."""
    try:
        verify_spans(reader, context)
    except (ValueError, RepositoryError, KeyError) as exc:
        return {"policy": POLICY, "source_integrity": "invalid", "error": str(exc),
                "semantic_correctness": "not_scored", "review_required": True}
    base = {"policy": POLICY, "source_integrity": "verified", "semantic_correctness": "not_scored",
            "review_required": True, "human_approved": False, "training_eligible": False}
    if answer is None:
        return {**base, "answer_status": "missing", "claims": [], "dependency_edges": [], "commands": []}
    claims = answer.get("evidenceClaims")
    if claims is None:
        claims = [{"claim": answer.get("answer", ""), "evidence": answer.get("citations", [])}]
    reviewed = [{"claim_id": f"claim-{i + 1}", "text": claim["claim"],
                 "references": [reference_audit(reader, context, ref) for ref in claim["evidence"]],
                 "support": "needs_review", "factual_correctness": "needs_review",
                 "atomicity": "not_checked"} for i, claim in enumerate(claims)]
    return {**base, "answer_status": "present", "claims": reviewed,
            "dependency_edges": dependency_audit(reader, context, answer.get("dependencyGraph", {})),
            "commands": command_audit(reader, context, answer.get("contributionSetup", {})),
            "full_answer": answer,
            "review_dimensions": ["claim_support", "factual_correctness", "contradictions", "entry_scope",
                                  "completeness", "abstention", "uncited_free_text"],
            "limits": "Visible ranges do not entail a claim. Full answer and compound claims require review. "
                      "Static edges exclude call graphs and runtime dependencies; command declarations do not prove execution."}
