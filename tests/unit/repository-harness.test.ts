import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  buildRepositoryProfile: vi.fn(),
  buildRepositoryOverview: vi.fn(),
  buildEvidenceBundle: vi.fn(),
  buildRepositoryContributionContext: vi.fn(),
  buildContributionEvidence: vi.fn(),
  extractCommitSummary: vi.fn(),
}));

vi.mock("../../src/lib/repository-profile.js", () => mocks);
vi.mock("../../src/lib/repo.js", () => ({
  extractCommitSummary: mocks.extractCommitSummary,
}));

import {
  HarnessInvariantError,
  REPOSITORY_HARNESS_TOOLS,
  RepositoryHarness,
} from "../../src/services/repository-harness.js";
import type { HarnessSkillPolicy } from "../../src/types/index.js";

const profile = {
  fileCount: 4,
  fileIndex: ["README.md", "package.json", "src/index.ts", "src/core.ts", "tests/core.test.ts"],
  fileIndexTruncated: false,
  topLevelTree: ["README.md", "package.json", "src/"],
  treeTruncated: false,
  directoryStats: [],
  readme: { path: "README.md", content: "# App", truncated: false },
  manifests: [{ path: "package.json", content: "{}", truncated: false }],
  exampleManifests: [],
  configFiles: [],
  guidanceFiles: [],
  todoMarkers: [],
  languageStats: { TypeScript: 2 },
  entryCandidates: ["src/index.ts"],
  testCandidates: [],
};

