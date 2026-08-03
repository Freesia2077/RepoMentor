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
import {
  runStage,
  orchestrateRepositoryEvidence,
  ParseError,
  LLMError,
} from "./claude-client.js";
import type { StageOutputFor } from "./claude-client.js";
import { sseManager } from "../lib/sse.js";
import {
  cloneRepo,
  extractCommitSummary,
  parseRepoUrl,
  preflightGithubRepo,
} from "../lib/repo.js";
import {
  buildContributionEvidence,
  buildEvidenceBundle,
  buildRepositoryContributionContext,
  buildRepositoryOverview,
  buildRepositoryProfile,
} from "../lib/repository-profile.js";
import { config } from "../config.js";
import { getDb } from "../db/index.js";
import * as cacheRepo from "../db/repositories/analysis-cache.js";
import * as experienceRepo from "../db/repositories/experiences.js";
import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";

const ANALYSIS_PIPELINE_VERSION = "profile-evidence-v3";

// ========== Pipeline 上下文 & 类型 ==========

export interface PipelineLifecycleCallbacks {
  onStageStart: (stage: StageName) => void;
  onStageDone: (stage: StageName) => void;
  onStatusChange: (status: import("../types/index.js").TaskStatus) => void;
  onCommitHash: (commitHash: string) => void;
}

export interface PipelineContext {
  taskId: string;
  repoUrl: string;
  branch: string;
  stageProgress: StageProgress;
  abortController: AbortController;
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
  let localPath: string | undefined;

