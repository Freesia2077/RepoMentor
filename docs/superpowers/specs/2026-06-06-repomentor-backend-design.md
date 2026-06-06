# RepoMentor 后端设计文档

> 2026-06-06 | Status: Draft

## 1. 项目概述

**RepoMentor** 是一个面向开源仓库学习的 Agent 系统。用户输入 GitHub 仓库链接后，系统帮助：
1. 理解项目目标与整体架构
2. 学习关键模块与核心代码
3. 理解开发流程与代码规范
4. 找到适合入门贡献的任务

### 技术路线

- 后端优先，完善后再加入前端
- 基于 Claude Agent SDK 构建 Pipeline 式多智能体分析系统
- 独立 API 服务（Fastify + TypeScript + SQLite）
- 统一使用 `deepseek-v4-pro` 模型，通过 `DEEPSEEK_BASE_URL` + `DEEPSEEK_API_KEY` 连接 DeepSeek API（cc switch 是 Claude Code 内交互时的路由机制；本独立服务通过 SDK 的 base_url 参数指向 DeepSeek）

---

## 2. 系统架构

```
┌─────────────────────────────────────────────────┐
│                    Client (未来)                  │
│              SSE 接收进度 + 交互事件               │
└──────────────────────┬──────────────────────────┘
                       │ HTTP + SSE
┌──────────────────────▼──────────────────────────┐
│                 Fastify API Server                │
│  POST /analysis          → 创建分析任务           │
│  GET  /analysis/:id      → 查询任务状态/结果       │
│  GET  /analysis/:id/stream → SSE 进度流           │
│  POST /analysis/:id/ask  → 关键节点交互            │
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────┐
│               Orchestrator Service               │
│  - 任务生命周期管理                                │
│  - 阶段调度 & 状态机                               │
│  - SSE 事件广播                                   │
│  - Skill 模板匹配 & 经验注入                        │
│  - Clone 管理 & 数据准备                           │
└──────┬───────────────────────┬──────────────────┘
       │ Pipeline Stage 1      │ Stage 2          ...
       ▼                       ▼
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│   Explorer   │───▶│   Mentor     │───▶│ Contributor  │
│  (Subagent)  │    │  (Subagent)  │    │  (Subagent)  │
│              │    │              │    │              │
│ 工具:        │    │ 工具:        │    │ 工具:        │
│  Read, Glob  │    │  Read, Grep  │    │  Read, Grep  │
│  Grep        │    │  WebSearch   │    │              │
│ model:       │    │ model:       │    │ model:       │
│ deepseek-    │    │ deepseek-    │    │ deepseek-    │
│ v4-pro       │    │ v4-pro       │    │ v4-pro       │
└──────────────┘    └──────────────┘    └──────────────┘
```

**关键设计原则**：
- 单进程架构：不引入 Redis/消息队列，利用 Node.js 异步模型处理并发
- Clone 和所有副作用操作由 Orchestrator 完成，Subagent 不收 Bash 工具
- 每阶段输出固定 JSON Schema，阶段间数据契约清晰
- SSE 单向推送 + 交互走 HTTP POST 端点

---

## 3. Pipeline 阶段设计

### Stage 1: Explorer（仓库探索者）

**职责**：快速产出仓库的顶层结构画像，不深入源码细节。

**输入**：`{ localPath, fileCount, projectTypeHint? }`

**工作流程**：
1. 根据 fileCount 判断规模，选择分析深度
2. 收集元信息：package.json、README（前 200 行）、顶层目录结构
3. 识别项目类型（primary + secondary 数组）
4. 划分模块（仅基于目录结构，不读业务代码）
5. 自我校验输出 JSON Schema 合规性

