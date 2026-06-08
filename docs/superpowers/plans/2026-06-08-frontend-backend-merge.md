# Frontend-Backend Merge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge the React frontend and Fastify backend into a single-port deployment model with synchronized API routing.

**Architecture:** A unified monorepo setup where Fastify serves the Vite-built static React application (`web/dist`) and exposes backend APIs under an `/api` prefix to prevent routing conflicts.

**Tech Stack:** Node.js, Fastify, React, Vite.

---

### Task 1: Monorepo Foundation & Documentation

**Files:**
- Modify: `package.json`
- Modify: `.env.example`
- Modify: `CLAUDE.md`
- Create: `docs/.gitkeep`

- [ ] **Step 1: Create the docs directory**

```bash
mkdir -p docs
touch docs/.gitkeep
```

- [ ] **Step 2: Update `.env.example`**

Add `NODE_ENV=development` to `.env.example` to ensure the Fastify static plugin and fallback logic don't crash in local dev.

Modify `.env.example`:
```env
PORT=3000
HOST=127.0.0.1
LOG_LEVEL=info
NODE_ENV=development
```

- [ ] **Step 3: Update `package.json` for orchestration**

> **Warning:** Before adding `"type": "module"`, ensure your `tsconfig.json` has `"module": "NodeNext"` and your relative imports use `.js` extensions. (In this repo, the backend is already configured for ESM, so maintaining `"type": "module"` is safe).

Modify `package.json` to configure the workspaces and add the concurrent dev and build scripts.

```json
{
  "name": "repomentor",
  "version": "0.1.0",
  "description": "AI-powered open-source repository learning assistant",
  "type": "module",
  "workspaces": [
    "web"
  ],
  "scripts": {
    "dev:backend": "tsx watch src/index.ts",
    "dev:frontend": "npm run dev --workspace=web",
    "dev": "concurrently \"npm run dev:backend\" \"npm run dev:frontend\"",
    "build:backend": "tsc",
    "build:frontend": "npm run build --workspace=web",
    "build": "npm run build:frontend && npm run build:backend",
    "start": "NODE_ENV=production node dist/index.js",
    "test": "vitest run",
    "test:watch": "vitest"
  },
  "dependencies": {
    "@anthropic-ai/claude-agent-sdk": "^0.1.0",
    "better-sqlite3": "^11.7.0",
    "dotenv": "^17.4.2",
    "fastify": "^5.1.0",
    "simple-git": "^3.27.0",
    "zod": "^3.24.0"
  },
  "devDependencies": {
    "@types/better-sqlite3": "^7.6.0",
    "@types/node": "^22.0.0",
    "concurrently": "^8.2.2",
    "tsx": "^4.19.0",
    "typescript": "^5.7.0",
    "vitest": "^2.1.0"
  }
}
```

- [ ] **Step 4: Update `CLAUDE.md` documentation**

Append the monorepo structure overview to the bottom of `CLAUDE.md`.

```markdown
## 目录结构说明

这是一个前后端合并部署的 Monorepo：
- `/src`: Fastify 后端与 AI 编排逻辑（API 入口）。
- `/web`: React + Vite 前端应用。
- `/docs`: Superpowers 设计文档与执行计划。
```

- [ ] **Step 5: Create root `vitest.config.ts`**

Create `vitest.config.ts` to ensure root tests don't run `web/` tests and cause jsdom/node environment collisions.

```typescript
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    include: ['src/**/*.test.ts', 'tests/**/*.test.ts'],
  }
});
```

- [ ] **Step 6: Install dependencies and commit**

```bash
npm install
git add package.json package-lock.json .env.example CLAUDE.md docs/ vitest.config.ts
git commit -m "chore: setup monorepo workspaces and build scripts"
```

---

### Task 2: Backend API Routing & Static Serving

**Files:**
- Modify: `package.json`
- Modify: `src/index.ts`

- [ ] **Step 1: Install `@fastify/static`**

```bash
npm install @fastify/static@^8.0.0
```

- [ ] **Step 2: Update `src/index.ts`**

Modify `src/index.ts` to add the `/api` prefix to the API routes, and configure `@fastify/static` and the SPA fallback routing for production.

```typescript
import Fastify from "fastify";
import { config } from "./config.js";
import { analysisRoutes } from "./routes/analysis.js";
import { streamRoutes } from "./routes/stream.js";
import { getDb } from "./db/index.js";
import fastifyStatic from "@fastify/static";
import fs from "node:fs";
import path from "node:path";

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
    getDb();
    app.log.info("SQLite 数据库已初始化");
  } catch (err) {
    app.log.error(`数据库初始化失败: ${err instanceof Error ? err.message : String(err)}`);
    process.exit(1);
  }

  // 注册路由 (加上 /api 前缀)
  await app.register(analysisRoutes, { prefix: "/api" });
  await app.register(streamRoutes, { prefix: "/api" });

  // 静态资源托管与 SPA 兜底 (仅生产环境)
  if (process.env.NODE_ENV === "production") {
    await app.register(fastifyStatic, {
      root: path.resolve(process.cwd(), "web/dist"),
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
```

- [ ] **Step 3: Check types and commit**

```bash
npm run build:backend
git add package.json package-lock.json src/index.ts
git commit -m "feat(backend): configure API route prefixes and static serving"
```

---

