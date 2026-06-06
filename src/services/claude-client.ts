import type { StageName, ExplorerOutput, MentorOutput, ContributorOutput } from "../types/index.js";
import { validateExplorerOutput, validateMentorOutput, validateContributorOutput } from "../lib/schema.js";
import { config } from "../config.js";

// ========== 超时配置 ==========

const STAGE_TIMEOUT_MS: Record<StageName, number> = {
  explorer: 120_000,
  mentor: 300_000,
  contributor: 180_000,
};

// ========== 类型 ==========

export interface StageCallbacks {
  onProgress: (message: string) => void;
  onField: (field: string, value: unknown) => void;
}

export type StageOutputFor<S extends StageName> =
  S extends "explorer" ? ExplorerOutput :
  S extends "mentor" ? MentorOutput :
  S extends "contributor" ? ContributorOutput :
  never;

// ========== 错误 ==========

export class LLMError extends Error {
  retryable: boolean;
  constructor(message: string, retryable: boolean) {
    super(message);
    this.name = "LLMError";
    this.retryable = retryable;
  }
}

export class ParseError extends Error {
  rawOutput: string;
  constructor(message: string, rawOutput: string) {
    super(message);
    this.name = "ParseError";
    this.rawOutput = rawOutput;
  }
}

// ========== Subagent 定义路径 ==========

const AGENT_PATHS: Record<StageName, string> = {
  explorer: "agents/explorer/agent.md",
  mentor: "agents/mentor/agent.md",
  contributor: "agents/contributor/agent.md",
};

const VALIDATORS = {
  explorer: validateExplorerOutput,
  mentor: validateMentorOutput,
  contributor: validateContributorOutput,
} as const;

// ========== 核心函数 ==========

/**
 * 运行一个 Pipeline 阶段。
 *
 * 通过 Claude Agent SDK 调用指定 Subagent，
 * 流式监听输出，逐字段推送给 callbacks，
 * 解析完成 JSON 后经 Zod Schema 校验返回。
 */
export async function runStage<S extends StageName>(
  stage: S,
  input: Record<string, unknown>,
  callbacks: StageCallbacks,
): Promise<StageOutputFor<S>> {
  const validator = VALIDATORS[stage];

  callbacks.onProgress(`正在启动 ${stage} 阶段...`);

  // 1. 调用 Agent
  const rawOutput = await invokeAgent(stage, input, callbacks);

  // 2. 解析 JSON
  let parsed: unknown;
  try {
    parsed = extractJSON(rawOutput);
  } catch (err) {
    throw new ParseError(
      `Agent 输出不是有效的 JSON: ${err instanceof Error ? err.message : String(err)}`,
      rawOutput,
    );
  }

  // 3. Zod Schema 校验
  const result = validator(parsed);

  return result as StageOutputFor<S>;
}

// ========== Agent 调用（骨架实现） ==========

/**
 * 调用 Claude Agent SDK 运行 Subagent。
 *
 * SKELETON — 需要根据 @anthropic-ai/claude-agent-sdk 的实际 API 调整。
 *
 * 预期调用模式:
 *   import { ClaudeSDK } from "@anthropic-ai/claude-agent-sdk";
 *   const sdk = new ClaudeSDK({ apiKey: config.DEEPSEEK_API_KEY, baseURL: config.DEEPSEEK_BASE_URL });
 *   const result = await sdk.agent({ definition: AGENT_PATHS[stage], prompt, model: "deepseek-v4-pro", ... });
 */
async function invokeAgent(
  stage: StageName,
  input: Record<string, unknown>,
  callbacks: StageCallbacks,
): Promise<string> {
  const inputStr = JSON.stringify(input, null, 2);
  const prompt = buildPromptForStage(stage, inputStr);

  // ========== SDK 调用骨架 ==========
  //
  // 实际代码应类似:
  //
  // const { ClaudeSDK } = await import("@anthropic-ai/claude-agent-sdk");
  // const sdk = new ClaudeSDK({
  //   apiKey: config.DEEPSEEK_API_KEY,
  //   baseURL: config.DEEPSEEK_BASE_URL,
  // });
  //
  // const result = await sdk.agent({
  //   model: "deepseek-v4-pro",
  //   definition: AGENT_PATHS[stage],
  //   prompt,
  //   maxTokens: stage === "mentor" ? 16000 : 8000,
  //   timeout: STAGE_TIMEOUT_MS[stage],
  // });
  //
  // return result.content;
  //
  // ==========================================

  throw new Error(
    `Claude Agent SDK 调用尚未实现。请根据 @anthropic-ai/claude-agent-sdk 实际 API 调整 invokeAgent 函数。` +
    `\n  Stage: ${stage}` +
    `\n  Agent definition: ${AGENT_PATHS[stage]}` +
    `\n  Input keys: ${Object.keys(input).join(", ")}` +
    `\n  Token budget: ${stage === "mentor" ? 16000 : 8000}`,
  );
}

// ========== Prompt 构建 ==========

function buildPromptForStage(stage: StageName, inputStr: string): string {
  switch (stage) {
    case "explorer":
      return `请分析以下已克隆的仓库，输出结构化的项目分析报告。

输入数据：
${inputStr}

请严格按照你的 Agent 定义中规定的 JSON Schema 输出，不要包含 markdown 代码块标记。`;

    case "mentor":
      return `请基于 Explorer 阶段的输出，深入分析项目架构，生成学习路径。

输入数据：
${inputStr}

请严格按照你的 Agent 定义中规定的 JSON Schema 输出。`;

    case "contributor":
      return `请基于前两个阶段的分析，找到适合新手参与贡献的切入点。

输入数据：
${inputStr}

请严格按照你的 Agent 定义中规定的 JSON Schema 输出。`;

    default:
      return `请输出符合 Schema 的 JSON。输入:\n${inputStr}`;
  }
}

// ========== JSON 提取 ==========

/**
 * 从 Agent 输出中提取 JSON 对象。
 * 尝试顺序: 直接解析 → ```json 代码块 → 最外层 { }
 */
export function extractJSON(text: string): unknown {
  const trimmed = text.trim();

  // 1. 直接解析
  try {
    return JSON.parse(trimmed);
  } catch { /* 继续 */ }

  // 2. 提取 ```json ... ``` 代码块
  const codeBlock = trimmed.match(/```(?:json)?\s*\n?([\s\S]*?)```/);
  if (codeBlock?.[1]) {
    try {
      return JSON.parse(codeBlock[1].trim());
    } catch { /* 继续 */ }
  }

  // 3. 提取最外层 { ... }
  const firstBrace = trimmed.indexOf("{");
  const lastBrace = trimmed.lastIndexOf("}");
  if (firstBrace !== -1 && lastBrace !== -1 && lastBrace > firstBrace) {
    try {
      return JSON.parse(trimmed.slice(firstBrace, lastBrace + 1));
    } catch { /* 继续 */ }
  }

  throw new Error("无法从输出中提取 JSON");
}