**输出**：
```json
{
  "projectType": { "primary": "web-framework", "secondary": ["cli"] },
  "techStack": { "language": "typescript", "framework": "express", "buildTool": "tsup" },
  "fileCount": 342,
  "entryPoints": [{ "file": "src/index.ts", "role": "主入口" }],
  "moduleMap": [{
    "path": "src/core/",
    "responsibility": "核心引擎，包含请求生命周期",
    "importance": "core",
    "justification": "含 index.ts 入口，所有请求必经此处"
  }],
  "directorySummary": "项目分为 core/ cli/ utils/ 三个顶层模块...",
  "projectSummary": "一个轻量级 Web 框架，专注于路由和中间件"
}
```

**防爆策略**：
- moduleMap 最多 20 项，每项 responsibility 最多 100 字
- entryPoints 最多 10 项
- 不读 src/ 业务代码、不追踪 import 关系
- 文件数 < 500：全量；500-2000：2 层扫描；> 2000：仅元数据

### Stage 2: Mentor（学习导师）

**职责**：基于 Explorer 产出深入解读架构，生成学习路径。

**输入**：`{ explorerOutput, skillContent, experiences, userFocus? }`

Skill 模板和历史经验由 Orchestrator 在调用前注入 system prompt，Mentor 不持有 Skill 工具。

**工作流程**：
1. 阅读 entryPoints 前 150 行
2. 阅读 core 模块入口文件
3. Grep 搜索 import 语句，分析模块间依赖
4. 对照 Skill 模板生成架构解读
5. 生成推荐阅读路径（≤5 步）
6. 识别代码规范与设计模式

**输出**：
```json
{
  "architectureOverview": "markdown，≤800 字",
  "dependencyGraph": { "src/core/": ["src/utils/", "src/types/"] },
  "readingPath": [{ "step": 1, "file": "src/index.ts", "why": "应用启动全流程" }],
  "keyPatterns": [{ "pattern": "中间件链", "where": "src/core/middleware/", "description": "..." }],
  "codeConventions": [{ "rule": "所有公共 API 有 JSDoc", "example": "src/core/app.ts:45" }]
}
```

**Token 预算**：16K

### Stage 3: Contributor（贡献顾问）

**职责**：找到适合新手入门的具体任务点，生成贡献指南。

**输入**：`{ explorerOutput, mentorOutput, commitSummary }`

commitSummary 由 Orchestrator 通过 simple-git 预提取（近 50 条 commit 的频次分析），Contributor 不执行 git 命令。

**工作流程**：
1. 分析 commitSummary 高频变更区域
2. Grep 搜索 TODO/FIXME/HACK/XXX
3. 阅读 CONTRIBUTING.md（如有）
4. 识别低耦合、独立、有测试的模块 → goodFirstIssues
5. 从 package.json scripts 推断开发环境操作

**输出**：
```json
{
  "goodFirstIssues": [{ "area": "文档补全", "difficulty": "easy", "description": "..." }],
  "contributionSetup": { "devEnv": "Node.js 18+, pnpm install", "build": "pnpm build", "test": "pnpm test" },
  "entryFiles": [{ "file": "src/index.ts", "description": "...", "reason": "..." }],
  "notesForNewcomers": [{ "tip": "项目使用 Conventional Commits 规范" }]
}
```

**Token 预算**：8K

### 阶段超时

| Stage | 超时 | 理由 |
|-------|------|------|
| Explorer | 120s | 只做元信息扫描 |
| Mentor | 300s | 深入阅读 + Grep 依赖分析 |
| Contributor | 180s | Grep 搜索 + 报告生成 |
| 任务总计 | 600s | 硬上限 |

---

## 4. 关键节点交互

Pipeline 在以下时机暂停，通过 SSE 推送交互事件：

| 节点 | 时机 | 交互内容 |
|------|------|----------|
| 项目类型识别后 | Stage 1 中期 | "识别为 Web 框架，是否正确？" |
| 模块清单产出后 | Stage 1 结束 | "以下模块划分是否合理？可标记关注模块" |
| 依赖图生成后 | Stage 2 中期 | "想深入了解哪个模块？" |
| 阅读路径后 | Stage 2 结束 | "调整关注方向？" |

