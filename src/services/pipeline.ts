import type {
  StageName,
  StageProgress,
  ExplorerOutput,
  MentorOutput,
  ContributorOutput,
  AnalysisResult,
  TaskError,
  CommitSummary,
  EvidencePlan,
  HarnessTraceEntry,
  AnalysisRuntimeKind,
  AgentEvidenceRequest,
  EvidenceBundle,
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
  parseRepoUrl,
  preflightGithubRepo,
  resolveRepositoryCachePath,
} from "../lib/repo.js";
import { config } from "../config.js";
import { getDb } from "../db/index.js";
import * as cacheRepo from "../db/repositories/analysis-cache.js";
import * as experienceRepo from "../db/repositories/experiences.js";
import { createHash } from "node:crypto";
import type { ModelSettings } from "./model-settings.js";
import { RepositoryHarness } from "./repository-harness.js";
import { loadHarnessSkill } from "./harness-skills.js";
import {
  AgentRuntimeError,
  ClaudeAgentRuntime,
  isAgentCapabilityKnownUnsupported,
  rememberUnsupportedAgentCapability,
} from "./claude-agent-runtime.js";

const ANALYSIS_PIPELINE_VERSION = "bounded-agent-v8";

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
  modelSettings: ModelSettings;
  callbacks: PipelineLifecycleCallbacks;
  pendingQuestion: {
    questionId: string;
    stage: StageName;
    question: string;
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
    const settingsError = validateAnalysisRuntimeSettings(ctx.modelSettings);
    if (settingsError) throw settingsError;
    // 0. Clone
    sseManager.emit(ctx.taskId, {
      type: "task:created",
      taskId: ctx.taskId,
      status: "cloning",
    });

    const parsedRepo = parseRepoUrl(ctx.repoUrl);
    const taskDir = resolveRepositoryCachePath(ctx.repoUrl, ctx.branch, ctx.taskId);
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
    const modelSettings = ctx.modelSettings;
    const providerFingerprint = createHash("sha256")
      .update(`${modelSettings.provider}\0${modelSettings.baseUrl}\0${modelSettings.model}`)
      .digest("hex")
      .slice(0, 12);
    let analysisCacheKey = "";

    const harness = new RepositoryHarness(
      localPath,
      (entry) => emitHarnessTrace(ctx, entry),
    );
    const {
      profile: repositoryProfile,
      overview: repositoryOverview,
    } = await harness.getRepositoryMap();
    const repositoryProfileEvidencePaths = [
      repositoryProfile.readme?.path,
      ...repositoryProfile.manifests.map((file) => file.path),
      ...repositoryProfile.exampleManifests.map((file) => file.path),
      ...repositoryProfile.configFiles.map((file) => file.path),
      ...repositoryProfile.guidanceFiles.map((file) => file.path),
    ].filter((value): value is string => Boolean(value));
    const fileCount = repositoryProfile.fileCount;
    sseManager.emit(ctx.taskId, {
      type: "stage:progress",
      stage: "explorer",
      message: `仓库画像构建完成（${fileCount} 个文件，${repositoryProfile.manifests.length} 个项目清单，${repositoryProfile.configFiles.length} 个工程配置${repositoryProfile.readme ? "，已读取 README" : ""}）`,
    });

    const explorerSkill = loadHarnessSkill(["unknown"], "explorer");
    harness.activateSkillPolicy("explorer", explorerSkill.policy);
    const completeCoveragePlan = harness.createCompleteCoveragePlan(
      "explorer",
      explorerSkill.policy,
    );
    let runtimeKind = selectAnalysisRuntimeKind(
      ctx.modelSettings,
      Boolean(completeCoveragePlan),
    );
    const expectedCacheRuntimeKind: AnalysisRuntimeKind = config.MENTOR_SUBAGENTS_ENABLED
      && ctx.modelSettings.provider === "anthropic-compatible"
      && !isAgentCapabilityKnownUnsupported(ctx.modelSettings)
      ? "mentor-multi-agent"
      : runtimeKind;
    analysisCacheKey = buildAnalysisCacheKey(
      providerFingerprint,
      commitHash,
      expectedCacheRuntimeKind,
    );
    const cached = cacheRepo.findByCommit(
      db,
      cacheOwner,
      cacheRepoName,
      ctx.branch,
      analysisCacheKey,
    );
    if (cached) {
      const cachedResult = JSON.parse(cached.result) as AnalysisResult;
      emitHarnessTrace(ctx, {
        stage: "repository",
        kind: "decision",
        title: "已复用现有报告",
        summary: "仓库提交、模型配置与实际分析 Runtime 均匹配已有报告。",
        metadata: { cached: true, runtimeKind: expectedCacheRuntimeKind },
      });
      return { result: cachedResult, cached: true };
    }
    emitRuntimeSelection(ctx, expectedCacheRuntimeKind, completeCoveragePlan
      ? "仓库满足完整覆盖条件"
      : "根据 Provider 与 AGENT_RUNTIME_MODE 自适应选择");

    // 标记进入 analyzing
    ctx.callbacks.onStatusChange("analyzing");
    sseManager.emit(ctx.taskId, {
      type: "task:created",
      taskId: ctx.taskId,
      status: "analyzing",
    });

    // 1. Explorer：小仓库直接覆盖，其他仓库由 Orchestrator 定向规划
    beginStage(ctx, "explorer");
    const explorerPlanning = completeCoveragePlan
      ? { plan: completeCoveragePlan, runtimeKind: "workflow" as const }
      : await planEvidenceAdaptively({
          phase: "explorer",
          repositoryProfile,
          skillPolicy: explorerSkill.policy,
          sdkSkillNames: explorerSkill.sdkSkillNames,
          harnessState: harness.getContextView(),
          existingEvidencePaths: [],
        }, runtimeKind, harness, ctx, localPath);
    runtimeKind = explorerPlanning.runtimeKind;
    const explorerPlan = explorerPlanning.plan;
    const explorerEvidence = await harness.executeEvidencePlan(
      "explorer",
      explorerPlan,
    );
    const completeCoverageAchieved = Boolean(completeCoveragePlan)
      && harness.hasCompleteEvidenceCoverage("explorer");
    emitEvidenceReady(ctx, "explorer", explorerEvidence.files.length, explorerEvidence.totalBytes);

    const explorerStageOutput = await runStageWithRetry("explorer", {
      fileCount,
      repositoryProfile,
      evidenceBundle: explorerEvidence,
      harnessState: harness.getContextView(),
    }, ctx, localPath, true);
    const explorerOutput = harness.verifyEvidenceOutput(
      "explorer",
      explorerStageOutput,
      repositoryProfileEvidencePaths,
    );
    harness.completeStage("explorer");

    // 2. Mentor
    const mentorSkill = loadHarnessSkill([
      explorerOutput.projectType.primary,
      ...explorerOutput.projectType.secondary,
    ], "mentor");
    harness.activateSkillPolicy("mentor", mentorSkill.policy);
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
    const mentorEvidence = completeCoverageAchieved
      ? harness.reuseExistingEvidenceForStage("mentor")
      : await (async () => {
          const reusableEvidencePaths = harness.getContextView().evidenceIndex
            .filter((file) => !file.truncated)
            .map((file) => file.path);
          const mentorPlanning = await planEvidenceAdaptively({
            phase: "mentor",
            explorerOutput,
            repositoryProfile,
            existingEvidencePaths: reusableEvidencePaths,
            skillPolicy: mentorSkill.policy,
            sdkSkillNames: mentorSkill.sdkSkillNames,
            harnessState: harness.getContextView(),
          }, runtimeKind, harness, ctx, localPath);
          runtimeKind = mentorPlanning.runtimeKind;
          const mentorPlan = mentorPlanning.plan;
          return harness.executeEvidencePlan(
            "mentor",
            mentorPlan,
            reusableEvidencePaths,
          );
        })();
    const evidenceBundle = harness.getEvidenceBundle();
    emitEvidenceReady(ctx, "mentor", mentorEvidence.files.length, mentorEvidence.totalBytes);
    emitAnalysisContextReady(ctx, "mentor", repositoryOverview, evidenceBundle.totalBytes);

    const mentorInput = {
      explorerOutput,
      repositoryOverview,
      evidenceBundle,
      skillContent: mentorSkill.promptContent,
      experiences: relevantExperiences
        .map((item) => item.content.replace(
          `[${ANALYSIS_PIPELINE_VERSION}]\n`,
          "",
        ))
        .join("\n\n"),
      harnessState: harness.getContextView(),
    };
    const mentorSynthesis = await runMentorSynthesis(
      mentorInput,
      evidenceBundle,
      mentorSkill.sdkSkillNames,
      runtimeKind,
      ctx,
      localPath,
    );
    runtimeKind = mentorSynthesis.runtimeKind;
    const mentorStageOutput = mentorSynthesis.output;
    const mentorOutput = harness.verifyEvidenceOutput("mentor", mentorStageOutput);
    harness.completeStage("mentor");

    // 3. Contributor
    const commitSummary = await harness.inspectGitHistory();
    const { repositoryContext, contributionEvidence } =
      harness.prepareContributorContext();
    emitContributorContextReady(
      ctx,
      repositoryContext,
      contributionEvidence.totalBytes,
    );

    const contributorStageOutput = await runStageWithRetry("contributor", {
      explorerOutput,
      mentorOutput,
      repositoryContext,
      contributionEvidence,
      commitSummary,
      harnessState: harness.getContextView(),
    }, ctx, localPath);
    const contributorOutput = harness.verifyEvidenceOutput(
      "contributor",
      contributorStageOutput,
      [
        ...repositoryContext.manifests.map((file) => file.path),
        ...repositoryContext.exampleManifests.map((file) => file.path),
        ...repositoryContext.configFiles.map((file) => file.path),
        ...repositoryContext.guidanceFiles.map((file) => file.path),
        ...repositoryContext.todoMarkers.map((marker) => marker.file),
      ],
    );
    harness.completeStage("contributor");

    const analysisResult: AnalysisResult = {
      explorer: explorerOutput,
      mentor: mentorOutput,
      contributor: contributorOutput,
    };

    // 保存缓存
    analysisCacheKey = buildAnalysisCacheKey(
      providerFingerprint,
      commitHash,
      runtimeKind,
    );
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
  emitHarnessTrace(ctx, {
    stage,
    kind: "decision",
    title: `${formatStageName(stage)} 阶段已启动`,
    summary: stage === "contributor"
      ? "正在依据已校验的仓库上下文和前序结论生成贡献指南。"
      : "正在为当前阶段准备有仓库证据支撑的分析。",
  });
}

