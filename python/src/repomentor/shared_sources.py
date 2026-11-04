"""Source sharing admits exact snapshot content, never another agent's prose as evidence."""

import hashlib

from .source_context import pack_observed_context
from .source_windows import verify_spans


def select_sources(reader, envelope, question, *, max_bytes=4000, excluded=()):
    if not envelope:
        return []
    if envelope["snapshot_id"] != reader.snapshot.snapshot_id:
        raise ValueError("shared sources belong to another snapshot")
    spans = envelope["spans"]
    verify_spans(reader, spans)
    eligible = [s for s in spans if s["path"] not in excluded]
    preferred = []
    # Reuse the source around previous citations, not the prior model's interpretation.
    # Never expand beyond a span that the previous turn actually observed.
    for ref in envelope.get("preferred_ranges", [])[:8]:
        for span in eligible:
            if (span["path"] == ref["path"]
                    and span["start_line"] <= ref["start_line"] <= ref["end_line"] <= span["end_line"]):
                start = max(span["start_line"], ref["start_line"] - 3)
                end = min(span["end_line"], ref["end_line"] + 3)
                content = "\n".join(span["content"].split("\n")[start - span["start_line"]:end - span["start_line"] + 1])
                item = {"path": span["path"], "start_line": start, "end_line": end, "content": content,
                        "content_sha256": hashlib.sha256(content.encode()).hexdigest()}
                if item not in preferred and sum(len(s["content"].encode()) for s in preferred) + len(content.encode()) <= max_bytes:
                    preferred.append(item)
                break
    selected, _ = pack_observed_context(
        eligible, question, lambda rows: sum(len(s["content"].encode()) for s in [*preferred, *rows]) <= max_bytes,
    )
    return preferred + [s for s in selected if s not in preferred]