### Task 3: Frontend API Migration

**Files:**
- Modify: `web/vite.config.ts`
- Modify: `web/src/api.ts`
- Modify: `web/src/api.test.ts`
- Modify: `web/src/hooks/useAnalysisStream.ts`
- Delete: `web/mock-server.mjs`

- [ ] **Step 1: Update `web/vite.config.ts`**

Update the Vite proxy configuration from `/analysis` to `/api`.

```typescript
/// <reference types="vitest" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@backend-types': path.resolve(__dirname, '../src/types')
    }
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:3000'
    }
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './vitest.setup.ts',
  }
});
```

- [ ] **Step 2: Update frontend fetch calls**

Modify `web/src/api.ts` to use `/api/analysis`:
```typescript
import { CreateAnalysisRequest, AskRequest, AnalysisResponse } from '@backend-types/index';

export async function createAnalysis(repoUrl: string, branch: string): Promise<{ taskId: string }> {
  const res = await fetch('/api/analysis', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ repoUrl, branch } satisfies CreateAnalysisRequest),
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ message: 'Failed to start analysis' }));
    throw new Error(error.message);
  }
  return res.json();
}

export async function getAnalysis(taskId: string): Promise<AnalysisResponse> {
  const res = await fetch(`/api/analysis/${taskId}`);
  if (!res.ok) {
    throw new Error('Failed to fetch analysis state');
  }
  return res.json();
}

export async function answerInteraction(taskId: string, questionId: string, answer: string): Promise<void> {
  const res = await fetch(`/api/analysis/${taskId}/ask`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ questionId, answer } satisfies AskRequest),
  });
  if (!res.ok) {
    throw new Error('Failed to submit answer');
  }
}
```

- [ ] **Step 3: Update `web/src/hooks/useAnalysisStream.ts`**

Only update the `EventSource` path from `/analysis/...` to `/api/analysis/...`. **Keep all other business logic and event names identical**.

Modify `web/src/hooks/useAnalysisStream.ts`:
```typescript
// ... (keep imports and types)
export function useAnalysisStream(taskId: string | null) {
  // ... (keep state setup)
  useEffect(() => {
    if (!taskId) return;
    
    // Reset state when taskId changes
    setState({ status: 'cloning', logs: [] });
    
    // ONLY CHANGE THIS LINE to add /api prefix:
    const eventSource = new EventSource(`/api/analysis/${taskId}/stream`);

    // ... KEEP ALL EXISTING eventSource.addEventListener CALLS EXACTLY AS THEY WERE
    
    return () => {
      eventSource.close();
    };
  }, [taskId]);

  return state;
}
```

- [ ] **Step 4: Update `web/src/api.test.ts`**

Update `web/src/api.test.ts` to test for the correct `/api/` prefixed URLs.
Replace `'/analysis'` with `'/api/analysis'`, `'/analysis/123'` with `'/api/analysis/123'`, and `'/analysis/123/ask'` with `'/api/analysis/123/ask'`.

```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createAnalysis, getAnalysis, answerInteraction } from './api';

global.fetch = vi.fn();

describe('API Integration', () => {
  beforeEach(() => {
    vi.mocked(fetch).mockReset();
  });

  it('createAnalysis posts to /api/analysis', async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ taskId: '123' })
    } as Response);

    const result = await createAnalysis('https://github.com/a/b', 'main');
    
    expect(fetch).toHaveBeenCalledWith('/api/analysis', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ repoUrl: 'https://github.com/a/b', branch: 'main' })
    }));
    expect(result.taskId).toBe('123');
  });

  it('createAnalysis throws on error', async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: false,
      json: async () => ({ message: 'Bad request' })
    } as Response);

    await expect(createAnalysis('a', 'b')).rejects.toThrow('Bad request');
  });

  it('getAnalysis gets from /api/analysis/:taskId', async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ status: 'completed', result: { projectSummary: 'A test project' } })
    } as Response);

    const result = await getAnalysis('123');
    
    expect(fetch).toHaveBeenCalledWith('/api/analysis/123');
    expect(result.status).toBe('completed');
    expect(result.result?.projectSummary).toBe('A test project');
  });

  it('getAnalysis throws on error', async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: false,
    } as Response);

    await expect(getAnalysis('123')).rejects.toThrow('Failed to fetch analysis state');
  });

  it('answerInteraction posts to /api/analysis/:taskId/ask', async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: true,
    } as Response);

    await answerInteraction('123', 'q1', 'my answer');
    
    expect(fetch).toHaveBeenCalledWith('/api/analysis/123/ask', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ questionId: 'q1', answer: 'my answer' })
    }));
  });

  it('answerInteraction throws on error', async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: false,
    } as Response);

    await expect(answerInteraction('123', 'q1', 'my answer')).rejects.toThrow('Failed to submit answer');
  });
});
```

- [ ] **Step 5: Clean up and Commit**

```bash
rm web/mock-server.mjs
npm run test --workspace=web
git add web/vite.config.ts web/src/api.ts web/src/api.test.ts web/src/hooks/useAnalysisStream.ts web/mock-server.mjs
git commit -m "feat(web): migrate frontend to use unified /api routing prefix and proxy"
```

- [ ] **Step 6: E2E Verification**

```bash
npm run build
```
Verify that `npm run build` succeeds, which runs both frontend and backend typescript builds.
