import { beforeEach, describe, expect, it, vi } from "vitest";

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

function resultMessage() {
  return { type: "result" };
}

describe("Claude stage execution", () => {
  beforeEach(() => {
    mocks.query.mockReset();
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
      "Explorer 正在探索仓库（工具调用 3 次：Glob × 2，Read × 1）...",
      "Explorer 正在思考与分析...",
      "Explorer 分析完成（工具调用 3 次：Glob × 2，Read × 1）",
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
});
