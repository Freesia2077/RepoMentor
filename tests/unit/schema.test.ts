import { describe, it, expect } from "vitest";
import {
  validateEvidencePlan,
  validateExplorerOutput,
  validateMentorOutput,
  validateContributorOutput,
} from "../../src/lib/schema.js";

describe("evidence plan schema", () => {
  it("accepts a bounded, prioritized reading plan", () => {
    const result = validateEvidencePlan({
      goal: "Map the public entry",
      rationale: "Verify the public entry and representative tests",
      questions: ["Where are exports defined?"],
      actions: [],
      files: [{
        path: "src/index.ts",
        purpose: "confirm public exports",
        priority: "high",
      }],
      stopConditions: ["Public exports have source evidence"],
    });

    expect(result.files[0]?.path).toBe("src/index.ts");
  });

  it("rejects more than twelve planned files", () => {
    expect(() => validateEvidencePlan({
      goal: "Read too much",
      rationale: "too many",
      questions: [],
      actions: [],
      files: Array.from({ length: 13 }, (_, index) => ({
        path: `src/${index}.ts`,
        purpose: "read",
        priority: "low",
      })),
      stopConditions: ["Enough files"],
    })).toThrow();
  });

  it("accepts bounded repository discovery actions", () => {
    const result = validateEvidencePlan({
      goal: "Trace the request flow",
      rationale: "Start from the API route and locate its dependencies",
      questions: ["Which service handles the request?"],
      actions: [{
        tool: "trace_module_dependencies",
        paths: ["src/routes/analysis.ts"],
        purpose: "resolve direct internal dependencies",
      }],
      files: [{
        path: "src/routes/analysis.ts",
        purpose: "request entry",
        priority: "high",
      }],
      stopConditions: ["The entry and direct service dependency are covered"],
    });
    expect(result.actions[0]?.tool).toBe("trace_module_dependencies");
  });
});

const validExplorer = {
  projectType: { primary: "library", secondary: [] },
  techStack: { language: "typescript", framework: null, buildTool: "tsup" },
  fileCount: 100,
  entryPoints: [{ file: "src/index.ts", role: "主入口" }],
  moduleMap: [{
    path: "src/core/",
    responsibility: "核心模块",
    importance: "core",
    justification: "包含入口文件",
  }],
  directorySummary: "一个库项目",
  projectSummary: "轻量级工具库",
  evidenceClaims: [{
    claim: "src/index.ts is the public entry",
    confidence: "high",
    evidence: [{ path: "src/index.ts", supports: "exports the API" }],
  }],
  evidenceCoverage: { examinedFiles: ["src/index.ts"], gaps: [] },
};

describe("validateExplorerOutput", () => {
  it("accepts valid output", () => {
    expect(() => validateExplorerOutput(validExplorer)).not.toThrow();
  });

  it("rejects missing required field", () => {
    const bad = { ...validExplorer };
    delete (bad as Record<string, unknown>).projectType;
    expect(() => validateExplorerOutput(bad)).toThrow();
  });

  it("rejects entryPoints exceeding max 10", () => {
    const bad = {
      ...validExplorer,
      entryPoints: Array.from({ length: 11 }, (_, i) => ({
        file: `src/file${i}.ts`,
        role: "x",
      })),
    };
    expect(() => validateExplorerOutput(bad)).toThrow();
  });

  it("rejects moduleMap exceeding max 6", () => {
    const bad = {
      ...validExplorer,
      moduleMap: Array.from({ length: 7 }, (_, i) => ({
        path: `src/module${i}/`,
        responsibility: "x",
        importance: "utility" as const,
        justification: "x",
      })),
    };
    expect(() => validateExplorerOutput(bad)).toThrow();
  });

  it("rejects module responsibility exceeding 300 chars", () => {
    const bad = {
      ...validExplorer,
      moduleMap: [{
        path: "src/core/",
        responsibility: "x".repeat(301),
        importance: "core" as const,
        justification: "x",
      }],
    };
    expect(() => validateExplorerOutput(bad)).toThrow();
  });

  it("accepts valid custom projectType primary", () => {
    const custom = {
      ...validExplorer,
      projectType: { primary: "custom-game-engine", secondary: [] },
    };
    expect(() => validateExplorerOutput(custom)).not.toThrow();
  });

  it("normalizes the unambiguous supporting alias to support", () => {
    const output = validateExplorerOutput({
      ...validExplorer,
      moduleMap: [{
        ...validExplorer.moduleMap[0],
        importance: "supporting",
      }],
    });

    expect(output.moduleMap[0]?.importance).toBe("support");
  });

  it("normalizes legacy string coverage gaps into the structured contract", () => {
    const output = validateExplorerOutput({
      ...validExplorer,
      evidenceCoverage: {
        examinedFiles: ["src/index.ts"],
        gaps: ["demo.ipynb: 训练循环因截断未完整确认"],
      },
    });

    expect(output.evidenceCoverage.gaps).toEqual([{
      kind: "missing_evidence",
      subject: "demo.ipynb",
      summary: "demo.ipynb: 训练循环因截断未完整确认",
      severity: "medium",
    }]);
  });

  it("rejects unknown module importance values", () => {
    expect(() => validateExplorerOutput({
      ...validExplorer,
      moduleMap: [{
        ...validExplorer.moduleMap[0],
        importance: "auxiliary",
      }],
    })).toThrow();
  });
});

