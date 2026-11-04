"""Audit frozen P6.2 C records without SDK/model/network calls or editing them."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from repomentor.answer_audit import POLICY, audit_answer
from repomentor.paths import data_root
from repomentor.records import write
from repomentor.repository import SnapshotStore

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "experiments/p6-answer-audit"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def build(output):
    sources = [ROOT / "experiments" / name for name in ("p6-rag-context", "p6-rag-context-fix")]
    protected = {p.relative_to(ROOT).as_posix(): sha(p) for folder in sources for p in folder.rglob("*") if p.is_file()}
    protocol = {"policy": POLICY, "source_files": protected,
                "implementation_sha256": sha(ROOT / "src/repomentor/answer_audit.py"),
                "runner_sha256": sha(Path(__file__)), "split": "validation",
                "human_approved": False, "training_eligible": False,
                "purpose": "Development failure analysis; selected, nonblind, repeated cases. No accuracy estimate."}
    if (output / "protocol.json").exists() and load(output / "protocol.json") != protocol:
        raise ValueError("Frozen audit differs: choose a new --output directory")
    records = []
    store = SnapshotStore(data_root() / "corpus")
    for folder in sources:
        for path in sorted((folder / "cases").glob("*.json")):
            case = load(path)
            if case["split"] != "validation":
                raise ValueError("Audit fixture must stay in validation")
            reader = store.open(case["snapshot_id"])
            if reader.snapshot.commit_sha != case["commit_sha"]:
                raise ValueError("snapshot commit mismatch")
            stages = case["stages"] if case["kind"] == "report" else [case["result"]]
            for stage in stages:
                context = stage.get("final_context", stage.get("context", []))
                audit = audit_answer(reader, stage.get("answer"), context)
                if audit["source_integrity"] != "verified":
                    raise ValueError(f"Invalid saved context: {path.name} / {stage.get('stage')}")
                records.append({"review_id": f"{folder.name}/{case['case_id']}/{stage.get('stage', 'qa')}",
                    "source_file": path.relative_to(ROOT).as_posix(), "source_sha256": sha(path),
                    "snapshot_id": case["snapshot_id"], "commit_sha": case["commit_sha"],
                    "repository": case["repository"], "split": case["split"], "question": case["question"],
                    "attempt_id": stage.get("attempt_id"), "stop_reason": stage.get("stop_reason"),
                    "observed_context": context, "audit": audit})
    refs = [ref for row in records for claim in row["audit"]["claims"] for ref in claim["references"]]
    summary = {"policy": POLICY, "source_case_records": sum(len(list((s / "cases").glob("*.json"))) for s in sources),
        "stage_appearances": len(records), "unique_attempts": len({r["attempt_id"] for r in records}),
        "answers": dict(Counter(r["audit"]["answer_status"] for r in records)),
        "reference_statuses": dict(Counter(r["status"] for r in refs)),
        "dependency_edge_statuses": dict(Counter(e["status"] for r in records for e in r["audit"]["dependency_edges"])),
        "command_statuses": dict(Counter(c["status"] for r in records for c in r["audit"]["commands"])),
        "new_model_requests": 0, "metadata_requests": 0, "paid_requests": 0,
        "protected_files_unchanged": len(protected), "semantic_correctness": "not_scored",
        "independent_review": "pending", "human_approved": False, "training_eligible": False,
        "counting": "Counts are appearances including reused stages and before/after cases, not independent samples."}
    for name, digest in protected.items():
        if sha(ROOT / name) != digest:
            raise ValueError(f"Historical artifact changed: {name}")
    write(output / "protocol.json", protocol)
    write(output / "review-packet.json", records)
    write(output / "summary.json", summary)
    # Readable source excerpts and raw answers; conclusions stay pending, not pseudo human labels.
    lines = ["# 历史回答复核材料", "", "开发诊断材料；非盲评；同一尝试可能重复出现。所有语义结论待复核。",
             "", "逐条核对：结论是否得到引用支持、事实与入口是否正确、是否自相矛盾、遗漏与拒答是否合理。",
             "复合结论先拆开；未引用的自由文本也需检查。引用可见不等于结论正确。", ""]
    for row in records:
        audit = row["audit"]
        lines.extend([f"## {row['review_id']}", "", f"问题：{row['question']}", "",
                      f"原记录：`{row['source_file']}`；尝试：`{row['attempt_id']}`；停止：`{row['stop_reason']}`", ""])
        for claim in audit["claims"]:
            lines.extend([f"### {claim['claim_id']}", "", claim["text"], "", "支持度/事实正确性：待复核", ""])
            for ref in claim["references"]:
                lines.extend([f"引用状态：`{ref['status']}`；`{ref['reference']}`", ""])
                for span in ref["excerpts"]:
                    lines.extend([f"`{span['path']}:{span['start_line']}-{span['end_line']}`；模型可见：{span['visible_to_model']}", ""])
                    lines.extend("    " + line for line in span["content"].splitlines())
                    lines.append("")
        lines.extend(["结构化诊断与完整原始回答：", ""])
        diagnostic = {k: audit.get(k) for k in ("dependency_edges", "commands", "full_answer")}
        lines.extend("    " + line for line in json.dumps(diagnostic, ensure_ascii=False, indent=2).splitlines())
        lines.append("")
    from repomentor.evaluation import atomic_write

    atomic_write(output / "review-packet.md", "\n".join(lines))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    build(parser.parse_args().output)
