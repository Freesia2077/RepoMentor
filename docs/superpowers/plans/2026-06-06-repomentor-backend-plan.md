# RepoMentor 后端实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建 RepoMentor 后端 API 服务——用户提交 GitHub 仓库链接，系统通过 Explorer → Mentor → Contributor 三阶段 Pipeline 分析仓库架构、生成学习路径、发现贡献机会。

**Architecture:** Fastify HTTP 服务 + SQLite 持久化。Orchestrator 管理分析任务生命周期，通过 Claude Agent SDK 依次调用三个 Subagent（Pipeline 模式），进度通过 SSE 实时推送。Clone、Git 操作、模板注入均由 Orchestrator 完成，Subagent 不持有 Bash/Skill 工具。

**Tech Stack:** TypeScript, Fastify 5, better-sqlite3, Zod, simple-git, Claude Agent SDK, vitest

---

## File Map

| File | Responsibility |
|------|---------------|
| `src/types/index.ts` | 共享类型定义（TaskStatus, StageName, SSEEvent, 各阶段输入/输出） |
| `src/config.ts` | 环境变量 Zod 校验，fail-fast |
| `src/db/index.ts` | SQLite 连接初始化 & 迁移执行 |
| `src/db/migrations/001-init.sql` | DDL: analysis_cache + experiences 表 |
| `src/db/repositories/analysis-cache.ts` | 分析结果缓存 CRUD |
| `src/db/repositories/experiences.ts` | 经验库 CRUD |
| `src/lib/schema.ts` | 三阶段输出的 Zod Schema 定义 & 校验函数 |
| `src/lib/sandbox.ts` | 路径安全约束（resolvePath） |
| `src/lib/repo.ts` | Git clone、preCheck、commit hash、commit 摘要提取、清理 |
| `src/lib/sse.ts` | SSE 事件广播器（EventEmitter 封装） |
| `src/services/claude-client.ts` | Claude Agent SDK 封装，runStage() |
| `src/services/pipeline.ts` | Pipeline 状态机，阶段衔接 & 重试逻辑 |
| `src/services/orchestrator.ts` | 任务生命周期：创建、调度、缓存检查、交互处理 |
| `src/routes/analysis.ts` | POST /analysis, GET /analysis/:id, POST /analysis/:id/ask |
| `src/routes/stream.ts` | GET /analysis/:id/stream (SSE) |
| `src/index.ts` | Fastify 启动入口，路由注册，兜底清理 |
| `agents/explorer/agent.md` | Explorer Subagent 定义 |
| `agents/mentor/agent.md` | Mentor Subagent 定义 |
| `agents/contributor/agent.md` | Contributor Subagent 定义 |
| `skills/analyze-web-framework/SKILL.md` | Web 框架分析模板 |
| `skills/analyze-cli-tool/SKILL.md` | CLI 工具分析模板 |
| `skills/analyze-monorepo/SKILL.md` | Monorepo 分析模板 |
| `skills/analyze-generic/SKILL.md` | 通用兜底模板 |

---

### Task 1: 项目脚手架

**Files:**
- Create: `package.json`
- Create: `tsconfig.json`
- Create: `.env.example`
- Create: `.gitignore`

- [ ] **Step 1: 创建 package.json**

```bash
cd "D:\Edge Download\Coding"
```

```json
{
  "name": "repomentor",
  "version": "0.1.0",
  "description": "AI-powered open-source repository learning assistant",
  "type": "module",
  "scripts": {
    "dev": "tsx watch src/index.ts",
    "build": "tsc",
    "start": "node dist/index.js",
    "test": "vitest run",
    "test:watch": "vitest"
  },
  "dependencies": {
    "fastify": "^5.1.0",
    "@anthropic-ai/claude-agent-sdk": "^0.1.0",
    "better-sqlite3": "^11.7.0",
    "zod": "^3.24.0",
    "simple-git": "^3.27.0"
  },
  "devDependencies": {
    "typescript": "^5.7.0",
    "vitest": "^2.1.0",
    "tsx": "^4.19.0",
    "@types/better-sqlite3": "^7.6.0",
    "@types/node": "^22.0.0"
  }
}
```

- [ ] **Step 2: 创建 tsconfig.json**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "esModuleInterop": true,
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "outDir": "dist",
    "rootDir": "src",
    "declaration": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true
  },
  "include": ["src/**/*.ts"],
  "exclude": ["node_modules", "dist", "tests", "tmp"]
}
```

- [ ] **Step 3: 创建 .env.example**

```bash
# DeepSeek API
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_BASE_URL=https://api.deepseek.com

# Server
PORT=3000
HOST=0.0.0.0

# Git
CLONE_TIMEOUT_MS=60000
CLONE_DEPTH=1
MAX_REPO_SIZE_MB=200

# Analysis
TASK_TOTAL_TIMEOUT_MS=600000
INTERACTION_TIMEOUT_MS=30000

# DB
SQLITE_PATH=./data/repomentor.db

# Logging
LOG_LEVEL=info
```

- [ ] **Step 4: 创建 .gitignore**

```
node_modules/
dist/
tmp/
data/
.env
*.db
*.db-journal
```

- [ ] **Step 5: 安装依赖**

```bash
npm install
```

- [ ] **Step 6: 验证 TypeScript 编译**

```bash
npx tsc --noEmit
```
Expected: 没有源文件所以无错误，或 error TS18003（No inputs were found — 正常，还没创建 .ts 文件）

---

### Task 2: 类型定义

**Files:**
- Create: `src/types/index.ts`

- [ ] **Step 1: 创建类型文件**

```typescript
// ========== 任务生命周期 ==========

export type TaskStatus = "cloning" | "analyzing" | "completed" | "failed";

export type StageName = "explorer" | "mentor" | "contributor";

export type StageProgress = Record<StageName, "pending" | "running" | "done">;

// ========== 项目类型 ==========

export type ProjectTypePrimary = "library" | "cli" | "web-framework" | "monorepo" | "unknown";

export interface ProjectType {
  primary: ProjectTypePrimary;
  secondary: ProjectTypePrimary[];
}

export interface TechStack {
  language: string;
  framework: string | null;
  buildTool: string;
}

export interface EntryPoint {
  file: string;
  role: string;
}

export interface ModuleInfo {
  path: string;
  responsibility: string;
  importance: "core" | "support" | "utility";
  justification: string;
}

// ========== Stage 1: Explorer ==========

export interface ExplorerInput {
  localPath: string;
  fileCount: number;
  projectTypeHint?: string;
}

export interface ExplorerOutput {
  projectType: ProjectType;
  techStack: TechStack;
  fileCount: number;
  entryPoints: EntryPoint[];
  moduleMap: ModuleInfo[];
  directorySummary: string;
  projectSummary: string;
}

// ========== Stage 2: Mentor ==========

export interface DependencyGraph {
  [modulePath: string]: string[];
}

export interface ReadingStep {
  step: number;
  file: string;
  why: string;
}

export interface KeyPattern {
  pattern: string;
  where: string;
  description: string;
}

export interface CodeConvention {
  rule: string;
  example: string;
}

export interface MentorInput {
  explorerOutput: ExplorerOutput;
  skillContent: string;
  experiences: string;
  userFocus?: string;
}

export interface MentorOutput {
  architectureOverview: string;
  dependencyGraph: DependencyGraph;
  readingPath: ReadingStep[];
  keyPatterns: KeyPattern[];
  codeConventions: CodeConvention[];
}

// ========== Stage 3: Contributor ==========

export interface CommitSummary {
  frequentFiles: { file: string; commits: number; recent: boolean }[];
  recentThemes: string[];
  contributorCount: number;
}

export interface ContributorInput {
  explorerOutput: ExplorerOutput;
  mentorOutput: MentorOutput;
  commitSummary: CommitSummary;
}

export interface GoodFirstIssue {
  area: string;
  difficulty: "easy" | "medium" | "hard";
  description: string;
}

export interface ContributionSetup {
  devEnv: string;
  build: string;
  test: string;
  lint?: string;
}

export interface EntryFile {
  file: string;
  description: string;
  reason: string;
}

export interface NewcomerNote {
  tip: string;
}

export interface ContributorOutput {
  goodFirstIssues: GoodFirstIssue[];
  contributionSetup: ContributionSetup;
  entryFiles: EntryFile[];
  notesForNewcomers: NewcomerNote[];
}

// ========== 分析结果 ==========

export interface AnalysisResult {
  explorer: ExplorerOutput;
  mentor: MentorOutput;
  contributor: ContributorOutput;
}

// ========== 错误 ==========

export type ErrorCategory = "clone_failed" | "llm_failed" | "parse_failed" | "internal";

export interface TaskError {
  category: ErrorCategory;
  message: string;
  retryable: boolean;
}

// ========== SSE 事件 ==========

export interface SSETaskCreatedEvent {
  type: "task:created";
  taskId: string;
  status: TaskStatus;
}

export interface SSETaskCompletedEvent {
  type: "task:completed";
  taskId: string;
  summary: string;
}

export interface SSETaskErrorEvent {
  type: "task:error";
  taskId: string;
  error: TaskError;
}

export interface SSEStageStartEvent {
  type: "stage:start";
  stage: StageName;
}

export interface SSEStageProgressEvent {
  type: "stage:progress";
  stage: StageName;
  message: string;
}

export interface SSEStageFieldEvent {
  type: "stage:field";
  stage: StageName;
  field: string;
  value: unknown;
}

export interface SSEStageDoneEvent {
  type: "stage:done";
  stage: StageName;
  output: unknown;
}

export interface SSEInteractAskEvent {
  type: "interact:ask";
  questionId: string;
  stage: StageName;
  question: string;
  options?: string[];
}

export interface SSEInteractTimeoutEvent {
  type: "interact:timeout";
  questionId: string;
}

export type SSEEvent =
  | SSETaskCreatedEvent
  | SSETaskCompletedEvent
  | SSETaskErrorEvent
  | SSEStageStartEvent
  | SSEStageProgressEvent
  | SSEStageFieldEvent
  | SSEStageDoneEvent
  | SSEInteractAskEvent
  | SSEInteractTimeoutEvent;

// ========== API 请求/响应 ==========

export interface CreateAnalysisRequest {
  repoUrl: string;
  branch?: string;
}

export interface CreateAnalysisResponse {
  taskId: string;
  status: TaskStatus;
  createdAt: string;
}

export interface GetAnalysisResponse {
  taskId: string;
  status: TaskStatus;
  currentStage?: StageName;
  stageProgress?: StageProgress;
  result?: AnalysisResult;
  error?: TaskError;
  createdAt: string;
  completedAt?: string;
  cached: boolean;
}