describe("RepositoryHarness", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.buildRepositoryProfile.mockResolvedValue(profile);
    mocks.buildRepositoryOverview.mockReturnValue({ fileCount: 4 });
    mocks.extractCommitSummary.mockResolvedValue({
      frequentFiles: [{ file: "src/core.ts", commits: 3, recent: true }],
      recentThemes: ["refactor core"],
      contributorCount: 2,
    });
    mocks.buildEvidenceBundle.mockResolvedValue({ files: [], skippedPaths: [], omissions: [], totalBytes: 0 });
  });

  it("publishes a provider-neutral repository map trace", async () => {
    const traces: any[] = [];
    const harness = new RepositoryHarness("C:\\repo", (entry) => traces.push(entry));

    const result = await harness.getRepositoryMap();

    expect(result.profile).toBe(profile);
    expect(traces).toContainEqual(expect.objectContaining({
      stage: "repository",
      kind: "tool",
      tool: "get_repository_map",
      files: ["README.md", "package.json"],
      metadata: expect.objectContaining({ files: 4, readme: true }),
    }));
  });

  it("records the evidence plan and bounded batch result without source contents", async () => {
    mocks.buildEvidenceBundle.mockResolvedValue({
      files: [{
        path: "src/core.ts",
        content: "secret source",
        truncated: false,
        purpose: "core flow",
        phase: "explorer",
      }],
      skippedPaths: ["missing.ts"],
      omissions: [{ path: "missing.ts", reason: "not_tracked" }],
      totalBytes: 13,
    });
    const traces: any[] = [];
    const harness = new RepositoryHarness("C:\\repo", (entry) => traces.push(entry));
    const plan = {
      goal: "Trace core flow",
      rationale: "Trace the core control flow",
      questions: ["Where is the core flow implemented?"],
      actions: [],
      files: [
        { path: "src/core.ts", purpose: "core flow", priority: "high" as const },
        { path: "missing.ts", purpose: "missing dependency", priority: "medium" as const },
      ],
      stopConditions: ["Core implementation is covered"],
    };

    await harness.getRepositoryMap();
    harness.recordEvidencePlan("explorer", plan);
    await harness.readEvidenceBatch("explorer", plan, ["src/index.ts"]);

    expect(mocks.buildEvidenceBundle).toHaveBeenCalledWith(
      "C:\\repo",
      plan,
      "explorer",
      ["src/index.ts"],
    );
    expect(traces).toHaveLength(3);
    expect(JSON.stringify(traces)).not.toContain("secret source");
    expect(traces[2]).toMatchObject({
      kind: "evidence",
      tool: "read_evidence_batch",
      files: ["src/core.ts"],
      metadata: { filesRead: 1, skippedFiles: 1, bytes: 13 },
    });
    expect(harness.getContextView()).toMatchObject({
      budget: { usedEvidenceBatches: 1, filesRead: 1, evidenceBytes: 13 },
      unresolvedQuestions: ["missing.ts: missing dependency"],
      stages: {
        explorer: {
          status: "evidence_ready",
          examinedPaths: ["src/core.ts"],
          skippedPaths: ["missing.ts"],
        },
      },
    });
    const verified = harness.verifyEvidenceOutput("explorer", {
      evidenceClaims: [],
      evidenceCoverage: { examinedFiles: [], gaps: [] },
    });
    expect(verified.evidenceCoverage.gaps).toEqual([
      expect.objectContaining({
        kind: "missing_evidence",
        subject: "missing.ts",
      }),
    ]);
    expect(() => harness.recordEvidencePlan("explorer", plan)).toThrow(HarnessInvariantError);
  });

  it("selects complete relevant coverage for a small repository and reuses it for Mentor", async () => {
    mocks.buildEvidenceBundle.mockImplementation(async (_path, plan, phase) => ({
      files: plan.files.map((request: any) => ({
        path: request.path,
        content: `source:${request.path}`,
        truncated: false,
        purpose: request.purpose,
        phase,
      })),
      skippedPaths: [],
      omissions: [],
      totalBytes: 60,
    }));
    const traces: any[] = [];
    const harness = new RepositoryHarness("C:\\repo", (entry) => traces.push(entry));
    await harness.getRepositoryMap();
    const policy: HarnessSkillPolicy = {
      skillNames: ["small-repository"],
      allowedTools: ["search_symbols", "trace_module_dependencies", "find_related_tests"],
      preferredTools: [],
      recommendedQuestions: ["核心流程是什么？"],
      evidenceRequirements: ["读取源码和测试"],
      stopConditions: ["所有相关文件均已读取"],
      maxDiscoveryActions: 3,
      maxEvidenceFiles: 8,
    };
    harness.activateSkillPolicy("explorer", policy);

    const plan = harness.createCompleteCoveragePlan("explorer", policy);
    expect(plan?.files.map((file) => file.path)).toEqual([
      "src/index.ts",
      "src/core.ts",
      "tests/core.test.ts",
    ]);
    await harness.executeEvidencePlan("explorer", plan!);
    expect(harness.hasCompleteEvidenceCoverage("explorer")).toBe(true);
    harness.completeStage("explorer");
    harness.activateSkillPolicy("mentor", policy);
    const reused = harness.reuseExistingEvidenceForStage("mentor");

    expect(reused.files).toHaveLength(3);
    expect(mocks.buildEvidenceBundle).toHaveBeenCalledOnce();
    expect(harness.getContextView()).toMatchObject({
      budget: { usedEvidenceBatches: 1, filesRead: 3 },
      stages: {
        mentor: {
          status: "evidence_ready",
          stopReason: "existing_evidence_reused",
          examinedPaths: ["src/index.ts", "src/core.ts", "tests/core.test.ts"],
        },
      },
    });
    expect(traces).toEqual(expect.arrayContaining([
      expect.objectContaining({ title: "已启用小仓库直接覆盖路径" }),
      expect.objectContaining({ title: "已复用完整仓库证据" }),
    ]));
  });

  it("does not claim complete coverage when a selected file is truncated", async () => {
    mocks.buildEvidenceBundle.mockImplementation(async (_path, plan, phase) => ({
      files: plan.files.map((request: any, index: number) => ({
        path: request.path,
        content: `source:${request.path}`,
        truncated: index === 0,
        purpose: request.purpose,
        phase,
      })),
      skippedPaths: [],
      omissions: [],
      totalBytes: 60,
    }));
    const harness = new RepositoryHarness("C:\\repo");
    await harness.getRepositoryMap();
    const policy: HarnessSkillPolicy = {
      skillNames: ["small-repository"],
      allowedTools: [],
      preferredTools: [],
      recommendedQuestions: [],
      evidenceRequirements: [],
      stopConditions: ["全部相关文件均有完整证据"],
      maxDiscoveryActions: 0,
      maxEvidenceFiles: 8,
    };
    harness.activateSkillPolicy("explorer", policy);
    const plan = harness.createCompleteCoveragePlan("explorer", policy)!;

    await harness.executeEvidencePlan("explorer", plan);
    harness.completeStage("explorer");

    expect(harness.hasCompleteEvidenceCoverage("explorer")).toBe(false);
    expect(() => harness.reuseExistingEvidenceForStage("mentor")).toThrow(
      "只有 Mentor 可以复用已完整读取的小仓库证据集",
    );
  });

  it("falls back to Orchestrator planning when an unclassified file could be relevant", async () => {
    mocks.buildRepositoryProfile.mockResolvedValueOnce({
      ...profile,
      fileIndex: [...profile.fileIndex, "schema/custom.avsc"],
      fileCount: 6,
    });
    const harness = new RepositoryHarness("C:\\repo");
    await harness.getRepositoryMap();
    const policy: HarnessSkillPolicy = {
      skillNames: ["small-repository"],
      allowedTools: [],
      preferredTools: [],
      recommendedQuestions: [],
      evidenceRequirements: [],
      stopConditions: ["相关文件已覆盖"],
      maxDiscoveryActions: 0,
      maxEvidenceFiles: 8,
    };
    harness.activateSkillPolicy("explorer", policy);

    expect(harness.createCompleteCoveragePlan("explorer", policy)).toBeNull();
  });

  it("treats already-read files as reused evidence instead of coverage gaps", async () => {
    mocks.buildEvidenceBundle.mockResolvedValue({
      files: [],
      skippedPaths: ["src/index.ts"],
      omissions: [{ path: "src/index.ts", reason: "already_available" }],
      totalBytes: 0,
    });
    const harness = new RepositoryHarness("C:\\repo");
    await harness.getRepositoryMap();
    const plan = {
      goal: "复用入口",
      rationale: "入口已读取",
      questions: [],
      actions: [],
      files: [{ path: "src/index.ts", purpose: "公共入口", priority: "high" as const }],
      stopConditions: ["入口证据可用"],
    };
    harness.recordEvidencePlan("explorer", plan);

    await harness.readEvidenceBatch("explorer", plan, ["src/index.ts"]);

    expect(harness.getContextView()).toMatchObject({
      unresolvedQuestions: [],
      stages: {
        explorer: {
          examinedPaths: ["src/index.ts"],
          skippedPaths: [],
          stopReason: "existing_evidence_reused",
        },
      },
    });
  });

  it("resolves an earlier omission when a later stage reads the file", async () => {
    mocks.buildEvidenceBundle
      .mockResolvedValueOnce({
        files: [],
        skippedPaths: ["src/core.ts"],
        omissions: [{ path: "src/core.ts", reason: "budget_exhausted" }],
        totalBytes: 0,
      })
      .mockResolvedValueOnce({
        files: [{
          path: "src/core.ts",
          content: "export class Core {}",
          truncated: false,
          purpose: "核心实现",
          phase: "mentor",
        }],
        skippedPaths: [],
        omissions: [],
        totalBytes: 20,
      });
    const harness = new RepositoryHarness("C:\\repo");
    const explorerPlan = {
      goal: "定位核心实现",
      rationale: "读取核心模块",
      questions: [],
      actions: [],
      files: [{ path: "src/core.ts", purpose: "核心实现", priority: "high" as const }],
      stopConditions: ["核心实现已检查"],
    };
    const mentorPlan = { ...explorerPlan, goal: "补齐核心实现证据" };

    await harness.getRepositoryMap();
    await harness.executeEvidencePlan("explorer", explorerPlan);
    harness.completeStage("explorer");
    await harness.executeEvidencePlan("mentor", mentorPlan);

    expect(harness.getEvidenceBundle()).toMatchObject({
      skippedPaths: [],
      omissions: [],
      files: [expect.objectContaining({ path: "src/core.ts", truncated: false })],
    });
    expect(harness.getContextView().unresolvedQuestions).toEqual([]);

    const verified = harness.verifyEvidenceOutput("mentor", {
      evidenceClaims: [],
      evidenceCoverage: {
        examinedFiles: [],
        gaps: [{
          kind: "missing_evidence" as const,
          subject: "src/core.ts",
          summary: "核心实现尚未读取",
          severity: "medium" as const,
        }],
      },
    });
    expect(verified.evidenceCoverage.gaps).toEqual([]);
  });

  it("keeps a runtime coverage gap when a later stage only reads a partial file", async () => {
    mocks.buildEvidenceBundle
      .mockResolvedValueOnce({
        files: [],
        skippedPaths: ["src/core.ts"],
        omissions: [{ path: "src/core.ts", reason: "budget_exhausted" }],
        totalBytes: 0,
      })
      .mockResolvedValueOnce({
        files: [{
          path: "src/core.ts",
          content: "partial core excerpt",
          truncated: true,
          purpose: "核心实现",
          phase: "mentor",
        }],
        skippedPaths: [],
        omissions: [],
        totalBytes: 20,
      });
    const harness = new RepositoryHarness("C:\\repo");
    const plan = {
      goal: "读取核心实现",
      rationale: "补齐核心模块证据",
      questions: [],
      actions: [],
      files: [{ path: "src/core.ts", purpose: "核心实现", priority: "high" as const }],
      stopConditions: ["核心实现已检查"],
    };

    await harness.getRepositoryMap();
    await harness.executeEvidencePlan("explorer", plan);
    harness.completeStage("explorer");
    await harness.executeEvidencePlan("mentor", plan);

    const verified = harness.verifyEvidenceOutput("mentor", {
      evidenceClaims: [],
      evidenceCoverage: {
        examinedFiles: [],
        gaps: Array.from({ length: 8 }, (_, index) => ({
          kind: "test_absent" as const,
          subject: `optional-${index}.ts`,
          summary: `可选模块 ${index} 没有直接测试`,
          severity: "medium" as const,
        })),
      },
    });
    expect(verified.evidenceCoverage.gaps).toHaveLength(8);
    expect(verified.evidenceCoverage.gaps[0]).toMatchObject({
      kind: "coverage_limit",
      subject: "src/core.ts",
      severity: "medium",
    });
  });

  it("keeps richer evidence when a later read returns a poorer excerpt", async () => {
    const richContent = "r".repeat(12_000);
    mocks.buildEvidenceBundle
      .mockResolvedValueOnce({
        files: [{
          path: "src/core.ts",
          content: richContent,
          truncated: true,
          purpose: "核心实现",
          phase: "explorer",
        }],
        skippedPaths: [],
        omissions: [],
        totalBytes: richContent.length,
      })
      .mockResolvedValueOnce({
        files: [{
          path: "src/core.ts",
          content: "short excerpt",
          truncated: true,
          purpose: "再次检查",
          phase: "mentor",
        }],
        skippedPaths: [],
        omissions: [],
        totalBytes: 13,
      });
    const harness = new RepositoryHarness("C:\\repo");
    const plan = {
      goal: "读取核心实现",
      rationale: "建立源码证据",
      questions: [],
      actions: [],
      files: [{ path: "src/core.ts", purpose: "核心实现", priority: "high" as const }],
      stopConditions: ["核心实现已读取"],
    };

    await harness.getRepositoryMap();
    await harness.executeEvidencePlan("explorer", plan);
    harness.completeStage("explorer");
    await harness.executeEvidencePlan("mentor", plan);

    expect(harness.getEvidenceBundle().files).toEqual([
      expect.objectContaining({
        path: "src/core.ts",
        content: richContent,
        phase: "explorer",
      }),
    ]);
  });

  it("enforces one evidence batch per stage and stage completion order", async () => {
    mocks.buildEvidenceBundle.mockResolvedValue({ files: [], skippedPaths: [], omissions: [], totalBytes: 0 });
    const harness = new RepositoryHarness("C:\\repo");
    const plan = {
      goal: "entry",
      rationale: "entry",
      questions: [],
      actions: [],
      files: [],
      stopConditions: ["entry checked"],
    };

    await harness.getRepositoryMap();
    harness.recordEvidencePlan("explorer", plan);
    await harness.readEvidenceBatch("explorer", plan);
    harness.completeStage("explorer");

    await expect(harness.readEvidenceBatch("explorer", plan)).rejects.toThrow(
      HarnessInvariantError,
    );
    expect(() => harness.completeStage("contributor")).toThrow(HarnessInvariantError);
  });

  it("removes unsupported claim references and exposes exact coverage", async () => {
    mocks.buildEvidenceBundle.mockResolvedValue({
      files: [{
        path: "src/core.ts",
        content: "export class Core {}",
        truncated: false,
        purpose: "core implementation",
        phase: "explorer",
      }],
      skippedPaths: [],
      omissions: [],
      totalBytes: 20,
    });
    const harness = new RepositoryHarness("C:\\repo");
    const plan = {
      goal: "verify core",
      rationale: "read the core implementation",
      questions: ["Where is Core implemented?"],
      actions: [],
      files: [{ path: "src/core.ts", purpose: "core", priority: "high" as const }],
      stopConditions: ["Core has source evidence"],
    };
    await harness.getRepositoryMap();
    await harness.executeEvidencePlan("explorer", plan);

    const verified = harness.verifyEvidenceOutput("explorer", {
      evidenceClaims: [{
        claim: "Core is implemented in two files",
        confidence: "high" as const,
        evidence: [
          { path: "src/core.ts", supports: "defines Core" },
          { path: "invented.ts", supports: "does not exist" },
        ],
      }],
      evidenceCoverage: { examinedFiles: ["invented.ts"], gaps: [] },
    });

    expect(verified.evidenceClaims[0]).toMatchObject({
      confidence: "medium",
      evidence: [{ path: "src/core.ts" }],
    });
    expect(verified.evidenceCoverage.examinedFiles).toEqual(["src/core.ts"]);
    expect(verified.evidenceCoverage.gaps).toEqual([]);
  });

  it("preserves semantic gaps, filters explicit exclusions, and deduplicates by subject", async () => {
    mocks.buildEvidenceBundle.mockResolvedValue({
      files: [{
        path: "src/core.ts",
        content: "export class Core {}",
        truncated: false,
        purpose: "core implementation",
        phase: "explorer",
      }],
      skippedPaths: [],
      omissions: [],
      totalBytes: 20,
    });
    const harness = new RepositoryHarness("C:\\repo");
    const plan = {
      goal: "verify core",
      rationale: "read core",
      questions: [],
      actions: [],
      files: [{ path: "src/core.ts", purpose: "core", priority: "high" as const }],
      stopConditions: ["core covered"],
    };
    await harness.getRepositoryMap();
    await harness.executeEvidencePlan("explorer", plan);

    const verified = harness.verifyEvidenceOutput("explorer", {
      evidenceClaims: [],
      evidenceCoverage: {
        examinedFiles: [],
        gaps: [
          {
            kind: "out_of_scope" as const,
            subject: "LICENSE",
            summary: "LICENSE 未读取",
            severity: "medium" as const,
          },
          {
            kind: "missing_evidence" as const,
            subject: "可选插件运行时",
            summary: "规划阶段未选择可选插件，因此其运行时行为尚未确认",
            severity: "medium" as const,
          },
          {
            kind: "test_absent" as const,
            subject: "src/core.ts",
            summary: "核心模块没有直接测试",
            severity: "medium" as const,
          },
          {
            kind: "test_absent" as const,
            subject: "src/core.ts",
            summary: "仍需补充核心模块测试",
            severity: "high" as const,
          },
        ],
      },
    });

    expect(verified.evidenceCoverage.gaps).toEqual([
      expect.objectContaining({
        kind: "missing_evidence",
        subject: "可选插件运行时",
      }),
      expect.objectContaining({
        kind: "test_absent",
        subject: "src/core.ts",
        severity: "high",
      }),
    ]);
  });

  it("declares a small deterministic domain-tool surface", () => {
    expect(REPOSITORY_HARNESS_TOOLS.map((tool) => tool.name)).toEqual([
      "get_repository_map",
      "search_symbols",
      "trace_module_dependencies",
      "find_related_tests",
      "read_evidence_batch",
      "inspect_git_history",
      "prepare_contributor_context",
    ]);
    expect(REPOSITORY_HARNESS_TOOLS.every((tool) => tool.deterministic)).toBe(true);
  });

  it("executes Orchestrator discovery actions and feeds their paths into evidence reading", async () => {
    const harness = new RepositoryHarness("C:\\repo");
    const plan = {
      goal: "Connect implementation to tests",
      rationale: "Use repository structure to locate verification",
      questions: ["How is the core module tested?"],
      actions: [{
        tool: "find_related_tests" as const,
        paths: ["src/core.ts"],
        purpose: "locate core tests",
      }],
      files: [{
        path: "src/core.ts",
        purpose: "read core implementation",
        priority: "high" as const,
      }],
      stopConditions: ["Implementation and representative test are selected"],
    };

    await harness.getRepositoryMap();
    await harness.executeEvidencePlan("explorer", plan);

    const executedPlan = mocks.buildEvidenceBundle.mock.calls[0]?.[1];
    expect(executedPlan.files).toEqual(expect.arrayContaining([
      expect.objectContaining({ path: "src/core.ts" }),
      expect.objectContaining({ path: "tests/core.test.ts", purpose: "locate core tests" }),
    ]));
    expect(harness.getContextView().observations).toContainEqual(expect.objectContaining({
      tool: "find_related_tests",
      paths: ["tests/core.test.ts"],
    }));
  });

  it("enforces the active Skill policy as a Harness runtime boundary", async () => {
    const harness = new RepositoryHarness("C:\\repo");
    await harness.getRepositoryMap();
    harness.activateSkillPolicy("explorer", {
      skillNames: ["test-focused"],
      allowedTools: ["find_related_tests"],
      preferredTools: ["find_related_tests"],
      recommendedQuestions: ["How is the core verified?"],
      evidenceRequirements: ["A representative core test"],
      stopConditions: ["A core test is selected or the gap is recorded"],
      maxDiscoveryActions: 1,
      maxEvidenceFiles: 2,
    });
    const plan = {
      goal: "Bound the investigation",
      rationale: "Use only the capability pack allowed by the Harness",
      questions: [],
      actions: [
        {
          tool: "search_symbols" as const,
          query: "Core",
          purpose: "blocked symbol search",
        },
        {
          tool: "find_related_tests" as const,
          paths: ["src/core.ts"],
          purpose: "allowed test lookup",
        },
      ],
      files: [
        { path: "src/core.ts", purpose: "core", priority: "high" as const },
        { path: "src/index.ts", purpose: "entry", priority: "medium" as const },
        { path: "README.md", purpose: "overflow", priority: "low" as const },
      ],
      stopConditions: ["Core is understood"],
    };

    await harness.executeEvidencePlan("explorer", plan);

    const executedPlan = mocks.buildEvidenceBundle.mock.calls[0]?.[1];
    expect(executedPlan.actions).toEqual([expect.objectContaining({
      tool: "find_related_tests",
    })]);
    expect(executedPlan.files).toHaveLength(2);
    expect(executedPlan.files).toEqual(expect.arrayContaining([
      expect.objectContaining({ path: "src/core.ts" }),
      expect.objectContaining({ path: "tests/core.test.ts" }),
    ]));
    expect(executedPlan.stopConditions).toContain(
      "A core test is selected or the gap is recorded",
    );
    expect(harness.getContextView()).toMatchObject({
      stages: {
        explorer: {
          skillPolicy: { skillNames: ["test-focused"], maxEvidenceFiles: 2 },
        },
      },
      unresolvedQuestions: expect.arrayContaining([
        expect.stringContaining("search_symbols"),
        expect.stringContaining("README.md"),
        expect.stringContaining("src/index.ts"),
      ]),
      observations: [expect.objectContaining({ tool: "find_related_tests" })],
    });
  });

  it("keeps Git inspection inside the Harness and after Mentor", async () => {
    mocks.buildEvidenceBundle.mockResolvedValue({ files: [], skippedPaths: [], omissions: [], totalBytes: 0 });
    const traces: any[] = [];
    const harness = new RepositoryHarness("C:\\repo", (entry) => traces.push(entry));
    const explorerPlan = {
      goal: "entry",
      rationale: "entry",
      questions: [],
      actions: [],
      files: [],
      stopConditions: ["entry checked"],
    };
    const mentorPlan = {
      goal: "architecture",
      rationale: "architecture",
      questions: [],
      actions: [],
      files: [],
      stopConditions: ["architecture checked"],
    };

    await harness.getRepositoryMap();
    await expect(harness.inspectGitHistory()).rejects.toThrow(HarnessInvariantError);
    harness.recordEvidencePlan("explorer", explorerPlan);
    await harness.readEvidenceBatch("explorer", explorerPlan);
    harness.completeStage("explorer");
    harness.recordEvidencePlan("mentor", mentorPlan);
    await harness.readEvidenceBatch("mentor", mentorPlan);
    harness.completeStage("mentor");

    const summary = await harness.inspectGitHistory();
    expect(summary.contributorCount).toBe(2);
    expect(traces).toContainEqual(expect.objectContaining({
      tool: "inspect_git_history",
      files: ["src/core.ts"],
    }));
  });
});
