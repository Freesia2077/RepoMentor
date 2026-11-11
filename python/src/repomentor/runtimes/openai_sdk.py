"""Official Chat Completions transport with local accounting, not an agent loop."""

# 适配 OpenAI Agents SDK 的模型边界，记录完整请求、响应和输入预算。
# Agent 迭代与工具调度交给 SDK Runner，本模块负责协议与观测。

from __future__ import annotations

import copy
import json
import time
from dataclasses import replace

import httpx2
from agents import Model, ModelSettings, OpenAIChatCompletionsModel, set_tracing_disabled
from agents.model_settings import ModelRetrySettings
from openai import AsyncOpenAI

from ..execution import HarnessError
from ..sdk_collaboration import ACTOR
from ..token_budget import input_budget, load_tokenizer
from .chat import ChatCompletionsRuntime, fingerprint


# 工具历史即使出现在最终无工具轮也计入输入，避免仅按当前工具列表低估上下文。
def sdk_input_budget(settings, body):
    """Account for complete SDK tool history even when the final turn disables all tools."""
    if not settings.tokenizer_sha256:
        return input_budget(settings, body["messages"], body.get("tools"), body.get("response_format"))
    if (settings.backend, settings.server_version, settings.reasoning_effort) != ("ollama", "0.34.3", "none"):
        raise ValueError("SDK tokenizer calibration requires pinned Ollama/Qwen with thinking off")
    wire = json.dumps(
        [body["messages"], body.get("tools", []), body.get("response_format")], ensure_ascii=False
    )
    count = len(load_tokenizer(settings.tokenizer_sha256).encode(wire, add_special_tokens=False).ids)
    count += 768 + 64 * len(body["messages"])
    total = count + settings.max_tokens + settings.template_reserve
    return {
        "mode": "qwen35_ollama_sdk_padded_v2",
        "input_tokens": count,
        "output_allowance": settings.max_tokens,
        "reserve": settings.template_reserve,
        "total": total,
        "context_window": settings.context_window,
        "fits": total <= settings.context_window,
    }


class ObservedModel(Model):
    """Observe SDK model boundaries; Runner alone owns iteration and tool dispatch."""

    def __init__(self, delegate, runtime, before_model, persist):
        self.delegate, self.runtime = delegate, runtime
        self.before_model, self.persist = before_model, persist

    # 围绕 SDK 单次模型调用记录输入、输出和预算，迭代与派发继续由 Runner 控制。
    async def get_response(self, **kwargs):
        # 带结构化输出 schema 的 Agent 仍可使用工具，工具状态由循环的实际边界决定。
        phase = (
            "finalize" if kwargs.get("output_schema") is not None and not kwargs.get("tools") else "research"
        )
        finalizing = self.before_model(copy.deepcopy(kwargs["input"]), phase)
        entry = {
            "actor": ACTOR.get(),
            "input": copy.deepcopy(kwargs["input"]),
            "system_instructions": kwargs["system_instructions"],
            "status": "pending",
            "request_sent": False,
        }
        self.runtime.calls.append(entry)
        self.runtime.active = entry
        self.persist()
        started = time.monotonic()
        try:
            if self.runtime.scripted_model is not None:
                entry["request_sent"] = True  # Offline model invocation; explicitly tagged below.
                entry["transport"] = "scripted"
            # 仅在结束边界关闭工具，前面的结构化输出轮继续允许正常取证。
            entry["phase"] = phase
            if finalizing or phase == "finalize":
                kwargs = {**kwargs, "model_settings": replace(kwargs["model_settings"], tool_choice="none")}
                entry["finalizing"] = True
            response = await self.delegate.get_response(**kwargs)
            entry.update(
                status="success",
                output=[v.model_dump(mode="json") for v in response.output],
                sdk_usage={
                    name: getattr(response.usage, name)
                    for name in ("requests", "input_tokens", "output_tokens", "total_tokens")
                },
            )
            if (finalizing or phase == "finalize") and any(
                v.type == "function_call" for v in response.output
            ):
                raise HarnessError(
                    "tool_choice_ignored", "provider emitted repository tools during final-answer turn"
                )
            self.runtime.model_completed()
            return response
        except BaseException as exc:
            entry.update(status="failed", error_type=type(exc).__name__)
            raise
        finally:
            entry["elapsed_seconds"] = round(time.monotonic() - started, 6)
            self.persist()

    async def stream_response(self, **kwargs):
        raise NotImplementedError("SDK experiment pins non-streaming Runner.run")
        yield  # pragma: no cover