export function selectAnalysisRuntimeKind(
  settings: ModelSettings,
  completeCoverage: boolean,
): AnalysisRuntimeKind {
  if (completeCoverage || config.AGENT_RUNTIME_MODE === "workflow") return "workflow";
  if (settings.provider === "openai-compatible") return "workflow";
  if (isAgentCapabilityKnownUnsupported(settings)) return "workflow";
  return "bounded-agent";
}

export function validateAnalysisRuntimeSettings(
  settings: ModelSettings,
  runtimeMode = config.AGENT_RUNTIME_MODE,
): TaskError | null {
  if (runtimeMode === "agentic" && settings.provider === "openai-compatible") {
    return {
      category: "invalid_model_settings",
      message: "AGENT_RUNTIME_MODE=agentic 不能与 OpenAI-compatible Provider 同时使用；请改用 adaptive 或 workflow",
      retryable: false,
    };
  }
  return null;
}

function buildAnalysisCacheKey(
  providerFingerprint: string,
  commitHash: string,
  runtimeKind: AnalysisRuntimeKind,
): string {
  return `${ANALYSIS_PIPELINE_VERSION}:${runtimeKind}:${providerFingerprint}:${commitHash}`;
}

function emitRuntimeSelection(
  ctx: PipelineContext,
  runtimeKind: AnalysisRuntimeKind,
  reason: string,
): void {
  emitHarnessTrace(ctx, {
    stage: "repository",
    kind: "decision",
    title: "分析 Runtime 已选择",
    summary: `${reason}，本次使用 ${runtimeKind}。`,
    metadata: {
      runtimeKind,
      runtimeMode: config.AGENT_RUNTIME_MODE,
      provider: ctx.modelSettings.provider,
    },
  });
}

