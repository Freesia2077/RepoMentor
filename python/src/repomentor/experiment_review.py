"""Local, source-checked review import and four-layer experiment summaries; no generation."""

import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from .paths import data_root
from .rag_evaluation import coverage
from .records import write
from .repository import SnapshotStore
from .runtimes.chat import fingerprint
from .semantic_review import validate_judgment
from .source_windows import citation_validity, verify_spans


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def import_reviews(folder, root):
    candidates = load(folder / "review-candidates.json")
    authored = load(folder / "review-decisions.json")
    judgments = []
    for candidate in candidates:
        key = Path(candidate["source_file"]).stem + "/" + candidate["stage"]
        decision = authored[key]
        value = {"candidate_id": candidate["candidate_id"], **decision}
        source = load(root / candidate["source_file"])
        result = (
            source["result"]
            if candidate["stage"] == "answer"
            else next(r for r in source["stages"] if r["stage"] == candidate["stage"])
        )
        assert fingerprint(result) == candidate["record_sha256"], "answer changed after review export"
        reader = SnapshotStore(data_root() / "corpus").open(candidate["snapshot_id"])
        checked = validate_judgment(
            value, {**candidate, "observed_context": candidate["visible_context"]}, reader
        )
        checked["review_type"] = (
            "nonblind_model_review_same_repository_confirmation"
            if source.get("split") == "confirmation-development"
            else "nonblind_model_development_review"
        )
        checked["solved"] = bool(
            candidate["status"] == "success"
            and checked["correctness"] == "correct"
            and checked["completeness"] == "adequate"
            and checked["citation_support"] in {"supported", "not_applicable"}
            and checked["abstention"] != "inappropriate"
            and checked["consistency"] == "consistent"
        )
        judgments.append(checked)
    assert len(authored) == len(candidates), "unused or missing review entries"
    write(folder / "judgments.json", judgments)
    return judgments


