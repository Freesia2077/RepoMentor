import Fastify from "fastify";
import fastifyStatic from "@fastify/static";
import { config } from "./config.js";
import { analysisRoutes } from "./routes/analysis.js";
import { streamRoutes } from "./routes/stream.js";
import { settingsRoutes } from "./routes/settings.js";
import { getDb } from "./db/index.js";
import * as taskRepo from "./db/repositories/tasks.js";
import fs from "node:fs";
import path from "node:path";
import { resolveAppAsset } from "./lib/app-paths.js";

const app = Fastify({
  logger: {
    level: config.LOG_LEVEL,
  },
});



async function start(): Promise<void> {
  // 启动时兜底清理 tmp/ 下超过 1 小时的残留
  try {
    const tmpDir = path.resolve("tmp");
    if (fs.existsSync(tmpDir)) {
      const entries = fs.readdirSync(tmpDir);
      const oneHourAgo = Date.now() - 60 * 60 * 1000;
      for (const entry of entries) {
        const entryPath = path.join(tmpDir, entry);
        const stat = fs.statSync(entryPath);
        if (stat.mtimeMs < oneHourAgo) {
          fs.rmSync(entryPath, { recursive: true, force: true });
          app.log.info(`清理残留临时目录: ${entryPath}`);
        }
      }
    }
  } catch {
    app.log.warn("tmp/ 清理失败，跳过");
  }

  // 初始化 DB
  try {
    const db = getDb();
    const interruptedTasks = taskRepo.markInterruptedAsFailed(db);
    app.log.info("SQLite 数据库已初始化");
    if (interruptedTasks > 0) {
      app.log.warn(`已将 ${interruptedTasks} 个因服务重启中断的任务标记为失败`);
    }
  } catch (err) {
    app.log.error(`数据库初始化失败: ${err instanceof Error ? err.message : String(err)}`);
    process.exit(1);
  }

  // 注册路由 (路由内部已包含 /api 前缀)
  await app.register(analysisRoutes);
  await app.register(streamRoutes);
  await app.register(settingsRoutes);

  // 静态资源托管与 SPA 兜底 (仅生产环境)
  if (process.env.NODE_ENV === "production") {
    await app.register(fastifyStatic, {
      root: resolveAppAsset("web", "dist"),
      wildcard: false, // 防治与 SPA 兜底冲突
    });

    app.setNotFoundHandler((request, reply) => {
      if (request.url.startsWith("/api")) {
        reply.status(404).send({ error: "not_found" });
      } else {
        reply.sendFile("index.html");
      }
    });
  }

  // 健康检查
  app.get("/health", async () => ({ status: "ok" }));

  // 启动
  try {
    await app.listen({ port: config.PORT, host: config.HOST });
    app.log.info(`RepoMentor 启动: http://${config.HOST}:${config.PORT}`);
  } catch (err) {
    app.log.error(err);
    process.exit(1);
  }
}

start();
