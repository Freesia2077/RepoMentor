"""Compatibility report stages, budgets and evidence ledger."""

# 兼容报告流程的状态与证据管理层，统一执行阶段顺序、文件预算和证据复用。
# 按路径维护最佳原文与缺口记录，Explorer、Mentor、Contributor 通过受控状态转换协作。

from __future__ import annotations

import json
import re
import threading
from copy import deepcopy
from pathlib import PurePosixPath

from pydantic import TypeAdapter, ValidationError

from .contracts import (
    Budget,
    DiscoveryAction,
    EvidenceBundle,
    EvidenceFile,
    EvidencePlan,
    Omission,
    SkillPolicy,
    StageState,
    ToolObservation,
)
from .execution import CancellationToken, HarnessError
from .repository import RepositoryError, SnapshotReader, build_profile, normalize_path, utf8_prefix
from .tools import find_tests, search_symbols, trace_dependencies


def deduplicate_requests(requests):
    ordered = sorted(
        enumerate(requests), key=lambda pair: ({"high": 0, "medium": 1, "low": 2}[pair[1].priority], pair[0])
    )
    seen = set()
    result = []
    for _, request in ordered:
        path = normalize_path(request.path)
        if path not in seen:
            result.append(request.model_copy(update={"path": path}))
            seen.add(path)
    return result