- 交互**可选**——用户不响应则 30s 后自动继续
- 用户通过 `POST /analysis/:id/ask` 响应

---

## 5. SSE 事件流

### 事件类型

```typescript
// 生命周期
{ type: "task:created",   taskId, status }
{ type: "task:completed", taskId, summary }
{ type: "task:error",     taskId, error }

// Pipeline 进度
{ type: "stage:start",    stage }              // "explorer" | "mentor" | "contributor"
{ type: "stage:progress", stage, message }     // 阶段内进度文字
{ type: "stage:field",    stage, field, value } // 每完成一个 Schema 字段就推送
{ type: "stage:done",     stage, output }       // 完整阶段输出

// 交互
{ type: "interact:ask",   questionId, stage, question, options? }
{ type: "interact:timeout", questionId }
```

### 错误分类

```typescript
type TaskError = {
  category: "clone_failed" | "llm_failed" | "parse_failed" | "internal",
  message: string,
  retryable: boolean
}
```

| category | 可重试 | 处理 |
|----------|--------|------|
| `clone_failed` | 否 | 返回明确错误，用户修正仓库链接 |
| `llm_failed` | 是 | 自动重试 1 次（切换 model），失败后通知用户 |
| `parse_failed` | 是 | 自动重试 1 次（相同 prompt），失败后通知用户并记录原始输出 |
| `internal` | 否 | 记录完整栈，返回通用错误 |

### 任务状态

```typescript
type TaskStatus = "cloning" | "analyzing" | "completed" | "failed";
type StageName = "explorer" | "mentor" | "contributor";
```

前端只需处理一套名称：`explorer | mentor | contributor`。`status` 只用于判断是否已完成/失败。

---

## 6. API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/analysis` | 创建分析任务 |
| `GET` | `/analysis/:id` | 查询任务状态 & 结果 |
| `GET` | `/analysis/:id/stream` | SSE 进度流 |
| `POST` | `/analysis/:id/ask` | 响应交互问题 |

### POST /analysis

```
Request:  { "repoUrl": "https://github.com/expressjs/express", "branch?": "main" }
Response: { "taskId": "exp_abc123", "status": "cloning", "createdAt": "..." }
Error 400: { "error": "invalid_repo_url", "message": "..." }
```

### GET /analysis/:id

```
运行中: { "taskId", "status": "analyzing", "currentStage": "mentor", "stageProgress": {...} }
完成:   { "taskId", "status": "completed", "result": { explorer, mentor, contributor }, "completedAt", "cached": false }
失败:   { "taskId", "status": "failed", "error": { category, message, retryable } }
```

### GET /analysis/:id/stream

`text/event-stream`，按 stage:start → stage:field × N → stage:done 顺序推送每个阶段。交互事件穿插其中。

### POST /analysis/:id/ask

```
Request:  { "questionId": "q1", "answer": "是" }
Response: { "accepted": true }
Error 400: { "error": "question_expired", "message": "..." }
```

---

## 7. 缓存策略

**缓存键**：`owner + repo + branch + commitHash`

**流程**：
1. Orchestrator clone 完成后取 HEAD commit hash
2. 查 analysis-cache 表
3. 命中（同 commit）→ 秒级返回 `cached: true`
4. 未命中 → 执行 Pipeline，结果写入缓存

不设 TTL——commit hash 是天然的失效标记。

---

## 8. 目录结构

