"""Offline readable exports of an existing answer and its local trajectory."""

import json


def render_run(record):
    if "report" in record or "outputs" in record:
        return render_report(record)
    row = record.get("result", record)
    answer = row.get("answer") or {}
    completion = row.get("completion", {})
    status = completion.get("status", record.get("status", "unknown"))
    body = answer.get("answer", json.dumps(answer, ensure_ascii=False, indent=2))
    if status != "success" and not answer:
        body = (
            f"本次未发布有效答案。错误：{completion.get('error_code') or status}；"
            f"结束原因：{completion.get('finish_reason') or 'unknown'}。原始输出保留在原记录。"
        )
        if status == "truncated":
            body += (
                "\n\n输出已截断；先检查重复生成或答案过长。不要补齐 JSON 后当作成功，也不要接着续写重复草稿。"
            )
        elif status == "context_limit":
            body += "\n\n输入与输出预留超过上下文窗口；先缩小相关源码范围，整文件对照需单独调整上下文。"
    lines = [
        "# RepoMentor 运行记录",
        "",
        str(row.get("question", record.get("question", ""))),
        "",
        f"生成状态：{status}；原运行新增模型请求：{row.get('new_model_requests', 'unknown')}；"
        f"耗时：{row.get('elapsed_seconds', row.get('completion', {}).get('elapsed_seconds', 'unknown'))} 秒",
        "",
        body,
        "",
    ]
    refs = list(
        dict.fromkeys((r["path"], r["start_line"], r["end_line"]) for r in answer.get("citations", []))
    )
    spans = (
        row.get("final_context", row.get("context", []))
        if refs
        else [*row.get("context", []), *row.get("final_context", [])]
    )
    lines.extend(["## 引用源码" if refs else "## 本轮保存源码（模型未引用，供继续核对）", ""])
    lines.extend(["模型引用：" + "、".join(f"{p}:{a}-{z}" for p, a, z in refs), ""] if refs else [])
    sources = {tuple(s[k] for k in ("path", "start_line", "end_line", "content")): s for s in spans}
    for span in sources.values():
        if refs and not any(
            p == span["path"] and a <= span["end_line"] and z >= span["start_line"] for p, a, z in refs
        ):
            continue
        label = "已交付上下文" if refs else "保存源码"
        lines.extend(
            [
                f"### {label} {span['path']}:{span['start_line']}-{span['end_line']}",
                "",
                "```text",
                *(f"{i}: {line}" for i, line in enumerate(span["content"].split("\n"), span["start_line"])),
                "```",
                "",
            ]
        )
    lines.extend(["## 工具轨迹", "", "| 工具 | 状态 | 来源位置 |", "| --- | --- | --- |"])
    for step in row.get("tool_trace", []):
        locations = "; ".join(
            f"{s['path']}:{s['start_line']}-{s['end_line']}" for s in step.get("source_spans", [])
        )
        lines.append(f"| {step['action']['tool']} | {step['observation']['status']} | {locations} |")
    lines.extend(
        [
            "",
            "## 质量边界",
            "",
            "格式和来源校验不等于语义正确；以下仅为确定性诊断。",
            "",
            "```json",
            json.dumps(row.get("quality", {}), ensure_ascii=False, indent=2),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def render_report(record):
    """Readable saved report; never infer semantic approval from generation success."""
    report = record.get("report", record)
    stages = {r["stage"]: r for r in record.get("stages", [])}
    lines = [
        "# RepoMentor 仓库阅读报告",
        "",
        f"生成状态：{report.get('status', 'unknown')}；停止原因：{report.get('stop_reason', 'unknown')}",
        "",
        "以下为保存的模型输出；生成成功和来源校验不代表内容已获语义审核。",
        "",
    ]
    for stage, title in (
        ("explorer", "仓库概览"),
        ("mentor", "实现与阅读路径"),
        ("contributor", "开发与贡献建议"),
    ):
        value = report.get("outputs", {}).get(stage)
        lines += ["## " + title, ""]
        if value is None:
            lines += ["本阶段未发布成功结果；原始失败见运行记录。", ""]
            continue
        for key in ("projectSummary", "architectureOverview", "directorySummary"):
            if value.get(key):
                lines += [value[key], ""]
        for item in value.get("readingPath", []):
            lines += [f"- {item['file']}：{item['why']}"]
        if value.get("contributionSetup"):
            lines += ["", "声明的开发命令（未执行目标仓库）：", ""]
            lines += [
                f"- {k}：{v if v is not None else '未确认'}" for k, v in value["contributionSetup"].items()
            ]
        for item in value.get("goodFirstIssues", []):
            lines += ["", "建议：" + item["description"]]
        lines += ["", "### 事实与来源", ""]
        for claim in value.get("evidenceClaims", []):
            lines += ["- " + claim["claim"]]
            lines += [f"  - {ref['path']}：{ref['supports']}" for ref in claim["evidence"]]
        lines += ["", "### 阅读缺口", ""]
        lines += ["- " + gap["summary"] for gap in value.get("evidenceCoverage", {}).get("gaps", [])]
        if stage in stages:
            row = stages[stage]
            lines += [
                "",
                f"本阶段新增请求：{row['new_model_requests']}；工具调用：{row['tool_calls']}；"
                f"耗时：{row['elapsed_seconds']} 秒。",
                "",
                "来源行号诊断：",
                "",
                "```json",
                json.dumps(row.get("publication_locations", {}), ensure_ascii=False, indent=2),
                "```",
                "",
            ]
    return "\n".join(lines)
