"""Keep raw model reports; constrain published graph/commands to visible static declarations."""

# 把可见来源约束应用到报告字段，集中处理阶段输出与证据范围的匹配。

import copy

from .answer_audit import command_audit, dependency_audit


# 只允许输出绑定当前上下文中已有的来源，保留缺证据字段的明确处理。
def constrain_report(reader, value, context):
    result, changes = copy.deepcopy(value), []
    if "dependencyGraph" in result:
        edges = dependency_audit(reader, context, result["dependencyGraph"])
        graph = {}
        for edge in edges:
            if edge["status"] == "supported_static_import" and edge["source_visible_to_model"]:
                graph.setdefault(edge["from"], []).append(edge["to"])
            else:
                changes.append(
                    {"field": "dependencyGraph", "original": edge, "action": "omitted_unverified_edge"}
                )
        result["dependencyGraph"] = graph
    setup = result.get("contributionSetup")
    if setup:
        audits = {r["field"]: r for r in command_audit(reader, context, setup)}
        visible_text = "\n".join(s["content"] for s in context)
        for field, value in list(setup.items()):
            if not value or not isinstance(value, str):
                continue
            audit = audits.get(field)
            supported = audit and audit["status"] == "declared_script" and audit["source_visible_to_model"]
            supported = supported or (not audit and value in visible_text)
            if not supported:
                setup[field] = None
                changes.append(
                    {"field": "contributionSetup." + field, "original": value, "action": "set_unknown"}
                )
    if changes:
        gap = {
            "kind": "missing_evidence",
            "subject": "发布报告中的依赖边与命令声明",
            "summary": "部分依赖边或命令缺少已见源码/声明支持，发布报告已省略；原始模型输出留档。",
            "severity": "medium",
        }
        gaps = result["evidenceCoverage"]["gaps"]
        if len(gaps) < 8:
            gaps.append(gap)
        else:
            # Preserve all original gaps in the raw output and adjustment ledger.
            # Published Coverage has a hard eight-item schema limit.
            changes.append(
                {
                    "field": "evidenceCoverage.gaps",
                    "original": gaps[-1],
                    "action": "replaced_last_gap_to_record_publication_constraint",
                }
            )
            gaps[-1] = gap
    return result, changes