export interface AskRequest {
  questionId: string;
  answer: string;
}

export interface AskResponse {
  accepted: boolean;
}

// ========== 任务存储 ==========

export interface TaskRecord {
  taskId: string;
  repoUrl: string;
  branch: string;
  status: TaskStatus;
  currentStage: StageName | null;
  stageProgress: StageProgress;
  result: AnalysisResult | null;
  error: TaskError | null;
  commitHash: string | null;
  cached: boolean;
  createdAt: string;
  completedAt: string | null;
}

// ========== 缓存记录 ==========

export interface AnalysisCacheRecord {
  id: number;
  owner: string;
  repo: string;
  branch: string;
  commitHash: string;
  result: string; // JSON string of AnalysisResult
  projectTypePrimary: string;
  framework: string | null;
  created_at: string;
}

// ========== 经验记录 ==========

export interface ExperienceRecord {
  id: number;
  owner: string;
  repo: string;
  primaryType: string;
  framework: string | null;
  secondaryType: string; // JSON array string
  content: string;
  created_at: string;
}
```

- [ ] **Step 2: 验证编译**

```bash
npx tsc --noEmit
```
Expected: 编译通过，无错误。

---

### Task 3: 配置模块

**Files:**
- Create: `src/config.ts`
- Create: `tests/unit/config.test.ts`
- Create: `vitest.config.ts`

- [ ] **Step 1: 创建 vitest.config.ts**

```typescript
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    globals: true,
  },
});
```

- [ ] **Step 2: 编写测试 — tests/unit/config.test.ts**

```typescript
import { describe, it, expect, beforeEach, vi } from "vitest";

// 因为 config.ts 在 import 时就解析 process.env，每次测试需要重置模块
// 使用 vi.resetModules() + 动态 import

describe("config", () => {
  beforeEach(() => {
    vi.resetModules();
    // 清除关键环境变量
    delete process.env.DEEPSEEK_API_KEY;
    delete process.env.DEEPSEEK_BASE_URL;
    delete process.env.PORT;
    delete process.env.HOST;
    delete process.env.CLONE_TIMEOUT_MS;
    delete process.env.CLONE_DEPTH;
    delete process.env.MAX_REPO_SIZE_MB;
    delete process.env.TASK_TOTAL_TIMEOUT_MS;
    delete process.env.INTERACTION_TIMEOUT_MS;
    delete process.env.SQLITE_PATH;
    delete process.env.LOG_LEVEL;
  });

  it("throws when DEEPSEEK_API_KEY is missing", async () => {
    await expect(() => import("../../src/config.js")).rejects.toThrow();
  });

  it("parses valid environment with defaults", async () => {
    process.env.DEEPSEEK_API_KEY = "sk-test";
    const { config } = await import("../../src/config.js");
    expect(config.PORT).toBe(3000);
    expect(config.HOST).toBe("0.0.0.0");
    expect(config.CLONE_DEPTH).toBe(1);
    expect(config.DEEPSEEK_BASE_URL).toBe("https://api.deepseek.com");
    expect(config.LOG_LEVEL).toBe("info");
  });

  it("parses custom values", async () => {
    process.env.DEEPSEEK_API_KEY = "sk-test";
    process.env.PORT = "8080";
    process.env.MAX_REPO_SIZE_MB = "500";
    process.env.LOG_LEVEL = "debug";
    const { config } = await import("../../src/config.js");
    expect(config.PORT).toBe(8080);
    expect(config.MAX_REPO_SIZE_MB).toBe(500);
    expect(config.LOG_LEVEL).toBe("debug");
  });

  it("rejects invalid LOG_LEVEL", async () => {
    process.env.DEEPSEEK_API_KEY = "sk-test";
    process.env.LOG_LEVEL = "verbose";
    await expect(() => import("../../src/config.js")).rejects.toThrow();
  });
});
```

- [ ] **Step 3: 运行测试，确认失败**

```bash
npx vitest run tests/unit/config.test.ts
```
Expected: FAIL — 无法找到 `../../src/config.js`（文件还不存在）

- [ ] **Step 4: 创建 src/config.ts**

```typescript
import { z } from "zod";
import "dotenv/config";

const envSchema = z.object({
  DEEPSEEK_API_KEY: z.string(),
  DEEPSEEK_BASE_URL: z.string().default("https://api.deepseek.com"),
  PORT: z.coerce.number().int().positive().default(3000),
  HOST: z.string().default("0.0.0.0"),
  CLONE_TIMEOUT_MS: z.coerce.number().int().positive().default(60000),
  CLONE_DEPTH: z.coerce.number().int().positive().default(1),
  MAX_REPO_SIZE_MB: z.coerce.number().int().positive().default(200),
  TASK_TOTAL_TIMEOUT_MS: z.coerce.number().int().positive().default(600000),
  INTERACTION_TIMEOUT_MS: z.coerce.number().int().positive().default(30000),
  SQLITE_PATH: z.string().default("./data/repomentor.db"),
  LOG_LEVEL: z.enum(["debug", "info", "warn", "error"]).default("info"),
});

export const config = envSchema.parse(process.env);
export type Config = z.infer<typeof envSchema>;
```

- [ ] **Step 5: 安装 dotenv**

```bash
npm install dotenv
```

- [ ] **Step 6: 运行测试**

```bash
npx vitest run tests/unit/config.test.ts
```
Expected: PASS — 4 tests passed.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: add types and config module"
```

---

### Task 4: 数据库层

**Files:**
- Create: `src/db/migrations/001-init.sql`
- Create: `src/db/index.ts`
- Create: `src/db/repositories/analysis-cache.ts`
- Create: `src/db/repositories/experiences.ts`

- [ ] **Step 1: 创建迁移脚本**

```sql
CREATE TABLE IF NOT EXISTS analysis_cache (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner TEXT NOT NULL,
  repo TEXT NOT NULL,
  branch TEXT NOT NULL DEFAULT 'main',
  commit_hash TEXT NOT NULL,
  result TEXT NOT NULL,
  project_type_primary TEXT NOT NULL,
  framework TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(owner, repo, branch, commit_hash)
);

CREATE INDEX IF NOT EXISTS idx_cache_lookup
  ON analysis_cache(owner, repo, branch, commit_hash);

CREATE TABLE IF NOT EXISTS experiences (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner TEXT NOT NULL,
  repo TEXT NOT NULL,
  primary_type TEXT NOT NULL,
  framework TEXT,
  secondary_type TEXT NOT NULL DEFAULT '[]',
  content TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_experiences_match
  ON experiences(primary_type, framework);

CREATE INDEX IF NOT EXISTS idx_experiences_owner
  ON experiences(owner);
```

- [ ] **Step 2: 创建 DB 连接模块 — src/db/index.ts**

```typescript
import Database from "better-sqlite3";
import { config } from "../config.js";
import fs from "node:fs";
import path from "node:path";

let db: Database.Database;

export function getDb(): Database.Database {
  if (!db) {
    const dir = path.dirname(config.SQLITE_PATH);
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }

    db = new Database(config.SQLITE_PATH);
    db.pragma("journal_mode = WAL");
    db.pragma("foreign_keys = ON");
    runMigrations(db);
  }
  return db;
}

function runMigrations(db: Database.Database): void {
  const migrationsDir = path.join(import.meta.dirname, "migrations");
  if (!fs.existsSync(migrationsDir)) return;

  const files = fs.readdirSync(migrationsDir)
    .filter(f => f.endsWith(".sql"))
    .sort();

  for (const file of files) {
    const sql = fs.readFileSync(path.join(migrationsDir, file), "utf-8");
    db.exec(sql);
  }
}

export function createMemoryDb(): Database.Database {
  const memDb = new Database(":memory:");
  memDb.pragma("journal_mode = WAL");
  const migrationsDir = path.join(import.meta.dirname, "migrations");
  if (fs.existsSync(migrationsDir)) {
    const files = fs.readdirSync(migrationsDir)
      .filter(f => f.endsWith(".sql"))
      .sort();
    for (const file of files) {
      const sql = fs.readFileSync(path.join(migrationsDir, file), "utf-8");
      memDb.exec(sql);
    }
  }
  return memDb;
}

export function closeDb(): void {
  if (db) {
    db.close();
    (db as unknown) = undefined;
  }
}
```

- [ ] **Step 3: 创建 analysis-cache repository — src/db/repositories/analysis-cache.ts**

```typescript
import Database from "better-sqlite3";
import type { AnalysisCacheRecord, AnalysisResult } from "../../types/index.js";

export function findByCommit(
  db: Database.Database,
  owner: string,
  repo: string,
  branch: string,
  commitHash: string,
): AnalysisCacheRecord | undefined {
  return db.prepare(`
    SELECT * FROM analysis_cache
    WHERE owner = ? AND repo = ? AND branch = ? AND commit_hash = ?
  `).get(owner, repo, branch, commitHash) as AnalysisCacheRecord | undefined;
}

export function save(
  db: Database.Database,
  params: {
    owner: string;
    repo: string;
    branch: string;
    commitHash: string;
    result: AnalysisResult;
    projectTypePrimary: string;
    framework: string | null;
  },
): void {
  db.prepare(`
    INSERT OR REPLACE INTO analysis_cache
      (owner, repo, branch, commit_hash, result, project_type_primary, framework)
    VALUES (?, ?, ?, ?, ?, ?, ?)
  `).run(
    params.owner,
    params.repo,
    params.branch,
    params.commitHash,
    JSON.stringify(params.result),
    params.projectTypePrimary,
    params.framework,
  );
}
```

- [ ] **Step 4: 创建 experiences repository — src/db/repositories/experiences.ts**

