import { query } from "@anthropic-ai/claude-agent-sdk";

import type { ModelSettings } from "./model-settings.js";

export interface ProviderToolCall {
  name: string;
  input: Record<string, unknown>;
}

export type ProviderEvent =
  | {
      type: "assistant";
      text: string;
      toolCalls: ProviderToolCall[];
    }
  | {
      type: "result";
      subtype: string;
      isError: boolean;
      output: string;
      structuredOutput?: unknown;
      turns?: number;
      usage?: Record<string, unknown>;
      modelUsage?: Record<string, unknown>;
      costUsd?: number;
      sessionId?: string;
      errors: string[];
    };

export interface ProviderRunRequest {
  prompt: string;
  systemPrompt: string;
  cwd: string;
  maxTurns: number;
  tools: string[];
  abortController: AbortController;
  canUseTool?: (...args: any[]) => any;
  outputSchema?: Record<string, unknown>;
}

export interface ModelProvider {
  readonly id: ModelSettings["provider"];
  run(request: ProviderRunRequest): AsyncIterable<ProviderEvent>;
}

export function createModelProvider(settings: ModelSettings): ModelProvider {
  if (settings.provider === "openai-compatible") {
    return new OpenAICompatibleProvider(settings);
  }
  return new ClaudeAgentSdkProvider(settings);
}

class ClaudeAgentSdkProvider implements ModelProvider {
  readonly id = "anthropic-compatible" as const;

  constructor(private readonly settings: ModelSettings) {}

  async *run(request: ProviderRunRequest): AsyncIterable<ProviderEvent> {
    for await (const message of query({
      prompt: request.prompt,
      options: {
        systemPrompt: request.systemPrompt,
        model: this.settings.model,
        tools: request.tools,
        allowedTools: request.tools,
        cwd: request.cwd,
        maxTurns: request.maxTurns,
        abortController: request.abortController,
        permissionMode: "dontAsk",
        canUseTool: request.canUseTool,
        settingSources: [],
        persistSession: false,
        outputFormat: request.outputSchema
          ? { type: "json_schema", schema: request.outputSchema }
          : undefined,
        env: {
          ...process.env,
          ANTHROPIC_BASE_URL: this.settings.baseUrl,
          ANTHROPIC_AUTH_TOKEN: this.settings.apiKey,
          ANTHROPIC_API_KEY: this.settings.apiKey,
          ANTHROPIC_MODEL: this.settings.model,
        },
      },
    })) {
      const record = message as Record<string, unknown>;
      if (record.type === "assistant") {
        const apiMessage = record.message as Record<string, unknown> | undefined;
        const content = Array.isArray(apiMessage?.content) ? apiMessage.content : [];
        yield {
          type: "assistant",
          text: extractText(content),
          toolCalls: content
            .filter((block): block is Record<string, unknown> =>
              Boolean(block) && typeof block === "object" && block.type === "tool_use"
            )
            .map((block) => ({
              name: typeof block.name === "string" ? block.name : "Unknown",
              input: isRecord(block.input) ? block.input : {},
            })),
        };
        continue;
      }

      if (record.type === "result") {
        const structuredOutput = record.structured_output;
        const output = typeof structuredOutput === "string"
          ? structuredOutput
          : structuredOutput !== undefined
            ? JSON.stringify(structuredOutput)
            : typeof record.result === "string" ? record.result : "";
        yield {
          type: "result",
          subtype: typeof record.subtype === "string" ? record.subtype : "unknown",
          isError: record.is_error === true,
          output,
          structuredOutput,
          turns: typeof record.num_turns === "number" ? record.num_turns : undefined,
          usage: isRecord(record.usage) ? record.usage : undefined,
          modelUsage: isRecord(record.modelUsage) ? record.modelUsage : undefined,
          costUsd: typeof record.total_cost_usd === "number"
            ? record.total_cost_usd
            : undefined,
          sessionId: typeof record.session_id === "string"
            ? record.session_id
            : undefined,
          errors: Array.isArray(record.errors)
            ? record.errors.filter((item): item is string => typeof item === "string")
            : [],
        };
      }
    }
  }
}

class OpenAICompatibleProvider implements ModelProvider {
  readonly id = "openai-compatible" as const;

  constructor(private readonly settings: ModelSettings) {}

