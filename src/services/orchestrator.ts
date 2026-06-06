import type {
  TaskRecord,
  TaskStatus,
  StageProgress,
  AnalysisResult,
  TaskError,
  GetAnalysisResponse,
  StageName,
} from "../types/index.js";
import { executePipeline, resolveQuestion, type PipelineContext, type PipelineResult } from "./pipeline.js";
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

// ========== 公共 API ==========

export async function createTask(repoUrl: string, branch = "main"): Promise<TaskRecord> {
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

  const ctx: PipelineContext = {
    taskId,
    repoUrl,
    branch,
    stageProgress: task.stageProgress,
    callbacks: {
      onStageStart: (stage: StageName) => {
        const rec = tasks.get(taskId);
        if (rec) {
          rec.currentStage = stage;
          rec.stageProgress[stage] = "running";
        }
      },
      onStageDone: (stage: StageName) => {
        const rec = tasks.get(taskId);
        if (rec) rec.stageProgress[stage] = "done";
      },
      onStatusChange: (status) => {
        const rec = tasks.get(taskId);
        if (rec) rec.status = status;
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
    const task = tasks.get(taskId);
    const e = err as { category?: string; message?: string; retryable?: boolean };
    const taskError: TaskError = e.category
      ? { category: e.category as TaskError["category"], message: e.message!, retryable: e.retryable ?? false }
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
