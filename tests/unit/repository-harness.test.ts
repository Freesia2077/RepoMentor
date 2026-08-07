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
    mocks.buildEvidenceBundle.mockResolvedValue({ files: [], skippedPaths: [], totalBytes: 0 });
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
    expect(() => harness.recordEvidencePlan("explorer", plan)).toThrow(HarnessInvariantError);
  });

  it("enforces one evidence batch per stage and stage completion order", async () => {
    mocks.buildEvidenceBundle.mockResolvedValue({ files: [], skippedPaths: [], totalBytes: 0 });
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
    expect(verified.evidenceCoverage.gaps.join(" ")).toContain("unsupported");
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
    mocks.buildEvidenceBundle.mockResolvedValue({ files: [], skippedPaths: [], totalBytes: 0 });
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
