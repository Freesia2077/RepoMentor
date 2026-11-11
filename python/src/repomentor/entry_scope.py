"""Conservative, inspectable entry selection from tracked package metadata and user intent."""

# 识别用户指定的入口与运行环境，把 Node、浏览器等入口差异传递给检索和取证范围。

import hashlib
import json
import re

from .repository import RepositoryError, is_source, is_test, safe_path
from .tools import trace_dependencies


def entry_catalog(reader):
    entries, provenance = {}, []
    if "package.json" not in reader.paths:
        return entries, provenance
    text, truncated = reader.read("package.json", 100_000)
    if truncated:
        return entries, provenance
    try:
        manifest = json.loads(text)
    except (ValueError, TypeError):
        return entries, provenance
    if not isinstance(manifest, dict):
        return entries, provenance

    def target(value, condition):
        if isinstance(value, str):
            try:
                path = safe_path(value.removeprefix("./"))
            except RepositoryError:
                return None
            return path if path in reader.paths else None
        if isinstance(value, dict):
            for key in (condition, "import", "require", "default"):
                if key in value and (path := target(value[key], condition)):
                    return path
        # Array fallbacks, wildcard exports and package aliases are deliberately unresolved.
        return None

    exports = manifest.get("exports", {})
    root = (
        exports.get(".", {})
        if isinstance(exports, dict) and any(k.startswith(".") for k in exports)
        else exports
    )
    for label in ("node", "browser"):
        source = root
        if label == "browser" and not (isinstance(root, dict) and "browser" in root):
            source = manifest.get("browser")
        path = target(source, label) or (target(manifest.get("main"), label) if label == "node" else None)
        if path:
            entries[label] = [path]
    if isinstance(exports, dict):
        for label, value in exports.items():
            if label.startswith("./") and (path := target(value, "node")) and is_source(path):
                entries[label[2:]] = [path]
    bins = manifest.get("bin", {})
    bins = {"bin": bins} if isinstance(bins, str) else bins
    if isinstance(bins, dict):
        paths = sorted({p for value in bins.values() if (p := target(value, "node"))})
        if paths:
            entries["cli"] = paths
    provenance.append(
        {
            "path": "package.json",
            "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "basis": "bounded static package exports/main/browser/bin; not full runtime resolution",
        }
    )
    return entries, provenance


# 根据明确入口和用户问题确定范围，使不同运行环境的同名接口分开取证。
def resolve_entry(reader, question, previous=None):
    entries, provenance = entry_catalog(reader)
    labels = []
    for label in entries:
        pattern = r"(?<![\w/-])" + re.escape(label) + r"(?![\w/-])"
        aliases = pattern + (r"|浏览器" if label == "browser" else "")
        affirmed = False
        for match in re.finditer(aliases, question, re.I):
            # A warning such as "不要套用 Node" does not select the Node entry.
            prefix = re.split(r"[，,。；;!?！？\n]|而是|而应|\bbut\b", question[: match.start()], flags=re.I)[
                -1
            ]
            negated = re.search(
                r"(?:不要|不应|不能|别|勿|并非|而非|不是|不使用|不采用)[^，,。；;!?！？\n]{0,32}$"
                r"|\b(?:not|without|rather than|instead of|do not|don't)\s+(?:use\s+|the\s+|apply\s+)*$",
                prefix,
                re.I,
            )
            if not negated:
                affirmed = True
        if affirmed:
            labels.append(label)
    # A comparison needs both implementations. Explicit switches clear stale focus.
    switch = bool(re.search(r"切换|换到|改为|switch|instead", question, re.I))
    label = labels[0] if len(labels) == 1 else None
    if not labels and not switch and re.search(r"它|这个|该|那么|继续|对应|\b(it|that)\b", question, re.I):
        label = previous if previous in entries else None
    excluded, selected, tests = set(), set(entries.get(label, [])), []
    explicit_tests = [p for p in reader.paths if is_test(p) and p in question]
    if label:
        other = set(p for k, paths in entries.items() if k != label for p in paths) - selected
        # Shared static imports remain usable even if also exported separately.
        for path in tuple(selected):
            selected.update(trace_dependencies(reader, reader.paths, [path])[0])
        excluded = other - selected
        for path in sorted(p for p in reader.paths if is_test(p))[:150]:
            dependencies = set(trace_dependencies(reader, reader.paths, [path])[0])
            if dependencies & selected:
                tests.append(path)
            elif dependencies & excluded:
                excluded.add(path)
        if explicit_tests:
            # Respect tests explicitly named by the user; this is not a gold-driven filter.
            excluded.update(p for p in reader.paths if is_test(p) and p not in explicit_tests)
            tests = explicit_tests
    return {
        "label": label,
        "entry_paths": entries.get(label, []),
        "related_test_candidates": tests[:12],
        "explicit_test_paths": explicit_tests,
        "excluded_paths": sorted(excluded),
        "catalog": entries,
        "provenance": provenance,
        "source_facts": False,
        "policy": "static-entry-scope-v2",
    }
