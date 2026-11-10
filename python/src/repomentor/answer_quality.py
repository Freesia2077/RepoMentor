"""Output diagnostics: deterministic defects, never a semantic correctness score."""

import re

from .source_windows import citation_validity


def inspect_answer(answer, context, status):
    issues = []
    if status != "success":
        issues.append({"code": "generation_" + status, "severity": "error"})
    if answer is not None:
        if not citation_validity(answer, context):
            issues.append({"code": "invalid_citations", "severity": "error"})
        text = answer.get("answer", "").rstrip()
        prose = re.sub(r"```[\s\S]*?```", "", text)
        dangling_value = re.search(r"(?:结果|返回值)(?:为|是)(?:字符串|整数|数值|布尔值)?\s*$", prose)
        if (
            text.endswith(("(", "（", "：", ":", ",", "，"))
            or text.count("```") % 2
            or prose.count("`") % 2
            or dangling_value
        ):
            issues.append({"code": "possibly_incomplete_text", "severity": "review"})
    return {
        "version": "answer-diagnostics-v2",
        "issues": issues,
        "semantic_correctness": "not_scored",
        "complete": None,
        "human_approved": False,
    }
