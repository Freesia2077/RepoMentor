"""The compatibility report pipeline. Directly callable without HTTP or a UI."""

# 调度仓库学习报告的固定阶段，连接 SDK、兼容运行时与结构化结果保存。

import asyncio
import time
from uuid import uuid4

from . import __version__
from .contracts import ContributorOutput, ExplorerOutput, MentorOutput, RunResult
from .execution import HarnessError
from .harness import ReportHarness
from .repository import RepositoryError
from .runtimes.claude import RuntimeFailure
from .skills import load_skill


# 按报告阶段组织任务和证据，将各阶段状态与最终结构化输出统一持久化。
async def run_report(
    reader, runtime, *, emit=lambda *args: None, token=None, run_id=None, total_timeout=600, project_types=()
):
    started = time.monotonic()
    usage_start = len(runtime.usage)
    run_id = run_id or uuid4().hex
    harness = ReportHarness(reader, emit, token)
    outputs = {}
    status, reason, error = "success", "report_completed", None
    try:
        async with asyncio.timeout(total_timeout):
            profile = harness.get_repository_map()
            for phase, model in (("explorer", ExplorerOutput), ("mentor", MentorOutput)):
                types = list(project_types)
                if "explorer" in outputs:
                    types += [outputs["explorer"]["projectType"]["primary"]]
                    types += outputs["explorer"]["projectType"]["secondary"]
                policy, skill = load_skill(types, phase)
                harness.activate_policy(phase, policy)
                if phase == "mentor" and harness.has_complete_coverage():
                    harness.reuse_evidence()
                else:
                    plan = harness.complete_coverage_plan(phase)
                    if plan is None:
                        plan = await runtime.plan(phase, harness, skill)
                    harness.execute_plan(phase, plan)
                payload = {
                    "repositoryProfile": profile,
                    "evidenceBundle": harness.evidence().model_dump(),
                    "harnessState": harness.context(),
                    **{k + "Output": v for k, v in outputs.items()},
                }
                output = await runtime.synthesize(phase, model, payload, harness, skill)
                output = harness.verify_output(phase, output, getattr(runtime, "provided_paths", None))
                outputs[phase] = output.model_dump()
                harness.complete_stage(phase)
                emit(phase, "completed", {"stage": phase})
            harness.token.check()
            payload = {
                "repositoryContext": profile,
                "contributionEvidence": harness.evidence().model_dump(),
                "commitSummary": reader.history(),
                **{k + "Output": v for k, v in outputs.items()},
            }
            output = await runtime.synthesize("contributor", ContributorOutput, payload, harness)
            outputs["contributor"] = harness.verify_output(
                "contributor", output, getattr(runtime, "provided_paths", None)
            ).model_dump()
            harness.complete_stage("contributor")
            emit("contributor", "completed", {"stage": "contributor"})
    except asyncio.CancelledError:
        harness.token.cancel()
        status, reason = "cancelled", "cancelled"
        error = {"code": reason, "message": "report cancelled"}
    except TimeoutError:
        harness.token.cancel()
        status, reason = "timeout", "total_timeout"
        error = {"code": reason, "message": "report deadline exceeded"}
    except (HarnessError, RuntimeFailure, RepositoryError) as exc:
        reason = exc.code
        status = "cancelled" if reason == "cancelled" else "timeout" if reason == "timeout" else "failed"
        error = {"code": exc.code, "message": str(exc)}
    except Exception as exc:
        status, reason = "failed", "internal_error"
        error = {"code": reason, "message": type(exc).__name__}
    result = RunResult(
        run_id=run_id,
        snapshot_id=reader.snapshot.snapshot_id,
        runtime=runtime.name,
        status=status,
        stop_reason=reason,
        outputs=outputs,
        usage=runtime.usage[usage_start:],
        elapsed_seconds=round(time.monotonic() - started, 3),
        error=error,
        versions={"repomentor": __version__, **runtime.versions},
        harness=harness.context(),
    )
    emit("report", "finished", {"status": status, "reason": reason})
    return result
