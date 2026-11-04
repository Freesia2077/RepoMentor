"""Offline interview report, readable anonymous packets and explicitly simulated usability checks."""

import argparse
import hashlib
import json
import secrets
from pathlib import Path

from rag_interview import CONDITIONS, OUT, PRIVATE, audit, freeze, load, reader_for, summarize

from repomentor.agent_tools import RepositoryEnvironment
from repomentor.config import write_json
from repomentor.contracts import ToolCall
from repomentor.presentation import render_run
from repomentor.rag_evaluation import coverage
from repomentor.repository import build_profile
from repomentor.retrieval_factory import build_index
from repomentor.runtimes.chat import fingerprint
from repomentor.semantic_review import RUBRIC


def submitted_sources(case):
    # Failed full-file preflight retains its proposed source, but nothing reached a model.
    return case["result"]["context"] if case["result"]["completion"].get("request_sent") else []


def publish_review():
    salt_path = PRIVATE / "review-salt.json"
    if not salt_path.exists():
        write_json(salt_path, secrets.token_hex(32))
    salt, rows, mapping = load(salt_path), [], {}
    for phase in ("development", "confirmation"):
        refs = {r["task_id"]: r for r in load(OUT / phase / "evaluation-only.json")}
        for path in sorted((OUT / phase / "cases").glob("*.json")):
            case = load(path)
            cid = fingerprint([salt, phase, path.stem])[:16]
            mapping[cid] = {"source": path.relative_to(OUT.parents[1]).as_posix(), "sha256": fingerprint(case)}
            ref = refs[case["task"]["task_id"]]
            rows.append({"candidate_id": cid, "question": case["task"]["input"]["question"],
                "snapshot_id": case["task"]["snapshot_id"], "answer": case["result"]["answer"],
                "completion_status": case["result"]["completion"]["status"],
                "input_sent": bool(case["result"]["completion"].get("request_sent")),
                "observed_context": submitted_sources(case), "evaluation_sources": ref["sources"],
                "required_points": ref["key_points"]})
    freeze(PRIVATE / "review-mapping.json", mapping)
    freeze(OUT / "review/packet.json", sorted(rows, key=lambda r: r["candidate_id"]))
    freeze(OUT / "review/rubric.json", {**RUBRIC, "reviewer": "Codex simulated reviewer",
        "blinding": "Labels masked; source length/style can reveal condition; implementer is not independent.",
        "human_approved": False, "independent_review_completed": False})
    print(json.dumps({"anonymous_candidates": len(rows)}))


def corrected_summary():
    summarize()
    summary = load(OUT / "summary.json")
    for phase in ("development", "confirmation"):
        refs = {r["task_id"]: r for r in load(OUT / phase / "evaluation-only.json")}
        for condition in CONDITIONS:
            cases = [load(p) for p in (OUT / phase / "cases").glob("*--" + condition + ".json")]
            g = summary["groups"][phase + "/" + condition]
            g["planned_complete_reference_tasks"] = g["complete_reference_tasks"]
            values = [coverage(refs[c["task"]["task_id"]]["sources"], submitted_sources(c)) for c in cases]
            g["mean_reference_coverage"] = sum(values) / len(values)
            g["complete_reference_tasks"] = sum(v == 1 for v in values)
    summary["source_coverage_policy"] = "Only source submitted in a model request; infeasible preflight is zero, proposed coverage separate."
    summary["postprocessing_sha256"] = hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    write_json(OUT / "summary.json", summary)


