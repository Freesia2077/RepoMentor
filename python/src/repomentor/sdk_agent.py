"""Repository agent on OpenAI Agents SDK; immutable tools and experiment ledger stay local."""

# 将初始检索、只读工具、上下文过滤与最终提交接入 SDK Runner。
# 本模块协调准备、恢复和验收；证据仍由共享选源器与工具环境统一维护。

from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib.metadata
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from agents import (
    Agent,
    FunctionTool,
    GuardrailFunctionOutput,
    RunConfig,
    Runner,
    ToolExecutionConfig,
    ToolsToFinalOutputResult,
    output_guardrail,
)
from agents.exceptions import MaxTurnsExceeded, OutputGuardrailTripwireTriggered

from .agent_prompts import FINAL_SYSTEM, REPORT_FINAL_SYSTEM, SYSTEM
from .agent_tools import DEFINITIONS, AgentLimits, RepositoryEnvironment
from .contracts import Citation as Citation
from .contracts import RepositoryAnswer, ToolCall
from .entry_scope import resolve_entry
from .execution import CancellationToken, HarnessError
from .grounded_schema import GroundedReportSchema, grounded_report_schema
from .rag import RagEngine, messages_for
from .records import write
from .report_validation import inspect_report_locations, validate_report_paths
from .runtimes.chat import CompletionResult, fingerprint
from .runtimes.openai_sdk import error_code, sdk_input_budget
from .sdk_collaboration import ACTOR, CollaborationSettings, specialist_tools
from .sdk_context import ContextFilter, LocalHooks
from .shared_sources import select_sources
from .source_context import pack_observed_context
from .source_windows import citation_validity, render_sources, uncovered, verify_spans
from .symbol_navigation import symbol_navigation


def sdk_version():
    root = Path(__file__).resolve().parent
    names = (
        "sdk_agent.py",
        "sdk_report.py",
        "runtimes/openai_sdk.py",
        "agent_prompts.py",
        "records.py",
        "agent_tools.py",
        "tools.py",
        "repository.py",
        "rag.py",
        "retrieval.py",
        "hybrid_retrieval.py",
        "token_budget.py",
        "retrieval_scope.py",
        "source_context.py",
        "chunking.py",
        "report_validation.py",
        "entry_scope.py",
        "shared_sources.py",
        "sdk_collaboration.py",
        "conversation.py",
        "symbol_navigation.py",
        "grounded_schema.py",
        "source_windows.py",
        "code_navigation.py",
        "code_retrieval.py",
        "retrieval_factory.py",
        "retrieval_policy.py",
        "sdk_context.py",
        "answer_quality.py",
        "report_constraints.py",
        "answer_audit.py",
        "contracts.py",
        "execution.py",
    )
    return {
        "openai_agents": importlib.metadata.version("openai-agents"),
        "openai": importlib.metadata.version("openai"),
        "implementation": fingerprint(
            {n: hashlib.sha256((root / n).read_bytes().replace(b"\r\n", b"\n")).hexdigest() for n in names}
        ),
        "loop": "agents.Runner.run",
        "transport": "chat-completions-nonstream",
        "tracing": "disabled",
        "http_retries": 0,
        "output": "sdk-source-grounded-submit-finalize-v5",
    }


# 把统一工具环境适配为 SDK FunctionTool，并按 actor 记录调用、原文共享和检查点。
def repository_tools(environment, persist=lambda: None, *, actor="main"):
    tools = []
    for name, (description, schema) in DEFINITIONS.items():

        async def invoke(context, arguments, tool_name=name):
            environment.check()
            try:
                args = json.loads(arguments)
            except json.JSONDecodeError:
                args = None  # The shared environment records invalid arguments as an observation.
            result = environment.step(
                ToolCall(
                    call_id=context.tool_call_id if actor == "main" else actor + ":" + context.tool_call_id,
                    tool=tool_name,
                    arguments=args if isinstance(args, dict) else {},
                ),
                actor=actor,
            )
            persist()
            return result["visible_json"]

        tools.append(
            FunctionTool(
                name=name,
                description=description,
                params_json_schema=copy.deepcopy(schema),
                on_invoke_tool=invoke,
                strict_json_schema=False,
            )
        )
    return tools


