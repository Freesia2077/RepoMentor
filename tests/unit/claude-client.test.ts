import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  query: vi.fn(),
}));

vi.mock("@anthropic-ai/claude-agent-sdk", () => ({
  query: mocks.query,
}));

import { runStage } from "../../src/services/claude-client.js";

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

  it("normalizes supporting locally and aggregates tool progress", async () => {
    const output = {
      ...baseExplorerOutput,
      moduleMap: [{
        ...baseExplorerOutput.moduleMap[0],
        importance: "supporting",
      }],
    };
    mocks.query.mockReturnValueOnce(messageStream([
      assistant([
        { type: "tool_use", name: "Glob", input: { pattern: "*" } },
        { type: "tool_use", name: "Glob", input: { pattern: "*.toml" } },
        { type: "tool_use", name: "Read", input: { file_path: "README.md" } },
      ]),
      { type: "user", tool_use_result: { ok: true } },
      { type: "user", tool_use_result: { ok: true } },
      { type: "user", tool_use_result: { ok: true } },
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
    expect(progress.mock.calls.map(([message]) => message)).toEqual([
      "正在启动 explorer 阶段...",
      "Explorer 正在探索仓库（工具请求 3 次：Glob × 2，Read × 1）...",
      "Explorer 正在思考与分析...",
      "Explorer 分析完成（工具请求 3 次：Glob × 2，Read × 1；模型轮次 1）",
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

  it("instructs Explorer to prefer a complete snapshot with an adaptive safety budget", async () => {
    const repositorySnapshot = {
      fileCount: 100,
      topLevelTree: ["README.md", "pyproject.toml", "src/", "src/example/"],
      treeTruncated: false,
      readme: { path: "README.md", content: "Example SDK", truncated: false },
      manifests: [{
        path: "pyproject.toml",
        content: "[project]\nname='example'",
        truncated: false,
      }],
      languageStats: { Python: 80 },
      entryCandidates: ["src/example/__init__.py"],
    };
    mocks.query.mockReturnValueOnce(messageStream([
      resultMessage(JSON.stringify(baseExplorerOutput)),
    ]));

    await runStage(
      "explorer",
      { fileCount: 100, repositorySnapshot },
      { onProgress: vi.fn(), onField: vi.fn() },
      process.cwd(),
    );

    expect(mocks.query.mock.calls[0]?.[0]).toMatchObject({
      prompt: expect.stringContaining('"repositorySnapshot"'),
      options: {
        systemPrompt: expect.stringContaining("工具安全上限为 8 次"),
      },
    });
  });

  it("injects Mentor strategy once while passing the shared snapshot in prompt input", async () => {
    const repositorySnapshot = {
      fileCount: 10,
      topLevelTree: ["src/"],
      treeTruncated: false,
      readme: null,
      manifests: [],
      exampleManifests: [],
      guidanceFiles: [],
      todoMarkers: [],
      languageStats: { TypeScript: 8 },
      entryCandidates: ["src/index.ts"],
    };
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
        repositorySnapshot,
        skillContent: "UNIQUE_SKILL_CONTENT",
        experiences: "UNIQUE_EXPERIENCE_CONTENT",
      },
      { onProgress: vi.fn(), onField: vi.fn() },
      process.cwd(),
    );

    const call = mocks.query.mock.calls[0]?.[0];
    expect(call.options.systemPrompt).toContain("UNIQUE_SKILL_CONTENT");
    expect(call.options.systemPrompt).toContain("UNIQUE_EXPERIENCE_CONTENT");
    expect(call.prompt).toContain('"repositorySnapshot"');
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
        num_turns: 24,
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
      message: "Explorer 达到最大分析轮次（24），未生成最终 JSON",
      retryable: false,
    });

    expect(progress.mock.calls.flat()).not.toContainEqual(
      expect.stringContaining("分析完成"),
    );
  });

  it("classifies a Contributor stall before the first response as retryable", async () => {
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

    await vi.advanceTimersByTimeAsync(45_000);
    await rejection;
  });
});