async function planEvidenceAdaptively(
  request: AgentEvidenceRequest,
  requestedRuntimeKind: AnalysisRuntimeKind,
  harness: RepositoryHarness,
  ctx: PipelineContext,
  localPath: string,
): Promise<{ plan: EvidencePlan; runtimeKind: AnalysisRuntimeKind }> {
  if (requestedRuntimeKind !== "bounded-agent") {
    return {
      plan: await planEvidenceWithRetry(request.phase, {
        ...request,
        sdkSkillNames: undefined,
      }, ctx, localPath),
      runtimeKind: "workflow",
    };
  }

  const runtime = new ClaudeAgentRuntime(
    ctx.modelSettings,
    (entry) => emitHarnessTrace(ctx, entry),
  );
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const result = await runtime.planEvidence({
        ...request,
        harnessState: harness.getContextView(),
      }, harness, ctx.abortController);
      emitHarnessTrace(ctx, {
        stage: request.phase,
        kind: "decision",
        title: `受限 ${request.phase === "explorer" ? "Explorer" : "Mentor"} Research Agent 已完成`,
        summary: "Agent 仅通过 RepoMentor 进程内 MCP 选择证据；源码将在循环外由 Harness 批量读取一次。",
        metadata: {
          runtimeKind: "bounded-agent",
          turns: result.turns,
          toolCalls: Object.values(result.toolCalls).reduce((sum, count) => sum + count, 0),
          tokens: countUsageTokens(result.modelUsage ?? result.usage),
          costUsd: result.costUsd ?? 0,
        },
      });
      return { plan: result.evidencePlan, runtimeKind: "bounded-agent" };
    } catch (error) {
      if (!(error instanceof AgentRuntimeError)) throw error;
      if (error.cancelled || ctx.abortController.signal.aborted) {
        throw {
          category: "timeout",
          message: error.message,
          retryable: false,
        } satisfies TaskError;
      }
      if (error.fallbackEligible) {
        if (error.rememberUnsupported) {
          rememberUnsupportedAgentCapability(ctx.modelSettings);
        }
        emitHarnessTrace(ctx, {
          stage: request.phase,
          kind: "decision",
          title: "受限 Agent 已降级为 Workflow",
          summary: `${error.message}；Harness 将使用现有 Orchestrator 规划，且不会重复已完成的源码读取。`,
          metadata: {
            from: "bounded-agent",
            to: "workflow",
            reason: error.message.slice(0, 300),
          },
        });
        const plan = await planEvidenceWithRetry(request.phase, {
          ...request,
          sdkSkillNames: undefined,
          harnessState: harness.getContextView(),
        }, ctx, localPath);
        return { plan, runtimeKind: "workflow" };
      }
      if (error.retryable && attempt === 0) {
        sseManager.emit(ctx.taskId, {
          type: "stage:progress",
          stage: request.phase,
          message: "受限 Agent 调用遇到临时错误，正在按原有语义重试一次...",
        });
        continue;
      }
      throw {
        category: "llm_failed",
        message: error.message,
        retryable: error.retryable,
      } satisfies TaskError;
    }
  }
  throw new Error("unreachable");
}

