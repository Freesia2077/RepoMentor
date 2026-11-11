"""Deterministic report provenance checks, not a semantic correctness judge."""

# 核对结构化报告里的路径与显式行号引用，并保留无效位置的诊断结果。

import re

from .answer_audit import visible
from .repository import RepositoryError, safe_path


# 仅解析有明确文件作用域的 Lx-Ly 引用，再对照该文件的可见行段核验位置。
def inspect_report_locations(reader, value, context):
    """Check explicit Lx-Ly references in path-scoped supports, not their meaning."""
    issues, checked, contents = [], 0, {}
    for i, claim in enumerate(value.get("evidenceClaims", [])):
        for j, ref in enumerate(claim.get("evidence", [])):
            path = ref["path"]
            field = f"evidenceClaims[{i}].evidence[{j}].supports"
            for match in re.finditer(r"\bL(\d+)(?:\s*[-–—]\s*L?(\d+))?\b", ref.get("supports", "")):
                start, end = int(match[1]), int(match[2] or match[1])
                checked += 1
                reason = None
                if path not in reader.paths:
                    reason = "not_in_snapshot"
                else:
                    if path not in contents:
                        contents[path] = reader.read(path, 500_000)
                    text, truncated = contents[path]
                    if start < 1 or end < start:
                        reason = "invalid_range"
                    elif truncated:
                        reason = "range_unverified_file_limit"
                    elif end > len(text.splitlines()):
                        reason = "out_of_bounds"
                    elif not visible(context, path, start, end):
                        reason = "not_visible_to_finalizer"
                if reason:
                    issues.append(
                        {"field": field, "path": path, "start_line": start, "end_line": end, "reason": reason}
                    )
    return {
        "version": "report-explicit-locations-v1",
        "checked_ranges": checked,
        "issues": issues,
        "passed": not issues,
        "semantic_correctness": "not_scored",
        "limits": "Only explicit Lx/Lx-Ly in path-scoped evidence supports; other prose still needs review.",
    }


def validate_report_paths(value, context, tracked_paths):
    if "evidenceCoverage" not in value:
        return None
    observed, tracked = {s["path"] for s in context}, set(tracked_paths)
    issues = []

    def check(path, field, directory=False):
        try:
            canonical = safe_path(path.rstrip("/") if directory else path)
        except (RepositoryError, ValueError):
            issues.append({"field": field, "path": path, "reason": "invalid_path"})
            return
        exists = canonical in tracked or (directory and any(p.startswith(canonical + "/") for p in tracked))
        seen = canonical in observed or (directory and any(p.startswith(canonical + "/") for p in observed))
        if not exists or not seen:
            issues.append(
                {"field": field, "path": path, "reason": "not_in_snapshot" if not exists else "not_observed"}
            )

    for name, field in (
        ("entryPoints", "file"),
        ("readingPath", "file"),
        ("entryFiles", "file"),
        ("moduleMap", "path"),
    ):
        for i, row in enumerate(value.get(name, [])):
            check(row[field], f"{name}[{i}].{field}", directory=name == "moduleMap")
    for i, path in enumerate(value["evidenceCoverage"]["examinedFiles"]):
        check(path, f"evidenceCoverage.examinedFiles[{i}]")
    for i, claim in enumerate(value["evidenceClaims"]):
        if not claim["evidence"]:
            issues.append({"field": f"evidenceClaims[{i}]", "reason": "missing_source"})
        for j, ref in enumerate(claim["evidence"]):
            check(ref["path"], f"evidenceClaims[{i}].evidence[{j}].path")
    # This graph describes local source paths, not unresolved external package names.
    for path, dependencies in value.get("dependencyGraph", {}).items():
        check(path, "dependencyGraph.key", directory=True)
        for dependency in dependencies:
            check(dependency, f"dependencyGraph[{path}]", directory=True)
    if not observed:
        positive = any(
            value.get(k)
            for k in (
                "entryPoints",
                "moduleMap",
                "readingPath",
                "keyPatterns",
                "codeConventions",
                "goodFirstIssues",
                "entryFiles",
                "evidenceClaims",
                "dependencyGraph",
            )
        ) or any(value.get("contributionSetup", {}).values())
        if positive or not value["evidenceCoverage"]["gaps"]:
            issues.append({"field": "evidenceCoverage", "reason": "no_source_context"})
    return {
        "policy": "report-path-provenance-v1",
        "passed": not issues,
        "issues": issues,
        "semantic_correctness": "not_scored",
        "limits": "Valid paths do not prove claims, graph edges or commands; free-text fields need review.",
    }
