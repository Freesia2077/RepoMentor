"""SDK input filter and local hooks; no model/tool loop or remote trace exporter."""

# 在每次 SDK 请求前核算输入并压缩上下文，再更新该 actor 当前可引用的源码范围。
# 模型实际收到的原文是可见性依据，读取日志中的其他片段继续留作审计。

import copy
import hashlib
import json

from agents import RunHooks
from agents.models.chatcmpl_converter import Converter
from agents.run_config import ModelInputData

from .source_context import pack_observed_context


# 从这一次请求的实际输入恢复原文行段，重新计算哈希，排除只存在于读取日志的内容。
def source_items(items):
    """Recover only exact raw source actually present in these model input items."""
    found = []

    def visit(value):
        if isinstance(value, str):
            try:
                visit(json.loads(value))
            except (ValueError, TypeError):
                pass
        elif isinstance(value, list):
            for row in value:
                visit(row)
        elif isinstance(value, dict):
            if all(k in value for k in ("path", "start_line", "end_line")):
                content = value.get("content")
                if isinstance(value.get("lines"), list):
                    content = "\n".join(s.partition(": ")[2] for s in value["lines"])
                elif isinstance(content, str) and content.startswith(f"L{value['start_line']}: "):
                    content = "\n".join(s.partition(": ")[2] for s in content.splitlines())
                if (
                    isinstance(content, str)
                    and len(content.split("\n")) == value["end_line"] - value["start_line"] + 1
                ):
                    span = {k: value[k] for k in ("path", "start_line", "end_line")}
                    span.update(content=content, content_sha256=hashlib.sha256(content.encode()).hexdigest())
                    if span not in found:
                        found.append(span)
                    return
            for child in value.values():
                visit(child)

    visit(items)
    return found


def without_sources(value):
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return value
        return json.dumps(without_sources(parsed), ensure_ascii=False)
    if isinstance(value, list):
        return [without_sources(v) for v in value]
    if isinstance(value, dict):
        return {
            k: ([] if k in ("spans", "verified_source", "context") else without_sources(v))
            for k, v in value.items()
        }
    return value


class ContextFilter:
    def __init__(self, env, runtime, question, events):
        self.env, self.runtime, self.question, self.events = env, runtime, question, events

    # 先核算完整消息、工具和 schema；超限时裁剪旧对话与原文，再发布新的可见来源表。
    def __call__(self, data):
        from .runtimes.openai_sdk import sdk_input_budget
        from .sdk_collaboration import ACTOR

        original = copy.deepcopy(data.model_data.input)
        # finalizer 已按输出 schema 打包并冻结原文，此处直接沿用那份上下文。
        if data.agent.output_type is not None and not data.agent.tools:
            return data.model_data
        progress = self.env.pending_reads(ACTOR.get())
        if progress:
            original.append(
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "kind": "repomentor.read-progress.v1",
                            "reading_progress": progress,
                            "instruction": "这些是尚未读取的请求区间，不是源码证据。仅当与当前问题有关时，复制 arguments 续读；不要反复搜索同一前缀。",
                        },
                        ensure_ascii=False,
                    ),
                }
            )
        tools = [Converter.tool_to_openai(t) for t in data.agent.tools]
        profile = self.runtime.settings.model_copy(
            update={"max_tokens": data.agent.model_settings.max_tokens}
        )

        def budget(items):
            messages = Converter.items_to_messages(items)
            if data.model_data.instructions:
                messages.insert(0, {"role": "system", "content": data.model_data.instructions})
            body = {"messages": messages, "tools": tools}
            if data.agent.output_type is not None:
                from agents import AgentOutputSchema

                schema = data.agent.output_type
                if not isinstance(schema, AgentOutputSchema):
                    schema = AgentOutputSchema(schema, strict_json_schema=False)
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "final_output", "strict": False, "schema": schema.json_schema()},
                }
            return sdk_input_budget(profile, body)

        visible = source_items(original)
        from .source_windows import uncovered, verify_spans

        visible = [
            s
            for s in visible
            if not uncovered(
                s["start_line"],
                s["end_line"],
                [p for p in self.env.state["context"] if p["path"] == s["path"]],
            )
        ]
        verify_spans(self.env.reader, visible)
        items, omitted = original, []
        initial_budget = budget(items)
        if not initial_budget["fits"]:
            items = without_sources(original)
            # 以完整旧对话轮为单位裁剪，始终保留工具调用与返回的配对关系。
            while len(items) >= 3 and items[0].get("role") == "user" and items[1].get("role") == "assistant":
                items = items[2:]
                if budget(items)["fits"]:
                    break

            def packed(spans):
                from .source_windows import render_sources

                return [
                    *items,
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "question": self.question,
                                "verified_source": render_sources(spans),
                                "note": "Earlier source bodies were compacted; only these raw lines remain visible.",
                            },
                            ensure_ascii=False,
                        ),
                    },
                ]

            selected, omitted = pack_observed_context(
                visible, self.question, lambda spans: budget(packed(spans))["fits"]
            )
            items = packed(selected)
            visible = selected
        # 以裁剪后真正发送的原文更新可见表；已经读取但本次未送入输入的行不能据此引用。
        self.env.state["visible_context"][ACTOR.get()] = visible
        final_budget = budget(items)
        self.events.append(
            {
                "event": "context_selection",
                "actor": ACTOR.get(),
                "agent": data.agent.name,
                "items_before": len(original),
                "items_after": len(items),
                "compacted": items != original,
                "source_ranges": [
                    {k: s[k] for k in ("path", "start_line", "end_line", "content_sha256")} for s in visible
                ],
                "omitted_sources": omitted,
                "budget": final_budget,
            }
        )
        return ModelInputData(input=items, instructions=data.model_data.instructions)


# 钩子只追加本地阶段和工具事件，便于追踪 Runner 的实际执行边界。
class LocalHooks(RunHooks):
    def __init__(self, events):
        self.events = events

    async def on_agent_start(self, context, agent):
        self.events.append({"event": "agent_start", "agent": agent.name})

    async def on_agent_end(self, context, agent, output):
        self.events.append({"event": "agent_end", "agent": agent.name})

    async def on_tool_start(self, context, agent, tool):
        self.events.append(
            {
                "event": "tool_start",
                "agent": agent.name,
                "tool": tool.name,
                "call_id": getattr(context, "tool_call_id", None),
            }
        )

    async def on_tool_end(self, context, agent, tool, result):
        self.events.append(
            {
                "event": "tool_end",
                "agent": agent.name,
                "tool": tool.name,
                "call_id": getattr(context, "tool_call_id", None),
            }
        )
