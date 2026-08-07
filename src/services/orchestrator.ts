import type {
  TaskRecord,
  StageProgress,
  TaskError,
  GetAnalysisResponse,
  StageName,
} from "../types/index.js";
import { executePipeline, resolveQuestion, type PipelineContext } from "./pipeline.js";
import { sseManager } from "../lib/sse.js";
import { isValidGithubUrl, normalizeGithubUrl, isValidBranchName } from "../lib/repo.js";
import { config } from "../config.js";
import { getDb } from "../db/index.js";
import * as taskRepo from "../db/repositories/tasks.js";
import {
  ModelSettingsError,
  requireModelSettings,
  type ModelSettings,
} from "./model-settings.js";

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
  if (!isValidBranchName(branch)) {
    throw new OrchestratorError("invalid_branch", "分支名称无效", false);
  }

  let modelSettings: ModelSettings;
  try {
    modelSettings = requireModelSettings();
  } catch (error) {
    if (error instanceof ModelSettingsError) {
      throw new OrchestratorError(error.code, error.message, false);
    }
    throw error;
  }

  const normalizedRepoUrl = normalizeGithubUrl(repoUrl);
  const normalizedBranch = branch.trim();

  const taskId = makeTaskId();
  const task: TaskRecord = {
    taskId,
    repoUrl: normalizedRepoUrl,
    branch: normalizedBranch,
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
  persistTask(task);

  const ctx: PipelineContext = {
    taskId,
    repoUrl: normalizedRepoUrl,
    branch: normalizedBranch,
    stageProgress: task.stageProgress,
    abortController: new AbortController(),
    modelSettings,
    callbacks: {
      onStageStart: (stage: StageName) => {
        const rec = tasks.get(taskId);
        if (rec) {
          rec.currentStage = stage;
          rec.stageProgress[stage] = "running";
          persistTask(rec);
        }
      },
      onStageDone: (stage: StageName) => {
        const rec = tasks.get(taskId);
        if (rec) {
          rec.stageProgress[stage] = "done";
          persistTask(rec);
        }
      },
      onStatusChange: (status) => {
        const rec = tasks.get(taskId);
        if (rec) {
          rec.status = status;
          persistTask(rec);
        }
      },
      onCommitHash: (commitHash) => {
        const rec = tasks.get(taskId);
        if (rec) {
          rec.commitHash = commitHash;
          persistTask(rec);
        }
      },
    },
    pendingQuestion: null,
  };

  pipelineContexts.set(taskId, ctx);

  // 异步执行 Pipeline
  void executePipelineSafe(taskId, ctx);

  return {
    ...task,
    stageProgress: { ...task.stageProgress },
  };
}

async function executePipelineSafe(taskId: string, ctx: PipelineContext): Promise<void> {
  let timeoutId: ReturnType<typeof setTimeout> | undefined;
  try {
    const timeoutPromise = new Promise<never>((_, reject) => {
      timeoutId = setTimeout(() => {
        const error = {
          category: "timeout",
          message: `任务超过 ${Math.round(config.TASK_TOTAL_TIMEOUT_MS / 1000)} 秒总时限`,
          retryable: true,
        };
        reject(error);
        ctx.abortController.abort(new Error(error.message));
      }, config.TASK_TOTAL_TIMEOUT_MS);
    });

    const { result, cached } = await Promise.race([
      executePipeline(ctx),
      timeoutPromise,
    ]);

    const task = tasks.get(taskId);
    if (task) {
      task.status = "completed";
      task.result = result;
      task.cached = cached;
      task.completedAt = new Date().toISOString();
      persistTask(task);
    }

    const summary = result.explorer.projectSummary;
    sseManager.emit(taskId, { type: "task:completed", taskId, summary, result });
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
      persistTask(task);
    }

    sseManager.emit(taskId, { type: "task:error", taskId, error: taskError });
  } finally {
    if (timeoutId) clearTimeout(timeoutId);
    pipelineContexts.delete(taskId);
  }
}

export function getTask(taskId: string): GetAnalysisResponse | null {
  const task = tasks.get(taskId) ?? taskRepo.findById(getDb(), taskId);
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
  return tasks.has(taskId) || taskRepo.findById(getDb(), taskId) !== undefined;
}

function persistTask(task: TaskRecord): void {
  taskRepo.save(getDb(), task);
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
