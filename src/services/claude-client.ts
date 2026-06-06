import type { StageName, ExplorerOutput, MentorOutput, ContributorOutput } from "../types/index.js";
import { validateExplorerOutput, validateMentorOutput, validateContributorOutput } from "../lib/schema.js";
import { config } from "../config.js";
import { query } from "@anthropic-ai/claude-agent-sdk";
import fs from "node:fs";

// ========== 超时配置 ==========

const STAGE_TIMEOUT_MS: Record<StageName, number> = {
  explorer: 120_000,
  mentor: 300_000,
  contributor: 180_000,
};

const MAX_TURNS: Record<StageName, number> = {
  explorer: 30,
  mentor: 40,
  contributor: 30,
};

// ========== 工具白名单 ==========

const ALLOWED_TOOLS: Record<StageName, string[]> = {
  explorer: ["Read", "Glob", "Grep"],
  mentor: ["Read", "Grep"],
  contributor: ["Read", "Grep"],
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
 * 通过 Claude Agent SDK 的 query() 调用 Subagent，
 * localPath 作为 cwd 限制 Agent 的文件系统访问范围。
 */
export async function runStage<S extends StageName>(
  stage: S,
  input: Record<string, unknown>,
  callbacks: StageCallbacks,
  localPath: string,
): Promise<StageOutputFor<S>> {
  const validator = VALIDATORS[stage];

  callbacks.onProgress(`正在启动 ${stage} 阶段...`);

  // 1. 调用 Agent
  const rawOutput = await invokeAgent(stage, input, callbacks, localPath);

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

// ========== Agent 调用（SDK 接线） ==========

/**
 * 调用 Claude Agent SDK 的 query() 运行 Subagent。
 *
 * - 读取 agents/<stage>/agent.md 作为 systemPrompt（去掉 YAML frontmatter）
 * - 通过 options.cwd 限定 Agent 文件系统访问范围为克隆仓库目录
 * - 通过 options.env 注入 ANTHROPIC_* 环境变量，SDK 子进程自动路由到 DeepSeek
 * - 通过 AbortController + setTimeout 实现超时
 */
async function invokeAgent(
  stage: StageName,
  input: Record<string, unknown>,
  callbacks: StageCallbacks,
  localPath: string,
): Promise<string> {
  // 1. 加载 Agent 定义（去掉 YAML frontmatter）
  const systemPrompt = loadAgentDefinition(stage);
  const inputStr = JSON.stringify(input, null, 2);
  const prompt = buildPromptForStage(stage, inputStr);

  // 2. 超时控制
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), STAGE_TIMEOUT_MS[stage]);

  // 3. 收集 assistant 消息中的文本输出
  const assistantChunks: string[] = [];

  try {
    for await (const message of query({
      prompt,
      options: {
        systemPrompt,
        model: config.ANTHROPIC_MODEL,
        allowedTools: ALLOWED_TOOLS[stage],
        cwd: localPath,
        maxTurns: MAX_TURNS[stage],
        env: {
          ...process.env,
          CLAUDE_CODE_GIT_BASH_PATH: process.env.CLAUDE_CODE_GIT_BASH_PATH || "D:\\Git\\usr\\bin\\bash.exe",
          ANTHROPIC_BASE_URL: config.ANTHROPIC_BASE_URL,
          ANTHROPIC_AUTH_TOKEN: config.ANTHROPIC_AUTH_TOKEN,
          ANTHROPIC_MODEL: config.ANTHROPIC_MODEL,
        },
      },
    })) {
      const msg = message as Record<string, unknown>;
      const type = msg.type as string;

      switch (type) {
        case "assistant": {
          // SDK assistant 消息: 内容在 msg.message 字段
          // msg.message 是 Anthropic Messages API 的 response 对象
          // 结构: { role: "assistant", content: [{type: "text", text: "..."}, {type: "tool_use", ...}] }
          const apiMessage = msg.message as Record<string, unknown> | undefined;
          if (apiMessage) {
            const text = extractFromContent(apiMessage.content);
            if (text) {
              // 每次 assistant 消息都可能包含文本，保留最后一条（最终 JSON 输出）
              assistantChunks.push(text);
            }
          }
          callbacks.onProgress(`Agent (${stage}) 输出中...`);
          break;
        }

        case "result": {
          // result 是元数据消息（duration, cost, usage 等），不包含文本输出
          callbacks.onProgress(`Agent (${stage}) 完成`);
          break;
        }

        case "user": {
          // user 消息包含工具调用结果（tool_use_result）
          // 可以用来跟踪工具使用进度
          const toolResult = msg.tool_use_result as Record<string, unknown> | undefined;
          if (toolResult) {
            callbacks.onProgress(`Agent (${stage}) 工具调用完成`);
          }
          break;
        }

        // system 等类型忽略
      }
    }
  } finally {
    clearTimeout(timeoutId);
  }
  // Agent 的文本输出在 assistant 消息的 message.content 中
  // 最终的 JSON 输出通常在最后一条包含文本的 assistant 消息中
  let rawOutput = "";
  if (assistantChunks.length > 0) {
    // 优先使用最后一条 assistant 文本（通常是最终 JSON 输出）
    // 但有时 JSON 分散在多条 assistant 消息中，所以全部拼接
    rawOutput = assistantChunks.join("\n");
  }

  if (!rawOutput.trim()) {
    throw new LLMError(`Agent (${stage}) 返回了空输出`, true);
  }

  return rawOutput;
}

// ========== 消息内容提取辅助 ==========

/**
 * 从 content 字段提取文本。
 * content 可能是 string | Array<{type: "text", text: string} | string>
 */
function extractFromContent(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) return extractContentArray(content);
  return "";
}

/**
 * 从 content array 中提取所有 text block 的文本
 */
function extractContentArray(arr: unknown[]): string {
  return arr
    .map((block: unknown) => {
      if (typeof block === "string") return block;
      if (block && typeof block === "object") {
        const b = block as Record<string, unknown>;
        if (b.type === "text" && typeof b.text === "string") return b.text;
      }
      return "";
    })
    .filter(Boolean)
    .join("\n");
}

/**
 * 加载 Agent 定义文件，去掉 YAML frontmatter（--- ... ---），
 * 剩余内容作为 systemPrompt 传给 SDK。
 */
function loadAgentDefinition(stage: StageName): string {
  const filePath = AGENT_PATHS[stage];
  try {
    const raw = fs.readFileSync(filePath, "utf-8");
    return stripFrontmatter(raw);
  } catch {
    return `You are the ${stage} agent for RepoMentor, a repository analysis pipeline.`;
  }
}

/**
 * 去掉 YAML frontmatter（以 --- 开头和结尾的元数据块）
 */
function stripFrontmatter(text: string): string {
  const lines = text.split("\n");
  if (lines[0]?.trim() === "---") {
    const endIdx = lines.findIndex((line, i) => i > 0 && line.trim() === "---");
    if (endIdx !== -1) {
      return lines.slice(endIdx + 1).join("\n").trim();
    }
  }
  return text.trim();
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