```typescript
import Database from "better-sqlite3";
import type { ExperienceRecord } from "../../types/index.js";

export function findRelevant(
  db: Database.Database,
  primaryType: string,
  framework: string | null,
  secondaryTypes: string[],
  owner: string,
  limit = 5,
): ExperienceRecord[] {
  // 第一优先级：primary type + framework 完全匹配
  let rows: ExperienceRecord[];

  if (framework) {
    rows = db.prepare(`
      SELECT * FROM experiences
      WHERE primary_type = ? AND framework = ?
      ORDER BY created_at DESC
      LIMIT ?
    `).all(primaryType, framework, limit) as ExperienceRecord[];

    if (rows.length > 0) return rows;
  }

  // 第二优先级：primary type 匹配
  rows = db.prepare(`
    SELECT * FROM experiences
    WHERE primary_type = ?
    ORDER BY created_at DESC
    LIMIT ?
  `).all(primaryType, limit) as ExperienceRecord[];

  if (rows.length > 0) return rows;

  // 第三优先级：framework 匹配 + secondary type 有交集
  if (framework && secondaryTypes.length > 0) {
    rows = db.prepare(`
      SELECT * FROM experiences
      WHERE framework = ?
      ORDER BY created_at DESC
      LIMIT ?
    `).all(framework, limit * 2) as ExperienceRecord[];

    const matching = rows.filter(r => {
      try {
        const secondary: string[] = JSON.parse(r.secondaryType);
        return secondary.some(s => secondaryTypes.includes(s));
      } catch { return false; }
    });

    if (matching.length > 0) return matching.slice(0, limit);
  }

  // 第四优先级：owner 匹配（弱参考）
  if (owner) {
    rows = db.prepare(`
      SELECT * FROM experiences
      WHERE owner = ?
      ORDER BY created_at DESC
      LIMIT ?
    `).all(owner, limit) as ExperienceRecord[];
  }

  return rows;
}

export function save(
  db: Database.Database,
  params: {
    owner: string;
    repo: string;
    primaryType: string;
    framework: string | null;
    secondaryType: string[];
    content: string;
  },
): void {
  db.prepare(`
    INSERT INTO experiences (owner, repo, primary_type, framework, secondary_type, content)
    VALUES (?, ?, ?, ?, ?, ?)
  `).run(
    params.owner,
    params.repo,
    params.primaryType,
    params.framework,
    JSON.stringify(params.secondaryType),
    params.content,
  );
}
```

- [ ] **Step 5: 验证 DB 模块编译**

```bash
npx tsc --noEmit
```
Expected: 编译通过。

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: add database layer with migrations and repositories"
```

---

### Task 5: 工具库 — Schema 校验 & 沙箱

**Files:**
- Create: `src/lib/schema.ts`
- Create: `src/lib/sandbox.ts`
- Create: `tests/unit/schema.test.ts`
- Create: `tests/unit/sandbox.test.ts`

- [ ] **Step 1: 编写 schema 测试 — tests/unit/schema.test.ts**

```typescript
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
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
npx vitest run tests/unit/schema.test.ts
```
Expected: FAIL — 无法找到模块

- [ ] **Step 3: 创建 src/lib/schema.ts**

```typescript
import { z } from "zod";

// ===== Explorer Output =====

const projectTypeSchema = z.object({
  primary: z.enum(["library", "cli", "web-framework", "monorepo", "unknown"]),
  secondary: z.array(z.enum(["library", "cli", "web-framework", "monorepo", "unknown"])),
});

const techStackSchema = z.object({
  language: z.string(),
  framework: z.string().nullable(),
  buildTool: z.string(),
});

const entryPointSchema = z.object({
  file: z.string(),
  role: z.string(),
});

const moduleInfoSchema = z.object({
  path: z.string(),
  responsibility: z.string().max(100),
  importance: z.enum(["core", "support", "utility"]),
  justification: z.string(),
});

export const explorerOutputSchema = z.object({
  projectType: projectTypeSchema,
  techStack: techStackSchema,
  fileCount: z.number().int().nonnegative(),
  entryPoints: z.array(entryPointSchema).max(10),
  moduleMap: z.array(moduleInfoSchema).max(20),
  directorySummary: z.string().max(500),
  projectSummary: z.string().max(300),
});

export function validateExplorerOutput(data: unknown) {
  return explorerOutputSchema.parse(data);
}

// ===== Mentor Output =====

const readingStepSchema = z.object({
  step: z.number().int().positive(),
  file: z.string(),
  why: z.string(),
});

const keyPatternSchema = z.object({
  pattern: z.string(),
  where: z.string(),
  description: z.string(),
});

const codeConventionSchema = z.object({
  rule: z.string(),
  example: z.string(),
});

export const mentorOutputSchema = z.object({
  architectureOverview: z.string().max(800),
  dependencyGraph: z.record(z.string(), z.array(z.string())),
  readingPath: z.array(readingStepSchema).max(5),
  keyPatterns: z.array(keyPatternSchema),
  codeConventions: z.array(codeConventionSchema),
});

export function validateMentorOutput(data: unknown) {
  return mentorOutputSchema.parse(data);
}

// ===== Contributor Output =====

const goodFirstIssueSchema = z.object({
  area: z.string(),
  difficulty: z.enum(["easy", "medium", "hard"]),
  description: z.string(),
});

const contributionSetupSchema = z.object({
  devEnv: z.string(),
  build: z.string(),
  test: z.string(),
  lint: z.string().optional(),
});

const entryFileSchema = z.object({
  file: z.string(),
  description: z.string(),
  reason: z.string(),
});

const newcomerNoteSchema = z.object({
  tip: z.string(),
});

export const contributorOutputSchema = z.object({
  goodFirstIssues: z.array(goodFirstIssueSchema),
  contributionSetup: contributionSetupSchema,
  entryFiles: z.array(entryFileSchema),
  notesForNewcomers: z.array(newcomerNoteSchema),
});

export function validateContributorOutput(data: unknown) {
  return contributorOutputSchema.parse(data);
}

// ===== Commit Summary (Orchestrator 层生成，不需要 Agent 输出校验) =====

export const commitSummarySchema = z.object({
  frequentFiles: z.array(z.object({
    file: z.string(),
    commits: z.number(),
    recent: z.boolean(),
  })),
  recentThemes: z.array(z.string()),
  contributorCount: z.number().int().nonnegative(),
});
```

- [ ] **Step 4: 编写 sandbox 测试 — tests/unit/sandbox.test.ts**

```typescript
import { describe, it, expect } from "vitest";
import { resolvePath } from "../../src/lib/sandbox.js";
import path from "node:path";

describe("resolvePath", () => {
  const workDir = path.resolve("/tmp/task123");

  it("resolves a normal path within work dir", () => {
    const result = resolvePath(workDir, "src/index.ts");
    expect(result).toBe(path.resolve("/tmp/task123/src/index.ts"));
  });

  it("resolves nested path", () => {
    const result = resolvePath(workDir, "src/core/middleware.ts");
    expect(result).toBe(path.resolve("/tmp/task123/src/core/middleware.ts"));
  });

  it("throws on path traversal with ..", () => {
    expect(() => resolvePath(workDir, "../../etc/passwd")).toThrow("路径逃逸");
  });

  it("throws on absolute path outside work dir", () => {
    expect(() => resolvePath(workDir, "/etc/passwd")).toThrow("路径逃逸");
  });

  it("throws on deep traversal", () => {
    expect(() => resolvePath(workDir, "src/../../../var/log")).toThrow("路径逃逸");
  });

  it("resolves empty string to work dir", () => {
    const result = resolvePath(workDir, "");
    expect(result).toBe(workDir);
  });
});
```

- [ ] **Step 5: 运行 sandbox 测试，确认失败**

```bash
npx vitest run tests/unit/sandbox.test.ts
```
Expected: FAIL — 无法找到模块

- [ ] **Step 6: 创建 src/lib/sandbox.ts**

```typescript
import path from "node:path";

export class SandboxError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "SandboxError";
  }
}

export function resolvePath(taskWorkDir: string, requestedPath: string): string {
  const resolved = path.resolve(taskWorkDir, requestedPath);

  // 规范化后必须仍以前缀开头
  if (!resolved.startsWith(taskWorkDir.endsWith(path.sep) ? taskWorkDir : taskWorkDir + path.sep)
      && resolved !== taskWorkDir) {
    throw new SandboxError("路径逃逸，拒绝访问");
  }

  return resolved;
}
```

- [ ] **Step 7: 运行全部单元测试**

```bash
npx vitest run tests/unit/
```
Expected: 全部 PASS。

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: add schema validators and sandbox path protection"
```

---

### Task 6: 工具库 — 仓库操作

**Files:**
- Create: `src/lib/repo.ts`
- Create: `tests/unit/repo.test.ts`

- [ ] **Step 1: 编写 repo 测试 — tests/unit/repo.test.ts**

```typescript
import { describe, it, expect } from "vitest";
import {
  parseRepoUrl,
  isValidGithubUrl,
  getMaxRepoSizeKB,
} from "../../src/lib/repo.js";

describe("parseRepoUrl", () => {
  it("parses standard GitHub URL", () => {
    const result = parseRepoUrl("https://github.com/expressjs/express");
    expect(result).toEqual({ owner: "expressjs", repo: "express", isGitHub: true });
  });

  it("parses URL with .git suffix", () => {
    const result = parseRepoUrl("https://github.com/vuejs/core.git");
    expect(result).toEqual({ owner: "vuejs", repo: "core", isGitHub: true });
  });

  it("parses URL with tree subpath", () => {
    const result = parseRepoUrl("https://github.com/facebook/react/tree/main/packages");
    expect(result).toEqual({ owner: "facebook", repo: "react", isGitHub: true });
  });

  it("returns isGitHub false for non-GitHub URL", () => {
    const result = parseRepoUrl("https://gitlab.com/user/repo");
    expect(result).toEqual({ owner: "user", repo: "repo", isGitHub: false });
  });

  it("throws on invalid URL", () => {
    // parseRepoUrl 不抛错但返回空 owner — 由 isValidGithubUrl 判断
    const result = parseRepoUrl("not-a-url");
    expect(result.owner).toBe("");
  });

  it("parses SSH format", () => {
    const result = parseRepoUrl("git@github.com:user/repo.git");
    expect(result).toEqual({ owner: "user", repo: "repo", isGitHub: true });
  });
});

describe("isValidGithubUrl", () => {
  it("returns true for valid GitHub URL", () => {
    expect(isValidGithubUrl("https://github.com/expressjs/express")).toBe(true);
  });

  it("returns false for invalid URL", () => {
    expect(isValidGithubUrl("not-a-url")).toBe(false);
  });

  it("returns false for non-GitHub URL", () => {
    expect(isValidGithubUrl("https://gitlab.com/user/repo")).toBe(false);
  });
});

describe("getMaxRepoSizeKB", () => {
  it("returns config-based value", () => {
    expect(getMaxRepoSizeKB()).toBe(200 * 1024);
  });
});
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
npx vitest run tests/unit/repo.test.ts
```
Expected: FAIL

- [ ] **Step 3: 创建 src/lib/repo.ts**

