"""Bounded source-unit expansion and query-relevant packing; no generated summaries."""

# 从已观察源码中选择完整声明或可容纳的连续片段，为上下文压缩和最终整理共用。

import hashlib
import re
from pathlib import PurePosixPath

from .chunking import syntax_units
from .retrieval import tokenize


def containing_unit(text, path, start, end):
    units, parser = syntax_units(text, PurePosixPath(path).suffix)
    matches = [u for u in units if u[0] <= start <= end <= u[1]]
    if not matches:
        return {"start_line": start, "end_line": end, "symbol": None, "parser": parser}
    first, last, symbol, kind = min(matches, key=lambda u: (u[1] - u[0], -u[0]))
    return {"start_line": first, "end_line": last, "symbol": symbol, "kind": kind, "parser": parser}


# 优先尝试完整片段；装不下时只拆分已读原文，连续行号与省略原因一起保留。
def pack_observed_context(context, question, fits):
    """Prefer whole spans; under pressure partition observed lines, never invent missing source."""
    terms = set(tokenize(question))

    def relevance(span):
        path_terms = set(tokenize(span["path"]))
        content = span["content"]
        names = set(re.findall(r"(?:def|function|class)\s+([\w$]+)", content))
        targets = len({n.casefold() for n in names} & terms)
        branches = min(3, len(re.findall(r"\b(?:return|raise|throw|if)\b", content)))
        return 4 * targets + len(terms & set(tokenize(content))) + 2 * len(terms & path_terms) + branches

    if not fits(context):
        partitioned = []
        for span in context:
            lines = span["content"].split("\n")
            if len(lines) <= 40:
                partitioned.append(span)
                continue
            for offset in range(0, len(lines), 40):
                content = "\n".join(lines[offset : offset + 40])
                partitioned.append(
                    {
                        **span,
                        "content": content,
                        "start_line": span["start_line"] + offset,
                        "end_line": span["start_line"] + offset + len(lines[offset : offset + 40]) - 1,
                        "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                    }
                )
        context = partitioned
    ranked = sorted(enumerate(context), key=lambda pair: (-relevance(pair[1]), pair[0]))
    chosen, omitted = [], []
    for _, span in ranked:
        if fits([*chosen, span]):
            chosen.append(span)
        else:
            omitted.append({k: span[k] for k in ("path", "start_line", "end_line", "content_sha256")})
    return chosen, omitted
