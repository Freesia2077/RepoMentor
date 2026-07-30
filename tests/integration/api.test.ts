import { describe, it, expect, beforeAll, afterAll, vi } from "vitest";
import Fastify from "fastify";

vi.mock("../../src/services/pipeline.js", () => ({
  executePipeline: vi.fn(async () => ({
    cached: false,
    result: {
      explorer: {
        projectType: { primary: "library", secondary: [] },
        techStack: { language: "typescript", framework: null, buildTool: "npm" },
        fileCount: 1,
        entryPoints: [],
        moduleMap: [],
        directorySummary: "test",
        projectSummary: "test repository",
      },
      mentor: {
        architectureOverview: "test",
        dependencyGraph: {},
        readingPath: [],
        keyPatterns: [],
        codeConventions: [],
      },
      contributor: {
        goodFirstIssues: [],
        contributionSetup: { devEnv: null, build: null, test: null },
        entryFiles: [],
        notesForNewcomers: [],
      },
    },
  })),
  resolveQuestion: vi.fn(() => false),
}));

import { analysisRoutes } from "../../src/routes/analysis.js";
import { streamRoutes } from "../../src/routes/stream.js";

let app: ReturnType<typeof Fastify>;

beforeAll(async () => {
  app = Fastify({ logger: false });
  await app.register(analysisRoutes);
  await app.register(streamRoutes);
  await app.ready();
});

afterAll(async () => {
  await app.close();
});

describe("POST /api/analysis", () => {
  it("rejects invalid URL", async () => {
    const res = await app.inject({
      method: "POST",
      url: "/api/analysis",
      payload: { repoUrl: "not-a-valid-url" },
    });

    expect(res.statusCode).toBe(400);
    const body = JSON.parse(res.payload);
    expect(body.error).toBe("invalid_repo_url");
  });

  it("accepts valid GitHub URL", async () => {
    const res = await app.inject({
      method: "POST",
      url: "/api/analysis",
      payload: { repoUrl: "https://github.com/expressjs/express" },
    });

    expect(res.statusCode).toBe(201);
    const body = JSON.parse(res.payload);
    expect(body.taskId).toBeDefined();
    expect(body.status).toBe("cloning");
    expect(body.createdAt).toBeDefined();
  });

  it("accepts owner/repo shorthand", async () => {
    const res = await app.inject({
      method: "POST",
      url: "/api/analysis",
      payload: { repoUrl: "facebook/react" },
    });

    expect(res.statusCode).toBe(201);
  });

  it("rejects an unsafe branch name", async () => {
    const res = await app.inject({
      method: "POST",
      url: "/api/analysis",
      payload: {
        repoUrl: "https://github.com/facebook/react",
        branch: "../main",
      },
    });

    expect(res.statusCode).toBe(400);
    expect(JSON.parse(res.payload).error).toBe("invalid_branch");
  });
});

describe("GET /api/analysis/:id", () => {
  it("returns 404 for unknown task", async () => {
    const res = await app.inject({
      method: "GET",
      url: "/api/analysis/unknown_task",
    });

    expect(res.statusCode).toBe(404);
  });

  it("returns task status for created task", async () => {
    const createRes = await app.inject({
      method: "POST",
      url: "/api/analysis",
      payload: { repoUrl: "https://github.com/expressjs/express" },
    });
    const { taskId } = JSON.parse(createRes.payload);

    const getRes = await app.inject({
      method: "GET",
      url: `/api/analysis/${taskId}`,
    });

    expect(getRes.statusCode).toBe(200);
    const body = JSON.parse(getRes.payload);
    expect(body.taskId).toBe(taskId);
    expect(body.createdAt).toBeDefined();
  });
});

describe("POST /api/analysis/:id/ask", () => {
  it("returns 404 for unknown task", async () => {
    const res = await app.inject({
      method: "POST",
      url: "/api/analysis/unknown_task/ask",
      payload: { questionId: "q1", answer: "是" },
    });

    expect(res.statusCode).toBe(404);
  });

  it("returns question_expired when no pending question", async () => {
    const createRes = await app.inject({
      method: "POST",
      url: "/api/analysis",
      payload: { repoUrl: "https://github.com/expressjs/express" },
    });
    const { taskId } = JSON.parse(createRes.payload);

    const res = await app.inject({
      method: "POST",
      url: `/api/analysis/${taskId}/ask`,
      payload: { questionId: "q1", answer: "是" },
    });

    expect(res.statusCode).toBe(400);
  });
});

describe("GET /api/analysis/:id/stream", () => {
  it("returns 404 for unknown task", async () => {
    const res = await app.inject({
      method: "GET",
      url: "/api/analysis/unknown_task/stream",
    });

    expect(res.statusCode).toBe(404);
  });
});