```typescript
import simpleGit, { type SimpleGit } from "simple-git";
import fs from "node:fs/promises";
import path from "node:path";
import type { CommitSummary } from "../types/index.js";

// 懒加载：不在模块顶层引用 config，避免单元测试因 DEEPSEEK_API_KEY 缺失而崩溃
export function getMaxRepoSizeKB(): number {
  const mb = parseInt(process.env.MAX_REPO_SIZE_MB ?? "200", 10);
  return mb * 1024;
}

interface ParsedRepo {
  owner: string;
  repo: string;
  isGitHub: boolean;
}

export function parseRepoUrl(url: string): ParsedRepo {
  // SSH 格式: git@github.com:owner/repo.git
  const sshMatch = url.match(/^git@([^:]+):(.+?)(?:\.git)?$/);
  if (sshMatch) {
    const host = sshMatch[1]!;
    const parts = sshMatch[2]!.split("/");
    return {
      owner: parts[0] ?? "",
      repo: (parts[1] ?? "").replace(/\.git$/, ""),
      isGitHub: host === "github.com",
    };
  }

  // HTTPS 格式: https://github.com/owner/repo/...
  try {
    const u = new URL(url.replace(/\.git$/, ""));
    const parts = u.pathname.replace(/\/$/, "").split("/").filter(Boolean);
    return {
      owner: parts[0] ?? "",
      repo: (parts[1] ?? "").replace(/\.git$/, ""),
      isGitHub: u.hostname === "github.com",
    };
  } catch {
    return { owner: "", repo: "", isGitHub: false };
  }
}

export function isValidGithubUrl(url: string): boolean {
  const parsed = parseRepoUrl(url);
  return parsed.isGitHub && parsed.owner !== "" && parsed.repo !== "";
}

export async function fetchRepoSize(owner: string, repo: string): Promise<number | null> {
  try {
    const response = await fetch(`https://api.github.com/repos/${owner}/${repo}`, {
      headers: { Accept: "application/vnd.github+json" },
    });

    if (!response.ok) return null;

    const data = await response.json() as { size?: number };
    return data.size ?? null; // size 单位: KB
  } catch {
    return null; // API 不可达时返回 null，调用方应降级为直接 clone
  }
}

export async function cloneRepo(
  url: string,
  taskDir: string,
): Promise<{ localPath: string; git: SimpleGit; commitHash: string }> {
  await fs.mkdir(taskDir, { recursive: true });
  const git = simpleGit();

  await git.clone(url, taskDir, {
    "--depth": String(parseInt(process.env.CLONE_DEPTH ?? "1", 10)),
    "--single-branch": null,
  });

  const taskGit = simpleGit(taskDir);
  const commitHash = await taskGit.revparse(["HEAD"]);

  return { localPath: taskDir, git: taskGit, commitHash };
}

export async function getFileCount(localPath: string): Promise<number> {
  const git = simpleGit(localPath);
  try {
    const result = await git.raw(["ls-files"]);
    return result.split("\n").filter(Boolean).length;
  } catch {
    return 0;
  }
}

export async function extractCommitSummary(localPath: string): Promise<CommitSummary> {
  const git = simpleGit(localPath);

  try {
    const log = await git.log({ maxCount: 50 });

    // 统计文件变更频率
    const fileFrequency = new Map<string, number>();
    for (const commit of log.all) {
      // 用 diff-tree 获取每个 commit 的变更文件（性能优于遍历 log.diff）
      // 这里用 --name-only 格式
      if (commit.diff) {
        const files = commit.diff.files?.map(f => f.file) ?? [];
        for (const file of files) {
          fileFrequency.set(file, (fileFrequency.get(file) ?? 0) + 1);
        }
      }
    }

    const frequentFiles = Array.from(fileFrequency.entries())
      .sort(([, a], [, b]) => b - a)
      .slice(0, 20)
      .map(([file, commits]) => ({
        file,
        commits,
        recent: true, // 都在 50 条以内，都算 recent
      }));

    // 提取 commit 主题
    const themes = log.all
      .map(c => c.message.split("\n")[0] ?? "")
      .filter(Boolean)
      .slice(0, 50);

    // 统计 contributor 数量
    const contributors = new Set(log.all.map(c => c.author_email));

    return {
      frequentFiles,
      recentThemes: themes,
      contributorCount: contributors.size,
    };
  } catch {
    return {
      frequentFiles: [],
      recentThemes: [],
      contributorCount: 0,
    };
  }
}

export async function cleanup(localPath: string): Promise<void> {
  try {
    await fs.rm(localPath, { recursive: true, force: true });
  } catch {
    // 清理失败不阻塞
  }
}
```

- [ ] **Step 4: 运行测试**

```bash
npx vitest run tests/unit/repo.test.ts
```
Expected: PASS（parseRepoUrl 和 isValidGithubUrl 测试通过）。

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add repo operations (parse, clone, commit summary, cleanup)"
```

---

### Task 7: SSE 模块

**Files:**
- Create: `src/lib/sse.ts`

- [ ] **Step 1: 创建 src/lib/sse.ts**

```typescript
import { EventEmitter } from "node:events";
import type { SSEEvent } from "../types/index.js";

export class SSEManager {
  private emitters = new Map<string, EventEmitter>();

  subscribe(taskId: string): EventEmitter {
    const existing = this.emitters.get(taskId);
    if (existing) return existing;

    const emitter = new EventEmitter();
    emitter.setMaxListeners(50);
    this.emitters.set(taskId, emitter);
    return emitter;
  }

  unsubscribe(taskId: string): void {
    const emitter = this.emitters.get(taskId);
    if (emitter) {
      emitter.removeAllListeners();
      this.emitters.delete(taskId);
    }
  }

  emit(taskId: string, event: SSEEvent): void {
    const emitter = this.emitters.get(taskId);
    if (emitter) {
      emitter.emit("event", event);
    }
  }

  /** 检查某任务是否有活跃的 SSE 连接 */
  hasSubscribers(taskId: string): boolean {
    const emitter = this.emitters.get(taskId);
    return emitter !== undefined && emitter.listenerCount("event") > 0;
  }

  /** 序列化 SSE event 为标准文本 */
  static serialize(event: SSEEvent): string {
    const data = JSON.stringify(event);
    return `event: ${event.type}\ndata: ${data}\n\n`;
  }
}

// 全局单例
export const sseManager = new SSEManager();
```

- [ ] **Step 2: 验证编译**

```bash
npx tsc --noEmit
```
Expected: 编译通过。

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "feat: add SSE event manager"
```

---

### Task 8: Agent 定义文件

**Files:**
- Create: `agents/explorer/agent.md`
- Create: `agents/mentor/agent.md`
- Create: `agents/contributor/agent.md`

- [ ] **Step 1: 创建 agents/explorer/agent.md**

```markdown
---
name: explorer
description: 探索已克隆的仓库目录结构，产出项目类型、技术栈、模块清单。Pipeline Stage 1。仅在被 Orchestrator 调用时触发，不独立工作。
tools:
  - Read
  - Glob
  - Grep
model: deepseek-v4-pro
---

# Explorer - 仓库探索者

## 职责

你是 Pipeline 的第一阶段。目标：**快速产出仓库的顶层结构画像，不深入源码细节。**
你接收的是一个已经克隆好的本地路径——不需要 clone 也不执行任何 shell 命令。

## 输入

从 Orchestrator 接收：
- `localPath`: 已克隆仓库的本地绝对路径
- `fileCount`: 预计算的文件总数
- `projectTypeHint?`: 用户可选的类型提示

## 工作流程

1. **判断规模**: 根据 fileCount 选择分析深度
2. **元信息收集**:
   - 读取 package.json（或等效的项目元数据文件：pyproject.toml, go.mod, Cargo.toml 等）
   - 读取 README 前 200 行
   - Glob 扫描顶层目录结构
3. **项目类型识别**: 根据依赖、目录结构、入口字段判断，primary 为一个主类型，secondary 为次要类型的数组
4. **模块划分**: 基于目录结构划分模块，不要分析源码内容
5. **自我校验**: 输出前严格检查 JSON Schema

## 规模策略

- 小型仓库 (<500 files): 全量扫描，token 预算 8K
- 中型仓库 (500-2000 files): 只扫描 2 层目录深度，token 预算 4K
- 大型仓库 (>2000 files): 仅分析元数据文件，token 预算 2K

## 输出格式

严格返回以下 JSON，不要包含 markdown 代码块标记，不要包含额外字段：

```json
{
  "projectType": {
    "primary": "web-framework",
    "secondary": []
  },
  "techStack": {
    "language": "typescript",
    "framework": "express",
    "buildTool": "tsup"
  },
  "fileCount": 342,
  "entryPoints": [
    {"file": "src/index.ts", "role": "主入口"}
  ],
  "moduleMap": [
    {
      "path": "src/core/",
      "responsibility": "核心引擎，包含请求生命周期",
      "importance": "core",
      "justification": "含 index.ts 入口，所有请求必经此处"
    }
  ],
  "directorySummary": "项目分为 core/ cli/ utils/ 三个顶层模块，其中 core/ 为核心引擎，cli/ 提供命令行入口",
  "projectSummary": "一个轻量级 Web 框架，专注于路由和中间件"
}
```

## 约束

- moduleMap 最多 20 项，每项 responsibility 最多 100 字
- entryPoints 最多 10 项
- 不要阅读 src/ 下的业务代码文件
- 不要追踪 import/require 关系
- projectType.primary 必须是以下之一: library, cli, web-framework, monorepo, unknown
- 如果无法确定某字段，使用 null 或空数组 []，不要编造
```

- [ ] **Step 2: 创建 agents/mentor/agent.md**

```markdown
---
name: mentor
description: 基于 Explorer 产出深入解读架构，生成学习路径。Pipeline Stage 2。仅在被 Orchestrator 调用时触发。
tools:
  - Read
  - Grep
  - WebSearch
model: deepseek-v4-pro
---

# Mentor - 学习导师

## 职责

基于 Stage 1 的结构化输出，深入解读项目架构，生成适合新手的学习路线。

## 输入

从 Orchestrator 接收：
- `explorerOutput`: Explorer 阶段的完整 JSON 输出
- `skillContent`: 匹配到的分析策略 Skill 模板内容（已注入到下方 system prompt 末尾）
- `experiences`: 相关历史分析经验的文本摘要（已注入到下方 system prompt 末尾，可能为空）
- `userFocus?`: 用户关注的特定模块或方向

## 工作流程

1. 阅读 entryPoints 指向的文件（每个文件前 150 行即可）
2. 阅读 moduleMap 中标记为 "core" 的模块入口文件
3. 通过 Grep 搜索 import/require 语句，分析模块间依赖关系
4. 对照 system prompt 中注入的 Skill 模板，生成架构解读
5. 生成推荐阅读路径（不超过 5 步）
6. 识别代码设计模式和命名/格式规范

