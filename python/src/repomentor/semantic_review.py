"""Validated, source-linked reviewer judgments; no automated semantic scoring or LLM calls."""

# 定义基于源码的评审量表和证据契约，分别判断事实、完整性、引用支持与拒答适当性。

import hashlib
import json
from collections import Counter, defaultdict

from pydantic import Field

from .answer_audit import visible
from .contracts import Contract


class Evidence(Contract):
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class ClaimReview(Contract):
    claim: str = Field(min_length=1)
    status: str
    rationale: str = Field(min_length=1)
    evidence: list[Evidence]


class Judgment(Contract):
    candidate_id: str
    correctness: str
    citation_support: str
    completeness: str
    abstention: str
    entry_scope: str
    consistency: str
    claims: list[ClaimReview] = Field(min_length=1)
    notes: str = Field(min_length=1)
    key_points: dict[str, bool] | None = None


RUBRIC = {
    "version": "repomentor.semantic-review.v1",
    "correctness": ["correct", "partially_correct", "incorrect", "unverifiable"],
    "citation_support": ["supported", "partial", "unsupported", "unverifiable", "not_applicable"],
    "completeness": ["adequate", "partial", "insufficient", "unverifiable"],
    "abstention": ["appropriate", "inappropriate", "not_applicable", "unverifiable"],
    "entry_scope": ["correct", "mixed", "unverifiable", "not_applicable"],
    "consistency": ["consistent", "contradictory", "unverifiable"],
    "claim_status": ["supported", "partial", "unsupported", "contradicted", "unverifiable"],
    "instructions": [
        "Judge source truth, support from the model's actual citations, and coverage separately.",
        "Split materially different factual assertions; review uncited prose as well as formal claims.",
        "A source-visible citation is not necessarily support. Check exact referenced lines.",
        "A correct answer may lack support; a warranted abstention may be incomplete. Record both.",
        "Use unverifiable for missing evidence, not incorrect or zero. Do not treat dataset answers as infallible.",
        "Workflow failure stays a failure regardless of the quality of a rejected parsed answer.",
        "Reviewer evidence can include extra snapshot source; it must not be sent to generation.",
        "This is selected development data, not an unbiased accuracy estimate or human approval.",
    ],
}


def candidate_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


# 把关键事实有据与任务完整解决分成两档，完整性、拒答和引用支持继续参与判断。
def quality_tiers(judgment, workflow_status):
    """Useful grounded facts versus complete, precisely cited task resolution."""
    citation_supported = judgment["citation_support"] in ("supported", "not_applicable")
    claims = judgment.get("claims", [])
    # Minor citation omissions can be useful only when the reviewer verified every
    # answered material claim against source actually delivered to this model.
    visible_support = bool(claims) and all(
        c["status"] == "supported" and any(e["visible_to_generator"] for e in c.get("verified_evidence", []))
        for c in claims
    )
    core = (
        workflow_status == "success"
        and judgment["correctness"] == "correct"
        and (citation_supported or (judgment["citation_support"] == "partial" and visible_support))
        and judgment["entry_scope"] in ("correct", "not_applicable")
        and judgment["consistency"] == "consistent"
        and judgment["abstention"] in ("appropriate", "not_applicable")
    )
    return {
        "core_supported": core,
        "fully_resolved": core and citation_supported and judgment["completeness"] == "adequate",
    }


# 先绑定候选身份，再核验评审所引原文与状态组合，避免评分脱离具体答案。
def validate_judgment(value, candidate, reader):
    judgment = Judgment.model_validate(value).model_dump()
    if judgment["candidate_id"] != candidate["candidate_id"]:
        raise ValueError("candidate ID mismatch")
    for key in (
        "correctness",
        "citation_support",
        "completeness",
        "abstention",
        "entry_scope",
        "consistency",
    ):
        if judgment[key] not in RUBRIC[key]:
            raise ValueError(f"invalid {key}")
    verified = []
    for claim in judgment["claims"]:
        if claim["status"] not in RUBRIC["claim_status"]:
            raise ValueError("invalid claim status")
        if claim["status"] in ("supported", "contradicted") and not claim["evidence"]:
            raise ValueError("supported/contradicted claims require source evidence")
        sources = []
        for ref in claim["evidence"]:
            if ref["path"] not in reader.paths:
                raise ValueError("review source is outside snapshot")
            content, truncated = reader.read(ref["path"], 500_000)
            start, end = ref["start_line"], ref["end_line"]
            if truncated or not 1 <= start <= end <= len(content.splitlines()):
                raise ValueError("invalid review source range")
            excerpt = "\n".join(content.splitlines()[start - 1 : end])
            sources.append(
                {
                    **ref,
                    "content": excerpt,
                    "content_sha256": hashlib.sha256(excerpt.encode()).hexdigest(),
                    "visible_to_generator": visible(candidate["observed_context"], ref["path"], start, end),
                }
            )
        verified.append({**claim, "verified_evidence": sources})
    if judgment["correctness"] == "correct" and any(
        c["status"] in ("contradicted", "unsupported") for c in verified
    ):
        raise ValueError("correct verdict conflicts with material claim judgments")
    if candidate["answer"] is None and judgment["correctness"] != "unverifiable":
        raise ValueError("missing answer cannot be scored as correct or incorrect")
    return {
        **judgment,
        "claims": verified,
        "candidate_sha256": candidate_digest(candidate),
        "reviewer": "Codex (current session)",
        "review_type": "model_assisted_nonblind_development_review",
        "human_approved": False,
        "training_eligible": False,
    }


def summarize_reviews(judgments, identities):
    groups = defaultdict(list)
    for row in judgments:
        groups[identities[row["candidate_id"]]["cohort"]].append(row)
    return {
        "cohorts": {
            name: {
                "unique_candidates": len(rows),
                **{
                    key: dict(Counter(r[key] for r in rows))
                    for key in (
                        "correctness",
                        "citation_support",
                        "completeness",
                        "abstention",
                        "entry_scope",
                        "consistency",
                    )
                },
            }
            for name, rows in groups.items()
        },
        "reviewer": "Codex",
        "blind": False,
        "human_approved": False,
        "overall_accuracy": "not_estimated_selected_development_cases",
        "training_eligible": False,
    }
