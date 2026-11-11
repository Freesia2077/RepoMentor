"""The three bounded discovery tools shared by the SDK and deterministic report path."""

# 实现受限符号搜索、静态依赖和关联测试定位，返回可供后续读取的候选路径。

import ast
import posixpath
import re
from pathlib import PurePosixPath

from .repository import SOURCE_EXTENSIONS, RepositoryError, SnapshotReader, is_source, is_test

TOOL_DESCRIPTIONS = {
    "search_symbols": "Search one literal identifier in indexed source. Returns paths/counts, never source text.",
    "trace_module_dependencies": "Resolve static internal imports from indexed paths. Returns paths/counts only.",
    "find_related_tests": "Find test paths by filename/directory relationships; this does not prove coverage.",
}


def search_symbols(reader: SnapshotReader, index: list[str], query: str):
    matches = []
    scanned = 0
    for path in [p for p in index if is_source(p)][:300]:
        if len(matches) >= 20 or scanned >= 1_500_000:
            break
        try:
            content, _ = reader.read(path, min(50_000, 1_500_000 - scanned))
        except RepositoryError:
            continue
        scanned += len(content.encode())
        for line, text in enumerate(content.splitlines(), 1):
            if query.strip().lower() in text.lower():
                matches.append({"path": path, "line": line})
                if len(matches) == 20:
                    break
    return list(dict.fromkeys(m["path"] for m in matches)), {"query": query, "matches": len(matches)}


# 解析仓库内的有限静态导入，外部包和动态目标以未解析信息返回。
def trace_dependencies(reader: SnapshotReader, index: list[str], seeds: list[str]):
    tracked = set(index)
    edges = []
    for seed in seeds[:6]:
        if seed not in tracked or not is_source(seed):
            continue
        try:
            content, _ = reader.read(seed, 50_000)
        except RepositoryError:
            continue
        specs = []
        for match in re.finditer(
            r"""(?:import|export)\s+(?:type\s+)?(?:[^"'`;\n]+?\s+from\s+)?["']([^"']+)["']|require\(\s*["']([^"']+)["']\s*\)|import\(\s*["']([^"']+)["']\s*\)""",
            content,
        ):
            specs.append(next(group for group in match.groups() if group))
        python_source = seed.endswith(".py")
        if python_source:
            specs = []
            try:
                tree = ast.parse(content)
            except SyntaxError:
                tree = ast.Module(body=[], type_ignores=[])
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    specs.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    module = "." * node.level + (node.module or "")
                    specs.extend(
                        module + ("." if node.module else "") + alias.name
                        for alias in node.names
                        if alias.name != "*"
                    )
                    specs.append(module)
        for spec in dict.fromkeys(specs):
            candidates = []
            if python_source:
                level = len(spec) - len(spec.lstrip("."))
                module = spec[level:].replace(".", "/")
                if level:
                    parent = posixpath.dirname(seed)
                    for _ in range(level - 1):
                        parent = posixpath.dirname(parent) if parent else ".."
                    candidates.append(posixpath.normpath(posixpath.join(parent, module)))
                else:
                    candidates.extend([module, "src/" + module])
            elif spec.startswith("."):
                base = posixpath.normpath(posixpath.join(posixpath.dirname(seed), spec))
                candidates.append(base)
                if re.search(r"\.(js|mjs|cjs)$", base, re.I):
                    candidates.append(re.sub(r"\.(js|mjs|cjs)$", "", base, flags=re.I))
            elif "." in spec:
                candidates.append(spec.replace(".", "/"))
            target = next(
                (
                    p
                    for base in candidates
                    for ext in ["", *sorted(SOURCE_EXTENSIONS)]
                    for p in [base + ext, base + "/index" + ext, base + "/__init__" + ext]
                    if p in tracked
                ),
                None,
            )
            if target:
                edges.append({"from": seed, "to": target})
            if len(edges) >= 24:
                break
        if len(edges) >= 24:
            break
    return list(dict.fromkeys(e["to"] for e in edges)), {"edges": len(edges), "seedFiles": len(seeds)}


# 按路径关系和符号提示寻找测试候选，是否有断言仍需后续读取原文核对。
def find_tests(index: list[str], sources: list[str]):
    scored = []
    for test in filter(is_test, index):
        score = 0
        for source in sources:
            source_base = PurePosixPath(source).stem
            directory = posixpath.basename(posixpath.dirname(source))
            if source_base.lower() in {"index", "main", "mod", "lib"}:
                source_base = directory
            test_base = re.sub(r"\.(test|spec)$", "", PurePosixPath(test).stem, flags=re.I)
            if source_base and source_base.lower() in test_base.lower():
                score += 10
            if directory and directory != "." and directory.lower() in test_base.lower():
                score += 8
            source_parts = set(posixpath.dirname(source).lower().split("/"))
            score += sum(p in source_parts for p in test.lower().split("/"))
        if score:
            scored.append((score, test))
    paths = [p for _, p in sorted(scored, key=lambda row: (-row[0], row[1]))[:12]]
    return paths, {"tests": len(paths), "seedFiles": len(sources)}
