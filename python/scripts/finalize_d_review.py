"""Keep first judgments, record explicit adjudication, and publish D's review/cost metrics."""

import copy
import json
import statistics
from collections import Counter

from prepare_semantic_review import OUT, ROOT, load
from record_d_reviews import claim, evidence
from run_rag_quality import frozen, sha

from repomentor.evaluation import atomic_write
from repomentor.paths import data_root
from repomentor.rag_evaluation import coverage
from repomentor.repository import SnapshotStore
from repomentor.semantic_review import summarize_reviews, validate_judgment

KEY_POINTS = {
    "sweqa-flask-001": {"default_true_escapes": "默认 True 对非 ASCII 字符转义", "false_preserves_unicode": "False 保留原 Unicode 字符"},
    "sweqa-flask-002": {"blocks_late_setup": "注册后拒绝 setup 修改", "prevents_inconsistent_registration": "避免后续修改不能一致应用到已注册应用"},
    "sweqa-flask-003": {"blueprint_static_separation": "蓝图独立静态资源服务", "shared_hook_and_config": "共享 get_send_file_max_age/SEND_FILE_MAX_AGE_DEFAULT 约定",
                        "explicit_blueprint_max_age": "蓝图显式计算 max_age 后传给文件服务"},
    "sweqa-requests-001": {"argument_precedes_environment": "非 None 参数优先于环境 no_proxy", "nonempty_means_no_bypass_with_config": "非空表示未绕过且有代理配置，并非真实网络执行证明"},
    "sweqa-requests-002": {"preserves_case": "直接保留编码值，未规范化", "one_encoding_per_fixture": "每个 test_pragmas 样例恰好一项",
                           "content_formats": "测试输入是 HTML/XML 正文中的多种声明形式"},
    "sweqa-requests-003": {"dual_exception_categories": "可按 Requests 或 ValueError 家族捕获", "request_exception_details": "RequestException 的 IOError/请求响应上下文",
                           "correct_raise_context": "无适配器等 InvalidSchema 场景，不能混同无 host 的 InvalidURL"},
}
GOLD = {
    "sweqa-flask-001": [evidence("src/flask/json/provider.py", 144, 148), evidence("src/flask/json/provider.py", 166, 179), evidence("tests/test_json.py", 48, 54)],
    "sweqa-flask-002": [evidence("src/flask/sansio/blueprints.py", 213, 221), evidence("src/flask/sansio/blueprints.py", 319, 335), evidence("src/flask/sansio/scaffold.py", 42, 49)],
    "sweqa-flask-003": [evidence("src/flask/blueprints.py", 55, 102), evidence("src/flask/app.py", 281, 328)],
    "sweqa-requests-001": [evidence("src/requests/utils.py", 769, 771), evidence("src/requests/utils.py", 816, 825), evidence("tests/test_utils.py", 267, 272)],
    "sweqa-requests-002": [evidence("src/requests/utils.py", 493, 501), evidence("tests/test_utils.py", 368, 392)],
    "sweqa-requests-003": [evidence("src/requests/exceptions.py", 12, 24), evidence("src/requests/exceptions.py", 103, 104), evidence("src/requests/sessions.py", 783, 792)],
}