  try {
    // 0. Clone
    sseManager.emit(ctx.taskId, {
      type: "task:created",
      taskId: ctx.taskId,
      status: "cloning",
    });

    const parsedRepo = parseRepoUrl(ctx.repoUrl);
    const repoCacheName = parsedRepo.owner && parsedRepo.repo ? `${parsedRepo.owner}_${parsedRepo.repo}` : ctx.taskId;
    const branchCacheKey = createHash("sha256").update(ctx.branch).digest("hex").slice(0, 12);
    const taskDir = path.join(process.cwd(), "data/repos", repoCacheName, branchCacheKey);
    const repoPreflight = await preflightGithubRepo(parsedRepo.owner, parsedRepo.repo);
    if (repoPreflight.status === "not_found") {
      throw {
        category: "clone_failed",
        message: `GitHub 仓库 ${parsedRepo.owner}/${parsedRepo.repo} 不存在或无访问权限`,
        retryable: false,
      };
    }

    const repoSizeKb = repoPreflight.status === "available" ? repoPreflight.sizeKb : null;
    const maxRepoSizeKb = config.MAX_REPO_SIZE_MB * 1024;
    if (repoSizeKb !== null && repoSizeKb > maxRepoSizeKb) {
      throw {
        category: "clone_failed",
        message: `仓库大小约 ${Math.ceil(repoSizeKb / 1024)}MB，超过 ${config.MAX_REPO_SIZE_MB}MB 限制`,
        retryable: false,
      };
    }

    let cloneResult;
    try {
      cloneResult = await cloneRepo(ctx.repoUrl, taskDir, ctx.branch, {
        depth: config.CLONE_DEPTH,
        timeoutMs: config.CLONE_TIMEOUT_MS,
      });
    } catch (err) {
      throw {
        category: "clone_failed",
        message: `仓库拉取失败: ${err instanceof Error ? err.message : String(err)}`,
        retryable: true,
      };
    }

    const { localPath: lp, commitHash, cached: repoCached } = cloneResult;
    localPath = lp;
    ctx.callbacks.onCommitHash(commitHash);

    if (repoCached) {
      sseManager.emit(ctx.taskId, { type: "stage:progress", stage: "explorer", message: "检测到本地仓库缓存，已拉取最新代码" });
    } else {
      sseManager.emit(ctx.taskId, { type: "stage:progress", stage: "explorer", message: "仓库 Clone 完成" });
    }

    // 缓存检查（clone 后、Explorer 前）
    const { owner: cacheOwner, repo: cacheRepoName } = parseRepoUrl(ctx.repoUrl);
    const db = getDb();
    const analysisCacheKey = `${ANALYSIS_PIPELINE_VERSION}:${commitHash}`;
    const cached = cacheRepo.findByCommit(
      db,
      cacheOwner,
      cacheRepoName,
      ctx.branch,
      analysisCacheKey,
    );

    if (cached) {
      const cachedResult = JSON.parse(cached.result) as AnalysisResult;
      return { result: cachedResult, cached: true };
    }

    const repositoryProfile = await buildRepositoryProfile(localPath);
    const repositoryOverview = buildRepositoryOverview(repositoryProfile);
    const fileCount = repositoryProfile.fileCount;
    sseManager.emit(ctx.taskId, {
      type: "stage:progress",
      stage: "explorer",
      message: `仓库画像构建完成（${fileCount} 个文件，${repositoryProfile.manifests.length} 个项目清单，${repositoryProfile.configFiles.length} 个工程配置${repositoryProfile.readme ? "，已读取 README" : ""}）`,
    });

    // 标记进入 analyzing
    ctx.callbacks.onStatusChange("analyzing");
    sseManager.emit(ctx.taskId, {
      type: "task:created",
      taskId: ctx.taskId,
      status: "analyzing",
    });

    // 1. Explorer：先规划，再由后端批量读取真实文件
    beginStage(ctx, "explorer");
    const explorerPlan = await planEvidenceWithRetry("explorer", {
      repositoryProfile,
    }, ctx, localPath);
    const explorerEvidence = await buildEvidenceBundle(
      localPath,
      explorerPlan,
      "explorer",
    );
    emitEvidenceReady(ctx, "explorer", explorerEvidence.files.length, explorerEvidence.totalBytes);

    const explorerOutput = await runStageWithRetry("explorer", {
      fileCount,
      repositoryProfile,
      evidenceBundle: explorerEvidence,
    }, ctx, localPath, true);

    // 交互点：Explorer 结果确认
    const explorerFeedback = await askUser(ctx, "q_explorer_review", "explorer",
      `识别项目类型为 ${explorerOutput.projectType.primary}，是否正确？`,
      ["是", "否，请纠正"]);

    // 2. Mentor
    const skillContent = loadSkillTemplates(
      explorerOutput.projectType.primary,
      explorerOutput.projectType.secondary,
    );
    const relevantExperiences = experienceRepo.findRelevant(
      db,
      explorerOutput.projectType.primary,
      explorerOutput.techStack.framework,
      explorerOutput.projectType.secondary,
      cacheOwner,
    ).filter((item) =>
      item.content.startsWith(`[${ANALYSIS_PIPELINE_VERSION}]\n`)
    );

    beginStage(ctx, "mentor");
    const mentorPlan = await planEvidenceWithRetry("mentor", {
      explorerOutput,
      repositoryProfile,
      existingEvidencePaths: explorerEvidence.files.map((file) => file.path),
      userFocus: explorerFeedback && explorerFeedback !== "是" ? explorerFeedback : undefined,
    }, ctx, localPath);
    const mentorEvidence = await buildEvidenceBundle(
      localPath,
      mentorPlan,
      "mentor",
      explorerEvidence.files.map((file) => file.path),
    );
    const evidenceBundle = {
      files: [...explorerEvidence.files, ...mentorEvidence.files],
      skippedPaths: [...explorerEvidence.skippedPaths, ...mentorEvidence.skippedPaths],
      totalBytes: explorerEvidence.totalBytes + mentorEvidence.totalBytes,
    };
    emitEvidenceReady(ctx, "mentor", mentorEvidence.files.length, mentorEvidence.totalBytes);
    emitAnalysisContextReady(ctx, "mentor", repositoryOverview, evidenceBundle.totalBytes);

    const mentorOutput = await runStageWithRetry("mentor", {
      explorerOutput,
      repositoryOverview,
      evidenceBundle,
      skillContent,
      experiences: relevantExperiences
        .map((item) => item.content.replace(
          `[${ANALYSIS_PIPELINE_VERSION}]\n`,
          "",
        ))
        .join("\n\n"),
      userFocus: explorerFeedback && explorerFeedback !== "是" ? explorerFeedback : undefined,
    }, ctx, localPath, true);

    // 交互点：依赖图反馈
    const deps = Object.entries(mentorOutput.dependencyGraph);
    const dependencyFocus = await askUser(ctx, "q_deps", "mentor",
      `依赖图包含 ${deps.length} 个模块。想深入了解哪个模块？`,
      deps.slice(0, 5).map(([mod]) => mod));

    // 3. Contributor
    const commitSummary = await extractCommitSummary(localPath);
    const repositoryContext = buildRepositoryContributionContext(repositoryProfile);
    const contributionEvidence = buildContributionEvidence(evidenceBundle);
    emitContributorContextReady(
      ctx,
      repositoryContext,
      contributionEvidence.totalBytes,
    );

    const contributorOutput = await runStageWithRetry("contributor", {
      explorerOutput,
      mentorOutput,
      repositoryContext,
      contributionEvidence,
      commitSummary,
      userFocus: dependencyFocus || undefined,
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
      commitHash: analysisCacheKey,
      result: analysisResult,
      projectTypePrimary: analysisResult.explorer.projectType.primary,
      framework: analysisResult.explorer.techStack.framework,
    });
    experienceRepo.save(getDb(), {
      owner: cacheOwner,
      repo: cacheRepoName,
      primaryType: analysisResult.explorer.projectType.primary,
      framework: analysisResult.explorer.techStack.framework,
      secondaryType: analysisResult.explorer.projectType.secondary,
      content: buildExperienceSummary(analysisResult),
    });

    return { result: analysisResult, cached: false };
  } finally {
    // 全局缓存机制下，不执行清理
    // if (localPath) {
    //   await cleanup(localPath);
    // }
  }
}

