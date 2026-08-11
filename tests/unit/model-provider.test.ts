import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const sdkMocks = vi.hoisted(() => ({ query: vi.fn() }));

vi.mock("@anthropic-ai/claude-agent-sdk", () => ({
  query: sdkMocks.query,
}));

import {
  buildOpenAIChatCompletionsUrl,
  createModelProvider,
  ProviderRequestError,
} from "../../src/services/model-provider.js";

describe("model provider adapters", () => {
  beforeEach(() => {
    sdkMocks.query.mockReset();
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("normalizes an OpenAI-compatible SSE stream", async () => {
    const body = [
      `data: ${JSON.stringify({ choices: [{ delta: { content: "hello " } }] })}`,
      `data: ${JSON.stringify({ choices: [{ delta: { content: "world" }, finish_reason: "stop" }] })}`,
      "data: [DONE]",
      "",
    ].join("\n\n");
    vi.mocked(fetch).mockResolvedValueOnce(new Response(body, {
      status: 200,
      headers: { "content-type": "text/event-stream" },
    }));

    const provider = createModelProvider({
      provider: "openai-compatible",
      apiKey: "secret",
      baseUrl: "https://api.example.com/v1",
      model: "example-model",
      source: "environment",
    });
    const events = [];
    for await (const event of provider.run({
      prompt: "question",
      systemPrompt: "system",
      cwd: process.cwd(),
      maxTurns: 4,
      tools: [],
      abortController: new AbortController(),
    })) {
      events.push(event);
    }

    expect(events.at(-1)).toMatchObject({
      type: "result",
      subtype: "success",
      output: "hello world",
      turns: 1,
    });
    expect(fetch).toHaveBeenCalledWith(
      "https://api.example.com/v1/chat/completions",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ Authorization: "Bearer secret" }),
      }),
    );
    const request = vi.mocked(fetch).mock.calls[0]?.[1];
    expect(JSON.parse(String(request?.body))).toMatchObject({
      model: "example-model",
      stream: true,
      messages: [
        { role: "system", content: "system" },
        { role: "user", content: "question" },
      ],
    });
  });

  it("passes JSON Schema to the Agent SDK and returns structured output", async () => {
    sdkMocks.query.mockReturnValueOnce((async function* () {
      yield {
        type: "result",
        subtype: "success",
        is_error: false,
        num_turns: 1,
        result: "",
        structured_output: { goal: "确认入口" },
        usage: { input_tokens: 12, output_tokens: 3 },
        total_cost_usd: 0.01,
        session_id: "sdk-session",
        errors: [],
      };
    })());
    const provider = createModelProvider({
      provider: "anthropic-compatible",
      apiKey: "secret",
      baseUrl: "https://api.example.com",
      model: "example-model",
      source: "environment",
    });
    const schema = {
      type: "object",
      required: ["goal"],
      properties: { goal: { type: "string" } },
    };
    const events = [];
    for await (const event of provider.run({
      prompt: "question",
      systemPrompt: "system",
      cwd: process.cwd(),
      maxTurns: 1,
      tools: [],
      abortController: new AbortController(),
      outputSchema: schema,
    })) {
      events.push(event);
    }

    expect(events.at(-1)).toMatchObject({
      type: "result",
      output: JSON.stringify({ goal: "确认入口" }),
      structuredOutput: { goal: "确认入口" },
      usage: { input_tokens: 12, output_tokens: 3 },
      costUsd: 0.01,
      sessionId: "sdk-session",
    });
    expect(sdkMocks.query).toHaveBeenCalledWith(expect.objectContaining({
      options: expect.objectContaining({
        outputFormat: { type: "json_schema", schema },
        settingSources: [],
        persistSession: false,
      }),
    }));
  });

  it("supports a non-streaming JSON fallback from compatible endpoints", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify({
      choices: [{ message: { content: "json response" } }],
    }), {
      status: 200,
      headers: { "content-type": "application/json" },
    }));
    const provider = createModelProvider({
      provider: "openai-compatible",
      apiKey: "secret",
      baseUrl: "https://api.example.com/v1",
      model: "example-model",
      source: "environment",
    });
    const events = [];
    for await (const event of provider.run({
      prompt: "question",
      systemPrompt: "system",
      cwd: process.cwd(),
      maxTurns: 4,
      tools: [],
      abortController: new AbortController(),
    })) {
      events.push(event);
    }
    expect(events.at(-1)).toMatchObject({ type: "result", output: "json response" });
  });

  it("classifies rate-limit errors as retryable", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(new Response("rate limited", { status: 429 }));
    const provider = createModelProvider({
      provider: "openai-compatible",
      apiKey: "secret",
      baseUrl: "https://api.example.com/v1",
      model: "example-model",
      source: "environment",
    });
    const consume = async () => {
      for await (const _event of provider.run({
        prompt: "question",
        systemPrompt: "system",
        cwd: process.cwd(),
        maxTurns: 4,
        tools: [],
        abortController: new AbortController(),
      })) {
        // consume
      }
    };
    await expect(consume()).rejects.toMatchObject<Partial<ProviderRequestError>>({
      retryable: true,
    });
  });

  it("does not duplicate an explicit chat completions path", () => {
    expect(buildOpenAIChatCompletionsUrl("https://example.com/v1/chat/completions/"))
      .toBe("https://example.com/v1/chat/completions");
  });
});
