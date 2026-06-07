import { describe, it, expect, beforeAll, afterAll } from "vitest";
import Fastify from "fastify";
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

describe("POST /analysis", () => {
  it("rejects invalid URL", async () => {
    const res = await app.inject({
      method: "POST",
      url: "/analysis",
      payload: { repoUrl: "not-a-valid-url" },
    });

    expect(res.statusCode).toBe(400);
    const body = JSON.parse(res.payload);
    expect(body.error).toBe("invalid_repo_url");
  });

  it("accepts valid GitHub URL", async () => {
    const res = await app.inject({
      method: "POST",
      url: "/analysis",
      payload: { repoUrl: "https://github.com/expressjs/express" },
    });

    expect(res.statusCode).toBe(201);
    const body = JSON.parse(res.payload);
    expect(body.taskId).toBeDefined();
    expect(body.status).toBe("cloning");
    expect(body.createdAt).toBeDefined();
  });
});

describe("GET /analysis/:id", () => {
  it("returns 404 for unknown task", async () => {
    const res = await app.inject({
      method: "GET",
      url: "/analysis/unknown_task",
    });

    expect(res.statusCode).toBe(404);
  });

  it("returns task status for created task", async () => {
    const createRes = await app.inject({
      method: "POST",
      url: "/analysis",
      payload: { repoUrl: "https://github.com/expressjs/express" },
    });
    const { taskId } = JSON.parse(createRes.payload);

    const getRes = await app.inject({
      method: "GET",
      url: `/analysis/${taskId}`,
    });

    expect(getRes.statusCode).toBe(200);
    const body = JSON.parse(getRes.payload);
    expect(body.taskId).toBe(taskId);
    expect(body.createdAt).toBeDefined();
  });
});

describe("POST /analysis/:id/ask", () => {
  it("returns 404 for unknown task", async () => {
    const res = await app.inject({
      method: "POST",
      url: "/analysis/unknown_task/ask",
      payload: { questionId: "q1", answer: "是" },
    });

    expect(res.statusCode).toBe(404);
  });

  it("returns question_expired when no pending question", async () => {
    const createRes = await app.inject({
      method: "POST",
      url: "/analysis",
      payload: { repoUrl: "https://github.com/expressjs/express" },
    });
    const { taskId } = JSON.parse(createRes.payload);

    const res = await app.inject({
      method: "POST",
      url: `/analysis/${taskId}/ask`,
      payload: { questionId: "q1", answer: "是" },
    });

    expect(res.statusCode).toBe(400);
  });
});

describe("GET /analysis/:id/stream", () => {
  it("returns 404 for unknown task", async () => {
    const res = await app.inject({
      method: "GET",
      url: "/analysis/unknown_task/stream",
    });

    expect(res.statusCode).toBe(404);
  });
});
