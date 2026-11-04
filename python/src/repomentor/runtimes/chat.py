"""A bounded Chat Completions transport. Tool calls are returned, never executed here."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import time
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from pydantic import Field, SecretStr, model_validator

from ..contracts import Contract, ToolCall


def fingerprint(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


class ChatSettings(Contract):
    provider: Literal["openai-compatible"] = "openai-compatible"
    backend: Literal["generic", "ollama"] = "generic"
    apiKey: SecretStr = Field(default_factory=lambda: SecretStr(""))
    baseUrl: str
    model: str = Field(min_length=1)
    model_revision: str = Field(min_length=8)
    quantization: str = Field(min_length=1)
    server_version: str = Field(min_length=1)
    context_window: int = Field(default=4096, ge=512)
    max_tokens: int = Field(default=512, ge=1)
    timeout_seconds: float = Field(default=180, gt=0)
    temperature: float = Field(default=0, ge=0, le=2)
    top_p: float = Field(default=1, gt=0, le=1)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    seed: int = 42
    reasoning_effort: str | None = "none"
    stream: bool = True
    template_reserve: int = Field(default=256, ge=64)
    tokenizer_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_endpoint(self):
        url = urlparse(self.baseUrl)
        if (
            url.scheme not in ("http", "https")
            or not url.netloc
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("invalid model endpoint")
        if not self.apiKey.get_secret_value() and url.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("remote endpoints require an explicit credential")
        if self.max_tokens + self.template_reserve >= self.context_window:
            raise ValueError("output allowance exceeds context")
        return self

    def public(self):
        value = self.model_dump(mode="json", exclude={"apiKey"})
        for optional in ("tokenizer_sha256", "presence_penalty"):
            if value[optional] is None:
                value.pop(optional)  # Preserve identities of configurations without the option.
        return value

    @property
    def config_id(self):
        return fingerprint(self.public())[:24]


class CompletionResult(Contract):
    status: Literal["success", "failed", "timeout", "cancelled", "truncated", "format_error", "context_limit"]
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    raw_tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, int | None] = Field(
        default_factory=lambda: {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
    )
    elapsed_seconds: float = 0
    time_to_first_token_seconds: float | None = None
    reasoning_observed: bool = False
    error_code: str | None = None
    request_hash: str
    request_sent: bool = False
    input_budget: dict[str, Any] = Field(default_factory=dict)


class ProtocolError(Exception):
    pass


class ChatCompletionsRuntime:
    name = "chat-completions"

    def __init__(self, settings: ChatSettings, *, transport=None, before_dispatch=None):
        self.settings = settings
        self.transport = transport
        self.before_dispatch = before_dispatch

    def request_body(self, messages, tools=None, response_schema=None):
        body = {
            "model": self.settings.model,
            "messages": messages,
            "stream": self.settings.stream,
            "temperature": self.settings.temperature,
            "top_p": self.settings.top_p,
            "seed": self.settings.seed,
            "max_tokens": self.settings.max_tokens,
        }
        if self.settings.reasoning_effort is not None:
            body["reasoning_effort"] = self.settings.reasoning_effort
        if self.settings.presence_penalty is not None:
            body["presence_penalty"] = self.settings.presence_penalty
        if self.settings.stream:
            body["stream_options"] = {"include_usage": True}
        if tools:
            body["tools"] = tools
        if response_schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "answer", "strict": True, "schema": response_schema},
            }
        return body

    def _client(self):
        headers = {}
        if self.settings.apiKey.get_secret_value():
            headers["Authorization"] = "Bearer " + self.settings.apiKey.get_secret_value()
        return httpx.AsyncClient(
            headers=headers,
            timeout=self.settings.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=self.transport,
        )

    async def verify_backend(self):
        """Ollama tags are mutable; verify weights, quantization, context and server before a run."""
        if self.settings.backend != "ollama":
            return {"verification": "caller_declared_revision"}
        origin = self.settings.baseUrl.removesuffix("/").removesuffix("/v1")
        async with self._client() as client:
            version = (await client.get(origin + "/api/version")).raise_for_status().json()
            tags = (await client.get(origin + "/api/tags")).raise_for_status().json()
            entry = next((m for m in tags["models"] if m["name"] == self.settings.model), None)
            if (
                not entry
                or entry["digest"] != self.settings.model_revision
                or version["version"] != self.settings.server_version
            ):
                raise ValueError("local model or server revision changed")
            show = (
                (await client.post(origin + "/api/show", json={"model": self.settings.model}))
                .raise_for_status()
                .json()
            )
            parameters = show.get("parameters", "")
            import re

            context = re.search(r"(?m)^num_ctx\s+(\d+)\s*$", parameters)
            if not context or int(context[1]) != self.settings.context_window:
                raise ValueError("server context differs from the experiment configuration")
            if show["details"]["quantization_level"] != self.settings.quantization:
                raise ValueError("quantization differs from the experiment configuration")
            return {
                "verification": "ollama_metadata",
                "digest": entry["digest"],
                "server_version": version["version"],
                "context_window": int(context[1]),
                "quantization": show["details"]["quantization_level"],
            }

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools=None,
        response_schema=None,
        token=None,
        on_text=lambda text: None,
    ):
        started = time.monotonic()
        body = self.request_body(messages, tools, response_schema)
        result = CompletionResult(status="failed", request_hash=fingerprint(body))
        from ..token_budget import input_budget

        result.input_budget = input_budget(self.settings, messages, tools, response_schema)
        if not result.input_budget["fits"]:
            result.status, result.error_code = "context_limit", "input_budget"
            return result
        calls = {}

        def consume(packet, streaming):
            if packet.get("error"):
                raise ProtocolError("provider_stream_error")
            if packet.get("usage"):
                result.usage = {key: packet["usage"].get(key) for key in result.usage}
            choices = packet.get("choices", [])
            if not choices:
                return
            choice = choices[0]
            if choice.get("index", 0) != 0:
                raise ProtocolError("unexpected_choice")
            message = choice.get("delta", {}) if streaming else choice["message"]
            if message.get("reasoning") or message.get("reasoning_content"):
                result.reasoning_observed = True
            content = message.get("content") or ""
            if not isinstance(content, str):
                raise ProtocolError("non_text_content")
            if streaming and (content or message.get("tool_calls")):
                if result.time_to_first_token_seconds is None:
                    result.time_to_first_token_seconds = time.monotonic() - started
            if content:
                result.content += content
                on_text(content)
            for ordinal, call in enumerate(message.get("tool_calls") or []):
                index = call.get("index", ordinal)
                if not isinstance(index, int) or index < 0 or index > 31:
                    raise ProtocolError("invalid_tool_index")
                row = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                if call.get("id"):
                    if row["id"] and row["id"] != call["id"]:
                        raise ProtocolError("conflicting_tool_id")
                    row["id"] = call["id"]
                function = call.get("function") or {}
                row["name"] += function.get("name", "")
                row["arguments"] += function.get("arguments", "")
            if choice.get("finish_reason") is not None:
                result.finish_reason = choice["finish_reason"]

        async def request():
            async with self._client() as client:
                if self.before_dispatch:
                    self.before_dispatch(body, result.input_budget)
                result.request_sent = True
                async with client.stream(
                    "POST", self.settings.baseUrl.rstrip("/") + "/chat/completions", json=body
                ) as response:
                    if response.status_code != 200:
                        code = response.status_code
                        result.error_code = (
                            "authentication"
                            if code in (401, 403)
                            else "rate_limit"
                            if code == 429
                            else "provider_unavailable"
                            if code >= 500
                            else "request_rejected"
                        )
                        return
                    if not self.settings.stream:
                        raw = await response.aread()
                        consume(json.loads(raw), False)
                        return
                    data = []
                    done = False
                    async for line in response.aiter_lines():
                        if line.startswith("data:"):
                            data.append(line[5:].lstrip())
                        elif not line and data:
                            value = "\n".join(data)
                            data.clear()
                            if value == "[DONE]":
                                done = True
                                break
                            consume(json.loads(value), True)
                    if not done:
                        raise ProtocolError("incomplete_stream")

        task = None
        try:
            if token and token.cancelled:
                result.status, result.error_code = "cancelled", "cancelled"
                return result
            async with asyncio.timeout(self.settings.timeout_seconds):
                task = asyncio.create_task(request())
                while not task.done():
                    if token and token.cancelled:
                        result.status, result.error_code = "cancelled", "cancelled"
                        return result
                    await asyncio.wait({task}, timeout=0.05)
                await task
            if result.error_code:
                return result
            if (
                result.input_budget["mode"] == "qwen35_ollama_tools_padded_v1"
                and result.usage["prompt_tokens"] is not None
                and result.usage["prompt_tokens"] > result.input_budget["input_tokens"]
            ):
                result.status, result.error_code = "format_error", "tool_prompt_budget_underestimated"
                return result
            if self.settings.tokenizer_sha256 and not tools and result.usage["prompt_tokens"] is not None:
                # No silent truncation or unrecorded renderer/tokenizer drift.
                if result.usage["prompt_tokens"] != result.input_budget["input_tokens"]:
                    result.status, result.error_code = "format_error", "prompt_token_mismatch"
                    return result
            if result.finish_reason == "length":
                result.status, result.error_code = "truncated", "output_limit"
                return result
            if result.finish_reason not in ("stop", "tool_calls"):
                raise ProtocolError("missing_or_unsupported_finish_reason")
            ids = set()
            for row in calls.values():
                arguments = json.loads(row["arguments"])
                if not row["id"] or row["id"] in ids or not row["name"] or not isinstance(arguments, dict):
                    raise ProtocolError("invalid_tool_call")
                ids.add(row["id"])
                result.tool_calls.append(ToolCall(call_id=row["id"], tool=row["name"], arguments=arguments))
            if bool(calls) != (result.finish_reason == "tool_calls"):
                raise ProtocolError("tool_finish_mismatch")
            if not result.content.strip() and not result.tool_calls:
                raise ProtocolError("empty_response")
            if response_schema:
                import jsonschema

                try:
                    jsonschema.validate(json.loads(result.content), response_schema)
                except (ValueError, jsonschema.ValidationError):
                    result.status, result.error_code = "format_error", "schema_validation"
                    return result
            result.status = "success"
        except (TimeoutError, httpx.TimeoutException):
            result.status, result.error_code = "timeout", "timeout"
        except asyncio.CancelledError:
            result.status, result.error_code = "cancelled", "cancelled"
            if token:
                token.cancel()
        except httpx.HTTPError:
            result.error_code = "transport"
        except ProtocolError as exc:
            result.status, result.error_code = "format_error", str(exc)
        except (ValueError, KeyError, TypeError):
            result.status, result.error_code = "format_error", "invalid_response"
        finally:
            result.raw_tool_calls = list(calls.values())
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, httpx.HTTPError):
                    await task
            result.elapsed_seconds = round(time.monotonic() - started, 6)
        return result
