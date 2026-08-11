import fs from "node:fs";
import path from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";

const sdkMocks = vi.hoisted(() => ({
  query: vi.fn(),
  createSdkMcpServer: vi.fn((options: Record<string, unknown>) => options),
  tool: vi.fn((
    name: string,
    description: string,
    inputSchema: unknown,
    handler: (input: Record<string, unknown>) => Promise<unknown>,
    extras: unknown,
  ) => ({ name, description, inputSchema, handler, extras })),
}));

vi.mock("@anthropic-ai/claude-agent-sdk", () => ({
  query: sdkMocks.query,
  createSdkMcpServer: sdkMocks.createSdkMcpServer,
  tool: sdkMocks.tool,
}));

import {
  AgentRuntimeError,
  CLAUDE_AGENT_SDK_CONTRACT_VERSION,
  ClaudeAgentRuntime,
  isAgentCapabilityKnownUnsupported,
  rememberUnsupportedAgentCapability,
  resetAgentCapabilityMemory,
} from "../../src/services/claude-agent-runtime.js";
import type {
  AgentEvidenceRequest,
  EvidenceBundle,
  HarnessToolObservation,
  MentorOutput,
} from "../../src/types/index.js";

const settings = {
  provider: "anthropic-compatible",
  apiKey: "secret",
  baseUrl: "https://provider.example/anthropic",
  model: "example-model",
  source: "environment",
} as const;

const profile = {
  fileCount: 4,
  fileIndex: ["README.md", "src/index.ts", "src/core.ts", "tests/core.test.ts"],
  fileIndexTruncated: false,
  topLevelTree: ["README.md", "src/", "tests/"],
  treeTruncated: false,
  directoryStats: [],
  readme: { path: "README.md", content: "TOP SECRET README", truncated: false },
  manifests: [],
  exampleManifests: [],
  configFiles: [],
  guidanceFiles: [],
  todoMarkers: [],
  languageStats: { TypeScript: 3 },
  entryCandidates: ["src/index.ts"],
  testCandidates: ["tests/core.test.ts"],
};

const skillPolicy = {
  skillNames: ["analyze-generic"],
  allowedTools: ["find_related_tests" as const],
  preferredTools: ["find_related_tests" as const],
  recommendedQuestions: ["How is core verified?"],
  evidenceRequirements: ["Core and test"],
  stopConditions: ["Core and test selected"],
  maxDiscoveryActions: 1,
  maxEvidenceFiles: 4,
};

const evidencePlan = {
  goal: "确认核心入口与测试",
  rationale: "根据受限调查结果选择文件",
  questions: ["核心实现如何验证？"],
  actions: [],
  files: [
    { path: "src/core.ts", purpose: "核心实现", priority: "high" },
    { path: "tests/core.test.ts", purpose: "代表性测试", priority: "medium" },
  ],
  stopConditions: ["核心实现与测试均已选择"],
};

function request(): AgentEvidenceRequest {
  return {
    phase: "explorer",
    repositoryProfile: profile,
    skillPolicy,
    sdkSkillNames: ["repomentor:analyze-generic"],
    harnessState: {
      stages: {
        explorer: emptyStage(skillPolicy),
        mentor: emptyStage(null),
        contributor: emptyStage(null),
      },
      unresolvedQuestions: [],
      userFocus: [],
      budget: {
        maxEvidenceBatches: 2,
        usedEvidenceBatches: 0,
        maxFilesRead: 18,
        filesRead: 0,
        maxEvidenceBytes: 88_000,
        evidenceBytes: 0,
      },
      evidenceIndex: [],
      observations: [],
    },
    existingEvidencePaths: [],
  };
}