// ========== 带重试的阶段执行 ==========

function beginStage(ctx: PipelineContext, stage: StageName): void {
  ctx.callbacks.onStageStart(stage);
  sseManager.emit(ctx.taskId, { type: "stage:start", stage });
}

async function planEvidenceWithRetry(
  phase: "explorer" | "mentor",
  input: Record<string, unknown>,
  ctx: PipelineContext,
  localPath: string,
) {
  const maxRetries = 1;
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      return await orchestrateRepositoryEvidence(
        phase,
        input,
        {
          onProgress: (message) => {
            sseManager.emit(ctx.taskId, {
              type: "stage:progress",
              stage: phase,
              message,
            });
          },
          onField: () => {},
        },
        localPath,
        ctx.abortController,
      );
    } catch (err) {
      if (err instanceof ParseError) {
        emitError(ctx.taskId, {
          category: "parse_failed",
          message: `证据阅读计划输出校验失败: ${err.message}`,
          retryable: true,
        });
        throw {
          category: "parse_failed",
          message: err.message,
          retryable: true,
        };
      }
      if (err instanceof LLMError && attempt < maxRetries && err.retryable) {
        sseManager.emit(ctx.taskId, {
          type: "stage:progress",
          stage: phase,
          message: `Orchestrator 调用失败，重试中... (${attempt + 1}/${maxRetries})`,
        });
        continue;
      }
      if (err instanceof LLMError) {
        const category = err.timedOut ? "timeout" : "llm_failed";
        emitError(ctx.taskId, {
          category,
          message: err.message,
          retryable: err.retryable,
        });
        throw { category, message: err.message, retryable: err.retryable };
      }
      throw err;
    }
  }
  throw new Error("unreachable");
}

function emitEvidenceReady(
  ctx: PipelineContext,
  stage: "explorer" | "mentor",
  fileCount: number,
  totalBytes: number,
): void {
  sseManager.emit(ctx.taskId, {
    type: "stage:progress",
    stage,
    message: `${stage === "explorer" ? "Explorer" : "Mentor"} 定向证据已就绪（${fileCount} 个文件，${Math.ceil(totalBytes / 1024)}KB）`,
  });
}

function emitAnalysisContextReady(
  ctx: PipelineContext,
  stage: "mentor",
  repositoryContext: unknown,
  evidenceBytes: number,
): void {
  const repositoryBytes = Buffer.byteLength(JSON.stringify(repositoryContext), "utf8");
  sseManager.emit(ctx.taskId, {
    type: "stage:progress",
    stage,
    message: `Mentor 分析上下文已准备（仓库概览 ${Math.ceil(repositoryBytes / 1024)}KB，源码证据 ${Math.ceil(evidenceBytes / 1024)}KB）`,
  });
}