## 输出格式

严格返回以下 JSON，不要包含 markdown 代码块标记：

```json
{
  "architectureOverview": "markdown 格式的架构描述，不超过 800 字",
  "dependencyGraph": {
    "src/core/": ["src/utils/", "src/types/"],
    "src/cli/": ["src/core/"]
  },
  "readingPath": [
    {"step": 1, "file": "src/index.ts", "why": "从这里看到应用启动和配置加载全流程"}
  ],
  "keyPatterns": [
    {"pattern": "中间件链", "where": "src/core/middleware/", "description": "使用洋葱模型组织中间件，每个中间件是 async 函数"}
  ],
  "codeConventions": [
    {"rule": "所有公共 API 导出带有 JSDoc 注释", "example": "src/core/app.ts:45"}
  ]
}
```

## 约束

- architectureOverview 不超过 800 字
- readingPath 最多 5 步
- 依赖关系图只包含模块目录级别，不需要精确到单个文件
```

- [ ] **Step 3: 创建 agents/contributor/agent.md**

```markdown
---
name: contributor
description: 基于前两阶段产出和 commit 摘要，分析贡献机会和入手路径。Pipeline Stage 3。仅在被 Orchestrator 调用时触发。
tools:
  - Read
  - Grep
model: deepseek-v4-pro
---

# Contributor - 贡献顾问

## 职责

找到适合新手入门的具体任务点，生成第一次贡献的完整路径指南。
你不需要执行 git 命令——所有 Git 数据已由 Orchestrator 预提取并结构化传入。

## 输入

从 Orchestrator 接收：
- `explorerOutput`: Stage 1 的完整 JSON 输出
- `mentorOutput`: Stage 2 的完整 JSON 输出
- `commitSummary`: 近 50 条 commit 的结构化摘要

```json
{
  "frequentFiles": [
    {"file": "src/core/middleware.ts", "commits": 12, "recent": true}
  ],
  "recentThemes": ["性能优化", "TypeScript 严格模式迁移"],
  "contributorCount": 5
}
```

## 工作流程

1. 分析 commitSummary 中变更最频繁的文件区域
2. 通过 Grep 搜索代码中的 TODO/FIXME/HACK/XXX 标记
3. 如果存在 CONTRIBUTING.md，阅读它
4. 根据前两阶段的分析，识别结构独立、功能内聚、有测试覆盖的模块 → goodFirstIssues
5. 从 package.json 的 scripts 字段推断开发环境搭建步骤

## 输出格式

严格返回以下 JSON，不要包含 markdown 代码块标记：

```json
{
  "goodFirstIssues": [
    {
      "area": "文档补全",
      "difficulty": "easy",
      "description": "src/utils/ 下有 3 个导出函数缺少 JSDoc 注释，可以补充文档"
    }
  ],
  "contributionSetup": {
    "devEnv": "Node.js 18+, pnpm install",
    "build": "pnpm build",
    "test": "pnpm test",
    "lint": "pnpm lint"
  },
  "entryFiles": [
    {
      "file": "src/index.ts",
      "description": "应用入口文件，从这里了解启动流程",
      "reason": "所有请求路径的起点"
    }
  ],
  "notesForNewcomers": [
    {"tip": "项目使用 Conventional Commits 规范提交代码"}
  ]
}
```

## 约束

- difficulty 必须是 easy, medium, hard 之一
- 如果没有找到特定内容（如 CONTRIBUTING.md），对应输出可以为空数组
```

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat: add Explorer, Mentor, and Contributor agent definitions"
```

---

### Task 9: Skill 模板

**Files:**
- Create: `skills/analyze-web-framework/SKILL.md`
- Create: `skills/analyze-cli-tool/SKILL.md`
- Create: `skills/analyze-monorepo/SKILL.md`
- Create: `skills/analyze-generic/SKILL.md`

- [ ] **Step 1: 创建 skills/analyze-web-framework/SKILL.md**

```markdown
---
name: analyze-web-framework
description: 分析 Web 框架类开源项目的策略模板
---

# Web 框架分析策略

## 关注重点

- 路由定义方式与请求生命周期
- 中间件/插件机制的设计模式
- 配置加载与启动流程
- 模板引擎和响应渲染机制
- 扩展点：如何添加自定义中间件/插件

## 推荐探索路径

1. 找到应用入口：查看 package.json 的 main/bin 字段，或启动脚本
2. 理解路由系统：搜索 router、routes 关键词，理解 URL → handler 的映射
3. 跟踪请求生命周期：从监听端口 → 路由匹配 → 中间件链 → 响应
4. 学习配置机制：查看 config/ 目录或配置文件加载方式
5. 了解扩展机制：如何编写和注册插件/中间件

## 常见架构模式

- 洋葱模型（中间件层层包裹）
- 工厂模式创建应用实例
- 观察者模式用于生命周期钩子
```

- [ ] **Step 2: 创建 skills/analyze-cli-tool/SKILL.md**

```markdown
---
name: analyze-cli-tool
description: 分析 CLI 命令行工具类项目的策略模板
---

# CLI 工具分析策略

## 关注重点

- 命令行入口点与参数解析方式
- 命令（子命令）注册与路由机制
- CLI 与核心功能库的边界划分
- 输出格式化与日志级别控制
- 配置文件的读取（.rc 文件、环境变量等）

## 推荐探索路径

1. 找 CLI 入口：package.json 的 bin 字段指向的文件
2. 理解参数解析：搜索 commander、yargs、argparse 或手写解析器
3. 拆解命令树：理解主命令 → 子命令的注册方式
4. 追踪核心逻辑：子命令如何调用核心库完成实际工作
5. 了解输出机制：console.log vs 结构化日志，--json 等输出格式选项

## 常见架构模式

- 命令模式（每个子命令独立处理）
- "薄 CLI + 胖核心" 分层（CLI 只负责解析和格式化）
- 管道模式（多个处理步骤串联）
```

- [ ] **Step 3: 创建 skills/analyze-monorepo/SKILL.md**

```markdown
---
name: analyze-monorepo
description: 分析 Monorepo 多包管理类项目的策略模板
---

# Monorepo 分析策略

## 关注重点

- 包之间的依赖关系图
- 共享配置与工具链
- 包发布流程与版本管理
- 工作区（workspaces）组织方式
- 构建系统与任务编排

## 推荐探索路径

1. 理解包结构：查看 packages/ 或 apps/ 目录下的子包列表
2. 读取根配置：pnpm-workspace.yaml, lerna.json, turbo.json 等
3. 绘制依赖图：各 package.json 之间的相互引用关系
4. 找到共享包：shared/, common/, utils/ 等被多个包引用的模块
5. 了解 CI/CD：查看测试、构建、发布的流水线配置

## 常见架构模式

- 分层架构（applications → libraries → shared）
- 依赖注入（通过 workspace 协议引用）
- 统一构建（Turborepo/Nx 的任务编排）
```

- [ ] **Step 4: 创建 skills/analyze-generic/SKILL.md**

```markdown
---
name: analyze-generic
description: 通用项目分析策略（兜底模板，适用于任何类型的项目）
---

# 通用项目分析策略

## 关注重点

- 项目入口文件与启动流程
- 顶层目录结构反映的模块划分
- 依赖关系（重点关注核心依赖）
- README 中提到的关键概念
- 构建、测试、部署相关配置

## 推荐探索路径

1. 阅读 README 了解项目目的和基本使用方式
2. 查看 package.json（或等效文件）的入口、脚本和依赖
3. 浏览顶层目录结构，理解模块组织方式
4. 阅读主入口文件的前 100 行，了解程序启动流程
5. 检查 CONTRIBUTING.md 了解开发流程

## 常见架构模式

由于项目类型未知，请根据实际代码特征自行归纳架构模式，重点关注：
- 模块之间的依赖方向（是否单向？是否有循环？）
- 核心抽象（接口/基类）被哪些模块实现/继承
- 数据流向（输入 → 处理 → 输出 的路径）
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add four Skill analysis templates"
```

---

### Task 10: Claude Agent SDK 封装

**Files:**
- Create: `src/services/claude-client.ts`

- [ ] **Step 1: 创建 src/services/claude-client.ts**

