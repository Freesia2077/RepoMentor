import asyncio
import json

import httpx
import pytest

from repomentor.harness import CancellationToken
from repomentor.runtimes.chat import ChatCompletionsRuntime, ChatSettings


def settings(**changes):
    return ChatSettings(
        baseUrl="http://127.0.0.1:11435/v1",
        model="fixture",
        model_revision="sha256:" + "a" * 64,
        quantization="Q4_K_M",
        server_version="fixture-v1",
        **changes,
    )


def packet(content="", finish=None, **message):
    return {"choices": [{"index": 0, "delta": {"content": content, **message}, "finish_reason": finish}]}


def sse(*packets, done=True):
    return "".join("data: " + json.dumps(p, ensure_ascii=False) + "\n\n" for p in packets).encode() + (
        b"data: [DONE]\n\n" if done else b""
    )


async def test_stream_unicode_and_usage_only_final_chunk():
    observed = []

    def handler(request):
        body = json.loads(request.content)
        assert body["reasoning_effort"] == "none" and body["stream_options"]["include_usage"]
        assert body["presence_penalty"] == 1.5
        return httpx.Response(
            200,
            content=sse(
                packet("仓库"),
                packet("学习", "stop"),
                {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16}},
            ),
        )

    result = await ChatCompletionsRuntime(
        settings(presence_penalty=1.5), transport=httpx.MockTransport(handler)
    ).complete([{"role": "user", "content": "问题"}], on_text=observed.append)
    assert result.status == "success" and result.content == "仓库学习"
    assert observed == ["仓库", "学习"]
    assert result.usage["total_tokens"] == 16 and result.request_sent
    assert result.time_to_first_token_seconds is not None


async def test_timeout_cleanup_preserves_result_when_transport_raises_during_cancellation():
    async def handler(request):
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise httpx.ReadTimeout("transport failed while closing cancelled request") from None

    runtime = ChatCompletionsRuntime(settings(timeout_seconds=0.02), transport=httpx.MockTransport(handler))
    result = await runtime.complete([{"role": "user", "content": "fixture"}])
    assert result.status == "timeout" and result.error_code == "timeout"
    assert result.request_sent and result.elapsed_seconds is not None


@pytest.mark.parametrize("stream", [False, True])
async def test_tool_call_ids_and_arguments_survive_normalization(stream):
    if stream:
        content = sse(
            packet(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "call-real-7",
                        "function": {"name": "search_symbols", "arguments": '{"query":'},
                    }
                ]
            ),
            packet(finish="tool_calls", tool_calls=[{"index": 0, "function": {"arguments": '"helper"}'}}]),
        )
    else:
        content = json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-real-7",
                                    "function": {"name": "search_symbols", "arguments": '{"query":"helper"}'},
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        ).encode()
    result = await ChatCompletionsRuntime(
        settings(stream=stream),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=content)),
    ).complete([{"role": "user", "content": "locate helper"}])
    assert result.status == "success"
    assert result.tool_calls[0].call_id == "call-real-7"
    assert result.tool_calls[0].arguments == {"query": "helper"}
    assert result.usage["completion_tokens"] is None
    assert (result.time_to_first_token_seconds is not None) == stream
    assert result.raw_tool_calls[0]["id"] == "call-real-7"


@pytest.mark.parametrize(
    "status,code",
    [(401, "authentication"), (429, "rate_limit"), (503, "provider_unavailable"), (400, "request_rejected")],
)
async def test_errors_never_echo_provider_body_or_retry(status, code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": "private-key-in-provider-error"})

    result = await ChatCompletionsRuntime(settings(), transport=httpx.MockTransport(handler)).complete([])
    assert result.status == "failed" and result.error_code == code and len(calls) == 1
    assert "private-key" not in result.model_dump_json()


async def test_stream_disconnect_keeps_partial_output_and_is_not_success():
    runtime = ChatCompletionsRuntime(
        settings(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=sse(packet("partial"), done=False))
        ),
    )
    result = await runtime.complete([])
    assert result.content == "partial" and result.error_code == "incomplete_stream"
    assert result.status == "format_error"


async def test_length_and_schema_errors_are_distinct():
    async def run(finish):
        runtime = ChatCompletionsRuntime(
            settings(),
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=sse(packet('{"partial":', finish)))
            ),
        )
        return await runtime.complete([], response_schema={"type": "object", "required": ["answer"]})

    assert (await run("length")).status == "truncated"
    assert (await run("stop")).status == "format_error"


async def test_oversized_input_rejected_without_silent_truncation():
    def forbidden(request):
        pytest.fail("no HTTP request permitted")

    result = await ChatCompletionsRuntime(settings(), transport=httpx.MockTransport(forbidden)).complete(
        [{"role": "user", "content": "汉" * 2000}]
    )
    assert result.status == "context_limit" and not result.request_sent


@pytest.mark.parametrize("cancel", [False, True])
async def test_timeout_and_cancellation_close_pending_request(cancel):
    closed = []

    async def slow(request):
        try:
            await asyncio.sleep(10)
        finally:
            closed.append(True)

    token = CancellationToken()
    if cancel:
        asyncio.get_running_loop().call_later(0.02, token.cancel)
    result = await ChatCompletionsRuntime(
        settings(timeout_seconds=1 if cancel else 0.02), transport=httpx.MockTransport(slow)
    ).complete([], token=token)
    assert result.status == ("cancelled" if cancel else "timeout")
    assert closed and result.request_sent


def test_cache_configuration_separates_quantization_and_excludes_keys():
    q4 = settings()
    bf16 = q4.model_copy(update={"quantization": "BF16"})
    other_key = q4.model_copy(update={"apiKey": "not-for-public-config"})
    assert q4.config_id != bf16.config_id
    assert q4.config_id == other_key.config_id
    assert "apiKey" not in q4.public()
    assert "presence_penalty" not in q4.public()
    assert "presence_penalty" not in ChatCompletionsRuntime(q4).request_body([])
    changed = q4.model_copy(update={"presence_penalty": 1.5})
    assert changed.config_id != q4.config_id and changed.public()["presence_penalty"] == 1.5


async def test_ollama_changed_revision_is_rejected_before_generation():
    profile = settings(backend="ollama")

    def handler(request):
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": profile.server_version})
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": profile.model, "digest": "changed"}]})

    with pytest.raises(ValueError, match="revision"):
        await ChatCompletionsRuntime(profile, transport=httpx.MockTransport(handler)).verify_backend()
