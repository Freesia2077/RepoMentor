import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  query: vi.fn(),
}));

vi.mock("@anthropic-ai/claude-agent-sdk", () => ({
  query: mocks.query,
}));

import {
  orchestrateRepositoryEvidence,
  runStage,
} from "../../src/services/claude-client.js";

const baseExplorerOutput = {
  projectType: { primary: "library", secondary: [] },
  techStack: { language: "python", framework: null, buildTool: null },
  fileCount: 100,
  entryPoints: [{ file: "src/index.py", role: "主入口" }],
  moduleMap: [{
    path: "src/",
    responsibility: "核心源码",
    importance: "core",
    justification: "包含主要实现",
  }],
  directorySummary: "Python SDK 项目",
  projectSummary: "一个 Python SDK",
};

function messageStream(messages: Array<Record<string, unknown>>) {
  return (async function* () {
    for (const message of messages) yield message;
  })();
}

function assistant(content: Array<Record<string, unknown>>) {
  return { type: "assistant", message: { content } };
}

function resultMessage(result = "") {
  return {
    type: "result",
    subtype: "success",
    is_error: false,
    num_turns: 1,
    result,
  };
}

describe("Claude stage execution", () => {
  beforeEach(() => {
    mocks.query.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("normalizes supporting locally during tool-free synthesis", async () => {
    const output = {
      ...baseExplorerOutput,
      moduleMap: [{
        ...baseExplorerOutput.moduleMap[0],
        importance: "supporting",
      }],
    };
    mocks.query.mockReturnValueOnce(messageStream([
      assistant([{ type: "text", text: `\`\`\`json\n${JSON.stringify(output)}\n\`\`\`` }]),
      resultMessage(),
    ]));
    const progress = vi.fn();

    const result = await runStage(
      "explorer",
      { localPath: "D:\\repo", fileCount: 100 },
      { onProgress: progress, onField: vi.fn() },
      process.cwd(),
    );

    expect(result.moduleMap[0]?.importance).toBe("support");
    expect(mocks.query).toHaveBeenCalledOnce();
    expect(mocks.query.mock.calls[0]?.[0]).toMatchObject({
      options: {
        tools: [],
        allowedTools: [],
        maxTurns: 4,
      },
    });
    expect(progress.mock.calls.map(([message]) => message)).toEqual([
      "正在启动 explorer 阶段...",
      "Explorer 正在思考与分析...",
      "Explorer 分析完成（模型轮次 1）",
    ]);
  });

  it("uses one short tool-free repair pass for an invalid schema value", async () => {
    const invalidOutput = {
      ...baseExplorerOutput,
      moduleMap: [{
        ...baseExplorerOutput.moduleMap[0],
        importance: "auxiliary",
      }],
    };
    const repairedOutput = {
      ...invalidOutput,
      moduleMap: [{
        ...invalidOutput.moduleMap[0],
        importance: "utility",
      }],
    };
    mocks.query
      .mockReturnValueOnce(messageStream([
        assistant([{ type: "text", text: JSON.stringify(invalidOutput) }]),
        resultMessage(),
      ]))
      .mockReturnValueOnce(messageStream([
        assistant([{
          type: "text",
          text: `\`\`\`json\n${JSON.stringify(repairedOutput)}\n\`\`\``,
        }]),
        resultMessage(),
      ]));
    const progress = vi.fn();

    const result = await runStage(
      "explorer",
      { localPath: "D:\\repo", fileCount: 100 },
      { onProgress: progress, onField: vi.fn() },
      process.cwd(),
    );

    expect(result.moduleMap[0]?.importance).toBe("utility");
    expect(mocks.query).toHaveBeenCalledTimes(2);
    expect(mocks.query.mock.calls[1]?.[0]).toMatchObject({
      options: {
        tools: [],
        allowedTools: [],
        maxTurns: 2,
      },
    });
    expect(progress).toHaveBeenCalledWith(
      "Explorer 输出校验失败，正在自动修复 JSON（无需重新扫描仓库）...",
    );
  });

  it("uses the successful SDK result as the authoritative final output", async () => {
    mocks.query.mockReturnValueOnce(messageStream([
      assistant([{ type: "tool_use", name: "Read", input: { file_path: "README.md" } }]),
      { type: "user", tool_use_result: { ok: true } },
      resultMessage(JSON.stringify(baseExplorerOutput)),
    ]));

    const result = await runStage(
      "explorer",
      { localPath: "D:\\repo", fileCount: 100 },
      { onProgress: vi.fn(), onField: vi.fn() },
      process.cwd(),
    );

    expect(result.projectSummary).toBe("一个 Python SDK");
  });

  it("treats a successful result as terminal instead of waiting for stream cleanup", async () => {
    mocks.query.mockReturnValueOnce((async function* () {
      yield resultMessage(JSON.stringify(baseExplorerOutput));
      throw new Error("post-result cleanup failure");
    })());

    const result = await runStage(
      "explorer",
      {},
      { onProgress: vi.fn(), onField: vi.fn() },
      process.cwd(),
    );

    expect(result.projectSummary).toBe("一个 Python SDK");
    expect(mocks.query).toHaveBeenCalledOnce();
  });

  it("does not log completion when a successful SDK result contains no output", async () => {
    mocks.query.mockReturnValueOnce(messageStream([resultMessage()]));
    const progress = vi.fn();

    await expect(runStage(
      "explorer",
      {},
      { onProgress: progress, onField: vi.fn() },
      process.cwd(),
    )).rejects.toMatchObject({
      message: "Explorer 返回了空输出",
      retryable: true,
    });

    expect(progress.mock.calls.flat()).not.toContainEqual(
      expect.stringContaining("分析完成"),
    );
  });

  it("passes a rich repository profile and planned evidence to Explorer", async () => {
    const repositoryProfile = {
      fileCount: 100,
      fileIndex: ["README.md", "pyproject.toml", "src/example/__init__.py"],
      fileIndexTruncated: false,
      topLevelTree: ["README.md", "pyproject.toml", "src/", "src/example/"],
      treeTruncated: false,
      directoryStats: [],
      readme: { path: "README.md", content: "Example SDK", truncated: false },
      manifests: [{
        path: "pyproject.toml",
        content: "[project]\nname='example'",
        truncated: false,
      }],
      languageStats: { Python: 80 },
      entryCandidates: ["src/example/__init__.py"],
      testCandidates: [],
    };
    const evidenceBundle = {
      files: [{
        path: "src/example/__init__.py",
        content: "class Client: pass",
        truncated: false,
        purpose: "verify entry",
        phase: "explorer",
      }],
      skippedPaths: [],
      totalBytes: 18,
    };
    mocks.query.mockReturnValueOnce(messageStream([
      resultMessage(JSON.stringify(baseExplorerOutput)),
    ]));

    await runStage(
      "explorer",
      { fileCount: 100, repositoryProfile, evidenceBundle },
      { onProgress: vi.fn(), onField: vi.fn() },
      process.cwd(),
    );

    expect(mocks.query.mock.calls[0]?.[0]).toMatchObject({
      prompt: expect.stringContaining('"repositoryProfile"'),
      options: {
        systemPrompt: expect.stringContaining("evidenceBundle"),
        tools: [],
        allowedTools: [],
      },
    });
    expect(mocks.query.mock.calls[0]?.[0].prompt).not.toContain("repositorySnapshot");
  });

  it("injects Mentor strategy once while passing a compact overview and evidence", async () => {
    const repositoryOverview = {
      fileCount: 10,
      topLevelTree: ["src/"],
      treeTruncated: false,
      directoryStats: [],
      languageStats: { TypeScript: 8 },
      entryCandidates: ["src/index.ts"],
      testCandidates: [],
      projectFiles: {
        readme: "README.md",
        manifests: ["package.json"],
        exampleManifests: [],
        configFiles: ["tsconfig.json"],
        guidanceFiles: [],
      },
    };
    const evidenceBundle = { files: [], skippedPaths: [], totalBytes: 0 };
    mocks.query.mockReturnValueOnce(messageStream([
      resultMessage(JSON.stringify({
        architectureOverview: "architecture",
        dependencyGraph: { "src/": [] },
        readingPath: [],
        keyPatterns: [],
        codeConventions: [],
      })),
    ]));

    await runStage(
      "mentor",
      {
        explorerOutput: baseExplorerOutput,
        repositoryOverview,
        evidenceBundle,
        skillContent: "UNIQUE_SKILL_CONTENT",
        experiences: "UNIQUE_EXPERIENCE_CONTENT",
      },
      { onProgress: vi.fn(), onField: vi.fn() },
      process.cwd(),
    );

    const call = mocks.query.mock.calls[0]?.[0];
    expect(call.options.systemPrompt).toContain("UNIQUE_SKILL_CONTENT");
    expect(call.options.systemPrompt).toContain("UNIQUE_EXPERIENCE_CONTENT");
    expect(call.prompt).toContain('"repositoryOverview"');
    expect(call.prompt).not.toContain('"fileIndex"');
    expect(call.prompt).toContain('"evidenceBundle"');
    expect(call.prompt).not.toContain("UNIQUE_SKILL_CONTENT");
    expect(call.prompt).not.toContain("UNIQUE_EXPERIENCE_CONTENT");
  });

  it("reports max-turn exhaustion accurately without logging completion", async () => {
    mocks.query.mockReturnValueOnce(messageStream([
      assistant([{ type: "tool_use", name: "Glob", input: { pattern: "*" } }]),
      {
        type: "result",
        subtype: "error_max_turns",
        is_error: true,
        num_turns: 4,
        errors: [],
      },
    ]));
    const progress = vi.fn();

    await expect(runStage(
      "explorer",
      { localPath: "D:\\repo", fileCount: 100 },
      { onProgress: progress, onField: vi.fn() },
      process.cwd(),
    )).rejects.toMatchObject({
      message: "Explorer 达到最大分析轮次（4），未生成最终 JSON",
      retryable: false,
    });

    expect(progress.mock.calls.flat()).not.toContainEqual(
      expect.stringContaining("分析完成"),
    );
  });

  it("allows Contributor 90 seconds before classifying a first-response stall", async () => {
    vi.useFakeTimers();
    mocks.query.mockImplementationOnce(({ options }: any) => (async function* () {
      await new Promise<void>((_, reject) => {
        options.abortController.signal.addEventListener("abort", () => {
          reject(options.abortController.signal.reason ?? new Error("aborted"));
        }, { once: true });
      });
      yield {};
    })());

    const stagePromise = runStage(
      "contributor",
      {},
      { onProgress: vi.fn(), onField: vi.fn() },
      process.cwd(),
    );
    const rejection = expect(stagePromise).rejects.toMatchObject({
      message: "Contributor 首次响应超时",
      retryable: true,
      timedOut: true,
      timeoutKind: "first_response",
    });

    await vi.advanceTimersByTimeAsync(90_000);
    await rejection;
  });

  it("allows Mentor 90 seconds for its first response", async () => {
    vi.useFakeTimers();
    mocks.query.mockImplementationOnce(({ options }: any) => (async function* () {
      await new Promise<void>((_, reject) => {
        options.abortController.signal.addEventListener("abort", () => {
          reject(options.abortController.signal.reason ?? new Error("aborted"));
        }, { once: true });
      });
      yield {};
    })());

    const stagePromise = runStage(
      "mentor",
      { skillContent: "", experiences: "" },
      { onProgress: vi.fn(), onField: vi.fn() },
      process.cwd(),
    );
    const rejection = expect(stagePromise).rejects.toMatchObject({
      message: "Mentor 首次响应超时",
      retryable: true,
      timedOut: true,
      timeoutKind: "first_response",
    });

    await vi.advanceTimersByTimeAsync(89_999);
    await vi.advanceTimersByTimeAsync(1);
    await rejection;
  });

  it("creates a tool-free evidence plan from the repository file index", async () => {
    const plan = {
      rationale: "Read the public entry and its test",
      files: [
        {
          path: "src/index.ts",
          purpose: "verify public exports",
          priority: "high",
        },
      ],
    };
    mocks.query.mockReturnValueOnce(messageStream([
      resultMessage(JSON.stringify(plan)),
    ]));

    const progress = vi.fn();
    const result = await orchestrateRepositoryEvidence(
      "explorer",
      {
        repositoryProfile: {
          fileIndex: ["src/index.ts", "tests/index.test.ts"],
        },
      },
      { onProgress: progress, onField: vi.fn() },
      process.cwd(),
    );

    expect(result).toEqual(plan);
    expect(mocks.query.mock.calls[0]?.[0]).toMatchObject({
      prompt: expect.stringContaining('"phase":"explorer"'),
      options: {
        tools: [],
        allowedTools: [],
        maxTurns: 4,
      },
    });
    expect(progress).toHaveBeenCalledWith("Orchestrator 分析完成（模型轮次 1）");
  });
});
