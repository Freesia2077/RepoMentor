"""Offline P4 artifact audit and review packet. No model calls, no semantic auto-scoring."""

import hashlib
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

import jsonschema

from repomentor.config import write_json
from repomentor.contracts import ContributorOutput, ExplorerOutput, MentorOutput
from repomentor.evaluation import ANSWER_SCHEMA
from repomentor.paths import data_root
from repomentor.rag import citation_validity, messages_for, verify_spans
from repomentor.rag_evaluation import load_rag_tasks, retrieval_metrics
from repomentor.repository import SnapshotStore
from repomentor.runtimes.chat import ChatSettings, CompletionResult

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / "experiments/p4"
STAGES = {"explorer": ExplorerOutput, "mentor": MentorOutput, "contributor": ContributorOutput}


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def render_report(value, destination):
    labels = {
        "evidenceClaims": "主要结论与引用",
        "evidenceCoverage": "覆盖与缺口",
        "projectType": "项目类型",
        "techStack": "技术栈",
        "fileCount": "文件数",
        "entryPoints": "入口",
        "moduleMap": "模块职责",
        "directorySummary": "目录概览",
        "projectSummary": "项目简介",
        "architectureOverview": "架构解释",
        "dependencyGraph": "依赖描述",
        "readingPath": "阅读路径",
        "keyPatterns": "关键实现模式",
        "codeConventions": "代码约定",
        "goodFirstIssues": "候选贡献建议",
        "contributionSetup": "开发环境与命令",
        "entryFiles": "建议阅读文件",
        "notesForNewcomers": "入门提示",
    }

    def render(field, indent=0):
        prefix = " " * indent
        if isinstance(field, dict):
            lines = []
            for key, item in field.items():
                if isinstance(item, (dict, list)) and item:
                    lines.append(f"{prefix}- **{key}**：")
                    lines.extend(render(item, indent + 2))
                else:
                    lines.append(f"{prefix}- **{key}**：{item if item is not None else '未提供'}")
            return lines
        if isinstance(field, list):
            lines = []
            for i, item in enumerate(field, 1):
                lines.append(
                    f"{prefix}- 第 {i} 项" if isinstance(item, (dict, list)) else f"{prefix}- {item}"
                )
                if isinstance(item, (dict, list)):
                    lines.extend(render(item, indent + 2))
            return lines or [prefix + "未提供"]
        return [prefix + str(field)]

    text = [
        "# 仓库学习报告",
        "",
        f"运行 ID：`{value['run_id']}`；快照：`{value['snapshot_id']}`；状态：{value['status']}。",
        "",
        "以下内容来自保存的模型输出，未经人工签核。候选贡献方向不是已存在的 GitHub issue。",
        "",
    ]
    for stage, output in value["outputs"].items():
        text.extend(
            [
                "## "
                + {"explorer": "仓库概览", "mentor": "代码学习路径", "contributor": "开发与贡献"}[stage],
                "",
            ]
        )
        for name, field in output.items():
            text.extend(["### " + labels.get(name, name), "", *render(field), ""])
    destination.write_text("\n".join(text), encoding="utf-8")


