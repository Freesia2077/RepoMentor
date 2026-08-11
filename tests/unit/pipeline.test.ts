import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => {
  class MockParseError extends Error {}
  class MockLLMError extends Error {
    retryable = false;
    timedOut = false;
    timeoutKind: "first_response" | "stage" | null = null;
  }
  class MockAgentRuntimeError extends Error {
    constructor(
      message: string,
      public fallbackEligible: boolean,
      public retryable: boolean,
      public cancelled = false,
      public rememberUnsupported = false,
    ) {
      super(message);
    }
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
    MockAgentRuntimeError,
    agentPlan: vi.fn(),
    mentorSynthesis: vi.fn(),
    agentKnownUnsupported: vi.fn(() => false),
    rememberUnsupported: vi.fn(),
  };
});

vi.mock("../../src/services/claude-client.js", () => ({
  runStage: mocks.runStage,
  orchestrateRepositoryEvidence: mocks.planEvidence,
  ParseError: mocks.MockParseError,
  LLMError: mocks.MockLLMError,
}));

vi.mock("../../src/services/claude-agent-runtime.js", () => ({
  AgentRuntimeError: mocks.MockAgentRuntimeError,
  ClaudeAgentRuntime: class {
    planEvidence(...args: unknown[]) {
      return mocks.agentPlan(...args);
    }
    synthesizeMentorWithSubagents(...args: unknown[]) {
      return mocks.mentorSynthesis(...args);
    }
  },
  isAgentCapabilityKnownUnsupported: mocks.agentKnownUnsupported,
  rememberUnsupportedAgentCapability: mocks.rememberUnsupported,
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
  manifests: [{ path: "package.json", content: "{}", truncated: false }],
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
  buildContributionEvidence: vi.fn((bundle: any, referencedPaths: string[] = []) => ({
    examinedFiles: bundle.files.map((file: any) => ({
      path: file.path,
      purpose: file.purpose,
      phase: file.phase,
      truncated: file.truncated,
    })),
    focusedFiles: bundle.files.filter((file: any) =>
      referencedPaths.includes(file.path) || file.phase === "mentor"
    ),
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

import {
  executePipeline,
  validateAnalysisRuntimeSettings,
  type PipelineContext,
} from "../../src/services/pipeline.js";
import { sseManager } from "../../src/lib/sse.js";
import { buildRepositoryProfile } from "../../src/lib/repository-profile.js";

describe("analysis pipeline", () => {
  beforeEach(() => {
    mocks.runStage.mockReset();
    mocks.planEvidence.mockReset();
    mocks.buildEvidenceBundle.mockReset();
    mocks.cacheFind.mockReset();
    mocks.cacheSave.mockReset();
    mocks.experienceFind.mockReset();
    mocks.experienceSave.mockReset();
    mocks.agentPlan.mockReset();
    mocks.mentorSynthesis.mockReset();
    mocks.agentKnownUnsupported.mockReset();
    mocks.agentKnownUnsupported.mockReturnValue(false);
    mocks.rememberUnsupported.mockReset();
    vi.mocked(buildRepositoryProfile).mockResolvedValue(repositoryProfile);
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
      plan: any,
      phase: "explorer" | "mentor",
    ) => ({
      files: phase === "explorer"
        ? plan.files.map((request: any) => ({
            path: request.path,
            content: `source:${request.path}`,
            truncated: false,
            purpose: request.purpose,
            phase,
          }))
        : [{
            path: "src/core.ts",
            content: "export class Core {}",
            truncated: false,
            purpose: "core",
            phase,
          }],
      skippedPaths: [],
      omissions: [],
      totalBytes: 10,
    }));
    mocks.agentPlan.mockImplementation(async (request: any) => ({
      evidencePlan: {
        goal: `${request.phase} Agent plan`,
        rationale: "bounded discovery",
        questions: [],
        actions: [],
        files: [{ path: "src/core.ts", purpose: "core", priority: "high" }],
        stopConditions: ["core selected"],
      },
      toolCalls: { find_related_tests: 1 },
      turns: 2,
      degraded: false,
    }));
  });

  it("rejects forced agentic mode for an OpenAI-compatible provider", () => {
    expect(validateAnalysisRuntimeSettings({
      ...pipelineModelSettings,
      provider: "openai-compatible",
      baseUrl: "https://provider.example/v1",
    }, "agentic")).toMatchObject({
      category: "invalid_model_settings",
      retryable: false,
    });
  });

  it("uses the complete-coverage path for small repositories and reuses evidence", async () => {
    mocks.cacheFind.mockReturnValue(undefined);
    mocks.experienceFind.mockReturnValue([{
      content: "[bounded-agent-v8]\nhistorical architecture lesson",
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

    await executePipeline(ctx);

    const mentorCall = mocks.runStage.mock.calls.find(([stage]) => stage === "mentor");
    expect(mentorCall?.[1]).toMatchObject({
      experiences: "historical architecture lesson",
      repositoryOverview,
      evidenceBundle: expect.objectContaining({
        files: expect.arrayContaining([
          expect.objectContaining({ path: "src/index.ts" }),
          expect.objectContaining({ path: "src/core.ts" }),
          expect.objectContaining({ path: "tests/core.test.ts" }),
        ]),
      }),
      harnessState: expect.objectContaining({
        userFocus: [],
        budget: expect.objectContaining({ usedEvidenceBatches: 1, filesRead: 3 }),
        stages: expect.objectContaining({
          mentor: expect.objectContaining({ stopReason: "existing_evidence_reused" }),
        }),
      }),
    });

    const contributorCall = mocks.runStage.mock.calls.find(([stage]) => stage === "contributor");
    expect(contributorCall?.[1]).toMatchObject({
      mentorOutput: {
        evidenceClaims: [expect.objectContaining({
          evidence: [expect.objectContaining({ path: "README.md" })],
        })],
      },
      repositoryContext,
      contributionEvidence: expect.objectContaining({
        focusedFiles: [expect.objectContaining({ path: "src/index.ts" })],
      }),
      harnessState: expect.objectContaining({
        userFocus: [],
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
        files: expect.arrayContaining([
          expect.objectContaining({ path: "src/index.ts" }),
          expect.objectContaining({ path: "src/core.ts" }),
          expect.objectContaining({ path: "tests/core.test.ts" }),
        ]),
      }),
      harnessState: expect.objectContaining({
        budget: expect.objectContaining({ usedEvidenceBatches: 1, filesRead: 3 }),
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
        trace: expect.objectContaining({ title: "已启用小仓库直接覆盖路径" }),
      }),
    ]));
    expect(mocks.planEvidence).not.toHaveBeenCalled();
    expect(mocks.buildEvidenceBundle).toHaveBeenCalledOnce();
    expect(mentorCall?.[5]).toBe(pipelineModelSettings);
    expect(mocks.cacheFind).toHaveBeenCalledWith(
      {},
      "owner",
      "repo",
      "main",
      expect.stringMatching(/^bounded-agent-v8:workflow:[a-f0-9]{12}:abc123$/),
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

  it("routes a large Anthropic-compatible repository through bounded Agent planning", async () => {
    vi.mocked(buildRepositoryProfile).mockResolvedValue({
      ...repositoryProfile,
      fileCount: 100,
    });
    mocks.cacheFind.mockReturnValue(undefined);
    mocks.experienceFind.mockReturnValue([]);
    mockSuccessfulStageOutputs(100);
    const ctx = createContext("task-bounded-agent", pipelineModelSettings);

    await executePipeline(ctx);

    expect(mocks.agentPlan).toHaveBeenCalledTimes(2);
    expect(mocks.planEvidence).not.toHaveBeenCalled();
    expect(mocks.cacheSave.mock.calls[0]?.[1].commitHash).toMatch(
      /^bounded-agent-v8:bounded-agent:[a-f0-9]{12}:abc123$/,
    );
    const completionTitles = sseManager.getEventsAfter("task-bounded-agent")
      .map(({ event }) => event)
      .filter((event) => event.type === "harness:trace")
      .map((event) => event.trace.title);
    expect(completionTitles).toEqual(expect.arrayContaining([
      "受限 Explorer Research Agent 已完成",
      "受限 Mentor Research Agent 已完成",
    ]));
  });

  it("falls back once on an Agent capability error and remembers that provider fingerprint", async () => {
    vi.mocked(buildRepositoryProfile).mockResolvedValue({
      ...repositoryProfile,
      fileCount: 100,
    });
    mocks.cacheFind.mockReturnValue(undefined);
    mocks.experienceFind.mockReturnValue([]);
    mockSuccessfulStageOutputs(100);
    mocks.agentPlan.mockRejectedValueOnce(new mocks.MockAgentRuntimeError(
      "MCP protocol unsupported",
      true,
      false,
      false,
      true,
    ));
    const ctx = createContext("task-agent-fallback", pipelineModelSettings);

    await executePipeline(ctx);

    expect(mocks.agentPlan).toHaveBeenCalledOnce();
    expect(mocks.planEvidence).toHaveBeenCalledTimes(2);
    expect(mocks.rememberUnsupported).toHaveBeenCalledOnce();
    expect(mocks.cacheSave.mock.calls[0]?.[1].commitHash).toMatch(
      /^bounded-agent-v8:workflow:[a-f0-9]{12}:abc123$/,
    );
  });

  it("retries a temporary Agent error without running Workflow in parallel", async () => {
    vi.mocked(buildRepositoryProfile).mockResolvedValue({
      ...repositoryProfile,
      fileCount: 100,
    });
    mocks.cacheFind.mockReturnValue(undefined);
    mocks.agentPlan.mockRejectedValue(new mocks.MockAgentRuntimeError(
      "429 rate limit",
      false,
      true,
    ));
    const ctx = createContext("task-agent-rate-limit", pipelineModelSettings);

    await expect(executePipeline(ctx)).rejects.toMatchObject({
      category: "llm_failed",
      retryable: true,
    });
    expect(mocks.agentPlan).toHaveBeenCalledTimes(2);
    expect(mocks.planEvidence).not.toHaveBeenCalled();
    expect(mocks.runStage).not.toHaveBeenCalled();
  });

  it("propagates Agent cancellation without retrying or falling back", async () => {
    vi.mocked(buildRepositoryProfile).mockResolvedValue({
      ...repositoryProfile,
      fileCount: 100,
    });
    mocks.cacheFind.mockReturnValue(undefined);
    mocks.agentPlan.mockRejectedValueOnce(new mocks.MockAgentRuntimeError(
      "task cancelled",
      false,
      false,
      true,
    ));
    const ctx = createContext("task-agent-cancelled", pipelineModelSettings);

    await expect(executePipeline(ctx)).rejects.toMatchObject({
      category: "timeout",
      retryable: false,
    });
    expect(mocks.agentPlan).toHaveBeenCalledOnce();
    expect(mocks.planEvidence).not.toHaveBeenCalled();
    expect(mocks.runStage).not.toHaveBeenCalled();
  });

  it("keeps a large OpenAI-compatible repository on the tool-free Workflow", async () => {
    vi.mocked(buildRepositoryProfile).mockResolvedValue({
      ...repositoryProfile,
      fileCount: 100,
    });
    mocks.cacheFind.mockReturnValue(undefined);
    mocks.experienceFind.mockReturnValue([]);
    mockSuccessfulStageOutputs(100);
    const ctx = createContext("task-openai-workflow", {
      ...pipelineModelSettings,
      provider: "openai-compatible",
      baseUrl: "https://provider.example/v1",
    });

    await executePipeline(ctx);

    expect(mocks.agentPlan).not.toHaveBeenCalled();
    expect(mocks.planEvidence).toHaveBeenCalledTimes(2);
    expect(mocks.cacheSave.mock.calls[0]?.[1].commitHash).toMatch(
      /^bounded-agent-v8:workflow:[a-f0-9]{12}:abc123$/,
    );
  });
});

function createContext(
  taskId: string,
  modelSettings: PipelineContext["modelSettings"],
): PipelineContext {
  return {
    taskId,
    repoUrl: "https://github.com/owner/repo.git",
    branch: "main",
    stageProgress: { explorer: "pending", mentor: "pending", contributor: "pending" },
    abortController: new AbortController(),
    modelSettings,
    callbacks: {
      onStageStart: vi.fn(),
      onStageDone: vi.fn(),
      onStatusChange: vi.fn(),
      onCommitHash: vi.fn(),
    },
    pendingQuestion: null,
  };
}

function mockSuccessfulStageOutputs(fileCount: number): void {
  mocks.runStage.mockImplementation(async (stage: string) => {
    if (stage === "explorer") {
      return {
        projectType: { primary: "library", secondary: [] },
        techStack: { language: "typescript", framework: null, buildTool: "npm" },
        fileCount,
        entryPoints: [{ file: "src/core.ts", role: "core" }],
        moduleMap: [],
        directorySummary: "test",
        projectSummary: "test",
        evidenceClaims: [{
          claim: "Core is selected",
          confidence: "high",
          evidence: [{ path: "src/core.ts", supports: "core evidence" }],
        }],
        evidenceCoverage: { examinedFiles: ["src/core.ts"], gaps: [] },
      };
    }
    if (stage === "mentor") {
      return {
        architectureOverview: "architecture",
        dependencyGraph: {},
        readingPath: [],
        keyPatterns: [],
        codeConventions: [],
        evidenceClaims: [],
        evidenceCoverage: { examinedFiles: ["src/core.ts"], gaps: [] },
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
}