async function runMentorSynthesis(
  input: Record<string, unknown>,
  evidenceBundle: EvidenceBundle,
  sdkSkillNames: string[],
  currentRuntimeKind: AnalysisRuntimeKind,
  ctx: PipelineContext,
  localPath: string,
): Promise<{ output: MentorOutput; runtimeKind: AnalysisRuntimeKind }> {
  const multiAgentEligible = config.MENTOR_SUBAGENTS_ENABLED
    && ctx.modelSettings.provider === "anthropic-compatible"
    && !isAgentCapabilityKnownUnsupported(ctx.modelSettings);
  if (!multiAgentEligible) {
    return {
      output: await runStageWithRetry("mentor", input, ctx, localPath, true),
      runtimeKind: currentRuntimeKind,
    };
  }

  const runtime = new ClaudeAgentRuntime(
    ctx.modelSettings,
    (entry) => emitHarnessTrace(ctx, entry),
  );
  try {
    const result = await runtime.synthesizeMentorWithSubagents(
      input,
      evidenceBundle,
      sdkSkillNames,
      ctx.abortController,
    );
    ctx.callbacks.onStageDone("mentor");
    sseManager.emit(ctx.taskId, {
      type: "stage:done",
      stage: "mentor",
      output: result.output,
    });
    emitHarnessTrace(ctx, {
      stage: "mentor",
      kind: "decision",
      title: "Mentor 双子智能体综合已完成",
      summary: "架构分析与学习路径审阅各委派一次，且只访问 Harness 已读取的裁剪证据视图。",
      metadata: {
        runtimeKind: "mentor-multi-agent",
        delegations: 2,
        turns: result.turns,
        tokens: countUsageTokens(result.modelUsage ?? result.usage),
        costUsd: result.costUsd ?? 0,
        schemaValidated: true,
      },
    });
    return { output: result.output, runtimeKind: "mentor-multi-agent" };
  } catch (error) {
    if (error instanceof AgentRuntimeError && (error.cancelled || ctx.abortController.signal.aborted)) {
      throw {
        category: "timeout",
        message: error.message,
        retryable: false,
      } satisfies TaskError;
    }
    emitHarnessTrace(ctx, {
      stage: "mentor",
      kind: "decision",
      title: "Mentor 双子智能体已回退",
      summary: `${error instanceof Error ? error.message : String(error)}；将复用相同证据执行单 Mentor 综合，不重新读取仓库。`,
      metadata: {
        from: "mentor-multi-agent",
        to: currentRuntimeKind,
        evidenceReread: false,
      },
    });
    return {
      output: await runStageWithRetry("mentor", input, ctx, localPath, true),
      runtimeKind: currentRuntimeKind,
    };
  }
}