```typescript
/**
 * Claude Agent SDK 封装
 *
 * 职责：通过 Claude Agent SDK 的 Agent 工具调用 Subagent，
 * 输入结构化数据，流式监听输出，逐字段推送给回调。
 *
 * 因为使用 deepseek-v4-pro，通过 SDK 的 base_url + api_key 配置指向 DeepSeek API。
 */

import type { StageName, ExplorerOutput, MentorOutput, ContributorOutput } from "../types/index.js";
import { validateExplorerOutput, validateMentorOutput, validateContributorOutput } from "../lib/schema.js";
import { config } from "../config.js";

// ========== 超时配置 ==========

const STAGE_TIMEOUT_MS: Record<StageName, number> = {
  explorer: 120_000,
  mentor: 300_000,
  contributor: 180_000,
};

// ========== 类型 ==========

export interface StageCallbacks {
  onProgress: (message: string) => void;
  onField: (field: string, value: unknown) => void;
}

export type StageOutputFor<S extends StageName> =
  S extends "explorer" ? ExplorerOutput :
  S extends "mentor" ? MentorOutput :
  S extends "contributor" ? ContributorOutput :
  never;

// ========== Subagent 定义路径 ==========

const AGENT_PATHS: Record<StageName, string> = {
  explorer: "agents/explorer/agent.md",
  mentor: "agents/mentor/agent.md",
  contributor: "agents/contributor/agent.md",
};

const VALIDATORS = {
  explorer: validateExplorerOutput,
  mentor: validateMentorOutput,
  contributor: validateContributorOutput,
} as const;

// ========== Agent SDK 调用 ==========

export class LLMError extends Error {
  retryable: boolean;
  constructor(message: string, retryable: boolean) {
    super(message);
    this.name = "LLMError";
    this.retryable = retryable;
  }
}

export class ParseError extends Error {
  rawOutput: string;
  constructor(message: string, rawOutput: string) {
    super(message);
    this.name = "ParseError";
    this.rawOutput = rawOutput;
  }
}

/**
 * 运行一个 Pipeline 阶段。
 *
 * 通过 Claude Agent SDK 调用指定 Subagent。
 * 实际实现取决于 SDK 的具体 API —— 以下提供完整骨架，
 * 需要根据实际 SDK 版本微调。
 */
export async function runStage<S extends StageName>(
  stage: S,
  input: Record<string, unknown>,
  callbacks: StageCallbacks,
): Promise<StageOutputFor<S>> {
  const timeout = STAGE_TIMEOUT_MS[stage];
  const validator = VALIDATORS[stage];

  // TODO: 实际调用需要根据 Claude Agent SDK 的 API 具体调整。
  // SDK 的核心模式是：
  //   1. 创建 Subagent（加载 agent.md 定义）
  //   2. 传入结构化 input 作为初始消息
  //   3. 流式监听 Agent 的工具调用和文本输出
  //   4. Agent 完成后解析输出 JSON
  //
  // 导入 SDK: import { ClaudeSDK } from "@anthropic-ai/claude-agent-sdk";
  //
  // const sdk = new ClaudeSDK({
  //   apiKey: config.DEEPSEEK_API_KEY,
  //   baseUrl: config.DEEPSEEK_BASE_URL,
  // });
  //
  // const agent = await sdk.createAgent({
  //   definition: AGENT_PATHS[stage],
  //   model: "deepseek-v4-pro",
  //   systemPrompt: buildSystemPrompt(stage, input),
  //   maxTokens: stage === "mentor" ? 16000 : 8000,
  // });
  //
  // 以下为骨架实现

  callbacks.onProgress(`正在启动 ${stage} 阶段...`);

  // 1. 调用 Agent（骨架 —— 替换为实际 SDK 调用）
  const rawOutput = await invokeAgent(stage, input, timeout, callbacks);

  // 2. 解析 & 校验
  let parsed: unknown;
  try {
    parsed = extractJSON(rawOutput);
  } catch {
    throw new ParseError("Agent 输出不是有效的 JSON", rawOutput);
  }

  const result = validator(parsed);

  return result as StageOutputFor<S>;
}

/**
 * 调用 Subagent（骨架实现 — 等待 SDK API 确定后替换）
 */
async function invokeAgent(
  stage: StageName,
  input: Record<string, unknown>,
  timeoutMs: number,
  callbacks: StageCallbacks,
): Promise<string> {
  // 骨架：模拟一个基本的 SDK 调用流程
  // 实际实现会用 SDK 的事件循环监听 Agent 操作

  const inputStr = JSON.stringify(input, null, 2);

  // 构建 prompt
  const prompt = buildPromptForStage(stage, inputStr);

  // 调用 SDK 的 Agent API
  // 实际: sdk.agent.run({ definition, prompt, ... })
  // 这里提供骨架结构

  callbacks.onProgress("正在扫描仓库结构...");

  // ========== 实际 SDK 调用示例（待验证） ==========
  /*
  const { ClaudeSDK } = await import("@anthropic-ai/claude-agent-sdk");

  const sdk = new ClaudeSDK({
    apiKey: config.DEEPSEEK_API_KEY,
    baseURL: config.DEEPSEEK_BASE_URL,
  });

  const result = await sdk.agent({
    model: "deepseek-v4-pro",
    definition: AGENT_PATHS[stage],
    prompt: prompt,
    maxTokens: stage === "mentor" ? 16000 : 8000,
    timeout: timeoutMs,
    tools: getToolsForStage(stage),
    onProgress: (msg) => callbacks.onProgress(msg),
  });

  return result.content;
  */

  throw new Error(
    `Claude Agent SDK 调用尚未实现。请根据 SDK 实际 API 调整 invokeAgent 函数。` +
    `\nStage: ${stage}\nInput keys: ${Object.keys(input).join(", ")}`
  );
}

function buildPromptForStage(stage: StageName, inputStr: string): string {
  switch (stage) {
    case "explorer":
      return `请分析以下已克隆的仓库，输出结构化的项目分析报告。

输入数据：
${inputStr}

请严格按照你的 Agent 定义中规定的 JSON Schema 输出，不要包含 markdown 代码块标记。`;

    case "mentor":
      return `请基于 Explorer 阶段的输出，深入分析项目架构，生成学习路径。

输入数据：
${inputStr}

请严格按照你的 Agent 定义中规定的 JSON Schema 输出。`;

    case "contributor":
      return `请基于前两个阶段的分析，找到适合新手参与贡献的切入点。

输入数据：
${inputStr}

请严格按照你的 Agent 定义中规定的 JSON Schema 输出。`;

    default:
      return `请输出符合 Schema 的 JSON。输入:\n${inputStr}`;
  }
}

/**
 * 从 Agent 输出中提取 JSON 对象
 */
export function extractJSON(text: string): unknown {
  // 尝试直接解析
  try {
    return JSON.parse(text.trim());
  } catch { /* 继续 */ }

  // 尝试提取 ```json ... ``` 代码块
  const codeBlock = text.match(/```(?:json)?\s*\n?([\s\S]*?)```/);
  if (codeBlock?.[1]) {
    try {
      return JSON.parse(codeBlock[1].trim());
    } catch { /* 继续 */ }
  }

  // 尝试提取 { ... } 最外层
  const firstBrace = text.indexOf("{");
  const lastBrace = text.lastIndexOf("}");
  if (firstBrace !== -1 && lastBrace !== -1 && lastBrace > firstBrace) {
    try {
      return JSON.parse(text.slice(firstBrace, lastBrace + 1));
    } catch { /* 继续 */ }
  }

  throw new Error("无法从输出中提取 JSON");
}
```

- [ ] **Step 2: 验证编译**

```bash
npx tsc --noEmit
```
Expected: 编译通过（`import.meta.dirname` 可能需要 `nodenext` 模块解析 — 如报错则调整 tsconfig）

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "feat: add Claude Agent SDK wrapper with retry and validation logic"
```

---

### Task 11: Pipeline 状态机

**Files:**
- Create: `src/services/pipeline.ts`

- [ ] **Step 1: 创建 src/services/pipeline.ts**

```typescript
import type {
  StageName,
  StageProgress,
  ExplorerOutput,
  MentorOutput,
  ContributorOutput,
  AnalysisResult,
  TaskError,
  CommitSummary,
} from "../types/index.js";
import { runStage, ParseError, LLMError } from "./claude-client.js";
import type { StageOutputFor, StageCallbacks } from "./claude-client.js";
import { sseManager } from "../lib/sse.js";
import { cloneRepo, getFileCount, extractCommitSummary, cleanup } from "../lib/repo.js";
import { parseRepoUrl } from "../lib/repo.js";
import { config } from "../config.js";
import fs from "node:fs";

export interface PipelineContext {
  taskId: string;
  repoUrl: string;
  branch: string;
  stageProgress: StageProgress;
  callbacks: PipelineLifecycleCallbacks;  // ← 生命周期回调，与单阶段 StageCallbacks 不同
  // 交互相关
  pendingQuestion: {
    questionId: string;
    resolve: (answer: string) => void;
    timer: ReturnType<typeof setTimeout>;
  } | null;
}

export interface PipelineLifecycleCallbacks {
  onStageStart: (stage: StageName) => void;
  onStageDone: (stage: StageName) => void;
}

export interface PipelineResult {
  result: AnalysisResult;
  cached: boolean;
}

/**
 * 执行完整 Pipeline: Explorer → Mentor → Contributor
 */
export async function executePipeline(ctx: PipelineContext): Promise<PipelineResult> {
  const taskDir = `tmp/${ctx.taskId}`;
  let localPath: string | undefined;

  try {
    // 0. Clone
    sseManager.emit(ctx.taskId, {
      type: "task:created",
      taskId: ctx.taskId,
      status: "cloning",
    });

    const { localPath: lp, commitHash } = await cloneRepo(ctx.repoUrl, taskDir);
    localPath = lp;

    const fileCount = await getFileCount(localPath);

    // 标记进入 analyzing
    sseManager.emit(ctx.taskId, {
      type: "task:created",
      taskId: ctx.taskId,
      status: "analyzing",
    });

    // 1. Explorer
    const explorerOutput = await runStageWithRetry("explorer", {
      localPath,
      fileCount,
    }, ctx);

    // 交互点：项目类型确认
    const typeAnswer = await askUser(
      ctx,
      "q_type",
      "explorer",
      `识别为 ${explorerOutput.projectType.primary}，是否正确？`,
      ["是", "否，请纠正"],
    );

    // 交互点：模块划分确认
    await askUser(
      ctx,
      "q_modules",
      "explorer",
      `模块划分完成：${explorerOutput.moduleMap.map(m => m.path).join(", ")}。是否合理？`,
      ["合理，继续", "需要调整"],
    );

    // 2. Mentor
    // 加载 Skill 模板
    const skillContent = loadSkillTemplate(explorerOutput.projectType.primary);

    // 加载历史经验
    // const experiences = await loadExperiences(explorerOutput); // DB 接入时启用

    const mentorOutput = await runStageWithRetry("mentor", {
      explorerOutput,
      skillContent,
      experiences: "", // 经验文本注入点
    }, ctx);

    // 交互点：依赖图反馈
    const deps = Object.entries(mentorOutput.dependencyGraph);
    await askUser(
      ctx,
      "q_deps",
      "mentor",
      `依赖图包含 ${deps.length} 个模块。想深入了解哪个模块？`,
      deps.slice(0, 5).map(([mod]) => mod),
    );

    // 3. Contributor
    const commitSummary = await extractCommitSummary(localPath);

    const contributorOutput = await runStageWithRetry("contributor", {
      explorerOutput,
      mentorOutput,
      commitSummary,
    }, ctx);

    const analysisResult: AnalysisResult = {
      explorer: explorerOutput,
      mentor: mentorOutput,
      contributor: contributorOutput,
    };

    return { result: analysisResult, cached: false };
  } finally {
    if (localPath) {
      await cleanup(localPath);
    }
  }
}

/**
 * 带重试的阶段执行
 */
async function runStageWithRetry<S extends StageName>(
  stage: S,
  input: Record<string, unknown>,
  ctx: PipelineContext,
): Promise<StageOutputFor<S>> {
  const maxRetries = 1;

  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      return await runStageWithSSE(stage, input, ctx);
    } catch (err) {
      if (err instanceof ParseError) {
        if (attempt < maxRetries) {
          // parse_failed 可重试
          sseManager.emit(ctx.taskId, {
            type: "stage:progress",
            stage,
            message: `JSON 解析失败，重试中... (${attempt + 1}/${maxRetries})`,
          });
          continue;
        }
        // 重试耗尽
        const taskError: TaskError = {
          category: "parse_failed",
          message: `Agent 输出解析失败（已重试 ${maxRetries} 次）: ${err.message}`,
          retryable: true,
        };
        emitError(ctx.taskId, taskError);
        throw taskError;
      }

      if (err instanceof LLMError) {
        if (attempt < maxRetries && err.retryable) {
          sseManager.emit(ctx.taskId, {
            type: "stage:progress",
            stage,
            message: `LLM 调用失败，重试中... (${attempt + 1}/${maxRetries})`,
          });
          continue;
        }
        const taskError: TaskError = {
          category: "llm_failed",
          message: err.message,
          retryable: false,
        };
        emitError(ctx.taskId, taskError);
        throw taskError;
      }

      // 未知错误
      const taskError: TaskError = {
        category: "internal",
        message: err instanceof Error ? err.message : String(err),
        retryable: false,
      };
      emitError(ctx.taskId, taskError);
      throw taskError;
    }
  }

  throw new Error("unreachable");
}