# 保留直接请求的优先级，同时为工具发现的新路径留少量名额，避免计划锁死在旧候选。
def select_requests(direct, discovered, maximum):
    direct = deduplicate_requests(direct)
    direct_paths = {r.path for r in direct}
    discovered = [r for r in deduplicate_requests(discovered) if r.path not in direct_paths]
    if not discovered:
        return direct[:maximum]
    slots = min(len(discovered), max(1, min(3, maximum // 3)))
    return deduplicate_requests(direct[: max(0, maximum - slots)] + discovered[:slots])


# 先准备各文件实际长度；预算不足时先给基础额度，再按优先级分配余量。
def build_evidence_bundle(reader, plan, phase, existing_paths=(), check=lambda: None):
    seen, existing = set(), set(map(normalize_path, existing_paths))
    candidates, omissions = [], []
    maximum = 10 if phase == "explorer" else 8
    for request in plan.files:
        check()
        path = normalize_path(request.path)
        reason = (
            "duplicate_request"
            if path in seen
            else "already_available"
            if path in existing
            else "not_tracked"
            if path not in reader.entries
            else "file_limit"
            if len(candidates) >= maximum
            else None
        )
        seen.add(path)
        if reason:
            omissions.append(Omission(path=path, reason=reason))
        else:
            candidates.append(request.model_copy(update={"path": path}))
    prepared = []
    for request in candidates:
        check()
        try:
            text, truncated = reader.read(request.path, 24_000)
            prepared.append((request, text, truncated))
        except RepositoryError as error:
            if error.code not in ("unreadable", "oversized", "not_tracked"):
                raise
            omissions.append(Omission(path=request.path, reason=error.code))
    targets = [len(text.encode()) for _, text, _ in prepared]
    total = 48_000 if phase == "explorer" else 40_000
    if sum(targets) <= total:
        allocations = targets
    else:
        # 先为每个候选保留基础空间，再分配剩余预算，避免前几个文件吃完全部额度。
        allocations = [min(n, 4000) for n in targets]
        remaining = total - sum(allocations)
        order = sorted(
            range(len(prepared)),
            key=lambda i: ({"high": 0, "medium": 1, "low": 2}[prepared[i][0].priority], i),
        )
        for i in order:
            granted = min(targets[i] - allocations[i], remaining)
            allocations[i] += granted
            remaining -= granted
    files = []
    for (request, text, truncated), amount in zip(prepared, allocations):
        check()
        content = utf8_prefix(text.encode(), amount)
        files.append(
            EvidenceFile(
                path=request.path,
                content=content,
                truncated=truncated or len(content.encode()) < len(text.encode()),
                purpose=request.purpose,
                phase=phase,
            )
        )
    return EvidenceBundle(
        files=files,
        omissions=omissions,
        skippedPaths=list(dict.fromkeys(o.path for o in omissions)),
        totalBytes=sum(len(f.content.encode()) for f in files),
    )


class ReportHarness:
    def __init__(self, reader: SnapshotReader, emit=lambda stage, kind, data: None, token=None):
        self.reader = reader
        self.emit = emit
        self.token = token or CancellationToken()
        self.profile = None
        self.budget = Budget()
        self.stages = {name: StageState() for name in ("explorer", "mentor", "contributor")}
        self.ledger = {}
        self.observations = {"explorer": {}, "mentor": {}}
        self.verified_paths = set()
        self._complete_requested = False
        self._lock = threading.RLock()

    def get_repository_map(self):
        self.token.check()
        self.profile = build_profile(self.reader)
        self.emit(
            "repository",
            "tool",
            {
                "tool": "get_repository_map",
                "files": self.profile["fileCount"],
                "snapshot_id": self.reader.snapshot.snapshot_id,
            },
        )
        return deepcopy(self.profile)

    def activate_policy(self, phase: str, policy: SkillPolicy):
        self.token.check()
        state = self.stages[phase]
        if state.status != "pending" or state.skillPolicy is not None:
            raise HarnessError("stage_order", "policy can only be activated once before planning")
        state.skillPolicy = policy.model_copy(deep=True)

    def _phase_ready(self, phase):
        self.token.check()
        if self.profile is None:
            raise HarnessError("profile_required", "repository map is required")
        if phase == "mentor" and self.stages["explorer"].status != "completed":
            raise HarnessError("stage_order", "mentor requires completed explorer")

    @staticmethod
    def fingerprint(action):
        value = {"tool": action.tool}
        if action.tool == "search_symbols":
            value["query"] = action.query
        else:
            value["paths"] = sorted(set(map(normalize_path, action.paths)))
        return json.dumps(value, sort_keys=True, ensure_ascii=False)

    def discover(self, phase: str, raw_action):
        with self._lock:
            self._phase_ready(phase)
            try:
                action = TypeAdapter(DiscoveryAction).validate_python(raw_action)
            except ValidationError as exc:
                raise HarnessError("invalid_arguments", "invalid discovery arguments") from exc
            state = self.stages[phase]
            if state.status not in ("pending", "planned"):
                raise HarnessError("stage_order", "discovery stage is closed")
            policy = state.skillPolicy
            if policy is None or action.tool not in policy.allowedTools:
                raise HarnessError("tool_denied", "tool is not allowed by the active policy")
            if action.tool != "search_symbols":
                action.paths = [normalize_path(p) for p in action.paths]
                if any(p not in self.profile["fileIndex"] for p in action.paths):
                    raise HarnessError("not_tracked", "discovery paths must be in the repository index")
            key = self.fingerprint(action)
            if key in self.observations[phase]:
                raise HarnessError("duplicate_action", "reuse the previous observation")
            if state.discoveryActionsUsed >= policy.maxDiscoveryActions:
                raise HarnessError("action_budget", "discovery action budget exhausted")
            if action.tool == "search_symbols":
                paths, metadata = search_symbols(self.reader, self.profile["fileIndex"], action.query)
            elif action.tool == "trace_module_dependencies":
                paths, metadata = trace_dependencies(self.reader, self.profile["fileIndex"], action.paths)
            else:
                paths, metadata = find_tests(self.profile["fileIndex"], action.paths)
            self.token.check()
            observation = ToolObservation(
                stage=phase,
                tool=action.tool,
                purpose=action.purpose,
                summary=f"Found {len(paths)} candidate paths; source text is not included.",
                paths=paths,
                metadata=metadata,
            )
            self.observations[phase][key] = observation
            state.discoveryActionsUsed += 1
            self.emit(phase, "tool", observation.model_dump())
            return observation.model_copy(deep=True)

    def record_plan(self, phase, plan):
        self._phase_ready(phase)
        state = self.stages[phase]
        if state.status != "pending":
            raise HarnessError("stage_order", "a stage can only register one plan")
        state.plan = plan.model_copy(deep=True)
        state.status = "planned"
        self.emit(phase, "plan", {"files": [r.path for r in plan.files], "actions": len(plan.actions)})

    def execute_plan(self, phase, plan: EvidencePlan):
        self._phase_ready(phase)
        state = self.stages[phase]
        policy = state.skillPolicy
        if policy is None:
            raise HarnessError("tool_denied", "policy must be activated")
        direct = []
        for request in deduplicate_requests(plan.files):
            if request.path in self.profile["fileIndex"]:
                direct.append(request)
            else:
                state.unresolvedQuestions.append(f"{request.path}: not_tracked")
        for request in direct[policy.maxEvidenceFiles :]:
            state.unresolvedQuestions.append(f"{request.path}: file_limit")
        direct = direct[: policy.maxEvidenceFiles]
        discovered = []
        from .contracts import FileRequest

        for action in plan.actions:
            key = self.fingerprint(action)
            if key in self.observations[phase]:
                observation = self.observations[phase][key]
            elif (
                action.tool in policy.allowedTools and state.discoveryActionsUsed < policy.maxDiscoveryActions
            ):
                observation = self.discover(phase, action.model_dump())
            else:
                state.unresolvedQuestions.append(f"{action.tool}: action_budget_or_denied")
                continue
            discovered.extend(
                FileRequest(path=p, purpose=action.purpose, priority="medium") for p in observation.paths
            )
        selected = select_requests(direct, discovered, policy.maxEvidenceFiles)
        selected_paths = {r.path for r in selected}
        for request in deduplicate_requests(direct + discovered):
            if request.path not in selected_paths:
                state.unresolvedQuestions.append(f"{request.path}: discovery_reservation")
        bounded = plan.model_copy(update={"files": selected}, deep=True)
        self.record_plan(phase, bounded)
        return self.read_batch(phase, bounded)

    # 完成受限读取与总预算校验后，再一起发布状态和证据，避免半完成读取污染账本。
    def read_batch(self, phase, plan):
        with self._lock:
            self._phase_ready(phase)
            state = self.stages[phase]
            if state.status != "planned" or state.plan != plan:
                raise HarnessError("stage_order", "read requires the registered plan")
            if self.budget.usedEvidenceBatches >= self.budget.maxEvidenceBatches:
                raise HarnessError("evidence_budget", "batch budget exhausted")
            bundle = build_evidence_bundle(
                self.reader,
                plan,
                phase,
                existing_paths=[
                    p for p, e in self.ledger.items() if e["bestEvidence"] and not e["bestEvidence"].truncated
                ],
                check=self.token.check,
            )
            next_files = self.budget.filesRead + len(bundle.files)
            next_bytes = self.budget.evidenceBytes + bundle.totalBytes
            if next_files > self.budget.maxFilesRead or next_bytes > self.budget.maxEvidenceBytes:
                raise HarnessError("evidence_budget", "global evidence budget exhausted")
            self.token.check()  # publish atomically after successful bounded reads
            self.budget.usedEvidenceBatches += 1
            self.budget.filesRead = next_files
            self.budget.evidenceBytes = next_bytes
            for file in bundle.files:
                self._merge(file)
            for omission in bundle.omissions:
                entry = self._entry(omission.path)
                benign = omission.reason in ("already_available", "duplicate_request")
                entry["attempts"].append(
                    {
                        "phase": phase,
                        "status": "reused" if benign else "missing",
                        "bytes": 0,
                        "reason": omission.reason,
                    }
                )
                if not benign and entry["bestEvidence"] is None:
                    entry["omission"] = omission
            reused = [o.path for o in bundle.omissions if o.reason == "already_available"]
            material = [
                o.path for o in bundle.omissions if o.reason not in ("already_available", "duplicate_request")
            ]
            state.examinedPaths = list(dict.fromkeys([f.path for f in bundle.files] + reused))
            state.skippedPaths = list(dict.fromkeys(material))
            state.unresolvedQuestions.extend(
                f"{r.path}: {r.purpose}" for r in plan.files if r.path in material
            )
            state.status = "evidence_ready"
            state.stopReason = (
                "global_evidence_budget_exhausted"
                if next_files >= self.budget.maxFilesRead or next_bytes >= self.budget.maxEvidenceBytes
                else "existing_evidence_reused"
                if not bundle.files and reused and not material
                else "no_valid_evidence_selected"
                if not bundle.files
                else "evidence_gaps_recorded"
                if state.unresolvedQuestions
                else "evidence_batch_completed"
            )
            self.emit(
                phase,
                "evidence",
                {
                    "paths": [f.path for f in bundle.files],
                    "bytes": bundle.totalBytes,
                    "omissions": [o.model_dump() for o in bundle.omissions],
                },
            )
            return bundle

    def _entry(self, path):
        return self.ledger.setdefault(path, {"bestEvidence": None, "omission": None, "attempts": []})

    # 同路径优先保留完整原文，其次保留较长片段，并清除已补齐的阶段缺口。
    def _merge(self, file):
        entry = self._entry(file.path)
        entry["attempts"].append(
            {
                "phase": file.phase,
                "status": "partial" if file.truncated else "available",
                "bytes": len(file.content.encode()),
            }
        )
        current = entry["bestEvidence"]
        if (
            current is None
            or (current.truncated and not file.truncated)
            or (
                current.truncated == file.truncated
                and len(file.content.encode()) > len(current.content.encode())
            )
        ):
            entry["bestEvidence"] = file.model_copy(deep=True)
        entry["omission"] = None
        for state in self.stages.values():
            state.skippedPaths = [p for p in state.skippedPaths if p != file.path]
            state.unresolvedQuestions = [
                q for q in state.unresolvedQuestions if q.split(":", 1)[0] != file.path
            ]

    def evidence(self):
        files = [e["bestEvidence"].model_copy(deep=True) for e in self.ledger.values() if e["bestEvidence"]]
        omissions = [e["omission"].model_copy(deep=True) for e in self.ledger.values() if e["omission"]]
        return EvidenceBundle(
            files=files,
            omissions=omissions,
            skippedPaths=[o.path for o in omissions],
            totalBytes=sum(len(f.content.encode()) for f in files),
        )

    # 仅当小仓库的完整覆盖条件满足时生成一次性计划，减少重复规划和二次读取。
    def complete_coverage_plan(self, phase):
        self._phase_ready(phase)
        state = self.stages[phase]
        if (
            phase != "explorer"
            or state.status != "pending"
            or not state.skillPolicy
            or self.profile["fileIndexTruncated"]
            or self.profile["fileCount"] > 60
        ):
            return None
        project = self.profile_paths()
        from .repository import SOURCE_EXTENSIONS

        extensions = SOURCE_EXTENSIONS | {
            ".sh",
            ".ipynb",
            ".html",
            ".css",
            ".scss",
            ".sass",
            ".less",
            ".sql",
            ".proto",
            ".graphql",
            ".gql",
            ".md",
            ".mdx",
            ".rst",
        }
        ignored = {
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".webp",
            ".bmp",
            ".ico",
            ".svg",
            ".mp3",
            ".mp4",
            ".wav",
            ".mov",
            ".pdf",
            ".zip",
            ".gz",
            ".woff",
            ".woff2",
            ".ttf",
            ".lock",
        }
        candidates = []
        for path in self.profile["fileIndex"]:
            if path in project:
                continue
            suffix = PurePosixPath(path).suffix.lower()
            if suffix in extensions and not re.search(
                r"(^|/)(node_modules|vendor|dist|build|coverage)/", path
            ):
                candidates.append(path)
            elif suffix not in ignored and not re.match(
                r"^(licen[cs]e|copying|notice)(\..*)?$|^\.(gitignore|gitattributes|editorconfig|npmignore)$",
                PurePosixPath(path).name,
                re.I,
            ):
                return None
        if not candidates or len(candidates) > state.skillPolicy.maxEvidenceFiles:
            return None
        self._complete_requested = True
        return EvidencePlan(
            goal="Cover the small repository",
            rationale="All relevant files fit the policy",
            questions=state.skillPolicy.recommendedQuestions[:6],
            actions=[],
            files=[
                {
                    "path": p,
                    "purpose": "Verify implementation and tests",
                    "priority": "high"
                    if p in self.profile["entryCandidates"] + self.profile["testCandidates"]
                    else "medium",
                }
                for p in candidates
            ],
            stopConditions=state.skillPolicy.stopConditions[:4],
        )

    def has_complete_coverage(self):
        state = self.stages["explorer"]
        return bool(
            self._complete_requested
            and state.status in ("evidence_ready", "completed")
            and state.plan
            and state.plan.files
            and not state.skippedPaths
            and all(
                r.path in self.ledger
                and self.ledger[r.path]["bestEvidence"]
                and not self.ledger[r.path]["bestEvidence"].truncated
                for r in state.plan.files
            )
        )

    def reuse_evidence(self):
        self._phase_ready("mentor")
        if not self.has_complete_coverage():
            raise HarnessError("incomplete_coverage", "only complete small-repository evidence can be reused")
        plan = EvidencePlan(
            goal="Reuse evidence",
            rationale="Complete explorer coverage",
            questions=[],
            actions=[],
            files=[],
            stopConditions=["Use existing evidence"],
        )
        self.record_plan("mentor", plan)
        state = self.stages["mentor"]
        state.status = "evidence_ready"
        state.examinedPaths = [f.path for f in self.evidence().files]
        state.stopReason = "existing_evidence_reused"
        self.emit("mentor", "reuse", {"paths": state.examinedPaths})

    def complete_stage(self, stage):
        self.token.check()
        if stage == "contributor":
            if self.stages["mentor"].status != "completed" or self.stages[stage].status == "completed":
                raise HarnessError("stage_order", "contributor requires completed mentor, and runs once")
        elif self.stages[stage].status != "evidence_ready":
            raise HarnessError("stage_order", "evidence must be ready before completing a stage")
        self.stages[stage].status = "completed"

    def profile_paths(self):
        files = [self.profile["readme"]] if self.profile["readme"] else []
        for key in ("manifests", "exampleManifests", "configFiles", "guidanceFiles"):
            files += self.profile[key]
        return {f["path"] for f in files}

    # 结构与来源在输出边界复核，移除超出已读范围的引用并记录检查结果。
    def verify_output(self, stage, output, provided_paths=None):
        value = output.model_dump()
        allowed = {f.path for f in self.evidence().files if stage != "explorer" or f.phase == "explorer"}
        allowed |= self.profile_paths()
        if stage != "explorer":
            allowed |= self.verified_paths
        if provided_paths is not None:
            allowed &= set(map(normalize_path, provided_paths))
        unsupported = removed = 0
        for claim in value["evidenceClaims"]:
            before = len(claim["evidence"])
            claim["evidence"] = [ref for ref in claim["evidence"] if normalize_path(ref["path"]) in allowed]
            removed += before - len(claim["evidence"])
            if not claim["evidence"]:
                claim["confidence"] = "low"
                unsupported += 1
            elif len(claim["evidence"]) < before and claim["confidence"] == "high":
                claim["confidence"] = "medium"
            self.verified_paths.update(normalize_path(ref["path"]) for ref in claim["evidence"])
        gaps = []
        for p, entry in self.ledger.items():
            if stage == "explorer" and not any(a["phase"] == stage for a in entry["attempts"]):
                continue
            file = entry["bestEvidence"]
            if file is None or file.truncated:
                gaps.append(
                    {
                        "kind": "missing_evidence" if file is None else "coverage_limit",
                        "subject": p[:200],
                        "summary": "Unavailable source"
                        if file is None
                        else "Only a bounded excerpt was read",
                        "severity": "medium",
                    }
                )
        gaps += [
            {
                "kind": "coverage_limit",
                "subject": q.split(":", 1)[0][:200],
                "summary": q[:500],
                "severity": "medium",
            }
            for q in self.stages[stage].unresolvedQuestions
        ]
        for gap in value["evidenceCoverage"]["gaps"]:
            entry = self.ledger.get(normalize_path(gap["subject"]))
            resolved = (
                provided_paths is None
                and entry
                and entry["bestEvidence"]
                and not entry["bestEvidence"].truncated
            )
            if (
                gap["kind"] != "out_of_scope"
                and gap["severity"] != "low"
                and not (resolved and gap["kind"] in ("coverage_limit", "missing_evidence"))
            ):
                gaps.append(gap)
        if unsupported:
            gaps.insert(
                0,
                {
                    "kind": "missing_evidence",
                    "subject": "unsupported_claims",
                    "summary": f"{unsupported} claims have no acceptable file reference",
                    "severity": "medium",
                },
            )
        unique = {}
        for gap in gaps:
            key = normalize_path(gap["subject"]).lower()
            if (
                key not in unique
                or {"low": 0, "medium": 1, "high": 2}[gap["severity"]]
                > {"low": 0, "medium": 1, "high": 2}[unique[key]["severity"]]
            ):
                unique[key] = gap
        value["evidenceCoverage"] = {"examinedFiles": sorted(allowed)[:24], "gaps": list(unique.values())[:8]}
        self.emit(
            stage,
            "verification",
            {
                "claims": len(value["evidenceClaims"]),
                "unsupported": unsupported,
                "removed_references": removed,
                "note": "path-membership validation; not semantic entailment",
            },
        )
        return type(output).model_validate(value)

    def context(self):
        return {
            "stages": {k: v.model_dump() for k, v in self.stages.items()},
            "budget": self.budget.model_dump(),
            "observations": [
                o.model_dump() for bucket in self.observations.values() for o in bucket.values()
            ],
            "ledger": {
                p: {
                    "status": "missing"
                    if e["bestEvidence"] is None
                    else "partial"
                    if e["bestEvidence"].truncated
                    else "available",
                    "attempts": deepcopy(e["attempts"]),
                }
                for p, e in self.ledger.items()
            },
        }


# Historical public name; new report orchestration uses ReportHarness explicitly.
RepositoryHarness = ReportHarness
