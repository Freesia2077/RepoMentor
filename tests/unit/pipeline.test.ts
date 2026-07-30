import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  runStage: vi.fn(),
  cacheFind: vi.fn(),
  cacheSave: vi.fn(),
  experienceFind: vi.fn(),
  experienceSave: vi.fn(),
}));

vi.mock("../../src/services/claude-client.js", () => ({
  runStage: mocks.runStage,
  ParseError: class ParseError extends Error {},
  LLMError: class LLMError extends Error {
    retryable = false;
    timedOut = false;
  },
}));

vi.mock("../../src/lib/repo.js", () => ({
  parseRepoUrl: () => ({ owner: "owner", repo: "repo", isGitHub: true }),
  fetchRepoSize: vi.fn(async () => 10),
  cloneRepo: vi.fn(async () => ({
    localPath: "C:\\tmp\\repo",
    commitHash: "abc123",
    cached: false,
  })),
  getFileCount: vi.fn(async () => 10),
  extractCommitSummary: vi.fn(async () => ({
    frequentFiles: [],
    recentThemes: [],
    contributorCount: 1,
  })),
}));

vi.mock("../../src/db/index.js", () => ({
  getDb: () => ({}),
}));

vi.mock("../../src/db/repositories/analysis-cache.js", () => ({
  findByCommit: mocks.cacheFind,
  save: mocks.cacheSave,
}));

vi.mock("../../src/db/repositories/experiences.js", () => ({
  findRelevant: mocks.experienceFind,
  save: mocks.experienceSave,
}));

import { executePipeline, resolveQuestion, type PipelineContext } from "../../src/services/pipeline.js";

describe("analysis pipeline", () => {
  it("feeds interaction answers and stored experiences into later agents", async () => {
    mocks.cacheFind.mockReturnValue(undefined);
    mocks.experienceFind.mockReturnValue([{ content: "historical architecture lesson" }]);
    mocks.runStage.mockImplementation(async (stage: string) => {
      if (stage === "explorer") {
        return {
          projectType: { primary: "library", secondary: [] },
          techStack: { language: "typescript", framework: null, buildTool: "npm" },
          fileCount: 10,
          entryPoints: [],
          moduleMap: [],
          directorySummary: "test",
          projectSummary: "test",
        };
      }
      if (stage === "mentor") {
        return {
          architectureOverview: "architecture",
          dependencyGraph: { "src/core": [] },
          readingPath: [],
          keyPatterns: [],
          codeConventions: [],
        };
      }
      return {
        goodFirstIssues: [],
        contributionSetup: { devEnv: null, build: null, test: null },
        entryFiles: [],
        notesForNewcomers: [],
      };
    });

    const ctx: PipelineContext = {
      taskId: "task-pipeline",
      repoUrl: "https://github.com/owner/repo.git",
      branch: "main",
      stageProgress: { explorer: "pending", mentor: "pending", contributor: "pending" },
      abortController: new AbortController(),
      callbacks: {
        onStageStart: vi.fn(),
        onStageDone: vi.fn(),
        onStatusChange: vi.fn(),
        onCommitHash: vi.fn(),
      },
      pendingQuestion: null,
    };

    const pipelinePromise = executePipeline(ctx);

    await vi.waitFor(() => expect(ctx.pendingQuestion?.questionId).toBe("q_explorer_review"));
    expect(resolveQuestion(ctx, "q_explorer_review", "实际是 CLI 工具")).toBe(true);

    await vi.waitFor(() => expect(ctx.pendingQuestion?.questionId).toBe("q_deps"));
    expect(resolveQuestion(ctx, "q_deps", "src/core")).toBe(true);

    await pipelinePromise;

    const mentorCall = mocks.runStage.mock.calls.find(([stage]) => stage === "mentor");
    expect(mentorCall?.[1]).toMatchObject({
      experiences: "historical architecture lesson",
      userFocus: "实际是 CLI 工具",
    });

    const contributorCall = mocks.runStage.mock.calls.find(([stage]) => stage === "contributor");
    expect(contributorCall?.[1]).toMatchObject({ userFocus: "src/core" });
    expect(mocks.experienceSave).toHaveBeenCalledOnce();
  });
});