async function runStageWithSSE<S extends StageName>(
  stage: S,
  input: Record<string, unknown>,
  ctx: PipelineContext,
): Promise<StageOutputFor<S>> {
  ctx.callbacks.onStageStart(stage);  // 通知 Orchestrator 更新 currentStage
  sseManager.emit(ctx.taskId, { type: "stage:start", stage });

  const result = await runStage(stage, input, {
    onProgress: (message: string) => {
      sseManager.emit(ctx.taskId, { type: "stage:progress", stage, message });
    },
    onField: (field: string, value: unknown) => {
      sseManager.emit(ctx.taskId, { type: "stage:field", stage, field, value });
    },
  });

  ctx.callbacks.onStageDone(stage);   // 通知 Orchestrator 更新 stageProgress
  sseManager.emit(ctx.taskId, { type: "stage:done", stage, output: result });

  return result;
}

// ========== 交互辅助 ==========

function askUser(
  ctx: PipelineContext,
  questionId: string,
  stage: StageName,
  question: string,
  options: string[],
): Promise<string> {
  return new Promise<string>((resolve) => {
    sseManager.emit(ctx.taskId, {
      type: "interact:ask",
      questionId,
      stage,
      question,
      options,
    });

    const timer = setTimeout(() => {
      sseManager.emit(ctx.taskId, {
        type: "interact:timeout",
        questionId,
      });
      ctx.pendingQuestion = null;
      resolve(""); // 超时默认空答案
    }, config.INTERACTION_TIMEOUT_MS);

    ctx.pendingQuestion = { questionId, resolve, timer };
  });
}

/**
 * 用户通过 POST /analysis/:id/ask 响应交互问题时调用
 */
export function resolveQuestion(ctx: PipelineContext, questionId: string, answer: string): boolean {
  if (!ctx.pendingQuestion || ctx.pendingQuestion.questionId !== questionId) {
    return false;
  }
  clearTimeout(ctx.pendingQuestion.timer);
  ctx.pendingQuestion.resolve(answer);
  ctx.pendingQuestion = null;
  return true;
}

// ========== Skill 模板加载 ==========

const SKILL_TEMPLATES: Record<string, string> = {
  "web-framework": "skills/analyze-web-framework/SKILL.md",
  "cli": "skills/analyze-cli-tool/SKILL.md",
  "monorepo": "skills/analyze-monorepo/SKILL.md",
  "library": "skills/analyze-generic/SKILL.md",
  "unknown": "skills/analyze-generic/SKILL.md",
};

function loadSkillTemplate(primaryType: string): string {
  const filePath = SKILL_TEMPLATES[primaryType] ?? SKILL_TEMPLATES["unknown"]!;
  try {
    const content = fs.readFileSync(filePath, "utf-8");
    return content;
  } catch {
    return ""; // 模板文件缺失时降级为空字符串
  }
}

// ========== 错误广播 ==========

function emitError(taskId: string, error: TaskError): void {
  sseManager.emit(taskId, { type: "task:error", taskId, error });
}
```

- [ ] **Step 2: 验证编译**

```bash
npx tsc --noEmit
```
Expected: 编译通过。

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "feat: add Pipeline state machine with retry, interaction, and SSE integration"
```

---

### Task 12: Orchestrator & 路由

**Files:**
- Create: `src/services/orchestrator.ts`
- Create: `src/routes/analysis.ts`
- Create: `src/routes/stream.ts`
- Create: `src/index.ts`

- [ ] **Step 1: 创建 src/services/orchestrator.ts**

```typescript
import type {
  TaskRecord,
  TaskStatus,
  StageProgress,
  AnalysisResult,
  TaskError,
  GetAnalysisResponse,
} from "../types/index.js";
import { executePipeline, resolveQuestion, type PipelineContext } from "./pipeline.js";
import { sseManager } from "../lib/sse.js";
import { parseRepoUrl, isValidGithubUrl } from "../lib/repo.js";
import { config } from "../config.js";

// ========== 内存任务存储 ==========

const tasks = new Map<string, TaskRecord>();
const pipelineContexts = new Map<string, PipelineContext>();

function makeTaskId(): string {
  const ts = Date.now().toString(36);
  const rand = Math.random().toString(36).slice(2, 8);
  return `task_${ts}_${rand}`;
}

function defaultProgress(): StageProgress {
  return { explorer: "pending", mentor: "pending", contributor: "pending" };
}

// ========== Orchestrator ==========

export async function createTask(repoUrl: string, branch = "main"): Promise<TaskRecord> {
  // 校验 URL
  if (!isValidGithubUrl(repoUrl)) {
    throw new OrchestratorError("invalid_repo_url", "不是有效的 GitHub 仓库地址", false);
  }

  const taskId = makeTaskId();
  const task: TaskRecord = {
    taskId,
    repoUrl,
    branch,
    status: "cloning",
    currentStage: null,
    stageProgress: defaultProgress(),
    result: null,
    error: null,
    commitHash: null,
    cached: false,
    createdAt: new Date().toISOString(),
    completedAt: null,
  };

  tasks.set(taskId, task);

  // 创建 Pipeline 上下文
  const ctx: PipelineContext = {
    taskId,
    repoUrl,
    branch,
    stageProgress: task.stageProgress,
    callbacks: {
      onStageStart: (stage) => {
        const rec = tasks.get(taskId);
        if (rec) {
          rec.currentStage = stage;
          rec.stageProgress[stage] = "running";
        }
      },
      onStageDone: (stage) => {
        const rec = tasks.get(taskId);
        if (rec) rec.stageProgress[stage] = "done";
      },
    },
    pendingQuestion: null,
  };

  pipelineContexts.set(taskId, ctx);

  // 异步执行 Pipeline
  executePipelineSafe(taskId, ctx);

  return task;
}

async function executePipelineSafe(taskId: string, ctx: PipelineContext): Promise<void> {
  try {
    const result = await executePipeline(ctx);

    const task = tasks.get(taskId);
    if (task) {
      task.status = "completed";
      task.result = result;
      task.completedAt = new Date().toISOString();
    }

    const summary = result.explorer.projectSummary;
    sseManager.emit(taskId, { type: "task:completed", taskId, summary });
  } catch (err) {
    const task = tasks.get(taskId);
    const taskError: TaskError = (err as { category?: string; message?: string; retryable?: boolean }).category
      ? err as TaskError
      : { category: "internal", message: err instanceof Error ? err.message : String(err), retryable: false };

    if (task) {
      task.status = "failed";
      task.error = taskError;
      task.completedAt = new Date().toISOString();
    }

    sseManager.emit(taskId, { type: "task:error", taskId, error: taskError });
  } finally {
    pipelineContexts.delete(taskId);
  }
}

export function getTask(taskId: string): GetAnalysisResponse | null {
  const task = tasks.get(taskId);
  if (!task) return null;

  return {
    taskId: task.taskId,
    status: task.status,
    currentStage: task.currentStage ?? undefined,
    stageProgress: task.stageProgress,
    result: task.result ?? undefined,
    error: task.error ?? undefined,
    createdAt: task.createdAt,
    completedAt: task.completedAt ?? undefined,
    cached: task.cached,
  };
}

export function answerQuestion(taskId: string, questionId: string, answer: string): boolean {
  const ctx = pipelineContexts.get(taskId);
  if (!ctx) return false;
  return resolveQuestion(ctx, questionId, answer);
}

export function hasTask(taskId: string): boolean {
  return tasks.has(taskId);
}

// ========== 错误 ==========

export class OrchestratorError extends Error {
  category: string;
  retryable: boolean;
  constructor(category: string, message: string, retryable: boolean) {
    super(message);
    this.category = category;
    this.retryable = retryable;
    this.name = "OrchestratorError";
  }
}
```

- [ ] **Step 2: 创建 src/routes/analysis.ts**

```typescript
import type { FastifyInstance } from "fastify";
import { createTask, getTask, answerQuestion, OrchestratorError } from "../services/orchestrator.js";
import type { CreateAnalysisRequest, AskRequest } from "../types/index.js";

export async function analysisRoutes(app: FastifyInstance): Promise<void> {
  // POST /analysis
  app.post<{ Body: CreateAnalysisRequest }>("/analysis", async (request, reply) => {
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

  // GET /analysis/:id
  app.get<{ Params: { id: string } }>("/analysis/:id", async (request, reply) => {
    const { id } = request.params;
    const response = getTask(id);

    if (!response) {
      return reply.status(404).send({ error: "not_found", message: "任务不存在" });
    }

    return reply.send(response);
  });

  // POST /analysis/:id/ask
  app.post<{ Params: { id: string }; Body: AskRequest }>("/analysis/:id/ask", async (request, reply) => {
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
  });
}
```

- [ ] **Step 3: 创建 src/routes/stream.ts**

```typescript
import type { FastifyInstance } from "fastify";
import { sseManager } from "../lib/sse.js";
import { hasTask } from "../services/orchestrator.js";

export async function streamRoutes(app: FastifyInstance): Promise<void> {
  app.get<{ Params: { id: string } }>("/analysis/:id/stream", async (request, reply) => {
    const { id } = request.params;

    if (!hasTask(id)) {
      return reply.status(404).send({ error: "not_found", message: "任务不存在" });
    }

    // 设置 SSE headers
    reply.raw.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
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

    // 等待连接关闭（SSE 是长连接）
    await new Promise<void>((resolve) => {
      request.raw.on("close", resolve);
    });
  });
}
```

- [ ] **Step 4: 创建 src/index.ts**

```typescript
import Fastify from "fastify";
import { config } from "./config.js";
import { analysisRoutes } from "./routes/analysis.js";
import { streamRoutes } from "./routes/stream.js";
import { getDb } from "./db/index.js";
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
    app.log.error("数据库初始化失败:", err);
    process.exit(1);
  }

  // 注册路由
  await app.register(analysisRoutes);
  await app.register(streamRoutes);

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

- [ ] **Step 5: 验证编译**

```bash
npx tsc --noEmit
```
Expected: 编译通过。

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: add Orchestrator, routes, and server entry point"
```