# 仅合并已观察的相邻行；读取缺口保持分段，重叠行内容冲突则立即报错。
def merged_context(context):
    """Merge overlapping observed lines, never fill unread gaps or read new source."""
    by_path = {}
    for span in context:
        lines = by_path.setdefault(span["path"], {})
        for number, line in enumerate(span["content"].split("\n"), span["start_line"]):
            if number in lines and lines[number] != line:
                raise ValueError("inconsistent observed source")
            lines[number] = line
    result = []
    for path, lines in by_path.items():
        groups = []
        for number in sorted(lines):
            if not groups or number != groups[-1][-1] + 1:
                groups.append([])
            groups[-1].append(number)
        for group in groups:
            content = "\n".join(lines[n] for n in group)
            result.append(
                {
                    "path": path,
                    "start_line": group[0],
                    "end_line": group[-1],
                    "content": content,
                    "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                }
            )
    return result


# 只携带已完成的研究摘要并标记为导航笔记，源码事实仍需原文支持。
def research_notes_payload(draft, maximum=4000):
    """Carry completed external notes, never partial output or provider reasoning blocks."""
    if not isinstance(draft, str) or not draft.strip() or "<think>" in draft:
        return None
    raw = draft.strip().encode("utf-8")
    text = raw[:maximum].decode("utf-8", errors="ignore")
    return {
        "text": text,
        "source": "completed_sdk_research",
        "is_evidence": False,
        "independently_verified": False,
        "original_bytes": len(raw),
        "kept_bytes": len(text.encode()),
        "truncated": len(text.encode()) < len(raw),
    }


@dataclass(frozen=True)
class _PreparedAnswer:
    question: str
    query: str | None
    memory: object
    task_context: object
    output_type: type
    token: CancellationToken
    focus: dict
    symbols: dict
    seeds: list
    prefetch: dict | None
    recent: list
    initial: list
    identity: dict
    instruction: str


class SdkAgentEngine:
    supports_source_sharing = True

    def __init__(
        self, reader, index, runtime, cache_dir, limits=None, *, collaboration=None, tools_enabled=True
    ):
        self.reader, self.index, self.runtime = reader, index, runtime
        self.cache_dir = Path(cache_dir)
        # Validate defaults too so CLI, scripts and conversations share cache identities.
        self.limits = AgentLimits(**(limits or AgentLimits()).model_dump())
        self.backend = None
        self.collaboration = collaboration or CollaborationSettings()
        if not isinstance(tools_enabled, bool):
            raise ValueError("tools_enabled must be a boolean")
        self.tools_enabled = tools_enabled

    # 冻结问题、入口范围、初始检索、工具约束与运行身份，作为恢复与请求的共同起点。
    def _prepare_answer(
        self,
        question,
        *,
        history,
        query,
        token,
        memory,
        output_type,
        instruction,
        agent_name,
        task_context,
        shared_context,
        shared_source_bytes,
    ):
        """Validate the request and freeze initial evidence, instructions and cache identity."""
        if not question.strip():
            raise ValueError("question must not be empty")
        if len(history) % 2 or any(
            row.get("role") != ("user" if i % 2 == 0 else "assistant") or set(row) != {"role", "content"}
            for i, row in enumerate(history)
        ):
            raise ValueError("history must contain complete user/assistant pairs")
        token = token or CancellationToken()
        focus = resolve_entry(self.reader, question, (task_context or {}).get("entry"))
        symbols = (
            self.index.symbols.navigation(question, focus, (task_context or {}).get("current_symbol"))
            if hasattr(self.index, "symbols")
            else symbol_navigation(self.reader, question, focus, (task_context or {}).get("current_symbol"))
        )
        if not 0 <= shared_source_bytes <= 100000:
            raise ValueError("invalid shared source seed budget")
        seeds = select_sources(
            self.reader,
            shared_context,
            query or question,
            max_bytes=min(shared_source_bytes, self.limits.max_source_bytes),
            excluded=focus["excluded_paths"],
        )
        prefetch = None
        if hasattr(self.index, "initial_sources"):
            prepared = RagEngine(self.reader, self.index, self.runtime, self.cache_dir).prepare(
                question, history=history, query=query, token=token
            )
            prefetch = {
                **prepared["initial_retrieval"],
                "sources": prepared["context"],
                "source_omissions": prepared["omissions"],
                "fixed_input_budget": prepared["budget"],
            }
            seeds = [*seeds, *(s for s in prefetch["sources"] if s not in seeds)]
        if sum(len(s["content"].encode()) for s in seeds) > self.limits.max_source_bytes:
            seeds = select_sources(
                self.reader,
                {"snapshot_id": self.reader.snapshot.snapshot_id, "spans": seeds},
                query or question,
                max_bytes=self.limits.max_source_bytes,
                excluded=focus["excluded_paths"],
            )
        instruction += (
            "\n先检查当前问题所需的函数声明、分支、关联实现及明确要求的测试是否已经可见。"
            "缺关键源码时按导航位置读取或缩小查询，不要凭相似函数猜测，也不要因一次搜索未命中就断言不存在。"
            "源码已充分时可以直接作答，不必为了调用工具而调用工具。"
        )
        if output_type is RepositoryAnswer:
            instruction += (
                "\n研究完成后调用 submit_answer 提交答案，恰含answer、citations、insufficient_evidence三个字段；"
                "citations每项含path、start_line、end_line。answer须覆盖题目的各项要求，"
                "引用只用本轮实际可见源码。缺少私有运行数据等必要信息时，正文说明具体缺口，"
                "并把insufficient_evidence设为true；不要正文拒答却把此字段填成false。"
            )
        else:
            instruction += (
                "\n完成研究后给出各项任务需要的事实与准确来源，未核验的部分说明缺口，随后由格式化Agent整理。"
            )
        if self.collaboration.mode == "auto":
            instruction += "\n可按需调用 inspect_implementation / inspect_tests 专家核对跨文件问题。专家笔记不是源码；以返回的 spans 核验。"
        instruction += "\n遵守 entry_focus 的入口范围；verified_source 是重新核验的源码，可直接复用，task_context 只提供导航。"
        instruction += (
            "\n先区分任务：推导给定调用时按其实际参数执行；选择接入配置时按用户目标选参数，不能用默认行为代替需求。"
            "只交付题目所需的调用或结论，复用题设已有对象，保留所有明确要求的参数；不照搬文档的其他业务示例。"
        )
        instruction += (
            "\nsymbol_navigation 只提供静态位置，不是证据。问题指定函数时优先读取其声明及 factory_candidates，"
            "不要用相似辅助函数代替；测试结论必须核对实际断言。"
        )
        recent = list(history)[-8:]
        payload = {
            "question": question,
            "resolved_query": query or question,
            "snapshot_id": self.reader.snapshot.snapshot_id,
            "limits": self.limits.model_dump(),
            "navigation_memory": memory,
            "task_context": task_context,
            "entry_focus": focus,
            "verified_source": render_sources(seeds),
            "symbol_navigation": symbols,
        }
        initial = [*recent, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        identity = {
            "snapshot_id": self.reader.snapshot.snapshot_id,
            "index_id": self.index.index_id,
            "config": self.runtime.settings.public(),
            "limits": self.limits.model_dump(),
            "runtime": sdk_version(),
            "input": initial,
            "instructions": instruction,
            "agent_name": agent_name,
            "output_schema": output_type.model_json_schema(),
            "collaboration": self.collaboration.model_dump(),
            "tools_enabled": self.tools_enabled,
        }
        return _PreparedAnswer(
            question=question,
            query=query,
            memory=memory,
            task_context=task_context,
            output_type=output_type,
            token=token,
            focus=focus,
            symbols=symbols,
            seeds=seeds,
            prefetch=prefetch,
            recent=recent,
            initial=initial,
            identity=identity,
            instruction=instruction,
        )

    # 仅打包已读原文与完成笔记，为最终结构化整理留足预算，不在此补读新源码。
    def _prepare_finalizer(self, prepared, context, draft, instruction, navigation_query, events):
        """Pack observed source and completed notes; never perform model or repository work."""
        question, recent, query = prepared.question, prepared.recent, prepared.query
        memory, task_context = prepared.memory, prepared.task_context
        focus, symbols, output_type = prepared.focus, prepared.symbols, prepared.output_type
        final_context, finalize_instruction, local_events = context, instruction, events

        def packed(spans, notes=None):
            messages = messages_for(
                question,
                recent,
                spans,
                instruction=finalize_instruction,
                schema=output_type.model_json_schema(),
                research_notes=notes,
                memory={
                    "navigation": memory,
                    "entry_focus": focus,
                    "task_context": task_context,
                    "symbol_navigation": symbols,
                    **navigation_query,
                },
            )
            return messages

        def fits(spans, notes=None):
            return sdk_input_budget(
                self.runtime.settings,
                {
                    "messages": packed(spans, notes),
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "final_output",
                            "strict": False,
                            "schema": grounded_report_schema(output_type.model_json_schema(), spans),
                        },
                    },
                },
            )["fits"]

        final_context, context_omissions = pack_observed_context(
            final_context,
            query or question,
            lambda spans: fits(spans),
        )
        # Raw source has priority. Trim/drop notes before evicting any already selected source.
        allowance = 4000
        research_notes = research_notes_payload(draft, allowance)
        while research_notes and not fits(final_context, research_notes):
            allowance //= 2
            research_notes = research_notes_payload(draft, allowance) if allowance >= 128 else None
        local_events.append(
            {
                "event": "research_notes_selection",
                "used": research_notes is not None,
                "kept_bytes": research_notes["kept_bytes"] if research_notes else 0,
                "is_evidence": False,
                "raw_source_priority": True,
            }
        )
        final_inputs = packed(final_context, research_notes)[1:]
        return final_inputs, final_context, context_omissions, research_notes

    # 组织准备、检查点恢复、Runner 调用和引用验收，完整保存中断与失败状态。
    async def answer(
        self,
        question,
        *,
        history=(),
        query=None,
        token=None,
        refresh=False,
        memory=None,
        output_type=RepositoryAnswer,
        instruction=SYSTEM,
        agent_name="RepoMentor",
        task_context=None,
        shared_context=None,
        shared_source_bytes=4000,
    ):
        prepared = self._prepare_answer(
            question,
            history=history,
            query=query,
            token=token,
            memory=memory,
            output_type=output_type,
            instruction=instruction,
            agent_name=agent_name,
            task_context=task_context,
            shared_context=shared_context,
            shared_source_bytes=shared_source_bytes,
        )
        token, focus, symbols = prepared.token, prepared.focus, prepared.symbols
        seeds, prefetch, recent = prepared.seeds, prepared.prefetch, prepared.recent
        initial, identity, instruction = prepared.initial, prepared.identity, prepared.instruction
        key = fingerprint(identity)
        path = self.cache_dir / self.runtime.settings.config_id / (key + ".json")
        previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        if previous and not refresh:
            if previous["cache_key"] != key:
                raise ValueError("SDK cache identity mismatch")
            verify_spans(self.reader, previous["context"])
            return {**previous, "cache_hit": True, "new_model_requests": 0}
        checkpoint = copy.deepcopy(previous.get("checkpoint")) if previous and refresh else None
        if not (checkpoint and checkpoint.get("resumable")):
            checkpoint = None
        env = RepositoryEnvironment(self.reader, self.index, self.limits, token=token)
        env.reset({"question": question})
        if checkpoint:
            env.restore(checkpoint["environment"])
        env.excluded_paths = focus["excluded_paths"]
        if not checkpoint:
            env.share(seeds, "main", "revalidated-input")
        inputs = checkpoint["input"] if checkpoint else initial
        inherited = copy.deepcopy(previous["decisions"]) if checkpoint else []
        prior_elapsed = previous["elapsed_seconds"] if checkpoint else 0
        attempt_id, started = uuid4().hex, time.monotonic()
        answer, stop_reason, output_items = None, "interrupted", []
        final_context, research_stop, research_notes = [], None, None
        context_omissions, report_validation = [], None
        local_events = []
        submitted = None
        completion = CompletionResult(status="failed", request_hash=key, error_code="interrupted")
        boundary = {"input": inputs, "environment": copy.deepcopy(env.state), "resumable": False}
        self.runtime.calls = []

        def save():
            decisions = inherited + self.runtime.calls
            row = {
                "schema_version": "repomentor.agent.sdk.v1",
                **identity,
                "attempt_id": attempt_id,
                "cache_key": key,
                "parent_attempt_id": previous["attempt_id"] if previous else None,
                "resumed_checkpoint": bool(checkpoint),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "commit_sha": self.reader.snapshot.commit_sha,
                "question": question,
                "config_id": self.runtime.settings.config_id,
                "backend_verification": self.backend,
                "mode": "sdk",
                "answer": answer,
                "completion": completion.model_dump(),
                "context": env.state["context"],
                "citation_ranges_valid": citation_validity(answer, env.state["context"]),
                "semantic_correctness": "not_scored",
                "stop_reason": stop_reason,
                "decisions": decisions,
                "tool_trace": env.state["observations"],
                "tool_calls": env.state["tool_calls"],
                "source_bytes": env.state["source_bytes"],
                "history_pairs_omitted": (len(history) - len(recent)) // 2,
                "cache_hit": False,
                "new_model_requests": sum(bool(d["request_sent"]) for d in self.runtime.calls),
                "total_model_requests": sum(bool(d["request_sent"]) for d in decisions),
                "elapsed_seconds": round(prior_elapsed + time.monotonic() - started, 6),
                "checkpoint": boundary,
                "sdk_output_items": output_items,
                "final_context": final_context,
                "research_stop": research_stop,
                "context_omissions": context_omissions,
                "report_validation": report_validation,
                "research_notes": research_notes,
                "entry_focus": focus,
                "shared_input_context": seeds,
                "symbol_navigation": symbols,
                "initial_retrieval": prefetch,
                "local_trace": local_events,
                "source_transfers": env.state["source_transfers"],
                "delegations": env.state["delegations"],
                "visible_context": env.state["visible_context"],
            }
            write(path.parent / "attempts" / (attempt_id + ".json"), row)
            write(path, row)
            return row

        def before_model(model_input, phase):
            token.check()
            if (
                phase == "research"
                and len(inherited) + len(self.runtime.calls) >= self.limits.max_decisions - 1
            ):
                raise HarnessError("research_budget", "reserve one global model call for the final answer")
            if len(inherited) + len(self.runtime.calls) >= self.limits.max_decisions:
                raise HarnessError("decision_budget", "global model attempt budget reached")
            boundary.update(
                input=model_input,
                environment=copy.deepcopy(env.state),
                resumable=ACTOR.get() == "main",
                phase=phase,
                actor=ACTOR.get(),
            )
            save()
            return (
                len(inherited) + len(self.runtime.calls) >= self.limits.max_decisions - 1
                or env.state["tool_calls"] >= self.limits.max_tool_calls
                or (not self.tools_enabled and output_type is not RepositoryAnswer)
            )

        async def run():
            nonlocal \
                output_items, \
                final_context, \
                research_stop, \
                research_notes, \
                context_omissions, \
                report_validation, \
                answer
            if self.backend is None:
                self.backend = await self.runtime.verify_backend()
            model = await self.runtime.open(
                before_model, save, token, lambda: boundary.update(resumable=False)
            )
            config = RunConfig(
                tracing_disabled=True,
                trace_include_sensitive_data=False,
                call_model_input_filter=ContextFilter(env, self.runtime, question, local_events),
                tool_execution=ToolExecutionConfig(max_function_tool_concurrency=1),
            )
            hooks = LocalHooks(local_events)
            remaining = self.limits.max_decisions - len(inherited)
            draft = ""

            @output_guardrail
            def report_provenance(context, agent, output):
                nonlocal report_validation, answer
                try:
                    value = output_type.model_validate_json(output) if isinstance(output, str) else output
                except (ValueError, TypeError):
                    # Research prose is handled by the typed finalizer, not accepted as a report.
                    return GuardrailFunctionOutput(output_info=None, tripwire_triggered=False)
                candidate = value.model_dump(mode="json")
                visible_context = (
                    final_context
                    if agent.name.endswith("-format") or submitted is not None
                    else env.visible_context()
                )
                report_validation = validate_report_paths(candidate, visible_context, self.reader.paths)
                if report_validation is not None:
                    locations = inspect_report_locations(self.reader, candidate, visible_context)
                    report_validation = {
                        **report_validation,
                        "locations": locations,
                        "passed": report_validation["passed"] and locations["passed"],
                    }
                blocked = report_validation is not None and not report_validation["passed"]
                if blocked:
                    answer = candidate  # Preserve rejected output for diagnosis; never publish as success.
                return GuardrailFunctionOutput(output_info=report_validation, tripwire_triggered=blocked)

            def after_tools(context, results):
                nonlocal final_context
                if submitted is not None:
                    value, final_context = submitted
                    return ToolsToFinalOutputResult(
                        is_final_output=True, final_output=value.model_dump_json()
                    )
                # SDK termination callback: never ask a provider to honor tool_choice after the budget.
                exhausted = (
                    env.state["tool_calls"] >= self.limits.max_tool_calls
                    or len(inherited) + len(self.runtime.calls) >= self.limits.max_decisions - 1
                )
                return ToolsToFinalOutputResult(
                    is_final_output=exhausted,
                    final_output="工具预算已用尽，请根据实际观察整理答案。" if exhausted else None,
                )

            if (
                not (checkpoint and checkpoint.get("phase") == "finalize")
                and remaining > 1
                and (self.limits.max_tool_calls > 0 or not self.tools_enabled)
            ):
                tools = repository_tools(env, save) if self.tools_enabled else []
                if self.tools_enabled:
                    tools += specialist_tools(
                        env,
                        model,
                        self.runtime.model_settings,
                        config,
                        self.collaboration,
                        repository_tools,
                        save,
                        lambda: self.limits.max_decisions - len(inherited) - len(self.runtime.calls),
                        focus,
                    )
                if output_type is RepositoryAnswer:

                    async def submit_answer(context, arguments):
                        nonlocal submitted
                        env.check()
                        try:
                            value = RepositoryAnswer.model_validate_json(arguments)
                        except ValueError:
                            return json.dumps({"accepted": False, "error": "invalid_answer_schema"})
                        # A read in this same tool batch has not yet been seen by the model.
                        visible = merged_context(boundary["environment"]["visible_context"].get("main", []))
                        candidate = value.model_dump(mode="json")
                        if not citation_validity(candidate, visible):
                            missing = [
                                {"path": ref.path, **gap}
                                for ref in value.citations
                                for gap in uncovered(
                                    ref.start_line,
                                    ref.end_line,
                                    [s for s in visible if s["path"] == ref.path],
                                )
                            ]
                            local_events.append(
                                {"event": "answer_submission_rejected", "missing_ranges": missing}
                            )
                            return json.dumps(
                                {
                                    "accepted": False,
                                    "error": "invalid_citations",
                                    "instruction": "只引用已见且支持结论的行段；缺证据时先 read_code 补读，再提交完整答案。",
                                    "unread_ranges": missing[:6],
                                },
                                ensure_ascii=False,
                            )
                        submitted = value, visible
                        local_events.append({"event": "answer_submission_accepted"})
                        return json.dumps({"accepted": True})

                    tools.append(
                        FunctionTool(
                            name="submit_answer",
                            description="提交完整答案；引用越界会返回错误，可补查后重新提交。",
                            params_json_schema=RepositoryAnswer.model_json_schema(),
                            on_invoke_tool=submit_answer,
                            strict_json_schema=False,
                        )
                    )
                agent = Agent(
                    name=agent_name,
                    instructions=instruction,
                    model=model,
                    model_settings=self.runtime.model_settings,
                    tools=tools,
                    tool_use_behavior=after_tools,
                    output_guardrails=[report_provenance],
                )
                try:
                    result = await Runner.run(
                        agent, inputs, max_turns=remaining - 1, run_config=config, hooks=hooks
                    )
                    output_items = [item.to_input_item() for item in result.new_items]
                    draft = result.final_output
                    research_stop = (
                        "tool_budget"
                        if env.state["tool_calls"] >= self.limits.max_tool_calls
                        else "draft_returned"
                    )
                    # Already-valid JSON needs no extra formatting request.
                    try:
                        value = (
                            draft
                            if isinstance(draft, output_type)
                            else output_type.model_validate_json(draft)
                        )
                        if submitted is None:
                            final_context = merged_context(env.visible_context())
                        return value
                    except (ValueError, TypeError):
                        pass
                except MaxTurnsExceeded:
                    research_stop = "research_turn_budget"
                except Exception as exc:
                    code = error_code(exc)
                    if code == "research_budget":
                        research_stop = "research_global_budget"
                    elif (
                        code == "truncated"
                        and self.runtime.calls
                        and env.state["context"]
                        and self.runtime.calls[-1].get("phase") == "research"
                        and self.runtime.calls[-1].get("actor") == "main"
                    ):
                        # A partial research draft is not an answer or new evidence. Keep
                        # its failure/raw response, then use the reserved SDK finalizer
                        # with verified source only; never retry or continue the draft.
                        research_stop = "research_truncated"
                        boundary["resumable"] = False
                        local_events.append(
                            {
                                "event": "research_truncated_to_finalizer",
                                "failed_request_preserved": True,
                                "partial_draft_used": False,
                            }
                        )
                    elif (
                        code == "context_limit"
                        and self.runtime.calls
                        and not self.runtime.calls[-1]["request_sent"]
                        and env.state["context"]
                    ):
                        # SDK transport can wrap our pre-dispatch check. Keep that rejected request
                        # in the ledger and use the reserved SDK formatter, never retry the same input.
                        research_stop = "research_context_limit"
                    else:
                        raise
            finalize_instruction = FINAL_SYSTEM if output_type is RepositoryAnswer else REPORT_FINAL_SYSTEM
            navigation_query = {"resolved_query": query} if query and query != question else {}
            if navigation_query:
                finalize_instruction += (
                    "\nquestion 是当前唯一作答目标；resolved_query 只辅助指代与检索，"
                    "其中的旧问题不是新任务。当前明确点名新函数时不得重复上一轮回答。"
                )
            final_context = merged_context(env.state["context"])
            if checkpoint and checkpoint.get("phase") == "finalize":
                final_inputs = inputs
                final_context = checkpoint["final_context"]
                context_omissions = checkpoint.get("context_omissions", [])
                research_notes = checkpoint.get("research_notes")
            else:
                final_inputs, final_context, context_omissions, research_notes = self._prepare_finalizer(
                    prepared,
                    final_context,
                    draft,
                    finalize_instruction,
                    navigation_query,
                    local_events,
                )
            boundary["final_context"] = final_context
            boundary["context_omissions"] = context_omissions
            boundary["research_notes"] = research_notes
            final_agent = Agent(
                name=agent_name + "-format",
                instructions=finalize_instruction,
                model=model,
                model_settings=self.runtime.final_model_settings,
                output_type=GroundedReportSchema(output_type, final_context),
                output_guardrails=[report_provenance],
            )
            result = await Runner.run(final_agent, final_inputs, max_turns=1, run_config=config, hooks=hooks)
            output_items.extend(item.to_input_item() for item in result.new_items)
            value = result.final_output
            if not citation_validity(value.model_dump(), final_context):
                raise HarnessError(
                    "invalid_citations", "final answer cites context omitted from finalization"
                )
            return value

        save()
        runner = None
        try:
            token.check()
            runner = asyncio.create_task(run())
            # Cancellation watchdog only; agent/tool iteration is entirely inside SDK Runner.
            async with asyncio.timeout(max(0.001, self.limits.timeout_seconds - prior_elapsed)):
                while not runner.done():
                    await asyncio.wait({runner}, timeout=0.1)
                    token.check()
                result = await runner
            answer = result.model_dump(mode="json")
            verify_spans(self.reader, env.state["context"])
            if not citation_validity(answer, final_context):
                raise HarnessError("invalid_citations", "answer references unavailable source ranges")
            stop_reason = "answered"
            completion = CompletionResult(
                status="success",
                content=json.dumps(answer, ensure_ascii=False),
                finish_reason="stop",
                request_hash=key,
                request_sent=any(d["request_sent"] for d in self.runtime.calls),
            )
            boundary["resumable"] = False
        except Exception as exc:
            stop_reason = "decision_budget" if isinstance(exc, MaxTurnsExceeded) else error_code(exc)
            if isinstance(exc, OutputGuardrailTripwireTriggered):
                stop_reason = "report_grounding"
            status = {
                "context_limit": "context_limit",
                "cancelled": "cancelled",
                "truncated": "truncated",
                "TimeoutError": "timeout",
                "APITimeoutError": "timeout",
            }.get(stop_reason, "failed")
            completion = CompletionResult(status=status, error_code=stop_reason, request_hash=key)
            # Only model-boundary failures resume; validation/tool failures require a fresh attempt.
            boundary["resumable"] = bool(
                boundary["resumable"]
                and stop_reason
                in (
                    "APIConnectionError",
                    "APIStatusError",
                    "InternalServerError",
                    "RateLimitError",
                    "APITimeoutError",
                    "TimeoutError",
                    "cancelled",
                    "ConnectionError",
                )
            )
        finally:
            if runner is not None and not runner.done():
                runner.cancel()
                await asyncio.gather(runner, return_exceptions=True)
            await self.runtime.close()
        # Aggregate only server-reported usage. Missing counts remain unknown, never invented zeroes.
        sent = [d for d in self.runtime.calls if d["request_sent"]]
        completion.usage = {
            name: (
                sum(d["usage"][name] for d in sent)
                if sent and all(isinstance(d.get("usage", {}).get(name), int) for d in sent)
                else None
            )
            for name in ("prompt_tokens", "completion_tokens", "total_tokens")
        }
        completion.request_sent = bool(sent)
        completion.elapsed_seconds = round(time.monotonic() - started, 6)
        from .answer_quality import inspect_answer

        row = save()
        row["quality"] = inspect_answer(answer, final_context, completion.status)
        write(path.parent / "attempts" / (attempt_id + ".json"), row)
        write(path, row)
        return row