```
repo-mentor/
├── CLAUDE.md
├── package.json
├── tsconfig.json
├── .env.example
│
├── agents/                          # 项目根目录（SDK 约定位置）
│   ├── explorer/
│   │   └── agent.md
│   ├── mentor/
│   │   └── agent.md
│   └── contributor/
│       └── agent.md
│
├── skills/                          # 项目根目录（SDK 约定位置）
│   ├── analyze-web-framework/
│   │   └── SKILL.md
│   ├── analyze-cli-tool/
│   │   └── SKILL.md
│   ├── analyze-monorepo/
│   │   └── SKILL.md
│   └── analyze-generic/
│       └── SKILL.md
│
├── src/                             # 纯 TypeScript
│   ├── index.ts                     # Fastify 启动入口
│   ├── config.ts                    # 环境变量 & Zod 校验
│   │
│   ├── routes/
│   │   ├── analysis.ts              # POST /analysis, GET /analysis/:id
│   │   └── stream.ts                # GET /analysis/:id/stream (SSE)
│   │
│   ├── services/
│   │   ├── orchestrator.ts          # 任务生命周期 & 阶段调度
│   │   ├── pipeline.ts              # Pipeline 状态机 & 阶段衔接
│   │   └── claude-client.ts         # Claude Agent SDK 封装
│   │
│   ├── lib/
│   │   ├── repo.ts                  # Clone + 预检 + 清理
│   │   ├── sandbox.ts               # 路径安全约束
│   │   ├── sse.ts                   # SSE 连接管理 & 事件广播
│   │   └── schema.ts                # Zod Schema 定义
│   │
│   ├── db/
│   │   ├── index.ts                 # DB 初始化 & 连接
│   │   ├── migrations/
│   │   │   └── 001-init.sql
│   │   └── repositories/
│   │       ├── analysis-cache.ts
│   │       └── experiences.ts
│   │
│   └── types/
│       └── index.ts
│
├── tests/
│   ├── unit/                        # 纯逻辑单元测试
│   ├── integration/                 # Fixture repo 集成测试
│   └── fixtures/
│       └── mini-repo/               # 3-5 个文件的 mock 仓库
│
└── tmp/                             # git clone 临时目录 (.gitignore)
```

---

## 9. Subagent 定义

### 通用约束

- 三个 Agent 均**不收 Bash 工具**——Clone 和 Git 操作由 Orchestrator 完成
- 三个 Agent 均**不收 Skill 工具**——模板由 Orchestrator 注入 system prompt
- 模型统一为 `deepseek-v4-pro`

### Explorer (`agents/explorer/agent.md`)

```markdown
---
name: explorer
description: 探索已克隆的仓库目录结构，产出项目类型、技术栈、模块清单。Pipeline Stage 1。
tools: [Read, Glob, Grep]
model: deepseek-v4-pro
---

输入: { localPath, fileCount, projectTypeHint? }
约束: moduleMap ≤20, entryPoints ≤10, 不读业务代码, 不追踪 import
```

### Mentor (`agents/mentor/agent.md`)

```markdown
---
name: mentor
description: 基于 Explorer 产出深入解读架构，生成学习路径。Pipeline Stage 2。
tools: [Read, Grep, WebSearch]
model: deepseek-v4-pro
---

输入: { explorerOutput, skillContent, experiences, userFocus? }
Skill 模板已注入 system prompt，严格遵循模板分析框架
```

### Contributor (`agents/contributor/agent.md`)

```markdown
---
name: contributor
description: 基于前两阶段产出和 commit 摘要，分析贡献机会。Pipeline Stage 3。
tools: [Read, Grep]
model: deepseek-v4-pro
---

输入: { explorerOutput, mentorOutput, commitSummary }
commitSummary 已预提取，不需要执行 git 命令
```

### Orchestrator 调用流程

```typescript
async function executePipeline(taskId: string, repoUrl: string) {
  // 1. Clone & 预检（Orchestrator 层）
  const { localPath, commitHash } = await preCheckAndClone(repoUrl, taskDir);

  // 2. Stage 1: Explorer
  const explorerOutput = await runStage("explorer", { localPath, fileCount });

  // 3. 匹配 Skill & 经验
  const skillContent = loadSkillTemplate(explorerOutput.projectType);
  const experiences = await db.experiences.find({...});

  // 4. Stage 2: Mentor
  const mentorOutput = await runStage("mentor", { explorerOutput, skillContent, experiences });

  // 5. 提取 commit 摘要（simple-git）
  const commitSummary = await extractCommitSummary(localPath);

  // 6. Stage 3: Contributor
  const contributorOutput = await runStage("contributor", { explorerOutput, mentorOutput, commitSummary });

  // 7. 缓存结果 & 清理
  await db.cache.save(commitHash, result);
  await cleanup(localPath);
}
```

