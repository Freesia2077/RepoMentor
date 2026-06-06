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
import type { StageOutputFor } from "./claude-client.js";
import { sseManager } from "../lib/sse.js";
import { cloneRepo, getFileCount, extractCommitSummary, cleanup, parseRepoUrl } from "../lib/repo.js";
import { config } from "../config.js";
import { getDb } from "../db/index.js";
import * as cacheRepo from "../db/repositories/analysis-cache.js";
import fs from "node:fs";

// ========== Pipeline 上下文 & 类型 ==========

export interface PipelineLifecycleCallbacks {
  onStageStart: (stage: StageName) => void;
  onStageDone: (stage: StageName) => void;
}

export interface PipelineContext {
  taskId: string;
  repoUrl: string;
  branch: string;
  stageProgress: StageProgress;
  callbacks: PipelineLifecycleCallbacks;
  pendingQuestion: {
    questionId: string;
    resolve: (answer: string) => void;
    timer: ReturnType<typeof setTimeout>;
  } | null;
}

export interface PipelineResult {
  result: AnalysisResult;
  cached: boolean;
}

// ========== 主 Pipeline ==========

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

    // 缓存检查（clone 后、Explorer 前）
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
      await cleanup(localPath);
      return { result: cachedResult, cached: true };
    }

    const fileCount = await getFileCount(localPath);

    // 标记进入 analyzing
    sseManager.emit(ctx.taskId, {
      type: "task:created",
      taskId: ctx.taskId,
      status: "analyzing",
    });

    // 1. Explorer
    const explorerOutput = await runStageWithRetry("explorer", {
      fileCount,
    }, ctx, localPath);

    // 交互点：项目类型确认
    await askUser(ctx, "q_type", "explorer",
      `识别为 ${explorerOutput.projectType.primary}，是否正确？`,
      ["是", "否，请纠正"]);

    // 交互点：模块划分确认
    await askUser(ctx, "q_modules", "explorer",
      `模块划分完成：${explorerOutput.moduleMap.map(m => m.path).join(", ")}。是否合理？`,
      ["合理，继续", "需要调整"]);

    // 2. Mentor
    const skillContent = loadSkillTemplate(explorerOutput.projectType.primary);

    const mentorOutput = await runStageWithRetry("mentor", {
      explorerOutput,
      skillContent,
      experiences: "",
    }, ctx, localPath);

    // 交互点：依赖图反馈
    const deps = Object.entries(mentorOutput.dependencyGraph);
    await askUser(ctx, "q_deps", "mentor",
      `依赖图包含 ${deps.length} 个模块。想深入了解哪个模块？`,
      deps.slice(0, 5).map(([mod]) => mod));

    // 3. Contributor
    const commitSummary = await extractCommitSummary(localPath);

    const contributorOutput = await runStageWithRetry("contributor", {
      explorerOutput,
      mentorOutput,
      commitSummary,
    }, ctx, localPath);

    const analysisResult: AnalysisResult = {
      explorer: explorerOutput,
      mentor: mentorOutput,
      contributor: contributorOutput,
    };

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
  } finally {
    if (localPath) {
      await cleanup(localPath);
    }
  }
}

// ========== 带重试的阶段执行 ==========

async function runStageWithRetry<S extends StageName>(
  stage: S,
  input: Record<string, unknown>,
  ctx: PipelineContext,
  localPath: string,
): Promise<StageOutputFor<S>> {
  const maxRetries = 1;

  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      return await runStageWithSSE(stage, input, ctx, localPath);
    } catch (err) {
      if (err instanceof ParseError) {
        if (attempt < maxRetries) {
          sseManager.emit(ctx.taskId, {
            type: "stage:progress", stage,
            message: `JSON 解析失败，重试中... (${attempt + 1}/${maxRetries})`,
          });
          continue;
        }
        emitError(ctx.taskId, {
          category: "parse_failed",
          message: `Agent 输出解析失败（已重试 ${maxRetries} 次）: ${err.message}`,
          retryable: true,
        });
        throw { category: "parse_failed", message: err.message, retryable: true };
      }

      if (err instanceof LLMError) {
        if (attempt < maxRetries && err.retryable) {
          sseManager.emit(ctx.taskId, {
            type: "stage:progress", stage,
            message: `LLM 调用失败，重试中... (${attempt + 1}/${maxRetries})`,
          });
          continue;
        }
        emitError(ctx.taskId, {
          category: "llm_failed",
          message: err.message,
          retryable: false,
        });
        throw { category: "llm_failed", message: err.message, retryable: false };
      }

      // 未知错误 → internal
      const msg = err instanceof Error ? err.message : String(err);
      emitError(ctx.taskId, { category: "internal", message: msg, retryable: false });
      throw { category: "internal", message: msg, retryable: false };
    }
  }

  throw new Error("unreachable");
}

// ========== 带 SSE 的阶段执行 ==========

async function runStageWithSSE<S extends StageName>(
  stage: S,
  input: Record<string, unknown>,
  ctx: PipelineContext,
  localPath: string,
): Promise<StageOutputFor<S>> {
  ctx.callbacks.onStageStart(stage);
  sseManager.emit(ctx.taskId, { type: "stage:start", stage });

  const result = await runStage(stage, input, {
    onProgress: (message: string) => {
      sseManager.emit(ctx.taskId, { type: "stage:progress", stage, message });
    },
    onField: (field: string, value: unknown) => {
      sseManager.emit(ctx.taskId, { type: "stage:field", stage, field, value });
    },
  }, localPath);

  ctx.callbacks.onStageDone(stage);
  sseManager.emit(ctx.taskId, { type: "stage:done", stage, output: result });

  return result;
}

// ========== 交互 ==========

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
      resolve("");
    }, config.INTERACTION_TIMEOUT_MS);

    ctx.pendingQuestion = { questionId, resolve, timer };
  });
}

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
    return fs.readFileSync(filePath, "utf-8");
  } catch {
    return "";
  }
}

// ========== 错误广播 ==========

function emitError(taskId: string, error: TaskError): void {
  sseManager.emit(taskId, { type: "task:error", taskId, error });
}
