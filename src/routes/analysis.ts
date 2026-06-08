import type { FastifyInstance } from "fastify";
import { createTask, getTask, answerQuestion, OrchestratorError } from "../services/orchestrator.js";
import type { CreateAnalysisRequest, AskRequest } from "../types/index.js";

export async function analysisRoutes(app: FastifyInstance): Promise<void> {
  // POST /api/analysis
  app.post<{ Body: CreateAnalysisRequest }>("/api/analysis", async (request, reply) => {
    const { repoUrl, branch } = request.body;

    try {
      const task = await createTask(repoUrl, branch ?? "main");
      return reply.status(201).send({
        taskId: task.taskId,
        status: task.status,
        createdAt: task.createdAt,
      });
    } catch (err) {
      if (err instanceof OrchestratorError) {
        return reply.status(400).send({
          error: err.category,
          message: err.message,
        });
      }
      throw err;
    }
  });

  // GET /api/analysis/:id
  app.get<{ Params: { id: string } }>("/api/analysis/:id", async (request, reply) => {
    const { id } = request.params;
    const response = getTask(id);

    if (!response) {
      return reply.status(404).send({ error: "not_found", message: "任务不存在" });
    }

    return reply.send(response);
  });

  // POST /api/analysis/:id/ask
  app.post<{ Params: { id: string }; Body: AskRequest }>(
    "/api/analysis/:id/ask",
    async (request, reply) => {
      const { id } = request.params;
      const { questionId, answer } = request.body;

      if (!getTask(id)) {
        return reply.status(404).send({ error: "not_found", message: "任务不存在" });
      }

      const accepted = answerQuestion(id, questionId, answer);

      if (!accepted) {
        return reply.status(400).send({
          error: "question_expired",
          message: "该交互问题已超时，分析已自动继续",
        });
      }

      return reply.send({ accepted: true });
    },
  );
}
