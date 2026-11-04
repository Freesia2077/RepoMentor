"""Versioned wire contracts. Report field names intentionally match the TS edition."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = "repomentor.v1"
Phase = Literal["explorer", "mentor"]
StageName = Literal["explorer", "mentor", "contributor"]
ToolName = Literal["search_symbols", "trace_module_dependencies", "find_related_tests"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Citation(Contract):
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class RepositoryAnswer(Contract):
    answer: str = Field(min_length=1, description="用中文完整回答题目各要点：直接结论、必要机制和条件、题干纠偏或具体信息缺口。主要事实要由引用支持。")
    citations: list[Citation] = Field(description="仅列实际可见且支持正文的源码行段；逻辑性拒答无需虚构引文。")
    insufficient_evidence: bool = Field(description="无法回答必要子问、缺必要源码或私有运行数据时为true；正文拒答时不得填false。")


ANSWER_SCHEMA = RepositoryAnswer.model_json_schema()


class ManifestEntry(Contract):
    path: str
    oid: str
    size: int
    mode: str


class RepositorySnapshot(Contract):
    schema_version: str = SCHEMA_VERSION
    snapshot_id: str
    source: str
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    manifest: list[ManifestEntry]


class CodeChunk(Contract):
    snapshot_id: str
    chunk_id: str
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    content_hash: str
    content: str
    symbol: str | None = None
    truncated: bool = False


class SearchAction(Contract):
    tool: Literal["search_symbols"]
    query: str = Field(min_length=1, max_length=120)
    purpose: str = Field(min_length=1, max_length=300)

    @field_validator("query", "purpose")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("empty tool argument")
        return value


class PathAction(Contract):
    tool: Literal["trace_module_dependencies", "find_related_tests"]
    paths: list[Annotated[str, Field(min_length=1)]] = Field(min_length=1, max_length=6)
    purpose: str = Field(min_length=1, max_length=300)

    @field_validator("purpose")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("empty tool argument")
        return value


DiscoveryAction = Annotated[SearchAction | PathAction, Field(discriminator="tool")]


class FileRequest(Contract):
    path: str = Field(min_length=1)
    purpose: str = Field(min_length=1, max_length=300)
    priority: Literal["high", "medium", "low"]


class EvidencePlan(Contract):
    goal: str = Field(min_length=1, max_length=500)
    rationale: str = Field(max_length=1000)
    questions: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(max_length=6)
    actions: list[DiscoveryAction] = Field(max_length=4)
    files: list[FileRequest] = Field(max_length=12)
    stopConditions: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(
        min_length=1, max_length=4
    )


class SkillPolicy(Contract):
    skillNames: list[str]
    allowedTools: list[ToolName]
    preferredTools: list[ToolName]
    recommendedQuestions: list[str]
    evidenceRequirements: list[str]
    stopConditions: list[str]
    maxDiscoveryActions: int = Field(ge=0, le=4)
    maxEvidenceFiles: int = Field(ge=1, le=10)


class EvidenceFile(Contract):
    path: str
    content: str
    truncated: bool
    purpose: str
    phase: Phase


class Omission(Contract):
    path: str
    reason: Literal[
        "not_tracked",
        "file_limit",
        "budget_exhausted",
        "oversized",
        "unreadable",
        "duplicate_request",
        "already_available",
    ]


class EvidenceBundle(Contract):
    files: list[EvidenceFile] = Field(default_factory=list)
    skippedPaths: list[str] = Field(default_factory=list)
    omissions: list[Omission] = Field(default_factory=list)
    totalBytes: int = 0


class Budget(Contract):
    maxEvidenceBatches: int = 2
    maxFilesRead: int = 18
    maxEvidenceBytes: int = 88_000
    usedEvidenceBatches: int = 0
    filesRead: int = 0
    evidenceBytes: int = 0


class StageState(Contract):
    status: Literal["pending", "planned", "evidence_ready", "completed"] = "pending"
    plan: EvidencePlan | None = None
    examinedPaths: list[str] = Field(default_factory=list)
    skippedPaths: list[str] = Field(default_factory=list)
    unresolvedQuestions: list[str] = Field(default_factory=list)
    skillPolicy: SkillPolicy | None = None
    discoveryActionsUsed: int = 0
    stopReason: str | None = None


class ToolCall(Contract):
    call_id: str
    tool: str
    arguments: dict[str, Any]


class ToolObservation(Contract):
    stage: Phase
    tool: ToolName
    purpose: str
    summary: str
    paths: list[str]
    metadata: dict[str, Any]


class RunEvent(Contract):
    schema_version: str = SCHEMA_VERSION
    run_id: str
    sequence: int
    stage: str
    kind: str
    data: dict[str, Any] = Field(default_factory=dict)


class EvidenceReference(Contract):
    path: str = Field(min_length=1)
    supports: str = Field(min_length=1, max_length=300)


class EvidenceClaim(Contract):
    claim: str = Field(min_length=1, max_length=600)
    confidence: Literal["high", "medium", "low"]
    evidence: list[EvidenceReference] = Field(max_length=4)


class EvidenceGap(Contract):
    kind: Literal["missing_evidence", "test_absent", "coverage_limit", "out_of_scope"]
    subject: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=500)
    severity: Literal["high", "medium", "low"]


class Coverage(Contract):
    examinedFiles: list[str] = Field(max_length=24)
    gaps: list[EvidenceGap] = Field(max_length=8)


class EvidenceOutput(Contract):
    evidenceClaims: list[EvidenceClaim] = Field(max_length=8)
    evidenceCoverage: Coverage


class ProjectType(Contract):
    primary: str = Field(min_length=1)
    secondary: list[str]


class TechStack(Contract):
    language: str | None
    framework: str | None
    buildTool: str | None


class EntryPoint(Contract):
    file: str
    role: str


class ModuleInfo(Contract):
    path: str
    responsibility: str = Field(max_length=300)
    importance: Literal["core", "support", "utility"]
    justification: str

    @field_validator("importance", mode="before")
    @classmethod
    def legacy_support(cls, value):
        return "support" if value == "supporting" else value


class ExplorerOutput(EvidenceOutput):
    projectType: ProjectType
    techStack: TechStack
    fileCount: int = Field(ge=0)
    entryPoints: list[EntryPoint] = Field(max_length=10)
    moduleMap: list[ModuleInfo] = Field(max_length=6)
    directorySummary: str = Field(max_length=1500)
    projectSummary: str = Field(max_length=800)


class ReadingStep(Contract):
    step: int = Field(gt=0)
    file: str
    why: str


class KeyPattern(Contract):
    pattern: str
    where: str
    description: str


class CodeConvention(Contract):
    rule: str
    example: str


class MentorOutput(EvidenceOutput):
    architectureOverview: str = Field(max_length=3000)
    dependencyGraph: dict[str, list[str]]
    readingPath: list[ReadingStep] = Field(max_length=5)
    keyPatterns: list[KeyPattern] = Field(max_length=10)
    codeConventions: list[CodeConvention] = Field(max_length=10)


class GoodFirstIssue(Contract):
    area: str
    difficulty: Literal["easy", "medium", "hard"]
    description: str


class ContributionSetup(Contract):
    devEnv: str | None
    build: str | None
    test: str | None
    lint: str | None = None


class EntryFile(Contract):
    file: str
    description: str
    reason: str


class NewcomerNote(Contract):
    tip: str


class ContributorOutput(EvidenceOutput):
    goodFirstIssues: list[GoodFirstIssue] = Field(max_length=6)
    contributionSetup: ContributionSetup
    entryFiles: list[EntryFile] = Field(max_length=10)
    notesForNewcomers: list[NewcomerNote] = Field(max_length=10)


class RunResult(Contract):
    schema_version: str = SCHEMA_VERSION
    run_id: str
    snapshot_id: str
    runtime: str
    status: Literal["success", "failed", "cancelled", "timeout"]
    outputs: dict[str, Any] = Field(default_factory=dict)
    stop_reason: str
    usage: list[dict[str, Any]] = Field(default_factory=list)
    elapsed_seconds: float = 0
    error: dict[str, str] | None = None
    versions: dict[str, str] = Field(default_factory=dict)
    harness: dict[str, Any] = Field(default_factory=dict)