const validMentor = {
  architectureOverview: "这是一个架构概述",
  dependencyGraph: { "src/core/": ["src/utils/"] },
  readingPath: [{ step: 1, file: "src/index.ts", why: "入口文件" }],
  keyPatterns: [{ pattern: "中间件模式", where: "src/core/", description: "使用洋葱模型" }],
  codeConventions: [{ rule: "使用 JSDoc", example: "src/core/app.ts:45" }],
  evidenceClaims: [{
    claim: "Core depends on utilities",
    confidence: "high",
    evidence: [{ path: "src/core/app.ts", supports: "imports utilities" }],
  }],
  evidenceCoverage: { examinedFiles: ["src/core/app.ts"], gaps: [] },
};

describe("validateMentorOutput", () => {
  it("accepts valid output", () => {
    expect(() => validateMentorOutput(validMentor)).not.toThrow();
  });

  it("rejects architectureOverview exceeding 3000 chars", () => {
    const bad = { ...validMentor, architectureOverview: "x".repeat(3001) };
    expect(() => validateMentorOutput(bad)).toThrow();
  });

  it("rejects readingPath exceeding 5 steps", () => {
    const bad = {
      ...validMentor,
      readingPath: Array.from({ length: 6 }, (_, i) => ({
        step: i + 1,
        file: `src/file${i}.ts`,
        why: "reason",
      })),
    };
    expect(() => validateMentorOutput(bad)).toThrow();
  });
});

const validContributor = {
  goodFirstIssues: [{ area: "文档", difficulty: "easy", description: "补充 JSDoc" }],
  contributionSetup: { devEnv: "Node 18+", build: "npm build", test: "npm test" },
  entryFiles: [{ file: "src/index.ts", description: "入口", reason: "启动" }],
  notesForNewcomers: [{ tip: "遵循 Conventional Commits" }],
  evidenceClaims: [{
    claim: "The project defines a test command",
    confidence: "high",
    evidence: [{ path: "package.json", supports: "contains scripts.test" }],
  }],
  evidenceCoverage: { examinedFiles: ["package.json"], gaps: [] },
};

describe("validateContributorOutput", () => {
  it("accepts valid output", () => {
    expect(() => validateContributorOutput(validContributor)).not.toThrow();
  });

  it("rejects an unsupported difficulty", () => {
    const bad = {
      ...validContributor,
      goodFirstIssues: [{ area: "文档", difficulty: "extreme", description: "补充 JSDoc" }],
    };
    expect(() => validateContributorOutput(bad)).toThrow();
  });

  it("rejects an unsupported module importance", () => {
    const bad = {
      ...validExplorer,
      moduleMap: [{
        path: "src/core/",
        responsibility: "核心",
        importance: "critical",
        justification: "x",
      }],
    };
    expect(() => validateExplorerOutput(bad)).toThrow();
  });
});