---

## 10. Skill 模板系统 & 经验沉淀

### 模板结构

只保留 SKILL.md（自然语言指令），砍掉 checklist.json。模板按项目类型组织：

```
skills/
  analyze-web-framework/SKILL.md
  analyze-cli-tool/SKILL.md
  analyze-monorepo/SKILL.md
  analyze-generic/SKILL.md     ← 兜底模板
```

### 匹配逻辑

Explorer 产出 `projectType: { primary, secondary[] }` 后：

1. 加载 `primary` 对应模板作为主策略
2. 从 `secondary` 各模板中提取关键 checklist 条目，合并为补充提示
3. 全部注入 Mentor 的 system prompt

### 经验沉淀

存储在 SQLite `experiences` 表。每次分析完成后，Mentor 额外输出一条经验摘要（~200 字），人工审核后入库。

**匹配维度（权重从高到低）**：
1. `projectType.primary` 相同
2. `techStack.framework` 相同
3. `projectType.secondary` 有交集
4. `owner` 相同（弱参考，仅辅助）

```sql
-- 主匹配：primary type + framework
SELECT * FROM experiences WHERE primary_type = ? AND framework = ? ORDER BY created_at DESC LIMIT 5;
-- 放宽：primary type 相同
-- 再放宽：framework 相同，secondary 有交集
-- 最后才考虑 owner
```

---

## 11. 安全 & 资源管理

### Clone 双层守卫

```typescript
async function preCheckAndClone(repoUrl: string, taskDir: string) {
  // 第一层：GitHub API 大小预检
  const repoSizeKb = await fetchRepoSize(owner, repo);  // GET /repos/{owner}/{repo}
  if (repoSizeKb > MAX_REPO_SIZE_KB) throw new TaskError({...});

  // 第二层：clone 超时保护
  await git.clone(repoUrl, taskDir, { "--depth": 1, "--single-branch": null }, { timeout: 60000 });
}
```

- GitHub API 调用失败时降级为直接 clone（靠超时保底）
- 非 GitHub 仓库跳过预检

### 沙箱

```typescript
function resolvePath(taskWorkDir: string, requestedPath: string): string {
  const resolved = path.resolve(taskWorkDir, requestedPath);
  if (!resolved.startsWith(taskWorkDir)) throw new SandboxError("路径逃逸");
  return resolved;
}
```

Subagent 的工具调用路径必须在此前缀下，防止目录遍历攻击。

### 临时目录清理

- 正常完成：Pipeline 结束 → 立即 `rm -rf tmp/<taskId>/`
- 异常崩溃：Orchestrator finally 块清理
- 兜底：Fastify 启动时清理 `tmp/` 下超过 1 小时的残留目录

---

## 12. 配置

### .env