---

### Task 13: Fixture 仓库 & 集成测试

**Files:**
- Create: `tests/fixtures/mini-repo/package.json`
- Create: `tests/fixtures/mini-repo/README.md`
- Create: `tests/fixtures/mini-repo/src/index.ts`
- Create: `tests/fixtures/mini-repo/src/utils/helper.ts`
- Create: `tests/integration/api.test.ts`

- [ ] **Step 1: 创建 fixture — tests/fixtures/mini-repo/package.json**

```json
{
  "name": "mini-repo",
  "version": "1.0.0",
  "description": "A minimal test fixture for RepoMentor integration tests",
  "main": "src/index.ts",
  "scripts": {
    "build": "tsc",
    "test": "vitest run",
    "lint": "eslint src/"
  },
  "dependencies": {
    "express": "^4.18.0"
  }
}
```

- [ ] **Step 2: 创建 tests/fixtures/mini-repo/README.md**

```markdown
# Mini Repo

A minimal web server built with Express for testing RepoMentor analysis.

## Features

- Basic request logging middleware
- JSON response helpers
- Configurable port
```

- [ ] **Step 3: 创建 tests/fixtures/mini-repo/src/index.ts**

```typescript
import express from "express";
import { logMiddleware } from "./utils/helper.js";

const app = express();

app.use(logMiddleware);

app.get("/", (_req, res) => {
  res.json({ status: "ok" });
});

export default app;
```

- [ ] **Step 4: 创建 tests/fixtures/mini-repo/src/utils/helper.ts**

```typescript
import type { Request, Response, NextFunction } from "express";

// TODO: add request timing
export function logMiddleware(req: Request, _res: Response, next: NextFunction): void {
  console.log(`[${new Date().toISOString()}] ${req.method} ${req.url}`);
  next();
}

// FIXME: error handling is incomplete
export function sendError(res: Response, message: string, statusCode = 500): void {
  res.status(statusCode).json({ error: message });
}
```

- [ ] **Step 5: 编写集成测试 — tests/integration/api.test.ts**

```typescript
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import Fastify from "fastify";
import { analysisRoutes } from "../../src/routes/analysis.js";
import { streamRoutes } from "../../src/routes/stream.js";

// 集成测试直接启动 Fastify，无需网络
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

// 使用本地 fixture
const FIXTURE_PATH = "tests/fixtures/mini-repo";

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
    // 创建任务
    const createRes = await app.inject({
      method: "POST",
      url: "/analysis",
      payload: { repoUrl: "https://github.com/expressjs/express" },
    });
    const { taskId } = JSON.parse(createRes.payload);

    // 查询任务
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
    // 创建一个任务（不会有 pending question，因为 Pipeline 还没跑）
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

    // 超时问题会返回 400
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
```

- [ ] **Step 6: 配置 test .env**

创建 `tests/integration/.env.test`（集成测试需要部分配置）：

```bash
DEEPSEEK_API_KEY=test-key
DEEPSEEK_BASE_URL=https://api.deepseek.com
PORT=3000
HOST=0.0.0.0
CLONE_TIMEOUT_MS=30000
CLONE_DEPTH=1
MAX_REPO_SIZE_MB=200
TASK_TOTAL_TIMEOUT_MS=600000
INTERACTION_TIMEOUT_MS=5000
SQLITE_PATH=:memory:
LOG_LEVEL=error
```

- [ ] **Step 7: 运行集成测试**

```bash
npx vitest run tests/integration/
```
Expected: 测试通过（URL 校验和任务创建/查询的成功路径应该能跑通；真实 Git clone 会因为无网络/速度慢而进入 Pipeline 错误流程——这是预期的，开发阶段手动 E2E 验证完整流程）

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: add fixture repo and integration tests"
```

---

### Task 14: E2E 手动验证

**不自动化**：用真实 GitHub 仓库手动跑全流程。

- [ ] **Step 1: 配置真实 API Key**

复制 `.env.example` 为 `.env`，填入真实的 `DEEPSEEK_API_KEY`

- [ ] **Step 2: 启动服务**

```bash
npm run dev
```
Expected: `RepoMentor 启动: http://0.0.0.0:3000`

- [ ] **Step 3: 小仓库测试**

```bash
curl -X POST http://localhost:3000/analysis \
  -H "Content-Type: application/json" \
  -d '{"repoUrl":"https://github.com/expressjs/express"}'
```
Expected: 返回 `{ "taskId": "...", "status": "cloning", "createdAt": "..." }`

- [ ] **Step 4: 查看任务状态**

```bash
curl http://localhost:3000/analysis/<taskId>
```
Expected: 状态从 `cloning` → `analyzing` → `completed`

- [ ] **Step 5: SSE 流验证**

```bash
curl -N http://localhost:3000/analysis/<taskId>/stream
```
Expected: 流式输出 SSE 事件

- [ ] **Step 6: 缓存验证**

对同一个仓库再次 POST /analysis，确认 `cached: true`（TODO: 需要在 Orchestrator 中接入 analysis-cache repository）

- [ ] **Step 7: 记录问题**

记录 E2E 测试中发现的问题，特别是 Claude Agent SDK 的实际 API 调用方式与骨架代码的差异，作为后续优化的输入。

---

### Task 15: 接入缓存 & 经验存储（补齐 TODO）

**Files:**
- Modify: `src/services/pipeline.ts`

缓存检查必须在 clone 完成之后、Explorer 之前执行。

- [ ] **Step 1: 在 pipeline.ts 顶部添加 DB 相关 import**

```typescript
import { getDb } from "../db/index.js";
import * as cacheRepo from "../db/repositories/analysis-cache.js";
```

- [ ] **Step 2: 修改 executePipeline 的 clone 后逻辑，加入缓存检查**

在 `executePipeline` 函数中，clone 完成后立即检查缓存。
Pipeline 返回类型已改为 `PipelineResult`（包含 `result` 和 `cached` 字段），不再用 `_cached` hack。

```typescript
// 替换现有 clone 代码块 (Task 11 中 Step 1 的 "0. Clone" 部分):

// 0. Clone
sseManager.emit(ctx.taskId, {
  type: "task:created",
  taskId: ctx.taskId,
  status: "cloning",
});

const { localPath: lp, commitHash } = await cloneRepo(ctx.repoUrl, taskDir);
localPath = lp;

// 0.5 缓存检查（clone 后、Explorer 前）
const { owner: cacheOwner, repo: cacheRepoName } = parseRepoUrl(ctx.repoUrl);
const db = getDb();
const cached = cacheRepo.findByCommit(db, cacheOwner, cacheRepoName, ctx.branch, commitHash);

if (cached) {
  const cachedResult = JSON.parse(cached.result) as AnalysisResult;
  sseManager.emit(ctx.taskId, {
    type: "task:completed",
    taskId: ctx.taskId,
    summary: cachedResult.explorer.projectSummary,
  });
  await cleanup(localPath!);
  return { result: cachedResult, cached: true };
}

// （后续不变：fileCount, status:analyzing, Explorer...）
```

- [ ] **Step 3: 在 executePipeline 结尾保存缓存**

在 `return { result: analysisResult, cached: false }` 之前添加：

```typescript
// 保存缓存
cacheRepo.save(getDb(), {
  owner: cacheOwner,
  repo: cacheRepoName,
  branch: ctx.branch,
  commitHash,
  result: analysisResult,
  projectTypePrimary: analysisResult.explorer.projectType.primary,
  framework: analysisResult.explorer.techStack.framework,
});

return { result: analysisResult, cached: false };
```

- [ ] **Step 4: 更新 orchestrator.ts 的 executePipelineSafe 处理 PipelineResult**

```typescript
// orchestrator.ts 的 executePipelineSafe 中:

try {
  const { result, cached } = await executePipeline(ctx);

  const task = tasks.get(taskId);
  if (task) {
    task.status = "completed";
    task.result = result;
    task.cached = cached;
    task.completedAt = new Date().toISOString();
  }

  const summary = result.explorer.projectSummary;
  sseManager.emit(taskId, { type: "task:completed", taskId, summary });
} catch (err) {
  // ... 错误处理不变
```

- [ ] **Step 5: 验证编译 & Commit**

```bash
npx tsc --noEmit && git add -A && git commit -m "feat: integrate analysis cache into Pipeline via PipelineResult"
```

---

## Implementation Order

```
Task 1  → 项目脚手架 (package.json, tsconfig, .gitignore)
Task 2  → 类型定义 (src/types/index.ts)
Task 3  → 配置模块 (config.ts + 测试)
Task 4  → 数据库层 (migrations, DB, repositories)
Task 5  → Schema 校验 & 沙箱 (lib/schema.ts, lib/sandbox.ts + 测试)
Task 6  → 仓库操作 (lib/repo.ts + 测试)
Task 7  → SSE 模块 (lib/sse.ts)
Task 8  → Agent 定义 (agents/*/agent.md)
Task 9  → Skill 模板 (skills/*/SKILL.md)
Task 10 → Claude SDK 封装 (services/claude-client.ts)
Task 11 → Pipeline 状态机 (services/pipeline.ts)
Task 12 → Orchestrator & 路由 (orchestrator, routes, index.ts)
Task 13 → Fixture & 集成测试
Task 14 → E2E 手动验证
Task 15 → 缓存 & 经验接入
```

---

## 备注

1. **Claude Agent SDK API**: Task 10 中的 `invokeAgent` 函数是骨架实现。需要根据 `@anthropic-ai/claude-agent-sdk` 的实际 API 调整。如果 SDK 的 Agent 调用方式与预期不同，只需修改该函数即可。

2. **import.meta.dirname**: Node.js 21+ 和 ESM 模式下可用。如果编译报错，在 `tsconfig.json` 中设置 `"module": "nodenext"` 和 `"moduleResolution": "nodenext"`。

3. **simple-git diff**: `extractCommitSummary` 中使用了 `commit.diff` 来获取每个 commit 的变更文件。如果 `simple-git` 的 `log()` 返回的 diff 格式与预期不同，可能需要改为逐个 commit 调用 `git.diffTree()` 或 `git.show()`。

4. **测试中的环境变量**: vitest 不会自动加载 `.env`。单元测试通过 `vi.resetModules()` + 设置 `process.env` 覆盖。集成测试可以使用 `vitest.config.ts` 的 `env` 字段或手动设置。