def main():
    tasks = {t["task_id"]: t for t in load_rag_tasks(EXP / "tasks.jsonl")}
    rows = [
        json.loads(line) for line in (EXP / "rag-baseline.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == len(tasks) * 3
    assert {(r["task_id"], r["mode"]) for r in rows} == {
        (t, m) for t in tasks for m in ("none", "bm25", "oracle")
    }
    snapshots = SnapshotStore(data_root() / "corpus")
    readers, checked, groups = {}, set(), defaultdict(list)
    for row in rows:
        task, result = tasks[row["task_id"]], row["result"]
        assert row["split"] == task["split"] == "validation"
        assert result["snapshot_id"] == task["snapshot_id"] and result["commit_sha"] == task["commit_sha"]
        assert result["config_id"] == ChatSettings.model_validate(result["configuration"]).config_id
        completion = CompletionResult.model_validate(result["completion"])
        assert result["input_messages"] == messages_for(task["input"]["question"], [], result["context"])
        assert result["citation_ranges_valid"] == citation_validity(result["answer"], result["context"])
        assert row["metrics"] == retrieval_metrics(task["gold"]["evidence"], result)
        if completion.status == "success":
            jsonschema.validate(result["answer"], ANSWER_SCHEMA)
            assert result["answer"] == json.loads(completion.content)
            assert completion.input_budget["fits"]
            assert completion.input_budget["input_tokens"] == completion.usage["prompt_tokens"]
        sid = result["snapshot_id"]
        if sid not in readers:
            readers[sid] = snapshots.open(sid)
        for span in result["context"]:
            key = (sid, span["path"], span["start_line"], span["end_line"], span["content_sha256"])
            if key not in checked:
                verify_spans(readers[sid], [span])
                checked.add(key)
        groups[(row["repository"], row["mode"])].append(row)
    breakdown = []
    for (repository, mode), group in groups.items():
        scored = [
            r["metrics"]["gold_span_selected_coverage"]
            for r in group
            if r["metrics"]["gold_span_selected_coverage"] is not None
        ]
        answerable = [r for r in group if tasks[r["task_id"]]["gold"]["evidence"]]
        unavailable = [r for r in group if not tasks[r["task_id"]]["gold"]["evidence"]]
        breakdown.append(
            {
                "repository": repository,
                "mode": mode,
                "tasks": len(group),
                "gold_coverage_mean": statistics.mean(scored) if scored else None,
                "answerable_abstentions": sum(
                    bool(r["result"]["answer"] and r["result"]["answer"]["insufficient_evidence"])
                    for r in answerable
                ),
                "unavailable_tasks": len(unavailable),
                "unavailable_abstentions": sum(
                    bool(r["result"]["answer"] and r["result"]["answer"]["insufficient_evidence"])
                    for r in unavailable
                ),
            }
        )
    reports, claims = [], []
    for repo in ("developit--mitt", "ai--nanoid"):
        paths = [ROOT / f"experiments/p2/{repo}-report.json", EXP / f"{repo}-qwen-report.json"]
        pair = [load(path) for path in paths]
        assert pair[0]["snapshot_id"] == pair[1]["snapshot_id"]
        reader = readers[pair[0]["snapshot_id"]]
        for path, report in zip(paths, pair, strict=True):
            count, present, supplied = 0, 0, 0
            local = report["runtime"] == "local-rag-report"
            stages = load(path.with_name(path.stem + ".stages.json")) if local else None
            for phase, output in report["outputs"].items():
                STAGES[phase].model_validate(output)
                allowed = (
                    {span["path"] for r in stages if r["stage"] == phase for span in r["context"]}
                    if local
                    else set(output["evidenceCoverage"]["examinedFiles"])
                )
                if local:
                    assert set(output["evidenceCoverage"]["examinedFiles"]) == allowed
                for i, claim in enumerate(output["evidenceClaims"]):
                    refs = [ref["path"] for ref in claim["evidence"]]
                    count += len(refs)
                    present += sum(p in reader.paths for p in refs)
                    supplied += sum(p in allowed for p in refs)
                    claims.append(
                        {
                            "report_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                            "report": path.relative_to(ROOT).as_posix(),
                            "stage": phase,
                            "claim_index": i,
                            "claim": claim,
                            "correctness_0_2": None,
                            "citation_support_0_2": None,
                            "reviewer": None,
                            "human_approved": False,
                        }
                    )
            token_count = sum(
                (row.get("usage", {}).get("prompt_tokens") or row.get("usage", {}).get("input_tokens") or 0)
                + (
                    row.get("usage", {}).get("completion_tokens")
                    or row.get("usage", {}).get("output_tokens")
                    or 0
                )
                for row in report["usage"]
            )
            reports.append(
                {
                    "report": path.relative_to(ROOT).as_posix(),
                    "status": report["status"],
                    "snapshot_id": report["snapshot_id"],
                    "commit_sha": reader.snapshot.commit_sha,
                    "stages": len(report["outputs"]),
                    "references": count,
                    "existing_paths": present,
                    "provided_paths": supplied,
                    "elapsed_seconds": report["elapsed_seconds"],
                    "recorded_usage_entries": len(report["usage"]),
                    "cached_stages": sum(bool(u.get("cache_hit")) for u in report["usage"])
                    if local
                    else None,
                    "new_model_requests": sum(u["new_model_requests"] for u in report["usage"])
                    if local
                    else None,
                    "reported_input_output_tokens": token_count,
                    "semantic_correctness": "pending_review",
                    "coverage": "pending_review",
                    "teaching_value": "pending_review",
                }
            )
            if local:
                render_report(report, path.with_suffix(".md"))
    record = {
        "offline_verification": "passed",
        "qa_rows": len(rows),
        "qa_tasks": len(tasks),
        "statuses": dict(Counter(r["result"]["completion"]["status"] for r in rows)),
        "unique_verified_source_spans": len(checked),
        "breakdown": breakdown,
        "reports": reports,
        "new_model_requests": 0,
        "notes": "Rule checks are not semantic correctness. DeepSeek/Qwen historical pipelines differ; compare systems, not controlled model ability. Test split sealed. No human approval.",
    }
    write_json(EXP / "offline-verification.json", record)
    review = EXP / "report-review-packet.json"
    if review.exists() and {c["report_sha256"] for c in load(review)["claims"]} != {
        c["report_sha256"] for c in claims
    }:
        # Preserve annotations tied to an older report before producing the current packet.
        archive = review.with_name(
            "report-review-packet-" + hashlib.sha256(review.read_bytes()).hexdigest()[:12] + ".json"
        )
        if not archive.exists():
            archive.write_bytes(review.read_bytes())
        refresh_review = True
    else:
        refresh_review = False
    if not review.exists() or refresh_review:
        write_json(
            review,
            {
                "rubric": {
                    "0": "错误或关键内容缺失",
                    "1": "部分正确但有重要缺口",
                    "2": "正确完整且有支持",
                    "null": "尚未评审或无法核验",
                },
                "claims": claims,
                "coverage_checklist": {
                    repo: [t["gold"]["required_facts"] for t in tasks.values() if t["repository"] == repo]
                    for repo in ("developit/mitt", "ai/nanoid")
                },
                "additional_dimensions": ["关键信息覆盖", "信息不足处理", "教学价值"],
                "human_approved": False,
            },
        )
    print(json.dumps({"verified": len(rows), "reports": len(reports), "new_model_requests": 0}))


if __name__ == "__main__":
    main()
