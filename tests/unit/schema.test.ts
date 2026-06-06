import { describe, it, expect } from "vitest";
import {
  validateExplorerOutput,
  validateMentorOutput,
  validateContributorOutput,
} from "../../src/lib/schema.js";

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

  it("rejects moduleMap exceeding max 20", () => {
    const bad = {
      ...validExplorer,
      moduleMap: Array.from({ length: 21 }, (_, i) => ({
        path: `src/module${i}/`,
        responsibility: "x",
        importance: "utility" as const,
        justification: "x",
      })),
    };
    expect(() => validateExplorerOutput(bad)).toThrow();
  });

  it("rejects module responsibility exceeding 100 chars", () => {
    const bad = {
      ...validExplorer,
      moduleMap: [{
        path: "src/core/",
        responsibility: "x".repeat(101),
        importance: "core" as const,
        justification: "x",
      }],
    };
    expect(() => validateExplorerOutput(bad)).toThrow();
  });

  it("rejects invalid importance value", () => {
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

  it("rejects invalid projectType primary", () => {
    const bad = {
      ...validExplorer,
      projectType: { primary: "invalid", secondary: [] },
    };
    expect(() => validateExplorerOutput(bad)).toThrow();
  });
});

const validMentor = {
  architectureOverview: "这是一个架构概述",
  dependencyGraph: { "src/core/": ["src/utils/"] },
  readingPath: [{ step: 1, file: "src/index.ts", why: "入口文件" }],
  keyPatterns: [{ pattern: "中间件模式", where: "src/core/", description: "使用洋葱模型" }],
  codeConventions: [{ rule: "使用 JSDoc", example: "src/core/app.ts:45" }],
};

describe("validateMentorOutput", () => {
  it("accepts valid output", () => {
    expect(() => validateMentorOutput(validMentor)).not.toThrow();
  });

  it("rejects architectureOverview exceeding 800 chars", () => {
    const bad = { ...validMentor, architectureOverview: "x".repeat(801) };
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
};

describe("validateContributorOutput", () => {
  it("accepts valid output", () => {
    expect(() => validateContributorOutput(validContributor)).not.toThrow();
  });

  it("rejects invalid difficulty", () => {
    const bad = {
      ...validContributor,
      goodFirstIssues: [{ area: "文档", difficulty: "extreme", description: "补充 JSDoc" }],
    };
    expect(() => validateContributorOutput(bad)).toThrow();
  });
});