describe("ClaudeAgentRuntime", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetAgentCapabilityMemory();
  });

  it("pins the installed SDK contract version", () => {
    const installed = JSON.parse(fs.readFileSync(
      path.join(process.cwd(), "node_modules", "@anthropic-ai", "claude-agent-sdk", "package.json"),
      "utf8",
    ));
    expect(installed.version).toBe("0.3.224");
    expect(CLAUDE_AGENT_SDK_CONTRACT_VERSION).toBe(installed.version);
  });

  it("scopes unsupported Agent capability memory to the model settings fingerprint", () => {
    rememberUnsupportedAgentCapability(settings);

    expect(isAgentCapabilityKnownUnsupported(settings)).toBe(true);
    expect(isAgentCapabilityKnownUnsupported({ ...settings, model: "another-model" }))
      .toBe(false);
  });

  it("runs discovery through only the bounded in-process MCP and cleans its isolated cwd", async () => {
    const observation: HarnessToolObservation = {
      stage: "explorer",
      tool: "find_related_tests",
      purpose: "代表性测试",
      summary: "找到 1 个测试候选。",
      paths: ["tests/core.test.ts"],
      metadata: { tests: 1 },
    };
    const executeDiscoveryTool = vi.fn(async () => observation);
    let capturedOptions: any;
    let mcpPayload: unknown;
    let structuredOutputDecision: any;
    let rawReadDecision: any;
    sdkMocks.query.mockImplementation(({ options }: any) => {
      capturedOptions = options;
      return (async function* () {
        const server = options.mcpServers.repomentor;
        const toolDefinition = server.tools.find((item: any) => item.name === "find_related_tests");
        mcpPayload = await toolDefinition.handler({
          paths: ["src/core.ts"],
          purpose: "代表性测试",
        });
        const pre = options.hooks.PreToolUse[0].hooks[0];
        structuredOutputDecision = await pre({
          hook_event_name: "PreToolUse",
          tool_name: "StructuredOutput",
          tool_input: evidencePlan,
        });
        rawReadDecision = await pre({
          hook_event_name: "PreToolUse",
          tool_name: "Read",
          tool_input: { file_path: "src/core.ts" },
        });
        yield {
          type: "result",
          subtype: "success",
          is_error: false,
          num_turns: 3,
          total_cost_usd: 0.012,
          session_id: "session-1",
          usage: { input_tokens: 100, output_tokens: 30 },
          structured_output: evidencePlan,
        };
      })();
    });

    const runtime = new ClaudeAgentRuntime(settings);
    const result = await runtime.planEvidence(
      request(),
      { executeDiscoveryTool } as any,
      new AbortController(),
    );

    expect(executeDiscoveryTool).toHaveBeenCalledWith("explorer", {
      tool: "find_related_tests",
      paths: ["src/core.ts"],
      purpose: "代表性测试",
    });
    expect(JSON.stringify(mcpPayload)).not.toContain("source:");
    expect(result).toMatchObject({
      evidencePlan: { actions: [] },
      toolCalls: { find_related_tests: 1 },
      turns: 3,
      costUsd: 0.012,
      sessionId: "session-1",
    });
    expect(capturedOptions).toMatchObject({
      tools: [],
      allowedTools: ["mcp__repomentor__find_related_tests", "StructuredOutput"],
      settingSources: [],
      persistSession: false,
      permissionMode: "dontAsk",
      maxTurns: 6,
      skills: ["repomentor:analyze-generic"],
      plugins: [{ type: "local", skipMcpDiscovery: true }],
    });
    expect(capturedOptions.disallowedTools).toEqual(expect.arrayContaining([
      "Read", "Glob", "Grep", "Bash", "Write", "Edit", "WebFetch", "Agent",
    ]));
    expect(structuredOutputDecision.hookSpecificOutput.permissionDecision).toBe("allow");
    expect(rawReadDecision.hookSpecificOutput.permissionDecision).toBe("deny");
    expect(capturedOptions.cwd).not.toBe(process.cwd());
    expect(capturedOptions.cwd).not.toContain("src/core.ts");
    expect(fs.existsSync(capturedOptions.cwd)).toBe(false);
    expect(sdkMocks.query.mock.calls[0]?.[0].prompt).not.toContain("TOP SECRET README");
  });

  it("defines exactly two non-recursive Mentor subagents and rejects a third delegation", async () => {
    const mentorOutput = validMentorOutput();
    let thirdDecision: any;
    let directEvidenceDecision: any;
    let childEvidenceDecision: any;
    let structuredOutputDecision: any;
    let childStructuredOutputDecision: any;
    let capturedOptions: any;
    sdkMocks.query.mockImplementation(({ options }: any) => {
      capturedOptions = options;
      return (async function* () {
        const pre = options.hooks.PreToolUse[0].hooks[0];
        const post = options.hooks.PostToolUse[0].hooks[0];
        for (const agent of ["architecture-analyst", "learning-path-reviewer"]) {
          await pre({
            hook_event_name: "PreToolUse",
            tool_name: "Agent",
            tool_input: { subagent_type: agent },
          });
          await post({
            hook_event_name: "PostToolUse",
            tool_name: "Agent",
            tool_input: { subagent_type: agent },
          });
        }
        thirdDecision = await pre({
          hook_event_name: "PreToolUse",
          tool_name: "Agent",
          tool_input: { subagent_type: "architecture-analyst" },
        });
        directEvidenceDecision = await pre({
          hook_event_name: "PreToolUse",
          tool_name: "mcp__repomentor_architecture__get_architecture_evidence",
          tool_input: {},
        });
        childEvidenceDecision = await pre({
          hook_event_name: "PreToolUse",
          tool_name: "mcp__repomentor_architecture__get_architecture_evidence",
          tool_input: {},
          agent_id: "child-1",
          agent_type: "architecture-analyst",
        });
        structuredOutputDecision = await pre({
          hook_event_name: "PreToolUse",
          tool_name: "StructuredOutput",
          tool_input: mentorOutput,
        });
        childStructuredOutputDecision = await pre({
          hook_event_name: "PreToolUse",
          tool_name: "StructuredOutput",
          tool_input: {},
          agent_id: "child-1",
          agent_type: "architecture-analyst",
        });
        const evidenceTool = options.mcpServers.repomentor_architecture.tools[0];
        const view = await evidenceTool.handler({});
        expect(JSON.stringify(view)).toContain("src/core.ts");
        expect(JSON.stringify(view)).not.toContain("unread.ts");
        yield {
          type: "result",
          subtype: "success",
          is_error: false,
          num_turns: 5,
          structured_output: mentorOutput,
        };
      })();
    });
    const evidence: EvidenceBundle = {
      files: [{
        path: "src/core.ts",
        content: "export class Core {}",
        truncated: false,
        purpose: "core",
        phase: "explorer",
      }],
      skippedPaths: ["unread.ts"],
      omissions: [{ path: "unread.ts", reason: "not_tracked" }],
      totalBytes: 20,
    };

    const runtime = new ClaudeAgentRuntime(settings);
    const result = await runtime.synthesizeMentorWithSubagents(
      { explorerOutput: {}, evidenceBundle: evidence },
      evidence,
      ["repomentor:analyze-generic"],
      new AbortController(),
    );

    expect(result.output).toEqual(mentorOutput);
    expect(capturedOptions.tools).toEqual(["Agent"]);
    expect(capturedOptions.allowedTools).toEqual(["Agent", "StructuredOutput"]);
    expect(Object.keys(capturedOptions.agents)).toEqual([
      "architecture-analyst",
      "learning-path-reviewer",
    ]);
    expect(capturedOptions.agents["architecture-analyst"].tools).toEqual([
      "mcp__repomentor_architecture__get_architecture_evidence",
    ]);
    expect(capturedOptions.agents["learning-path-reviewer"].tools).toEqual([
      "mcp__repomentor_learning__get_learning_evidence",
    ]);
    expect(capturedOptions.agents["architecture-analyst"].disallowedTools).toContain("Agent");
    expect(thirdDecision.hookSpecificOutput.permissionDecision).toBe("deny");
    expect(directEvidenceDecision.hookSpecificOutput.permissionDecision).toBe("deny");
    expect(childEvidenceDecision.hookSpecificOutput.permissionDecision).toBe("allow");
    expect(structuredOutputDecision.hookSpecificOutput.permissionDecision).toBe("allow");
    expect(childStructuredOutputDecision.hookSpecificOutput.permissionDecision).toBe("deny");
  });

  it("marks max-turns as a capability fallback without retrying Workflow itself", async () => {
    sdkMocks.query.mockReturnValueOnce((async function* () {
      yield {
        type: "result",
        subtype: "error_max_turns",
        is_error: true,
        num_turns: 6,
        errors: [],
      };
    })());
    const runtime = new ClaudeAgentRuntime(settings);

    await expect(runtime.planEvidence(
      request(),
      { executeDiscoveryTool: vi.fn() } as any,
      new AbortController(),
    )).rejects.toMatchObject<Partial<AgentRuntimeError>>({
      fallbackEligible: true,
      retryable: false,
      rememberUnsupported: false,
    });
  });
});

function emptyStage(policy: typeof skillPolicy | null) {
  return {
    status: "pending" as const,
    plan: null,
    examinedPaths: [],
    skippedPaths: [],
    unresolvedQuestions: [],
    skillPolicy: policy,
    discoveryActionsUsed: 0,
    stopReason: null,
  };
}

function validMentorOutput(): MentorOutput {
  return {
    architectureOverview: "核心模块由入口调用。",
    dependencyGraph: { "src/": [] },
    readingPath: [{ step: 1, file: "src/core.ts", why: "理解核心实现" }],
    keyPatterns: [{ pattern: "Core", where: "src/core.ts", description: "核心对象" }],
    codeConventions: [{ rule: "导出类", example: "src/core.ts" }],
    evidenceClaims: [{
      claim: "核心类位于 src/core.ts",
      confidence: "high",
      evidence: [{ path: "src/core.ts", supports: "定义 Core 类" }],
    }],
    evidenceCoverage: { examinedFiles: ["src/core.ts"], gaps: [] },
  };
}
