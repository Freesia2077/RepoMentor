"""Exact source windows: keep the matched line and report every omitted interval."""

# 统一处理原始行号、连续窗口、未读区间和引用校验，保证检索与工具展示同一份源码。

import hashlib


def source_span(path, lines, start, end):
    content = "\n".join(lines[start - 1 : end])
    return {
        "path": path,
        "start_line": start,
        "end_line": end,
        "content": content,
        "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
    }


# 对请求区间做逐段差集，得到真正未读的行号范围，支持去重与精确续读。
def uncovered(start, end, spans):
    cursor, result = start, []
    for span in sorted(spans, key=lambda s: s["start_line"]):
        if span["end_line"] < cursor or span["start_line"] > end:
            continue
        if span["start_line"] > cursor:
            result.append({"start_line": cursor, "end_line": min(end, span["start_line"] - 1)})
        cursor = max(cursor, span["end_line"] + 1)
    if cursor <= end:
        result.append({"start_line": cursor, "end_line": end})
    return result


# 围绕命中行向两侧扩展，字节和行数上限同时生效，保证返回窗口仍含命中。
def anchored_window(lines, start, end, anchor, maximum):
    """Grow around the hit, respecting byte and line bounds. Never truncate away the hit."""
    if len(lines[anchor - 1].encode()) > maximum:
        return None
    left = right = anchor
    used = len(lines[anchor - 1].encode())
    # Prefer nearby preceding conditions, then the following body; include a whole short unit.
    while left > start or right < end:
        changed = False
        for direction in (-1, 1):
            candidate = left - 1 if direction == -1 else right + 1
            if candidate < start or candidate > end or right - left + 1 >= 100:
                continue
            size = len(lines[candidate - 1].encode()) + 1
            if used + size > maximum:
                continue
            used += size
            if direction == -1:
                left = candidate
            else:
                right = candidate
            changed = True
        if not changed:
            break
    return left, right


def render_sources(context):
    """Render exact raw ranges with line numbers; never change their stored source hashes."""
    return [
        {
            "path": span["path"],
            "start_line": span["start_line"],
            "end_line": span["end_line"],
            "content": "\n".join(
                f"L{i}: {line}" for i, line in enumerate(span["content"].split("\n"), span["start_line"])
            ),
        }
        for span in context
    ]


def citation_validity(answer, context):
    if answer is None:
        return None
    if "citations" not in answer:
        allowed = {span["path"] for span in context}
        return all(
            ref["path"] in allowed for claim in answer.get("evidenceClaims", []) for ref in claim["evidence"]
        )
    for ref in answer["citations"]:
        cursor = ref["start_line"]
        if cursor < 1 or cursor > ref["end_line"]:
            return False
        for span in sorted((s for s in context if s["path"] == ref["path"]), key=lambda s: s["start_line"]):
            if span["start_line"] <= cursor:
                cursor = max(cursor, span["end_line"] + 1)
        if cursor <= ref["end_line"]:
            return False
    return bool(answer["citations"] or answer["insufficient_evidence"])


# 从固定快照重新读取并核对行段和内容哈希，防止缓存或传入上下文被替换。
def verify_spans(reader, context):
    for span in context:
        content, truncated = reader.read(span["path"], 500_000)
        if not 1 <= span["start_line"] <= span["end_line"] <= len(content.splitlines()):
            raise ValueError("invalid immutable source line range")
        selected = "\n".join(content.splitlines()[span["start_line"] - 1 : span["end_line"]])
        if (
            truncated
            or selected != span["content"]
            or hashlib.sha256(selected.encode()).hexdigest() != span["content_sha256"]
        ):
            raise ValueError("retrieved context differs from immutable source")
