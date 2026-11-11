"""Three SDK agents in a deterministic report workflow, sharing the QA tools and ledger."""

# 用固定的报告阶段组织多个 SDK Agent，各阶段内部仍由 Runner 执行工具循环。
# 前序摘要帮助导航，跨阶段可复用的事实来源通过原始源码再次核验。

from __future__ import annotations

import asyncio
import time
from uuid import uuid4

from .agent_prompts import RESEARCH_SYSTEM
from .agent_tools import AgentLimits
from .contracts import ContributorOutput, ExplorerOutput, MentorOutput, RunResult
from .execution import CancellationToken, HarnessError
from .repository import build_profile, is_test
from .runtimes.local_report import QUESTIONS
from .sdk_agent import SdkAgentEngine, merged_context, sdk_version
from .source_windows import source_span

STAGES = (("explorer", ExplorerOutput), ("mentor", MentorOutput), ("contributor", ContributorOutput))


# 从入口、配置、文档和测试候选挑选原始片段，作为各报告阶段的共同取证起点。
def report_sources(reader, profile, max_bytes):
    """Bounded original entry/config/docs/test excerpts, not generated summaries."""
    entries = sorted(
        (
            p
            for p in profile["entryCandidates"]
            if not is_test(p) and not p.endswith(".d.ts") and not p.startswith("examples/")
        ),
        key=lambda p: (p.startswith("."), p.count("/"), p),
    )
    paths = entries[:1] + [m["path"] for m in profile["manifests"][:2]]
    paths += [profile["readme"]["path"]] if profile["readme"] else []
    paths += profile["testCandidates"][:1]
    spans = []
    for path in dict.fromkeys(paths):
        text, _ = reader.read(path, 500_000)
        lines, selected, used = text.splitlines(), [], 0
        remaining = max_bytes - sum(len(s["content"].encode()) for s in spans)
        for line in lines[:200]:
            cost = len(line.encode()) + bool(selected)
            if used + cost > min(6000, remaining):
                break
            selected.append(line)
            used += cost
        if selected:
            spans.append(source_span(path, lines, 1, len(selected)))
    return spans