def finalize():
    audit()
    corrected_summary()
    hashes = {p.relative_to(OUT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(OUT.rglob("*")) if p.is_file() and p.name != "completed-files.json"}
    freeze(OUT / "completed-files.json", {"sha256": hashes, "training_eligible": False})


def review_page(offset, limit):
    for row in load(OUT / "review/packet.json")[offset:offset + limit]:
        print(json.dumps({k: row[k] for k in ("candidate_id", "question", "answer", "completion_status", "required_points")},
                         ensure_ascii=False))


def packet_markdown():
    lines = ["# 匿名语义评审包", "", "条件标签已移除，任务和答案顺序已打散。源码长度/风格仍可能暴露条件。",
             "评审请分别判断事实、引文支持和完整性，允许参考范围之外的有效证据；不要只匹配关键词。",
             "当前操作者为 Codex 实施者，属于匿名自审，非独立人审。", ""]
    for row in load(OUT / "review/packet.json"):
        lines += ["## " + row["candidate_id"], "", row["question"], "",
                  "```json", json.dumps(row["answer"], ensure_ascii=False, indent=2), "```", "",
                  "必要要点（允许语义等价与替代证据）：", ""]
        lines += ["- " + point for point in row["required_points"]]
        lines += ["", "核验源码：", ""]
        for s in row["evaluation_sources"]:
            lines += [f"{s['path']}:{s['start_line']}-{s['end_line']}", "", "```",
                      "\n".join(f"L{n}: {v}" for n, v in enumerate(s["content"].split("\n"), s["start_line"])), "```", ""]
    (OUT / "review/packet.md").write_text("\n".join(lines), encoding="utf-8")


def simulate():
    tasks = load(OUT / "development/tasks.json")
    mitt = next(t for t in tasks if t["repository"] == "developit/mitt")
    reader = reader_for(mitt)
    profile = build_profile(reader)
    index = build_index(reader, storage=PRIVATE / "simulation-index")
    env = RepositoryEnvironment(reader, index)
    env.reset({"question": "定位 mitt 入口，核对 on 的大小写测试，再切换到 off 与 emit"})
    actions = [
        ("search_code", {"query": "mitt"}),
        ("read_code", {"path": "src/index.ts", "start_line": 46, "end_line": 73}),
        ("read_code", {"path": "test/index_test.ts", "start_line": 79, "end_line": 90}),
        ("read_code", {"path": "src/index.ts", "start_line": 82, "end_line": 121}),
    ]
    trace = [env.step(ToolCall(call_id=f"simulation-{i}", tool=name, arguments=args))
             for i, (name, args) in enumerate(actions, 1)]
    access = {name: coverage([{"path": path, "start_line": a, "end_line": b}], env.state["context"]) == 1
              for name, path, a, b in (("entry", "src/index.ts", 46, 73),
                                      ("test", "test/index_test.ts", 79, 90),
                                      ("followup", "src/index.ts", 82, 121))}
    if not all(access.values()):
        raise ValueError("simulated task source access did not complete")
    published = load(OUT / "development/cases/history-dev-04--code_rag.json")
    document = render_run({**published["task"], "mode": "fixed", "result": published["result"]})
    (OUT / "simulated-answer.md").write_text(document, encoding="utf-8")
    write_json(OUT / "simulated-study.json", {
        "operator": "Codex simulated repository learner", "human_participants": 0,
        "real_user_study": False, "generation_requests": 0, "source": "current immutable mitt snapshot and saved development answer",
        "entry_candidates": profile["entryCandidates"], "tool_trace": trace, "source_access_checks": access,
        "missions": [
            {"id": "entry", "goal": "定位默认导出和阅读起点", "observed": "只读搜索及入口读取可得到固定路径、行号与函数；有明确问题时可继续阅读。", "completed_in_simulation": True},
            {"id": "test", "goal": "核对 on 是否转小写及其测试", "observed": "已实际读取 on 及大小写断言；保存答案的完整性另由匿名自审判定，工具拿到不等于回答利用。", "completed_in_simulation": True},
            {"id": "followup", "goal": "从 on 切换到 off/emit，核对参数与状态", "observed": "操作者显式切换读取目标，可查明 off 空数组与 emit 两组参数；本次是脚本化工具试用，不是新的自然对话模型实验。", "completed_in_simulation": True}],
        "feedback": ["CLI/Markdown 可复核来源，但仍需读者理解完成状态与语义正确的区别。",
                     "搜索与读取的轨迹能定位缺口；候选命中不应显示为任务已完成。",
                     "重复读取已显示区间会返回already_read；本次四个工具动作中出现一次，应先复用可见行段。",
                     "整文件和片段两种交付各有适用任务，需同时展示原文与未覆盖内容。"],
        "limitations": ["任务和路径由熟悉实现的 Codex 操作者选择，存在强先验与学习效应。",
                        "没有独立真人、满意度、用户省时或真实采用率数据。",
                        "模拟不构成新的多轮 Agent 正确率或用户研究。"], "training_eligible": False})


def comparison():
    mapping = load(PRIVATE / "review-mapping.json")
    judgments = load(OUT / "judgments.json")
    if set(mapping) != set(judgments):
        raise ValueError("unblinding requires all source-bound decisions first")
    freeze(OUT / "review/unblinding.json", {"decisions_sha256": fingerprint(load(OUT / "review/decisions.json")),
        "mapping": mapping, "policy": "Published after the complete explicit self-review was saved; not independent blind review."})
    groups = {}
    for cid, meta in mapping.items():
        case = load(OUT.parents[1] / meta["source"])
        phase = case["task"]["split"]
        task = groups.setdefault(case["task"]["task_id"], {"phase": phase, "question": case["task"]["input"]["question"], "conditions": {}})
        task["conditions"][case["condition"]] = {"candidate_id": cid, "solved": judgments[cid]["solved"],
            "correctness": judgments[cid]["correctness"], "citation_support": judgments[cid]["citation_support"],
            "completeness": judgments[cid]["completeness"], "notes": judgments[cid]["notes"],
            "workflow": case["result"]["completion"]["status"], "source_file": meta["source"]}
    pairs = {}
    for phase in {t["phase"] for t in groups.values()}:
        rows = [t for t in groups.values() if t["phase"] == phase]
        pairs[phase] = {"code_rag_gain_over_lexical": sum(t["conditions"]["code_rag"]["solved"] and not t["conditions"]["lexical"]["solved"] for t in rows),
                       "code_rag_loss_to_lexical": sum(t["conditions"]["lexical"]["solved"] and not t["conditions"]["code_rag"]["solved"] for t in rows)}
    write_json(OUT / "comparison.json", {"tasks": groups, "paired": pairs, "human_approved": False})
    lines = ["# 逐题对照（评审完成后揭盲）", "", "通过要求流程完成、事实正确、完整且引用支持。", "",
             "| 任务 | none | grep_read | located_file | lexical | code_rag |", "| --- | --- | --- | --- | --- | --- |"]
    for name, task in sorted(groups.items()):
        lines.append("| " + name + " | " + " | ".join("通过" if task["conditions"][c]["solved"] else task["conditions"][c]["workflow"] if task["conditions"][c]["workflow"] != "success" else "未解决" for c in CONDITIONS) + " |")
    for name, task in sorted(groups.items()):
        lines += ["", "## " + name, "", task["question"], ""]
        lines += ["- " + c + "：" + task["conditions"][c]["notes"] for c in CONDITIONS]
    (OUT / "comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def report():
    summary = load(OUT / "summary.json")
    rows = ["# 当前 9B RAG 面试补强：五条件对照与模拟试用", "", "日期：2026-10-20。",
            "固定单请求回答对照；开发六题与新建同仓库确认六题分开。原 SDK/4B 实验不改写。",
            "语义判断为匿名顺序的 Codex 自审，实施者并非独立评审者，也没有真人试用。", ""]
    for phase, title in (("development", "已知开发题"), ("confirmation", "新建同仓库确认")):
        rows += ["## " + title, "", "| 条件 | 流程成功 | 严格解决 | 完整参考输入 | 请求 | 端到端 p50 / p95 秒 |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
        for condition in CONDITIONS:
            g = summary["groups"][phase + "/" + condition]
            p50, p95 = g["end_to_end_p50_seconds"], g["end_to_end_p95_seconds"]
            latency = f"{p50:.2f} / {p95:.2f}" if p50 is not None else "无新请求样本"
            rows.append(f"| {condition} | {g['statuses'].get('success', 0)}/{g['tasks']} | {g['solved']}/{g['tasks']} | {g['complete_reference_tasks']}/{g['tasks']} | {g['new_model_requests']} | {latency} |")
        rows += [""]
    confirm = summary["groups"]
    enhanced, lexical = confirm["confirmation/code_rag"], confirm["confirmation/lexical"]
    rows += ["## 结果与失败解释", "",
             f"新确认 code_rag 与 lexical 均严格解决 {enhanced['solved']}/6；完整参考进入实际输入分别为 {enhanced['complete_reference_tasks']}/6 和 {lexical['complete_reference_tasks']}/6。候选增强改善一题取证，但没有净增严格解决。",
             "开发集增强新增解决两题、丢失两题；确认集双方解决相同三题。不能据此宣称增强整体更优，也不据这组小样本推断所有代码库。",
             "non-secure 新题在 code_rag 下读到正确实现，发送前总预留3613 token（含2048输出），实际输入1437；最终反复解释并用尽输出上限，失败保留。输入窗口不是该题未交付的原因，未自动扩预算或重生成。",
             "parse_dict_header 的多个答案给出了正确覆盖/None结论，却没有解释无等号后跳过键值拆分；这项机制是题目明确要求，按完整性不足记录，不靠关键词判分。",
             "Flask 开发题只询问注册保护的目的，正确解释setup报错即接受；没有把参考清单中额外的first_registration对比强加为必答项。",
             "mitt.emit('*') 的计数及参数误读、first_true 对假值的误解和测试存在性幻觉仍显示模型解释的限制。匿名自审判断保留逐条理由，可由其他评审修订为新版本。", "",
             "## 解释边界", "",
             "- 五个条件共用问题、模型、提示、16K 总预算和 2048 输出上限；每题每条件一次，没有调参后重生成。",
             "- lexical 与 code_rag 比较整组精确符号/一跳候选增强；相同语法块与窗口，不独立归因单个通道。",
             "- grep_read 是冻结的字面标识符排序与有界读取启发式，不代表熟练开发者的自适应 grep，也不覆盖所有代码搜索实现。",
             "- located_file 用同一字面定位器选第一文件；完整原文超预算则记不可行，不静默截断。文件定位错误同样计失败。",
             "- none 只能依据本次上下文回答源码事实，模型记忆猜对也没有提供可核验的仓库证据。",
             "- 完整参考输入衡量参考行段覆盖；允许有效替代证据，不能把覆盖率等同于严格解决率。",
             "- 整文件预检失败的候选原文未送入模型，实际输入覆盖计零，预期覆盖另存 planned_complete_reference_tasks。",
             "- 时间统一从打开快照/索引、选源、打包到回答完成；元数据预检排除。索引磁盘缓存已存在，模型/前缀缓存未控制，每组最多六个样本，不是生产 SLO。",
             "- 只读源码推导，不执行目标仓库安装、测试或任意代码；评测与模拟轨迹全部禁止进入训练。", "",
             "## 成本与复现", "",
             f"新请求 {summary['new_requests']}/60，本地付费 0；历史只读账本 {summary['historical_reserved']}，合计 {summary['combined_requests']}/600。",
             "新请求使用独立账本，不向已冻结历史账本追加记录。失败不退款、不自动重试。",
             "协议/任务/源码/实际输入/原始回答/匿名包/评分分别保存；全部自审保存后发布揭盲映射与逐题对照，匿名自审仍不等于独立盲评。",
             "完整离线237 passed、1 deselected，Ruff通过，60条件实际零HTTP回放；详见[离线检查回执](offline-checks.json)。工程检查不替代语义判断。",
             "离线回放：`D:\\anaconda\\envs\\repomentor\\python.exe -s scripts/rag_interview.py replay`；该命令实际阻断所有 HTTP 发送。", "",
             "模拟试用见 [simulated-study.json](simulated-study.json)，匿名评审见 [review/packet.md](review/packet.md)，逐题理由见 [comparison.md](comparison.md)，汇总见 [summary.json](summary.json)。", ""]
    (OUT / "report.md").write_text("\n".join(rows), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("review-page", "export-review", "packet", "simulate", "comparison", "report", "summary", "finalize"))
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    if args.action == "review-page":
        review_page(args.offset, args.limit)
    else:
        {"export-review": publish_review, "packet": packet_markdown, "simulate": simulate, "report": report,
         "comparison": comparison, "summary": corrected_summary, "finalize": finalize}[args.action]()


if __name__ == "__main__":
    main()