def main():
    candidates = {c["candidate_id"]: c for c in load(OUT / "candidates.json")}
    identities = load(OUT / "identity-map.json")
    original = load(OUT / "judgments.json")
    final = copy.deepcopy(original)
    changes = []
    for row in final:
        identity = identities[row["candidate_id"]]
        if identity["task_id"] == "sweqa-flask-001" and identity["cohort"] == "external-sdk-agent":
            # Do not invent the word "all" when the prior sentence already scopes non-ASCII.
            raw = {k: v for k, v in row.items() if k in (
                "candidate_id", "correctness", "citation_support", "completeness", "abstention", "entry_scope",
                "consistency", "claims", "notes", "key_points")}
            raw["claims"] = [{k: v for k, v in c.items() if k != "verified_evidence"} for c in raw["claims"]]
            raw["correctness"] = "correct"
            raw["claims"][1] = claim("dumps 参数传递解释没有完成", "partial",
                "原句先限定非 ASCII，不能额外添加所有 Unicode 再判错；实际缺陷是末句在 kwargs.setdefault( 处结束。",
                evidence("src/flask/json/provider.py", 166, 179))
            raw["notes"] = "已完成的事实正确，回答文本未写完且缺 False 分支；保留完整性 partial，不将截断片段当作虚构事实。"
            reader = SnapshotStore(data_root() / "corpus").open(identity["snapshot_id"])
            revised = validate_judgment(raw, candidates[row["candidate_id"]], reader)
            changes.append({"candidate_id": row["candidate_id"], "before": row, "after": revised,
                            "reason": "Codex self-adjudication: first assessment overinterpreted a scoped Unicode sentence.",
                            "reviewer": "Codex", "human_approved": False})
            row.clear()
            row.update(revised)
    assert len(changes) == 1
    frozen(OUT / "adjudications.json", {"original_sha256": sha(OUT / "judgments.json"), "changes": changes})
    frozen(OUT / "judgments-final.json", final)
    frozen(OUT / "semantic-summary-final.json", summarize_reviews(final, identities))
    frozen(OUT / "external-key-points.json", {"key_points": KEY_POINTS, "source_ranges": GOLD,
        "reviewer": "Codex", "timing": "Authored during nonblind source review, after generation; not a preregistered benchmark rubric.",
        "reference_policy": "Ranges are diagnostic, not unique valid evidence. Alternative cited docs may support the answer.",
        "training_eligible": False, "human_approved": False})
    details, costs = [], {}
    final_by_id = {r["candidate_id"]: r for r in final}
    for mode in ("fixed-bm25", "sdk-agent"):
        groups = []
        for cid, identity in identities.items():
            if identity["cohort"] != "external-" + mode:
                continue
            candidate, judgment = candidates[cid], final_by_id[cid]
            path = ROOT / identity["appearances"][0]["source_file"]
            record = load(path)["result"]
            task_id = identity["task_id"]
            if judgment["key_points"] is not None:
                assert set(judgment["key_points"]) == set(KEY_POINTS[task_id])
            else:
                assert candidate["answer"] is None
            if mode == "sdk-agent":
                sent = [d for d in record["decisions"] if d["request_sent"]]
                assert all(d["input_budget"]["fits"] and d["input_budget"]["context_window"] == 8192 for d in sent)
                usages = [d["usage"] for d in sent]
                elapsed = record["elapsed_seconds"]
            else:
                usages = [record["completion"]["usage"]]
                elapsed = record["completion"]["elapsed_seconds"]
            details.append({"candidate_id": cid, "task_id": task_id, "mode": mode,
                "workflow_status": record["completion"]["status"], "correctness": judgment["correctness"],
                "reference_source_coverage": coverage(GOLD[task_id], candidate["observed_context"]),
                "key_points_supported": sum(judgment["key_points"].values()) if judgment["key_points"] is not None else None,
                "key_points_total": len(KEY_POINTS[task_id]), "elapsed_seconds": elapsed,
                "new_model_requests": record["new_model_requests"],
                "prompt_tokens": sum(u["prompt_tokens"] for u in usages),
                "completion_tokens": sum(u["completion_tokens"] for u in usages)})
            groups.append(details[-1])
        costs[mode] = {"cases": len(groups), "workflow_statuses": dict(Counter(g["workflow_status"] for g in groups)),
            "new_model_requests": sum(g["new_model_requests"] for g in groups),
            "prompt_tokens": sum(g["prompt_tokens"] for g in groups),
            "completion_tokens": sum(g["completion_tokens"] for g in groups),
            "elapsed_median_seconds": round(statistics.median(g["elapsed_seconds"] for g in groups), 3),
            "reference_source_coverage_mean": round(statistics.mean(g["reference_source_coverage"] for g in groups), 4),
            "delivered_key_points": sum(g["key_points_supported"] or 0 for g in groups),
            "requested_key_points": sum(g["key_points_total"] for g in groups),
            "unscored_missing_answers": sum(g["key_points_supported"] is None for g in groups)}
    frozen(OUT / "external-metrics.json", {"modes": costs, "details": details,
        "latency_scope": "Saved original generator/agent elapsed time; excludes external clone/index construction and review; single runs.",
        "coverage_scope": "Diagnostic full-span coverage, not accuracy. Key-point delivery includes unanswered items in requested denominator; missing answer judgment remains unscored.",
        "rubric_timing": "post-generation development diagnosis", "paid_requests": 0, "judge_api_calls": 0})
    lines = ["# D：Codex 语义评审结果", "", "非盲开发评审；原始结论、自复核修订与模型失败均保留。人工未签核，样本不可用于训练。", "",
             "## 外部配对结果", "", "| 指标 | 固定 BM25 | SDK Agent |", "| --- | --- | --- |"]
    for name in ("new_model_requests", "prompt_tokens", "completion_tokens", "elapsed_median_seconds", "reference_source_coverage_mean", "delivered_key_points", "requested_key_points"):
        lines.append(f"| {name} | {costs['fixed-bm25'][name]} | {costs['sdk-agent'][name]} |")
    lines.extend(["", "来源覆盖不是答案正确率；关键点分母包括未交付答案，语义判定仍保留不可评状态。此小样本未支持 Agent 优于固定 RAG。", ""])
    for row in final:
        identity = identities[row["candidate_id"]]
        lines.extend([f"## {row['candidate_id']} — {identity['cohort']}", "", candidates[row["candidate_id"]]["question"], "",
            f"事实：{row['correctness']}；引用支持：{row['citation_support']}；完整性：{row['completeness']}；拒答：{row['abstention']}；一致性：{row['consistency']}", "",
            row["notes"], ""])
        for c in row["claims"]:
            refs = "; ".join(f"{r['path']}:{r['start_line']}-{r['end_line']}" for r in c["evidence"])
            lines.extend([f"- **{c['status']}**：{c['claim']}。{c['rationale']} 依据：{refs or '无可核实的外部事实来源'}", ""])
    atomic_write(OUT / "review-results.md", "\n".join(lines))
    print(json.dumps(costs, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