def summarize_batch(folder, root):
    candidates = load(folder / "review-candidates.json")
    judgments = (
        {j["candidate_id"]: j for j in load(folder / "judgments.json")}
        if (folder / "judgments.json").exists()
        else {}
    )
    experiment = root / "experiments/p63"
    references = {}
    for split in ("development", "confirmation"):
        references.update(load(experiment / "evaluation-only" / (split + ".json")))
    details, groups = [], defaultdict(list)
    for candidate in candidates:
        case = load(root / candidate["source_file"])
        result = (
            case["result"]
            if candidate["stage"] == "answer"
            else next(s for s in case["stages"] if s["stage"] == candidate["stage"])
        )
        assert fingerprint(result) == candidate["record_sha256"]
        reader = SnapshotStore(data_root() / "corpus").open(candidate["snapshot_id"])
        context = result.get("final_context", result["context"])
        verify_spans(reader, context)
        refs = (
            references.get(case.get("task_id"), {}).get("sources", [])
            if candidate["stage"] == "answer"
            else []
        )
        initial = result.get("initial_retrieval") or {}
        raw_candidates = [c["chunk"] for c in initial.get("retrieval", {}).get("candidates", [])]
        trace = result.get("tool_trace", [])
        decisions = [d for d in result.get("decisions", []) if d["request_sent"]]
        calls = decisions or ([result["completion"]] if result["completion"].get("request_sent") else [])
        for call in calls:
            assert call["input_budget"]["fits"] and call["input_budget"]["context_window"] == 8192
        usages = [c.get("usage", {}) for c in calls]
        judgment = judgments.get(candidate["candidate_id"])
        label = (
            f"{case['repository']}/{candidate['stage']}"
            if candidate["stage"] != "answer"
            else f"{case['dialogue_id']}/turn-{case['turn']}"
            if case.get("dialogue_id")
            else candidate["task_id"]
        )
        row = {
            "candidate_id": candidate["candidate_id"],
            "task_id": label,
            "mode": candidate["mode"],
            "kind": "report-stage" if candidate["stage"] != "answer" else case.get("kind"),
            "stage": candidate["stage"],
            "workflow_status": candidate["status"],
            "solved": judgment["solved"] if judgment else None,
            "citation_ranges_valid": citation_validity(result["answer"], context),
            "reference_candidate_coverage": coverage(refs, raw_candidates),
            "reference_initial_coverage": coverage(refs, initial.get("sources", [])),
            "reference_observed_coverage": coverage(refs, result["context"]),
            "reference_final_coverage": coverage(refs, context),
            "model_requests": len(calls),
            "new_model_requests": result["new_model_requests"],
            "cache_hit": result["cache_hit"],
            "elapsed_seconds": result.get("elapsed_seconds", result["completion"]["elapsed_seconds"]),
            "elapsed_scope": "SDK episode after initial retrieval"
            if result.get("decisions")
            else "fixed model completion only",
            "prompt_tokens": sum(u.get("prompt_tokens") or 0 for u in usages),
            "completion_tokens": sum(u.get("completion_tokens") or 0 for u in usages),
            "invalid_tool_calls": sum(s["observation"]["status"] == "error" for s in trace),
            "duplicate_tool_calls": sum(
                s["observation"].get("error_code") == "duplicate_action" for s in trace
            ),
            "context_compactions": sum(bool(e.get("compacted")) for e in result.get("local_trace", [])),
            "stop_reason": result.get("stop_reason", result["completion"].get("error_code")),
            "initial_source_sha256": fingerprint(initial.get("sources", [])),
        }
        details.append(row)
        groups[row["mode"]].append(row)
    summary = {}
    blocked = defaultdict(list)
    for path in sorted((folder / "cases").glob("*.json")):
        case = load(path)
        if case.get("result") is None and not case.get("stages"):
            blocked[case.get("mode", "sdk")].append({"case": path.stem, "reason": case.get("blocked_by")})
        elif case.get("stages"):
            attempted = {stage["stage"] for stage in case["stages"]}
            for stage in ("explorer", "mentor", "contributor"):
                if stage not in attempted:
                    blocked["sdk"].append(
                        {"case": path.stem + "/" + stage, "reason": "previous_failed_stage"}
                    )
    for mode, rows in groups.items():
        summary[mode] = {
            "cases": len(rows),
            "solved": sum(r["solved"] is True for r in rows),
            "scheduled_cases": len(rows) + len(blocked[mode]),
            "blocked_cases": blocked[mode],
            "reviewed": sum(r["solved"] is not None for r in rows),
            "statuses": dict(Counter(r["workflow_status"] for r in rows)),
            "mean_model_requests": statistics.mean(r["model_requests"] for r in rows),
            **{
                k: sum(r[k] for r in rows)
                for k in (
                    "model_requests",
                    "new_model_requests",
                    "prompt_tokens",
                    "completion_tokens",
                    "elapsed_seconds",
                    "invalid_tool_calls",
                    "duplicate_tool_calls",
                )
            },
        }
    paired = []
    for task_id in sorted({r["task_id"] for r in details if r["task_id"]}):
        pair = {r["mode"]: r for r in details if r["task_id"] == task_id}
        if {"fixed", "sdk"} <= pair.keys():
            equal = pair["fixed"]["initial_source_sha256"] == pair["sdk"]["initial_source_sha256"]
            paired.append(
                {
                    "task_id": task_id,
                    "kind": pair["sdk"]["kind"],
                    "initial_retrieval_equal": equal,
                    "fixed_solved": pair["fixed"]["solved"],
                    "sdk_solved": pair["sdk"]["solved"],
                }
            )
    gains = sum(
        r["kind"] in {"cross_function", "multi_file"}
        and r["sdk_solved"] is True
        and r["fixed_solved"] is False
        for r in paired
    )
    losses = sum(
        r["kind"] in {"cross_function", "multi_file"}
        and r["sdk_solved"] is False
        and r["fixed_solved"] is True
        for r in paired
    )
    result = {
        "modes": summary,
        "details": details,
        "pairs": paired,
        "cross_function_extra_solved": gains,
        "cross_function_regressions": losses,
        "cross_function_net_gain": gains - losses,
        "reviewer": "Codex",
        "blind": False,
        "human_approved": False,
        "training_eligible": False,
        "limits": "Reference range coverage is diagnostic and may omit alternative valid evidence. Semantic verdicts are authored separately; workflow success is not correctness. Latencies exclude initial retrieval: fixed completion vs SDK episode; these are not end-to-end wall times.",
    }
    write(folder / "comparison.json", result)
    lines = [
        "# P6.3 对照评测",
        "",
        "Codex 非盲模型评审；同仓库题目，不是官方榜单或人工签核。",
        "",
        "| 条件 | 已评审 / 总数 | 解决 | 平均请求 | 输入 / 输出 token | 已记录阶段耗时（秒） |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for mode, r in summary.items():
        lines.append(
            f"| {mode} | {r['reviewed']} / {r['scheduled_cases']} | {r['solved']} | {r['mean_model_requests']:.2f} | {r['prompt_tokens']} / {r['completion_tokens']} | {r['elapsed_seconds']:.1f} |"
        )
    if any(blocked.values()):
        lines += ["", "阻塞轮次计入计划题数和未解决数；平均请求数仅针对实际运行的轮次。"]
    lines += [
        "",
        "耗时口径：固定 RAG 为模型完成耗时，SDK 为初始检索之后的运行耗时；均不包含索引/初始检索，不作为端到端速度对比。检索冷缓存与热查询另表报告。",
    ]
    lines += [
        "",
        f"跨函数/多文件新增解决 {gains} 题、退步 {losses} 题、净增 {gains - losses} 题。",
        "",
        "## 逐题与过程归因",
        "",
        "| 题目 | 条件 | 工作流 | 解决 | 候选 / 已读 / 最终参考覆盖 | 请求 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in details:
        lines.append(
            f"| {r['task_id']} | {r['mode']} | {r['workflow_status']} | {r['solved']} | {r['reference_candidate_coverage']} / {r['reference_observed_coverage']} / {r['reference_final_coverage']} | {r['model_requests']} |"
        )
    (folder / "comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result