class OpenAISdkRuntime:
    def __init__(self, settings, *, transport=None, scripted_model=None, verifier=None, before_dispatch=None):
        # 当前后端走 SDK 非流式模型路径，将该差异纳入配置身份。
        self.settings = settings.model_copy(update={"stream": False})
        self.transport, self.scripted_model, self.verifier = transport, scripted_model, verifier
        self.calls, self.active, self.client = [], None, None
        self.before_dispatch = before_dispatch

    @property
    def model_settings(self):
        return ModelSettings(
            temperature=self.settings.temperature,
            top_p=self.settings.top_p,
            presence_penalty=self.settings.presence_penalty,
            max_tokens=min(1536, self.settings.max_tokens),
            parallel_tool_calls=False,
            extra_body={"seed": self.settings.seed, "reasoning_effort": self.settings.reasoning_effort},
            retry=ModelRetrySettings(max_retries=0),
        )

    @property
    def final_model_settings(self):
        return replace(self.model_settings, max_tokens=self.settings.max_tokens)

    async def verify_backend(self):
        if self.verifier:
            return await self.verifier()
        if self.scripted_model is not None:
            return {"verification": "offline-scripted"}
        return await ChatCompletionsRuntime(self.settings).verify_backend()

    async def open(self, before_model, persist, token, model_completed=lambda: None):
        # 同时关闭默认远程导出器与单轮 tracing，运行记录只在本地保存。
        set_tracing_disabled(True)
        self.calls = []
        self.persist, self.token = persist, token
        self.model_completed = model_completed
        if self.scripted_model is not None:
            delegate = self.scripted_model
        else:
            http_client = httpx2.AsyncClient(
                transport=self.transport,
                trust_env=False,
                follow_redirects=False,
                timeout=self.settings.timeout_seconds,
                event_hooks={"request": [self._request], "response": [self._response]},
            )
            self.client = AsyncOpenAI(
                base_url=self.settings.baseUrl,
                api_key=self.settings.apiKey.get_secret_value() if self.settings.apiKey else "local-only",
                max_retries=0,
                timeout=self.settings.timeout_seconds,
                http_client=http_client,
            )
            delegate = OpenAIChatCompletionsModel(
                model=self.settings.model,
                openai_client=self.client,
                should_replay_reasoning_content=False,
            )
        return ObservedModel(delegate, self, before_model, persist)

    async def close(self):
        if self.client is not None:
            await self.client.close()
            self.client = None

    # 发送钩子校验真实请求并登记身份，使保存的 wire 记录可与费用账本逐项对齐。
    async def _request(self, request):
        self.token.check()
        body = json.loads(request.content)
        # 只保存请求体，请求头与凭据留在传输层。
        allowance = body.get("max_completion_tokens", body.get("max_tokens", self.settings.max_tokens))
        budget = sdk_input_budget(self.settings.model_copy(update={"max_tokens": allowance}), body)
        self.active.update(request=body, request_hash=fingerprint(body), input_budget=budget)
        self.persist()
        if not budget["fits"]:
            raise HarnessError("context_limit", "SDK wire request exceeds pinned context window")
        if self.before_dispatch:
            self.before_dispatch(body, budget)
        self.active["request_sent"] = True
        self.persist()  # At most once per dispatch; ambiguous transport failures remain recorded.

    async def _response(self, response):
        await response.aread()
        self.active.update(http_status=response.status_code, raw_response=response.text)
        self.persist()  # Preserve even malformed, truncated and rejected outputs before validation.
        if not response.is_success:
            return
        raw = response.json()
        choice = (raw.get("choices") or [{}])[0]
        finish = choice.get("finish_reason")
        usage = raw.get("usage") or {}
        self.active.update(finish_reason=finish, usage=usage)
        if finish not in ("stop", "tool_calls"):
            raise HarnessError("truncated" if finish == "length" else "invalid_finish_reason", "bad finish")
        message = choice.get("message") or {}
        self.active["reasoning_observed"] = bool(message.get("reasoning_content") or message.get("reasoning"))
        calls = message.get("tool_calls") or []
        ids = [call.get("id") for call in calls]
        if len(set(ids)) != len(ids) or any(not value for value in ids):
            raise HarnessError("invalid_tool_call_id", "tool identities missing or duplicated")
        if finish == "tool_calls" and not calls:
            raise HarnessError("invalid_tool_calls", "empty tool_calls finish")
        count = usage.get("prompt_tokens")
        budget = self.active["input_budget"]
        if self.settings.backend == "ollama" and self.settings.tokenizer_sha256:
            if count is None or count > budget["input_tokens"]:
                raise HarnessError("prompt_accounting_mismatch", "server usage exceeds padded estimate")
        self.persist()


def error_code(exc):
    """SDK wraps HTTP-hook failures; retain the original business failure without logging secrets."""
    current, seen = exc, set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, HarnessError):
            return current.code
        current = current.__cause__ or current.__context__
    return type(exc).__name__