async function planEvidenceWithRetry(
  phase: "explorer" | "mentor",
  input: Record<string, unknown>,
  ctx: PipelineContext,
  localPath: string,
): Promise<EvidencePlan> {
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
        ctx.modelSettings,
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
  }, localPath, ctx.abortController, ctx.modelSettings);

  ctx.callbacks.onStageDone(stage);
  sseManager.emit(ctx.taskId, { type: "stage:done", stage, output: result });
  emitHarnessTrace(ctx, {
    stage,
    kind: "decision",
    title: `${formatStageName(stage)} 综合分析已完成`,
    summary: "结构化阶段输出已通过 Schema 校验，可以进入下一步。",
    metadata: { schemaValidated: true },
  });

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
      emitHarnessTrace(ctx, {
        stage,
        kind: "decision",
        title: "用户输入已超时",
        summary: `未收到问题“${question}”的回答，分析流程将在没有额外关注点的情况下继续。`,
        metadata: { questionId, answered: false },
      });
      ctx.pendingQuestion = null;
      resolve("");
    }, config.INTERACTION_TIMEOUT_MS);

    ctx.pendingQuestion = { questionId, stage, question, resolve, timer };
  });
}

export function resolveQuestion(ctx: PipelineContext, questionId: string, answer: string): boolean {
  if (!ctx.pendingQuestion || ctx.pendingQuestion.questionId !== questionId) {
    return false;
  }
  clearTimeout(ctx.pendingQuestion.timer);
  emitHarnessTrace(ctx, {
    stage: ctx.pendingQuestion.stage,
    kind: "decision",
    title: "用户指引已应用",
    summary: `问题“${ctx.pendingQuestion.question}”的回答已加入下一分析步骤。`,
    metadata: { questionId, answered: true },
  });
  ctx.pendingQuestion.resolve(answer);
  ctx.pendingQuestion = null;
  return true;
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

function countUsageTokens(value: unknown): number {
  if (!value || typeof value !== "object") return 0;
  let total = 0;
  for (const [key, nested] of Object.entries(value)) {
    if (
      typeof nested === "number"
      && /^(input|output)_?tokens$/i.test(key)
    ) {
      total += nested;
    } else if (nested && typeof nested === "object") {
      total += countUsageTokens(nested);
    }
  }
  return total;
}

function emitHarnessTrace(ctx: PipelineContext, entry: HarnessTraceEntry): void {
  const timestamp = entry.timestamp ?? new Date().toISOString();
  sseManager.emit(ctx.taskId, {
    type: "harness:trace",
    trace: { ...entry, timestamp },
    timestamp,
  });
}

function formatStageName(stage: StageName): string {
  return stage.charAt(0).toUpperCase() + stage.slice(1);
}
