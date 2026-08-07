import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => {
  class MockParseError extends Error {}
  class MockLLMError extends Error {
    retryable = false;
    timedOut = false;
    timeoutKind: "first_response" | "stage" | null = null;
  }

  return {
    runStage: vi.fn(),
    planEvidence: vi.fn(),
    buildEvidenceBundle: vi.fn(),
    cacheFind: vi.fn(),
    cacheSave: vi.fn(),
    experienceFind: vi.fn(),
    experienceSave: vi.fn(),
    MockParseError,
    MockLLMError,
  };
});

vi.mock("../../src/services/claude-client.js", () => ({
  runStage: mocks.runStage,
  orchestrateRepositoryEvidence: mocks.planEvidence,
  ParseError: mocks.MockParseError,
  LLMError: mocks.MockLLMError,
}));

vi.mock("../../src/lib/repo.js", () => ({
  parseRepoUrl: () => ({ owner: "owner", repo: "repo", isGitHub: true }),
  resolveRepositoryCachePath: () => "C:\\tmp\\repo",
  preflightGithubRepo: vi.fn(async () => ({ status: "available", sizeKb: 10 })),
  cloneRepo: vi.fn(async () => ({
    localPath: "C:\\tmp\\repo",
    commitHash: "abc123",
    cached: false,
  })),
  extractCommitSummary: vi.fn(async () => ({
    frequentFiles: [],
    recentThemes: [],
    contributorCount: 1,
  })),
}));

const repositoryProfile = {
  fileCount: 10,
  fileIndex: ["README.md", "package.json", "src/index.ts", "src/core.ts", "tests/core.test.ts"],
  fileIndexTruncated: false,
  topLevelTree: ["package.json", "src/", "src/index.ts"],
  treeTruncated: false,
  directoryStats: [],
  readme: { path: "README.md", content: "# Test project", truncated: false },
  manifests: [],
  exampleManifests: [],
  configFiles: [],
  guidanceFiles: [],
  todoMarkers: [],
  languageStats: { TypeScript: 8 },
  entryCandidates: ["src/index.ts"],
  testCandidates: ["tests/core.test.ts"],
};

const repositoryOverview = {
  fileCount: 10,
  topLevelTree: repositoryProfile.topLevelTree,
  treeTruncated: false,
  directoryStats: [],
  languageStats: { TypeScript: 8 },
  entryCandidates: ["src/index.ts"],
  testCandidates: ["tests/core.test.ts"],
  projectFiles: {
    readme: "README.md",
    manifests: [],
    exampleManifests: [],
    configFiles: [],
    guidanceFiles: [],
  },
};

const repositoryContext = {
  fileCount: 10,
  manifests: [],
  exampleManifests: [],
  configFiles: [],
  guidanceFiles: [],
  todoMarkers: [],
  languageStats: { TypeScript: 8 },
  entryCandidates: ["src/index.ts"],
  testCandidates: ["tests/core.test.ts"],
};

const pipelineModelSettings = {
  provider: "anthropic-compatible",
  apiKey: "task-key",
  baseUrl: "https://provider.example/anthropic",
  model: "task-model",
  source: "environment",
} as const;