class SdkReport:
    """Code selects the report stages; each Agent's tool loop is managed by SDK Runner."""

    def __init__(self, reader, index, runtime, cache_dir, *, limits=None, refresh=False, share_sources=True):
        self.reader, self.index, self.runtime = reader, index, runtime
        self.cache_dir, self.refresh = cache_dir, refresh
        self.limits = limits or AgentLimits(max_tool_calls=6, max_decisions=6)
        self.records = []
        self.share_sources = share_sources

    # 阶段次序由代码固定，每阶段研究循环由 Runner 管理，摘要与可复用原文分别传递。
    async def run(self, *, emit=lambda *args: None, token=None, run_id=None, total_timeout=600):
        started = time.monotonic()
        token = token or CancellationToken()
        self.records = []
        outputs, summaries = {}, {}
        shared = []
        status, reason, error = "success", "report_completed", None
        profile = build_profile(self.reader)
        metadata = {key: profile[key] for key in ("fileCount", "entryCandidates", "testCandidates")}
        metadata["entryCandidates"] = metadata["entryCandidates"][:6]
        metadata["testCandidates"] = metadata["testCandidates"][:6]
        metadata["available_files"] = profile["fileIndex"][:80]
        metadata["file_list_incomplete"] = profile["fileIndexTruncated"] or len(profile["fileIndex"]) > 80
        seed_budget = min(12000, self.limits.max_source_bytes)
        anchors = report_sources(self.reader, profile, seed_budget)
        try:
            async with asyncio.timeout(total_timeout):
                for stage, output_type in STAGES:
                    token.check()
                    instruction = (
                        RESEARCH_SYSTEM + f"当前角色是 {stage}，为仓库学习报告完成本阶段。"
                        "前序角色摘要只提供导航；verified_source 为已重新核验的原始源码，可复用，缺口再用工具补查。"
                        "最多 3 条 evidenceClaims、3 个模块、3 步阅读路径、2 个贡献建议。"
                        "建议必须标为建议，不得冒充真实 issue；未验证的安装/测试命令置 null。"
                        "evidenceCoverage.examinedFiles 只填实际读取的文件。未知项用 null、空列表或 gaps。"
                        "readingPath/entryPoints/entryFiles 只能填本轮已读文件，moduleMap 只填已读模块。"
                        "dependencyGraph 的键和值只填本轮已读的本地文件/目录，未知边用空对象。"
                        "此时只研究，不撰写完整报告或 JSON；最多四条短要点，每条含源码位置和事实或缺口。"
                        "报告字段由后续整理阶段填写，不在研究阶段展开章节、阅读路径或总结。"
                        "只看到类型声明或文件前缀不能推断没有实现；需要判断行为时先续读实际函数体。"
                    )
                    engine = SdkAgentEngine(
                        self.reader, self.index, self.runtime, self.cache_dir, self.limits
                    )
                    row = await engine.answer(
                        QUESTIONS[stage],
                        token=token,
                        refresh=self.refresh,
                        output_type=output_type,
                        instruction=instruction,
                        agent_name="RepoMentor-" + stage,
                        task_context={"repository_metadata": metadata, "prior_roles": summaries},
                        shared_context={
                            "snapshot_id": self.reader.snapshot.snapshot_id,
                            "spans": merged_context([*anchors, *(shared if self.share_sources else [])]),
                        },
                        shared_source_bytes=seed_budget,
                    )
                    self.records.append({"stage": stage, **row})
                    if row["completion"]["status"] != "success":
                        raise HarnessError(
                            row["stop_reason"], "SDK report stage failed; raw output preserved"
                        )
                    output = output_type.model_validate(row["answer"])
                    from .report_constraints import constrain_report

                    constrained, changes = constrain_report(
                        self.reader, output.model_dump(mode="json"), row["final_context"]
                    )
                    output = output_type.model_validate(constrained)
                    self.records[-1]["publication_adjustments"] = changes
                    from .report_validation import inspect_report_locations

                    self.records[-1]["publication_locations"] = inspect_report_locations(
                        self.reader, constrained, row["final_context"]
                    )
                    shared.extend(s for s in row["final_context"] if s not in shared)
                    # Deterministic coverage records where the model actually obtained source.
                    output.evidenceCoverage.examinedFiles = sorted({s["path"] for s in row["final_context"]})[
                        :24
                    ]
                    if stage == "explorer":
                        output.fileCount = profile["fileCount"]
                    outputs[stage] = output.model_dump(mode="json")
                    summaries[stage] = {
                        "overview": (
                            outputs[stage].get("projectSummary")
                            or outputs[stage].get("architectureOverview", "")
                        )[:800],
                        "claims": outputs[stage]["evidenceClaims"][:3],
                        "read_paths": output.evidenceCoverage.examinedFiles[:8],
                    }
                    emit(
                        stage, "completed", {"stage": stage, "new_model_requests": row["new_model_requests"]}
                    )
        except asyncio.CancelledError:
            token.cancel()
            status, reason = "cancelled", "cancelled"
        except TimeoutError:
            token.cancel()
            status, reason = "timeout", "total_timeout"
        except HarnessError as exc:
            reason = exc.code
            status = "cancelled" if token.cancelled else "failed"
        except Exception as exc:
            status, reason = "failed", type(exc).__name__
        if status != "success":
            error = {
                "code": reason,
                "message": "SDK report stopped; inspect sdk-stages.json and attempt ledger",
            }
        version = sdk_version()
        result = RunResult(
            run_id=run_id or uuid4().hex,
            snapshot_id=self.reader.snapshot.snapshot_id,
            runtime="openai-agents-sdk-report",
            status=status,
            stop_reason=reason,
            outputs=outputs,
            usage=[
                {
                    k: r[k]
                    for k in (
                        "stage",
                        "attempt_id",
                        "new_model_requests",
                        "cache_hit",
                        "total_model_requests",
                        "elapsed_seconds",
                    )
                }
                for r in self.records
            ],
            elapsed_seconds=round(time.monotonic() - started, 3),
            error=error,
            versions={
                "openai_agents": version["openai_agents"],
                "openai": version["openai"],
                "sdk_implementation": version["implementation"],
                "model": self.runtime.settings.model,
                "config_id": self.runtime.settings.config_id,
                "index_id": self.index.index_id,
            },
            harness={
                "workflow": "sequential-sdk-agents",
                "limits_per_stage": self.limits.model_dump(),
                "source_sharing": self.share_sources,
                "max_model_attempts": 3 * self.limits.max_decisions,
                "tools": "shared RepositoryEnvironment",
                "semantic_correctness": "not_scored",
            },
        )
        emit("report", "finished", {"status": status, "reason": reason})
        return result