function emitContributorContextReady(
  ctx: PipelineContext,
  repositoryContext: unknown,
  evidenceBytes: number,
): void {
  const contextBytes = Buffer.byteLength(JSON.stringify(repositoryContext), "utf8");
  sseManager.emit(ctx.taskId, {
    type: "stage:progress",
    stage: "contributor",
    message: `Contributor 分析上下文已准备（贡献资料 ${Math.ceil(contextBytes / 1024)}KB，重点源码 ${Math.ceil(evidenceBytes / 1024)}KB）`,
  });
}

async function runStageWithRetry<S extends StageName>(
  stage: S,
  input: Record<string, unknown>,
  ctx: PipelineContext,
  localPath: string,
  stageAlreadyStarted = false,
): Promise<StageOutputFor<S>> {
  const maxRetries = 1;

  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      return await runStageWithSSE(
        stage,
        input,
        ctx,
        localPath,
        stageAlreadyStarted,
      );
    } catch (err) {
      if (err instanceof ParseError) {
        const agentName = stage.charAt(0).toUpperCase() + stage.slice(1);
        emitError(ctx.taskId, {
          category: "parse_failed",
          message: `${agentName} 输出校验失败（已尝试自动修复）: ${err.message}`,
          retryable: true,
        });
        throw { category: "parse_failed", message: err.message, retryable: true };
      }

      if (err instanceof LLMError) {
        if (err.timedOut) {
          if (err.timeoutKind === "first_response" && attempt < maxRetries) {
            const agentName = stage.charAt(0).toUpperCase() + stage.slice(1);
            sseManager.emit(ctx.taskId, {
              type: "stage:progress",
              stage,
              message: `${agentName} 首次响应超时，正在重试... (${attempt + 1}/${maxRetries})`,
            });
            continue;
          }
          emitError(ctx.taskId, {
            category: "timeout",
            message: err.message,
            retryable: true,
          });
          throw { category: "timeout", message: err.message, retryable: true };
        }
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
  stageAlreadyStarted = false,
): Promise<StageOutputFor<S>> {
  if (!stageAlreadyStarted) beginStage(ctx, stage);

  const result = await runStage(stage, input, {
    onProgress: (message: string) => {
      sseManager.emit(ctx.taskId, { type: "stage:progress", stage, message });
    },
    onField: (field: string, value: unknown) => {
      sseManager.emit(ctx.taskId, { type: "stage:field", stage, field, value });
    },
  }, localPath, ctx.abortController);

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

function loadSkillTemplates(primaryType: string, secondaryTypes: string[]): string {
  const selectedTypes = [primaryType, ...secondaryTypes]
    .map((type) => SKILL_TEMPLATES[type] ? type : "unknown")
    .filter((type, index, values) => values.indexOf(type) === index);

  return selectedTypes
    .map((type) => {
      const filePath = SKILL_TEMPLATES[type]!;
      try {
        return fs.readFileSync(filePath, "utf-8");
      } catch {
        return "";
      }
    })
    .filter(Boolean)
    .join("\n\n---\n\n");
}

function buildExperienceSummary(result: AnalysisResult): string {
  const patterns = result.mentor.keyPatterns
    .slice(0, 3)
    .map((item) => `${item.pattern}（${item.where}）`)
    .join("、");
  const conventions = result.mentor.codeConventions
    .slice(0, 3)
    .map((item) => item.rule)
    .join("；");

  return [
    `[${ANALYSIS_PIPELINE_VERSION}]`,
    result.mentor.architectureOverview.slice(0, 800),
    patterns ? `关键模式：${patterns}` : "",
    conventions ? `代码约定：${conventions}` : "",
  ].filter(Boolean).join("\n");
}

// ========== 错误广播 ==========

function emitError(taskId: string, error: TaskError): void {
  sseManager.emit(taskId, { type: "task:error", taskId, error });
}