vi.mock("../../src/lib/repository-profile.js", () => ({
  buildRepositoryProfile: vi.fn(async () => repositoryProfile),
  buildRepositoryOverview: vi.fn(() => repositoryOverview),
  buildRepositoryContributionContext: vi.fn(() => repositoryContext),
  buildContributionEvidence: vi.fn((bundle: any) => ({
    examinedFiles: bundle.files.map((file: any) => ({
      path: file.path,
      purpose: file.purpose,
      phase: file.phase,
      truncated: file.truncated,
    })),
    focusedFiles: bundle.files.filter((file: any) => file.phase === "mentor"),
    totalBytes: 10,
  })),
  buildEvidenceBundle: mocks.buildEvidenceBundle,
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
import { sseManager } from "../../src/lib/sse.js";

describe("analysis pipeline", () => {
  beforeEach(() => {
    mocks.runStage.mockReset();
    mocks.planEvidence.mockReset();
    mocks.buildEvidenceBundle.mockReset();
    mocks.cacheFind.mockReset();
    mocks.cacheSave.mockReset();
    mocks.experienceFind.mockReset();
    mocks.experienceSave.mockReset();
    mocks.planEvidence.mockImplementation(async (phase: string) => ({
      goal: `${phase} coverage`,
      rationale: `${phase} plan`,
      questions: [`What should ${phase} verify?`],
      actions: [],
      files: [],
      stopConditions: ["Required evidence is available"],
    }));
    mocks.buildEvidenceBundle.mockImplementation(async (
      _localPath: string,
      _plan: unknown,
      phase: "explorer" | "mentor",
    ) => ({
      files: phase === "explorer"
        ? [{
            path: "src/index.ts",
            content: "export {}",
            truncated: false,
            purpose: "entry",
            phase,
          }]
        : [{
            path: "src/core.ts",
            content: "export class Core {}",
            truncated: false,
            purpose: "core",
            phase,
          }],
      skippedPaths: [],
      totalBytes: 10,
    }));
  });

  it("feeds interaction answers and stored experiences into later agents", async () => {
    mocks.cacheFind.mockReturnValue(undefined);
    mocks.experienceFind.mockReturnValue([{
      content: "[harness-skills-v6]\nhistorical architecture lesson",
    }]);
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
          evidenceClaims: [{
            claim: "The public entry is src/index.ts",
            confidence: "high",
            evidence: [{ path: "src/index.ts", supports: "exports the API" }],
          }, {
            claim: "The README defines the project purpose",
            confidence: "high",
            evidence: [{ path: "README.md", supports: "describes the project" }],
          }],
          evidenceCoverage: { examinedFiles: ["src/index.ts"], gaps: [] },
        };
      }
      if (stage === "mentor") {
        return {
          architectureOverview: "architecture",
          dependencyGraph: { "src/core": [] },
          readingPath: [],
          keyPatterns: [],
          codeConventions: [],
          evidenceClaims: [{
            claim: "The README defines the project purpose",
            confidence: "high",
            evidence: [{ path: "README.md", supports: "describes the project" }],
          }],
          evidenceCoverage: { examinedFiles: ["README.md"], gaps: [] },
        };
      }
      return {
        goodFirstIssues: [],
        contributionSetup: { devEnv: null, build: null, test: null },
        entryFiles: [],
        notesForNewcomers: [],
        evidenceClaims: [],
        evidenceCoverage: { examinedFiles: [], gaps: [] },
      };
    });

    const ctx: PipelineContext = {
      taskId: "task-pipeline",
      repoUrl: "https://github.com/owner/repo.git",
      branch: "main",
      stageProgress: { explorer: "pending", mentor: "pending", contributor: "pending" },
      abortController: new AbortController(),
      modelSettings: pipelineModelSettings,
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
      repositoryOverview,
      evidenceBundle: expect.objectContaining({
        files: expect.arrayContaining([
          expect.objectContaining({ path: "src/index.ts" }),
          expect.objectContaining({ path: "src/core.ts" }),
        ]),
      }),
      harnessState: expect.objectContaining({
        userFocus: ["实际是 CLI 工具"],
        budget: expect.objectContaining({ usedEvidenceBatches: 2, filesRead: 2 }),
      }),
    });

    const contributorCall = mocks.runStage.mock.calls.find(([stage]) => stage === "contributor");
    expect(contributorCall?.[1]).toMatchObject({
      userFocus: "src/core",
      mentorOutput: {
        evidenceClaims: [expect.objectContaining({
          evidence: [expect.objectContaining({ path: "README.md" })],
        })],
      },
      repositoryContext,
      contributionEvidence: expect.objectContaining({
        focusedFiles: [expect.objectContaining({ path: "src/core.ts" })],
      }),
      harnessState: expect.objectContaining({
        userFocus: ["实际是 CLI 工具", "src/core"],
        stages: expect.objectContaining({
          explorer: expect.objectContaining({ status: "completed" }),
          mentor: expect.objectContaining({ status: "completed" }),
        }),
      }),
    });
    const explorerCall = mocks.runStage.mock.calls.find(([stage]) => stage === "explorer");
    expect(explorerCall?.[1]).toMatchObject({
      fileCount: 10,
      repositoryProfile,
      evidenceBundle: expect.objectContaining({
        files: [expect.objectContaining({ path: "src/index.ts" })],
      }),
      harnessState: expect.objectContaining({
        budget: expect.objectContaining({ usedEvidenceBatches: 1, filesRead: 1 }),
        stages: expect.objectContaining({
          explorer: expect.objectContaining({ status: "evidence_ready" }),
        }),
      }),
    });
    expect(sseManager.getEventsAfter("task-pipeline").some(({ event }) =>
      event.type === "stage:progress"
      && event.message.includes("仓库画像构建完成")
    )).toBe(true);
    const harnessEvents = sseManager.getEventsAfter("task-pipeline")
      .map(({ event }) => event)
      .filter((event) => event.type === "harness:trace");
    expect(harnessEvents).toEqual(expect.arrayContaining([
      expect.objectContaining({
        trace: expect.objectContaining({ tool: "get_repository_map" }),
      }),
      expect.objectContaining({
        trace: expect.objectContaining({ tool: "inspect_git_history" }),
      }),
      expect.objectContaining({
        trace: expect.objectContaining({
          kind: "decision",
          title: "用户指引已应用",
        }),
      }),
    ]));
    expect(mocks.planEvidence).toHaveBeenCalledTimes(2);
    expect(mocks.buildEvidenceBundle).toHaveBeenCalledTimes(2);
    expect(mocks.planEvidence.mock.calls[0]?.[0]).toBe("explorer");
    expect(mocks.planEvidence.mock.calls[0]?.[5]).toBe(pipelineModelSettings);
    expect(mentorCall?.[5]).toBe(pipelineModelSettings);
    expect(mocks.planEvidence.mock.calls[0]?.[1]).toMatchObject({
      repositoryProfile,
      harnessState: expect.objectContaining({
        budget: expect.objectContaining({ usedEvidenceBatches: 0 }),
      }),
    });
    expect(mocks.planEvidence.mock.calls[1]?.[0]).toBe("mentor");
    expect(mocks.planEvidence.mock.calls[1]?.[1]).toMatchObject({
      repositoryProfile,
      existingEvidencePaths: ["src/index.ts"],
      userFocus: "实际是 CLI 工具",
    });
    expect(mocks.cacheFind).toHaveBeenCalledWith(
      {},
      "owner",
      "repo",
      "main",
      expect.stringMatching(/^harness-skills-v6:[a-f0-9]{12}:abc123$/),
    );
    expect(mocks.experienceSave).toHaveBeenCalledOnce();
  });

  it("does not rerun the full stage after JSON repair is exhausted", async () => {
    mocks.cacheFind.mockReturnValue(undefined);
    mocks.experienceFind.mockReturnValue([]);
    mocks.runStage.mockRejectedValue(new mocks.MockParseError("invalid importance"));

    const ctx: PipelineContext = {
      taskId: "task-parse-failure",
      repoUrl: "https://github.com/owner/repo.git",
      branch: "main",
      stageProgress: { explorer: "pending", mentor: "pending", contributor: "pending" },
      abortController: new AbortController(),
      modelSettings: pipelineModelSettings,
      callbacks: {
        onStageStart: vi.fn(),
        onStageDone: vi.fn(),
        onStatusChange: vi.fn(),
        onCommitHash: vi.fn(),
      },
      pendingQuestion: null,
    };

    await expect(executePipeline(ctx)).rejects.toMatchObject({
      category: "parse_failed",
      message: "invalid importance",
    });
    expect(mocks.runStage).toHaveBeenCalledOnce();
  });

  it("does not rerun a non-retryable max-turn failure", async () => {
    mocks.cacheFind.mockReturnValue(undefined);
    mocks.experienceFind.mockReturnValue([]);
    mocks.runStage.mockRejectedValue(
      new mocks.MockLLMError("Explorer 达到最大分析轮次，未生成最终 JSON"),
    );

    const ctx: PipelineContext = {
      taskId: "task-max-turns",
      repoUrl: "https://github.com/owner/repo.git",
      branch: "main",
      stageProgress: { explorer: "pending", mentor: "pending", contributor: "pending" },
      abortController: new AbortController(),
      modelSettings: pipelineModelSettings,
      callbacks: {
        onStageStart: vi.fn(),
        onStageDone: vi.fn(),
        onStatusChange: vi.fn(),
        onCommitHash: vi.fn(),
      },
      pendingQuestion: null,
    };

    await expect(executePipeline(ctx)).rejects.toMatchObject({
      category: "llm_failed",
      message: "Explorer 达到最大分析轮次，未生成最终 JSON",
    });
    expect(mocks.runStage).toHaveBeenCalledOnce();
  });

  it("retries once when a stage stalls before its first response", async () => {
    mocks.cacheFind.mockReturnValue(undefined);
    mocks.experienceFind.mockReturnValue([]);
    const timeout = new mocks.MockLLMError("Explorer 首次响应超时");
    timeout.retryable = true;
    timeout.timedOut = true;
    timeout.timeoutKind = "first_response";
    mocks.runStage
      .mockRejectedValueOnce(timeout)
      .mockRejectedValueOnce(new mocks.MockParseError("second attempt invalid"));

    const ctx: PipelineContext = {
      taskId: "task-first-response-timeout",
      repoUrl: "https://github.com/owner/repo.git",
      branch: "main",
      stageProgress: { explorer: "pending", mentor: "pending", contributor: "pending" },
      abortController: new AbortController(),
      modelSettings: pipelineModelSettings,
      callbacks: {
        onStageStart: vi.fn(),
        onStageDone: vi.fn(),
        onStatusChange: vi.fn(),
        onCommitHash: vi.fn(),
      },
      pendingQuestion: null,
    };

    await expect(executePipeline(ctx)).rejects.toMatchObject({
      category: "parse_failed",
      message: "second attempt invalid",
    });
    expect(mocks.runStage).toHaveBeenCalledTimes(2);
    expect(sseManager.getEventsAfter("task-first-response-timeout").some(({ event }) =>
      event.type === "stage:progress"
      && event.message.includes("首次响应超时，正在重试")
    )).toBe(true);
  });
});
