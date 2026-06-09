import type { FastifyInstance } from "fastify";
import { sseManager } from "../lib/sse.js";
import { hasTask } from "../services/orchestrator.js";

export async function streamRoutes(app: FastifyInstance): Promise<void> {
  app.get<{ Params: { id: string } }>(
    "/api/analysis/:id/stream",
    async (request, reply) => {
      const { id } = request.params;

      if (!hasTask(id)) {
        return reply.status(404).send({ error: "not_found", message: "任务不存在" });
      }

      // 设置 SSE headers
      reply.raw.writeHead(200, {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
      });

      // 发送初始心跳
      reply.raw.write(":ok\n\n");

      const emitter = sseManager.subscribe(id);

      const onEvent = (event: unknown) => {
        const typed = event as { type: string };
        reply.raw.write(`event: ${typed.type}\ndata: ${JSON.stringify(event)}\n\n`);
      };

      emitter.on("event", onEvent);

      // 连接关闭时清理
      request.raw.on("close", () => {
        emitter.off("event", onEvent);
      });

      // 等待连接关闭（SSE 长连接）
      await new Promise<void>((resolve) => {
        request.raw.on("close", resolve);
      });
    },
  );
}