  async *run(request: ProviderRunRequest): AsyncIterable<ProviderEvent> {
    if (request.tools.length > 0) {
      throw new ProviderRequestError(
        "OpenAI-compatible Provider 尚未启用工具调用适配",
        false,
      );
    }

    const response = await fetch(buildOpenAIChatCompletionsUrl(this.settings.baseUrl), {
      method: "POST",
      headers: {
        Authorization: `Bearer ${this.settings.apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: this.settings.model,
        messages: [
          { role: "system", content: request.systemPrompt },
          { role: "user", content: request.prompt },
        ],
        stream: true,
        ...(request.outputSchema ? { response_format: { type: "json_object" } } : {}),
      }),
      signal: request.abortController.signal,
    });

    if (!response.ok) {
      const body = await response.text();
      throw new ProviderRequestError(
        `OpenAI-compatible API 请求失败 (${response.status})${body ? `: ${body.slice(0, 1000)}` : ""}`,
        response.status === 408 || response.status === 409 || response.status === 429 || response.status >= 500,
      );
    }

    const contentType = response.headers.get("content-type") ?? "";
    if (contentType.includes("application/json")) {
      const payload = await response.json();
      const output = extractOpenAIResponseText(payload);
      if (!output.trim()) {
        throw new ProviderRequestError("OpenAI-compatible API 返回了空输出", true);
      }
      yield { type: "assistant", text: output, toolCalls: [] };
      yield {
        type: "result",
        subtype: "success",
        isError: false,
        output,
        structuredOutput: undefined,
        turns: 1,
        errors: [],
      };
      return;
    }
    if (!response.body) {
      throw new ProviderRequestError("OpenAI-compatible API 返回了空响应流", true);
    }

    let output = "";
    for await (const payload of parseServerSentEvents(response.body)) {
      if (payload === "[DONE]") break;
      let event: unknown;
      try {
        event = JSON.parse(payload);
      } catch {
        continue;
      }
      if (!isRecord(event)) continue;
      if (isRecord(event.error)) {
        const message = typeof event.error.message === "string"
          ? event.error.message
          : JSON.stringify(event.error);
        throw new ProviderRequestError(`OpenAI-compatible API 错误: ${message}`, true);
      }

      const choices = Array.isArray(event.choices) ? event.choices : [];
      for (const choice of choices) {
        if (!isRecord(choice)) continue;
        const delta = isRecord(choice.delta) ? choice.delta : {};
        const text = extractOpenAIText(delta.content);
        if (text) {
          output += text;
          yield { type: "assistant", text, toolCalls: [] };
        }
      }
    }

    if (!output.trim()) {
      throw new ProviderRequestError("OpenAI-compatible API 返回了空输出", true);
    }
    yield {
      type: "result",
      subtype: "success",
      isError: false,
      output,
      structuredOutput: undefined,
      turns: 1,
      errors: [],
    };
  }
}

export class ProviderRequestError extends Error {
  constructor(message: string, public readonly retryable: boolean) {
    super(message);
    this.name = "ProviderRequestError";
  }
}

export function buildOpenAIChatCompletionsUrl(baseUrl: string): string {
  const normalized = baseUrl.replace(/\/+$/, "");
  if (/\/chat\/completions$/i.test(normalized)) return normalized;
  return `${normalized}/chat/completions`;
}

async function* parseServerSentEvents(
  stream: ReadableStream<Uint8Array>,
): AsyncIterable<string> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split(/\r?\n\r?\n/);
      buffer = chunks.pop() ?? "";
      for (const chunk of chunks) {
        const data = chunk
          .split(/\r?\n/)
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart())
          .join("\n");
        if (data) yield data;
      }
    }

    buffer += decoder.decode();
    const data = buffer
      .split(/\r?\n/)
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart())
      .join("\n");
    if (data) yield data;
  } finally {
    reader.releaseLock();
  }
}

function extractText(content: unknown[]): string {
  return content
    .map((block) => {
      if (typeof block === "string") return block;
      if (isRecord(block) && block.type === "text" && typeof block.text === "string") {
        return block.text;
      }
      return "";
    })
    .filter(Boolean)
    .join("\n");
}

function extractOpenAIText(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .map((part) => isRecord(part) && typeof part.text === "string" ? part.text : "")
    .join("");
}

function extractOpenAIResponseText(payload: unknown): string {
  if (!isRecord(payload) || !Array.isArray(payload.choices)) return "";
  return payload.choices
    .map((choice) => {
      if (!isRecord(choice) || !isRecord(choice.message)) return "";
      return extractOpenAIText(choice.message.content);
    })
    .join("");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