```bash
# DeepSeek API (Claude Agent SDK 通过 base_url 指向 DeepSeek)
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

### config.ts

通过 Zod 校验环境变量，启动时 fail-fast：

```typescript
const envSchema = z.object({
  DEEPSEEK_API_KEY: z.string(),
  DEEPSEEK_BASE_URL: z.string().default("https://api.deepseek.com"),
  PORT: z.coerce.number().default(3000),
  HOST: z.string().default("0.0.0.0"),
  CLONE_TIMEOUT_MS: z.coerce.number().default(60000),
  CLONE_DEPTH: z.coerce.number().default(1),
  MAX_REPO_SIZE_MB: z.coerce.number().default(200),
  TASK_TOTAL_TIMEOUT_MS: z.coerce.number().default(600000),
  INTERACTION_TIMEOUT_MS: z.coerce.number().default(30000),
  SQLITE_PATH: z.string().default("./data/repomentor.db"),
  LOG_LEVEL: z.enum(["debug", "info", "warn", "error"]).default("info"),
});
```

---

## 13. 测试策略

### 三层结构

```
E2E (手动)          ← 真实仓库全流程，开发阶段手动跑
集成测试             ← Pipeline 阶段衔接 + DB + SSE 流
单元测试             ← 纯逻辑：Schema 校验、路径安全、状态机
```

### 单元测试

| 测试目标 | 内容 |
|----------|------|
| `schema.ts` | Zod Schema 对合法/非法输出的校验 |
| `sandbox.ts` | 路径逃逸拦截 (`../`, `../../etc/passwd`) |
| `pipeline.ts` | 状态机流转、超时触发、数据契约断裂处理 |
| `config.ts` | 环境变量缺失/非法值时 Zod 报错 |
| `repo.ts` | URL 解析 (https/ssh/.git 后缀/子路径) |

### 集成测试

使用 `tests/fixtures/mini-repo/`（3-5 个文件的 mock 仓库）替代真实 GitHub 调用：

| 测试目标 | 方式 |
|----------|------|
| POST → GET /analysis | Fixture 作为本地路径触发，验证完整链路 |
| SSE stream | 收集所有事件，断言顺序和字段 |
| 缓存命中 | 同 fixture 提交两次，第二次 cached: true |
| 交互超时 | 不响应交互，断言 30s 后继续 |
| 错误路径 | Clone 失败、Agent 非法 JSON、超时终止 |

### 测试依赖

```json
{ "devDependencies": { "vitest": "^2.x" } }
```

不需要 mocking 框架。better-sqlite3 用 `:memory:` 模式，Fastify 用 `.inject()`，Git 操作用 fixture 路径替代。

---

## 14. 技术依赖

```json
{
  "dependencies": {
    "fastify": "^5.x",
    "@anthropic-ai/claude-agent-sdk": "^0.x",
    "better-sqlite3": "^11.x",
    "zod": "^3.x",
    "simple-git": "^3.x"
  },
  "devDependencies": {
    "typescript": "^5.x",
    "vitest": "^2.x",
    "tsx": "^4.x"
  }
}
```

---

## 15. 设计决策速览

| # | 决策点 | 结论 |
|---|--------|------|
| 1 | 运行形态 | 独立 API 服务 (Fastify + TypeScript) |
| 2 | 分析深度 | 按需分层渐进分析 |
| 3 | 持久化 | SQLite (better-sqlite3) |
| 4 | 触发方式 | 异步 SSE 流式 + 关键节点交互 |
| 5 | 多智能体编排 | Pipeline: Explorer → Mentor → Contributor |
| 6 | 经验沉淀 | 手工模板库 + 积累后渐进自动提取 |
| 7 | 语言支持 | 通用框架 + 插件化语言解析器 |
| 8 | 仓库获取 | 服务端 clone (simple-git) |
| 9 | 克隆执行方 | Orchestrator，Agent 不收 Bash |
| 10 | Skill 注入 | Orchestrator 注入 system prompt |
| 11 | Git 数据传递 | Orchestrator 预提取结构化 commit 摘要 |
| 12 | 模型 | 统一 `deepseek-v4-pro` (cc switch) |
| 13 | projectType | `{ primary, secondary[] }` 数组 |
| 14 | 模板格式 | 只保留 SKILL.md |
| 15 | 经验匹配 | 多维加权: primary type > framework > secondary > owner |
| 16 | 缓存键 | `owner + repo + branch + commitHash` |
| 17 | 状态命名 | status: cloning/analyzing/completed/failed |
| 18 | 仓库预检 | GitHub API size + MAX_REPO_SIZE_MB=200 |
| 19 | 测试框架 | vitest，三层：单元/集成(含 fixture)/E2E(手动) |
