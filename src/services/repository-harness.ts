import type {
  ContributionEvidence,
  CommitSummary,
  EvidenceBundle,
  EvidenceFirstOutput,
  EvidenceGap,
  EvidencePhase,
  EvidencePlan,
  HarnessSkillPolicy,
  HarnessContextView,
  HarnessState,
  HarnessToolObservation,
  StageName,
  HarnessToolName,
  HarnessTraceEntry,
  RepositoryContributionContext,
  RepositoryOverview,
  RepositoryProfile,
} from "../types/index.js";
import {
  buildContributionEvidence,
  buildEvidenceBundle,
  buildRepositoryContributionContext,
  buildRepositoryOverview,
  buildRepositoryProfile,
} from "../lib/repository-profile.js";
import { extractCommitSummary } from "../lib/repo.js";
import {
  findRepositoryRelatedTests,
  searchRepositorySymbols,
  traceRepositoryDependencies,
} from "../lib/repository-tools.js";

export interface HarnessToolDefinition {
  name: HarnessToolName;
  purpose: string;
  deterministic: true;
}

/**
 * RepoMentor 的领域工具契约。Provider 只负责推理，文件访问、边界和
 * 运行轨迹由这一层统一控制，因此不同模型厂商得到相同的执行语义。
 */
export const REPOSITORY_HARNESS_TOOLS: readonly HarnessToolDefinition[] = [
  {
    name: "get_repository_map",
    purpose: "构建仓库文件索引、工程画像和候选入口，不返回仓库外内容",
    deterministic: true,
  },
  {
    name: "search_symbols",
    purpose: "在有界源码集合中定位符号或概念，并返回候选证据路径与行号摘要",
    deterministic: true,
  },
  {
    name: "trace_module_dependencies",
    purpose: "从已知源码入口提取并解析仓库内静态依赖边",
    deterministic: true,
  },
  {
    name: "find_related_tests",
    purpose: "依据文件名和目录关系为源码定位代表性测试",
    deterministic: true,
  },
  {
    name: "read_evidence_batch",
    purpose: "按已校验的证据计划批量读取仓库内文件，并执行文件数与字节预算",
    deterministic: true,
  },
  {
    name: "inspect_git_history",
    purpose: "提取近期提交主题、常改文件和贡献者数量，不执行仓库代码",
    deterministic: true,
  },
  {
    name: "prepare_contributor_context",
    purpose: "从已检查证据和工程资料中生成贡献分析上下文",
    deterministic: true,
  },
] as const;

export type HarnessTraceSink = (entry: HarnessTraceEntry) => void;

type EvidenceLedgerStatus = "available" | "partial" | "missing";

interface EvidenceReadAttempt {
  phase: EvidencePhase;
  status: EvidenceLedgerStatus | "reused";
  bytes: number;
  reason?: EvidenceBundle["omissions"][number]["reason"];
}

interface EvidenceLedgerEntry {
  path: string;
  bestEvidence: EvidenceBundle["files"][number] | null;
  omission: EvidenceBundle["omissions"][number] | null;
  attempts: EvidenceReadAttempt[];
}

const HARNESS_BUDGET = {
  maxEvidenceBatches: 2,
  maxFilesRead: 18,
  maxEvidenceBytes: 88_000,
} as const;

const COMPLETE_COVERAGE_MAX_REPOSITORY_FILES = 60;
const COMPLETE_COVERAGE_EXTENSIONS = new Set([
  ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
  ".py", ".go", ".rs", ".java", ".kt", ".kts",
  ".rb", ".php", ".cs", ".cpp", ".cc", ".c", ".h",
  ".swift", ".dart", ".ex", ".exs", ".vue", ".svelte",
  ".sol", ".sh", ".ipynb", ".html", ".css", ".scss", ".sass", ".less",
  ".sql", ".proto", ".graphql", ".gql", ".md", ".mdx", ".rst",
]);
const COMPLETE_COVERAGE_IGNORED_EXTENSIONS = new Set([
  ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".svg",
  ".mp3", ".mp4", ".wav", ".mov", ".avi", ".pdf", ".zip", ".gz",
  ".woff", ".woff2", ".ttf", ".eot", ".lock",
]);

export class HarnessInvariantError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "HarnessInvariantError";
  }
}

export class RepositoryHarness {
  private readonly state: HarnessState = createInitialState();
  private readonly verifiedClaimPaths = new Set<string>();
  private readonly evidenceLedger = new Map<string, EvidenceLedgerEntry>();
  private completeCoverageRequested = false;

  constructor(
    private readonly localPath: string,
    private readonly onTrace: HarnessTraceSink = () => {},
  ) {}

  activateSkillPolicy(
    phase: EvidencePhase,
    policy: HarnessSkillPolicy,
  ): void {
    const stage = this.state.stages[phase];
    if (stage.status !== "pending" || stage.skillPolicy) {
      throw new HarnessInvariantError(`${formatStage(phase)} Skill 策略只能在规划前激活一次`);
    }
    stage.skillPolicy = cloneSkillPolicy(policy);
    this.onTrace({
      stage: phase,
      kind: "decision",
      title: `${formatStage(phase)} 分析策略已激活`,
      summary: `已将 ${policy.skillNames.join("、")} 转换为当前阶段可执行的 Harness 约束。`,
      metadata: {
        skills: policy.skillNames.join(","),
        allowedTools: policy.allowedTools.length,
        maxActions: policy.maxDiscoveryActions,
        maxFiles: policy.maxEvidenceFiles,
      },
    });
  }

  async getRepositoryMap(): Promise<{
    profile: RepositoryProfile;
    overview: RepositoryOverview;
  }> {
    const profile = await buildRepositoryProfile(this.localPath);
    const overview = buildRepositoryOverview(profile);
    this.state.repositoryProfile = profile;
    this.state.repositoryOverview = overview;
    const projectFiles = [
      profile.readme?.path,
      ...profile.manifests.map((file) => file.path),
      ...profile.configFiles.map((file) => file.path),
      ...profile.guidanceFiles.map((file) => file.path),
    ].filter((file): file is string => Boolean(file));

    this.onTrace({
      stage: "repository",
      kind: "tool",
      title: "仓库画像已构建",
      summary: `已索引 ${profile.fileCount} 个文件，并识别供后续分析使用的项目结构。`,
      tool: "get_repository_map",
      files: projectFiles.slice(0, 12),
      metadata: {
        files: profile.fileCount,
        manifests: profile.manifests.length,
        configs: profile.configFiles.length,
        readme: Boolean(profile.readme),
        indexTruncated: profile.fileIndexTruncated,
      },
    });

    return { profile, overview };
  }

  /**
   * 小仓库无需再调用规划 Agent 来决定读哪些文件：当全部相关源码、测试和
   * 示例都能落在当前 Skill 文件预算内时，Harness 直接建立完整覆盖计划。
   */
  createCompleteCoveragePlan(
    phase: EvidencePhase,
    policy: HarnessSkillPolicy,
  ): EvidencePlan | null {
    const profile = this.state.repositoryProfile;
    const stage = this.state.stages[phase];
    if (!profile || stage.status !== "pending") {
      return null;
    }
    if (
      phase !== "explorer"
      || profile.fileIndexTruncated
      || profile.fileCount > COMPLETE_COVERAGE_MAX_REPOSITORY_FILES
    ) {
      return null;
    }

    const profileContentPaths = new Set([
      profile.readme?.path,
      ...profile.manifests.map((file) => file.path),
      ...profile.exampleManifests.map((file) => file.path),
      ...profile.configFiles.map((file) => file.path),
      ...profile.guidanceFiles.map((file) => file.path),
    ].filter((value): value is string => Boolean(value)).map(normalizePath));
    const unprofiledFiles = profile.fileIndex
      .map(normalizePath)
      .filter((file) => !profileContentPaths.has(file));
    const unsupportedFiles = unprofiledFiles.filter((file) =>
      !isCompleteCoverageFile(file) && !isCompleteCoverageIgnoredFile(file)
    );
    if (unsupportedFiles.length > 0) return null;
    const candidates = unprofiledFiles.filter(isCompleteCoverageFile);
    if (candidates.length === 0 || candidates.length > policy.maxEvidenceFiles) {
      return null;
    }

    const entryPaths = new Set(profile.entryCandidates.map(normalizePath));
    const testPaths = new Set(profile.testCandidates.map(normalizePath));
    const plan: EvidencePlan = {
      goal: "完整覆盖小型仓库的源码、测试与可执行示例",
      rationale: "全部相关文件均可落入当前证据预算，无需额外模型规划调用",
      questions: policy.recommendedQuestions.slice(0, 6),
      actions: [],
      files: candidates.map((file) => ({
        path: file,
        purpose: testPaths.has(file)
          ? "确认核心行为的测试覆盖与边界条件"
          : file.endsWith(".ipynb")
            ? "确认端到端示例与使用方式"
            : entryPaths.has(file)
              ? "确认公共入口与核心控制流"
              : "完整覆盖小型仓库的实现模块",
        priority: entryPaths.has(file) || testPaths.has(file) ? "high" : "medium",
      })),
      stopConditions: [...new Set(policy.stopConditions)].slice(0, 4),
    };
    this.completeCoverageRequested = true;

    this.onTrace({
      stage: phase,
      kind: "decision",
      title: "已启用小仓库直接覆盖路径",
      summary: `Harness 将直接读取 ${plan.files.length} 个相关文件，跳过额外的证据规划模型调用。`,
      files: plan.files.map((file) => file.path),
      metadata: { repositoryFiles: profile.fileCount, evidenceFiles: plan.files.length },
    });
    return plan;
  }

  reuseExistingEvidenceForStage(phase: EvidencePhase): EvidenceBundle {
    const evidence = this.getEvidenceBundle();
    if (
      phase !== "mentor"
      || evidence.files.length === 0
      || !this.hasCompleteEvidenceCoverage("explorer")
    ) {
      throw new HarnessInvariantError("只有 Mentor 可以复用已完整读取的小仓库证据集");
    }
    const plan: EvidencePlan = {
      goal: "复用完整仓库证据进行架构分析",
      rationale: "Explorer 已读取小仓库全部相关文件，无需重复规划或读取",
      questions: [],
      actions: [],
      files: [],
      stopConditions: ["复用完整证据集完成架构分析"],
    };
    this.recordEvidencePlan(phase, plan);
    const stage = this.state.stages[phase];
    stage.status = "evidence_ready";
    stage.examinedPaths = evidence.files.map((file) => file.path);
    stage.stopReason = "existing_evidence_reused";
    this.onTrace({
      stage: phase,
      kind: "decision",
      title: "已复用完整仓库证据",
      summary: `Mentor 将复用 Explorer 已读取的 ${evidence.files.length} 个文件，不再启动第二次证据规划。`,
      files: evidence.files.map((file) => file.path),
      metadata: { evidenceFiles: evidence.files.length, evidenceBytes: evidence.totalBytes },
    });
    return evidence;
  }

  hasCompleteEvidenceCoverage(phase: EvidencePhase): boolean {
    const stage = this.state.stages[phase];
    if (
      phase !== "explorer"
      || !this.completeCoverageRequested
      || (stage.status !== "evidence_ready" && stage.status !== "completed")
      || !stage.plan
      || stage.plan.files.length === 0
    ) {
      return false;
    }
    const examined = new Set(stage.examinedPaths.map(normalizePath));
    const truncated = new Set(
      this.state.evidence.filter((file) => file.truncated).map((file) => normalizePath(file.path)),
    );
    return stage.skippedPaths.length === 0
      && stage.plan.files.every((request) => {
        const path = normalizePath(request.path);
        return examined.has(path) && !truncated.has(path);
      });
  }

  recordEvidencePlan(phase: EvidencePhase, plan: EvidencePlan): void {
    if (!this.state.repositoryProfile) {
      throw new HarnessInvariantError("制定证据计划前必须先建立仓库画像");
    }
    if (phase === "mentor" && this.state.stages.explorer.status !== "completed") {
      throw new HarnessInvariantError("Mentor 证据规划必须在 Explorer 完成后进行");
    }
    const stage = this.state.stages[phase];
    if (stage.status !== "pending") {
      throw new HarnessInvariantError(`${formatStage(phase)} 已经制定过证据计划`);
    }
    stage.status = "planned";
    stage.plan = plan;
    this.onTrace({
      stage: phase,
      kind: "plan",
      title: `${formatStage(phase)} 证据阅读计划`,
      summary: `已规划 ${plan.files.length} 个直接阅读文件和 ${plan.actions.length} 个仓库调查动作，用于回答 ${plan.questions.length} 个分析问题。`,
      files: plan.files.map((request) => request.path),
      metadata: {
        goal: plan.goal,
        questions: plan.questions.length,
        actions: plan.actions.length,
        requestedFiles: plan.files.length,
        highPriority: plan.files.filter((request) => request.priority === "high").length,
      },
    });
  }

  async executeEvidencePlan(
    phase: EvidencePhase,
    plan: EvidencePlan,
    existingPaths: string[] = [],
  ): Promise<EvidenceBundle> {
    const policy = this.state.stages[phase].skillPolicy;
    const allowedTools = policy ? new Set(policy.allowedTools) : null;
    const blockedActions = allowedTools
      ? plan.actions.filter((action) => !allowedTools.has(action.tool))
      : [];
    const allowedActions = plan.actions.filter((action) =>
      !allowedTools || allowedTools.has(action.tool)
    );
    const permittedActions = allowedActions.slice(
      0,
      policy?.maxDiscoveryActions ?? allowedActions.length,
    );
    const omittedActions = allowedActions.slice(
      policy?.maxDiscoveryActions ?? allowedActions.length,
    );
    const boundedPlan: EvidencePlan = {
      ...plan,
      actions: permittedActions,
      files: deduplicateRequests(plan.files).slice(
        0,
        policy?.maxEvidenceFiles ?? plan.files.length,
      ),
      stopConditions: [
        ...new Set([
          ...plan.stopConditions,
          ...(policy?.stopConditions ?? []),
        ]),
      ],
    };
    for (const action of [...blockedActions, ...omittedActions]) {
      this.recordUnresolved(
        phase,
        `${action.tool}: 当前 Harness Skill 策略不允许执行该调查动作`,
      );
    }
    const omittedFiles = deduplicateRequests(plan.files).slice(
      policy?.maxEvidenceFiles ?? plan.files.length,
    );
    for (const request of omittedFiles) {
      this.recordUnresolved(
        phase,
        `${request.path}: 超出当前 Harness Skill 的证据文件上限`,
      );
    }

    this.recordEvidencePlan(phase, boundedPlan);
    const discoveredRequests: EvidencePlan["files"] = [];
    for (const action of boundedPlan.actions) {
      const observation = await this.executeDiscoveryAction(phase, action);
      this.state.observations.push(observation);
      for (const discoveredPath of observation.paths) {
        discoveredRequests.push({
          path: discoveredPath,
          purpose: action.purpose,
          priority: "medium",
        });
      }
    }

    const candidateRequests = deduplicateRequests([
      ...boundedPlan.files,
      ...discoveredRequests,
    ]);
    const selectedRequests = selectEvidenceRequests(
      boundedPlan.files,
      discoveredRequests,
      policy?.maxEvidenceFiles ?? Number.POSITIVE_INFINITY,
    );
    const selectedPaths = new Set(selectedRequests.map((request) => normalizePath(request.path)));
    for (const request of candidateRequests) {
      if (!selectedPaths.has(normalizePath(request.path))) {
        this.recordUnresolved(
          phase,
          `${request.path}: 为领域工具发现结果预留证据名额后未被选中`,
        );
      }
    }

    const executionPlan: EvidencePlan = {
      ...boundedPlan,
      files: selectedRequests,
    };
    this.state.stages[phase].plan = executionPlan;
    return this.readEvidenceBatch(phase, executionPlan, existingPaths);
  }

  async readEvidenceBatch(
    phase: EvidencePhase,
    plan: EvidencePlan,
    existingPaths: string[] = [],
  ): Promise<EvidenceBundle> {
    const stage = this.state.stages[phase];
    if (stage.status !== "planned" || stage.plan !== plan) {
      throw new HarnessInvariantError(`${formatStage(phase)} 必须先登记证据计划`);
    }
    if (this.state.budget.usedEvidenceBatches >= this.state.budget.maxEvidenceBatches) {
      throw new HarnessInvariantError("Harness 证据批次预算已用完");
    }

    const bundle = await buildEvidenceBundle(
      this.localPath,
      plan,
      phase,
      existingPaths,
    );
    const nextFilesRead = this.state.budget.filesRead + bundle.files.length;
    const nextEvidenceBytes = this.state.budget.evidenceBytes + bundle.totalBytes;
    if (
      nextFilesRead > this.state.budget.maxFilesRead
      || nextEvidenceBytes > this.state.budget.maxEvidenceBytes
    ) {
      throw new HarnessInvariantError("证据读取结果超出 Harness 总预算");
    }

    this.state.budget.usedEvidenceBatches += 1;
    this.state.budget.filesRead = nextFilesRead;
    this.state.budget.evidenceBytes = nextEvidenceBytes;
    for (const file of bundle.files) this.mergeEvidenceFile(file);
    for (const omission of bundle.omissions) this.recordEvidenceOmission(phase, omission);
    stage.status = "evidence_ready";
    const reusedPaths = bundle.omissions
      .filter((omission) => omission.reason === "already_available")
      .map((omission) => omission.path);
    stage.examinedPaths = [...new Set([
      ...bundle.files.map((file) => file.path),
      ...reusedPaths,
    ])];
    const materialOmissions = bundle.omissions.filter((omission) =>
      omission.reason !== "already_available" && omission.reason !== "duplicate_request"
    );
    stage.skippedPaths = [...new Set(materialOmissions.map((omission) => omission.path))];
    const skippedSet = new Set(stage.skippedPaths);
    const unresolved = plan.files
      .filter((request) => skippedSet.has(normalizePath(request.path)))
      .map((request) => `${request.path}: ${request.purpose}`);
    stage.unresolvedQuestions.push(...unresolved);
    this.state.unresolvedQuestions.push(...unresolved);
    stage.stopReason = nextFilesRead >= this.state.budget.maxFilesRead
      || nextEvidenceBytes >= this.state.budget.maxEvidenceBytes
      ? "global_evidence_budget_exhausted"
      : bundle.files.length === 0 && reusedPaths.length > 0 && materialOmissions.length === 0
        ? "existing_evidence_reused"
        : bundle.files.length === 0
          ? "no_valid_evidence_selected"
        : stage.unresolvedQuestions.length > 0
          ? "evidence_gaps_recorded"
          : "evidence_batch_completed";
    this.onTrace({
      stage: phase,
      kind: "evidence",
      title: `${formatStage(phase)} 证据已收集`,
      summary: `在证据预算内读取了 ${bundle.files.length} 个仓库文件${reusedPaths.length > 0 ? `，复用 ${reusedPaths.length} 个已有文件` : ""}${materialOmissions.length > 0 ? `，未能读取 ${materialOmissions.length} 个` : ""}。`,
      tool: "read_evidence_batch",
      files: bundle.files.map((file) => file.path),
      metadata: {
        filesRead: bundle.files.length,
        skippedFiles: materialOmissions.length,
        reusedFiles: reusedPaths.length,
        bytes: bundle.totalBytes,
        stopReason: stage.stopReason,
      },
    });

    return bundle;
  }

  prepareContributorContext(): {
    repositoryContext: RepositoryContributionContext;
    contributionEvidence: ContributionEvidence;
  } {
    const profile = this.state.repositoryProfile;
    if (!profile) {
      throw new HarnessInvariantError("构建 Contributor 上下文前必须先建立仓库画像");
    }
    if (this.state.stages.mentor.status !== "completed") {
      throw new HarnessInvariantError("Contributor 只能在 Mentor 完成后启动");
    }
    const evidence = this.getEvidenceBundle();
    const repositoryContext = buildRepositoryContributionContext(profile);
    const contributionEvidence = buildContributionEvidence(
      evidence,
      [...this.verifiedClaimPaths],
    );

    this.onTrace({
      stage: "contributor",
      kind: "evidence",
      title: "贡献分析上下文已准备",
      summary: "已汇总项目指南、仓库元数据、候选贡献点以及此前检查过的源码证据。",
      tool: "prepare_contributor_context",
      files: contributionEvidence.focusedFiles.map((file) => file.path),
      metadata: {
        examinedFiles: contributionEvidence.examinedFiles.length,
        focusedFiles: contributionEvidence.focusedFiles.length,
        todoMarkers: repositoryContext.todoMarkers.length,
      },
    });

    return { repositoryContext, contributionEvidence };
  }

  recordUserFocus(value: string | undefined): void {
    const focus = value?.trim();
    if (!focus || focus === "是" || this.state.userFocus.includes(focus)) return;
    this.state.userFocus.push(focus);
  }

  completeStage(stageName: StageName): void {
    const stage = this.state.stages[stageName];
    if (stageName !== "contributor" && stage.status !== "evidence_ready") {
      throw new HarnessInvariantError(`${formatStage(stageName)} 缺少已就绪的证据`);
    }
    if (stageName === "contributor" && this.state.stages.mentor.status !== "completed") {
      throw new HarnessInvariantError("Contributor 不能早于 Mentor 完成");
    }
    stage.status = "completed";
  }

  private mergeEvidenceFile(file: EvidenceBundle["files"][number]): void {
    const key = normalizePath(file.path);
    const entry = this.getOrCreateLedgerEntry(key);
    const incomingBytes = Buffer.byteLength(file.content, "utf8");
    entry.attempts.push({
      phase: file.phase,
      status: file.truncated ? "partial" : "available",
      bytes: incomingBytes,
    });

    const current = entry.bestEvidence;
    const currentBytes = current ? Buffer.byteLength(current.content, "utf8") : -1;
    const shouldReplace = !current
      || (current.truncated && !file.truncated)
      || (current.truncated === file.truncated && incomingBytes > currentBytes);
    if (shouldReplace) entry.bestEvidence = file;
    entry.omission = null;
    this.clearResolvedPath(key);
    this.syncEvidenceState();
  }

  private recordEvidenceOmission(
    phase: EvidencePhase,
    omission: EvidenceBundle["omissions"][number],
  ): void {
    const key = normalizePath(omission.path);
    const entry = this.getOrCreateLedgerEntry(key);
    const benign = omission.reason === "already_available" || omission.reason === "duplicate_request";
    entry.attempts.push({
      phase,
      status: benign ? "reused" : "missing",
      bytes: 0,
      reason: omission.reason,
    });
    if (!benign && !entry.bestEvidence) entry.omission = { ...omission, path: key };
  }

  private getOrCreateLedgerEntry(pathValue: string): EvidenceLedgerEntry {
    const path = normalizePath(pathValue);
    const existing = this.evidenceLedger.get(path);
    if (existing) return existing;
    const entry: EvidenceLedgerEntry = {
      path,
      bestEvidence: null,
      omission: null,
      attempts: [],
    };
    this.evidenceLedger.set(path, entry);
    return entry;
  }

  private getLedgerStatus(entry: EvidenceLedgerEntry): EvidenceLedgerStatus {
    if (entry.bestEvidence) return entry.bestEvidence.truncated ? "partial" : "available";
    return "missing";
  }

  private clearResolvedPath(pathValue: string): void {
    const path = normalizePath(pathValue);
    const matchesPath = (value: string) =>
      normalizePath(value.split(":", 1)[0]?.trim() ?? "") === path;
    for (const stage of Object.values(this.state.stages)) {
      stage.skippedPaths = stage.skippedPaths.filter((value) => normalizePath(value) !== path);
      stage.unresolvedQuestions = stage.unresolvedQuestions.filter((value) => !matchesPath(value));
    }
    this.state.unresolvedQuestions = this.state.unresolvedQuestions.filter(
      (value) => !matchesPath(value),
    );
  }

  private syncEvidenceState(): void {
    this.state.evidence = [...this.evidenceLedger.values()]
      .flatMap((entry) => entry.bestEvidence ? [entry.bestEvidence] : []);
  }

  getEvidenceBundle(): EvidenceBundle {
    const files = [...this.evidenceLedger.values()]
      .flatMap((entry) => entry.bestEvidence ? [entry.bestEvidence] : []);
    const omissions = [...this.evidenceLedger.values()]
      .flatMap((entry) => entry.omission ? [entry.omission] : []);
    return {
      files,
      skippedPaths: omissions.map((omission) => omission.path),
      omissions,
      totalBytes: files.reduce(
        (total, file) => total + Buffer.byteLength(file.content, "utf8"),
        0,
      ),
    };
  }

  async inspectGitHistory(): Promise<CommitSummary> {
    if (this.state.stages.mentor.status !== "completed") {
      throw new HarnessInvariantError("Git 历史检查只在 Contributor 准备阶段执行");
    }
    const summary = await extractCommitSummary(this.localPath);
    this.onTrace({
      stage: "contributor",
      kind: "tool",
      title: "Git 历史已检查",
      summary: "已提取近期变更主题和高频修改文件，用于生成贡献建议。",
      tool: "inspect_git_history",
      files: summary.frequentFiles.map((item) => item.file).slice(0, 12),
      metadata: {
        recentThemes: summary.recentThemes.length,
        frequentFiles: summary.frequentFiles.length,
        contributors: summary.contributorCount,
      },
    });
    return summary;
  }

  getContextView(): HarnessContextView {
    return {
      stages: cloneStages(this.state.stages),
      unresolvedQuestions: [...this.state.unresolvedQuestions],
      userFocus: [...this.state.userFocus],
      budget: { ...this.state.budget },
      evidenceIndex: this.state.evidence.map((file) => ({
        path: file.path,
        purpose: file.purpose,
        phase: file.phase,
        truncated: file.truncated,
      })),
      observations: this.state.observations.map((observation) => ({
        ...observation,
        paths: [...observation.paths],
        metadata: { ...observation.metadata },
      })),
    };
  }

  verifyEvidenceOutput<T extends EvidenceFirstOutput>(
    stageName: StageName,
    output: T,
    additionalAllowedPaths: string[] = [],
  ): T {
    const phaseEvidence = stageName === "explorer"
      ? this.state.evidence.filter((file) => file.phase === "explorer")
      : this.state.evidence;
    const allowedPaths = new Set([
      ...phaseEvidence.map((file) => normalizePath(file.path)),
      ...(stageName === "explorer" ? [] : this.verifiedClaimPaths),
      ...additionalAllowedPaths.map(normalizePath),
    ]);
    const resolvedCoverageSubjects = new Set(
      [...this.evidenceLedger.values()]
        .filter((entry) => this.isLedgerEntryRelevant(entry, stageName))
        .filter((entry) => this.getLedgerStatus(entry) === "available")
        .map((entry) => normalizePath(entry.path)),
    );
    const modelGaps: EvidenceGap[] = output.evidenceCoverage.gaps
      .filter((gap) => gap.kind !== "out_of_scope" && gap.severity !== "low")
      .filter((gap) => {
        if (gap.kind !== "coverage_limit" && gap.kind !== "missing_evidence") return true;
        return !resolvedCoverageSubjects.has(normalizePath(gap.subject));
      });
    let removedReferences = 0;
    let unsupportedClaims = 0;

    const evidenceClaims = output.evidenceClaims.map((claim) => {
      const evidence = claim.evidence.filter((reference) => {
        const allowed = allowedPaths.has(normalizePath(reference.path));
        if (!allowed) removedReferences += 1;
        return allowed;
      });
      let confidence = claim.confidence;
      if (evidence.length === 0) {
        unsupportedClaims += 1;
        confidence = "low";
      } else if (evidence.length < claim.evidence.length && confidence === "high") {
        confidence = "medium";
      }
      return { ...claim, confidence, evidence };
    });

    const unresolvedGaps = this.state.stages[stageName].unresolvedQuestions.map((value) => ({
      kind: "coverage_limit" as const,
      subject: value.split(":", 1)[0]?.trim() || "证据预算",
      summary: value,
      severity: "medium" as const,
    }));
    const gaps: EvidenceGap[] = [
      ...this.deriveLedgerCoverageGaps(stageName),
      ...unresolvedGaps,
      ...modelGaps,
    ];
    if (unsupportedClaims > 0) {
      gaps.unshift({
        kind: "missing_evidence",
        subject: "无文件依据的报告结论",
        summary: `${unsupportedClaims} 条报告结论缺少可接受的仓库文件依据。`,
        severity: "medium",
      });
    }

    const verified = {
      ...output,
      evidenceClaims,
      evidenceCoverage: {
        examinedFiles: [...allowedPaths],
        gaps: deduplicateEvidenceGaps(gaps).slice(0, 8),
      },
    };
    for (const claim of evidenceClaims) {
      for (const reference of claim.evidence) {
        this.verifiedClaimPaths.add(normalizePath(reference.path));
      }
    }

    this.onTrace({
      stage: stageName,
      kind: "evidence",
      title: `${formatStage(stageName)} 证据声明已校验`,
      summary: `已使用 ${allowedPaths.size} 个允许引用的证据文件校验 ${evidenceClaims.length} 条声明。`,
      files: [...new Set(evidenceClaims.flatMap((claim) =>
        claim.evidence.map((reference) => reference.path)
      ))],
      metadata: {
        claims: evidenceClaims.length,
        unsupportedClaims,
        removedReferences,
        coverageGaps: verified.evidenceCoverage.gaps.length,
      },
    });
    return verified;
  }

  private isLedgerEntryRelevant(entry: EvidenceLedgerEntry, stageName: StageName): boolean {
    return stageName !== "explorer"
      || entry.attempts.some((attempt) => attempt.phase === "explorer");
  }

  private deriveLedgerCoverageGaps(stageName: StageName): EvidenceGap[] {
    return [...this.evidenceLedger.values()].flatMap((entry): EvidenceGap[] => {
      if (!this.isLedgerEntryRelevant(entry, stageName)) return [];
      const status = this.getLedgerStatus(entry);
      if (status === "available") return [];
      if (status === "partial") {
        return [{
          kind: "coverage_limit",
          subject: entry.path,
          summary: `${entry.path}: 文件内容只读取了部分片段，相关结论仅基于当前可见证据。`,
          severity: "medium",
        }];
      }
      if (!entry.omission) return [];
      return [{
        kind: "missing_evidence",
        subject: entry.path,
        summary: formatEvidenceOmission(entry.path, entry.omission.reason),
        severity: "medium",
      }];
    });
  }

  private async executeDiscoveryAction(
    phase: EvidencePhase,
    action: EvidencePlan["actions"][number],
  ): Promise<HarnessToolObservation> {
    const profile = this.state.repositoryProfile;
    if (!profile) throw new HarnessInvariantError("执行领域工具前必须先建立仓库画像");
    const trackedPaths = new Set(profile.fileIndex.map(normalizePath));

    let observation: HarnessToolObservation;
    switch (action.tool) {
      case "search_symbols": {
        const matches = await searchRepositorySymbols(
          this.localPath,
          profile,
          action.query,
        );
        const paths = [...new Set(matches.map((match) => match.path))];
        observation = {
          stage: phase,
          tool: action.tool,
          purpose: action.purpose,
          summary: `在受限范围内的 ${paths.length} 个文件中找到 ${matches.length} 处符号匹配。`,
          paths,
          metadata: { query: action.query, matches: matches.length, files: paths.length },
        };
        break;
      }
      case "trace_module_dependencies": {
        const validatedPaths = action.paths
          .map(normalizePath)
          .filter((candidate) => trackedPaths.has(candidate));
        const result = await traceRepositoryDependencies(
          this.localPath,
          profile,
          validatedPaths,
        );
        observation = {
          stage: phase,
          tool: action.tool,
          purpose: action.purpose,
          summary: `从 ${validatedPaths.length} 个已校验起点解析出 ${result.edges.length} 条仓库内部依赖边。`,
          paths: result.paths,
          metadata: {
            requestedSeeds: action.paths.length,
            validatedSeeds: validatedPaths.length,
            edges: result.edges.length,
          },
        };
        break;
      }
      case "find_related_tests": {
        const validatedPaths = action.paths
          .map(normalizePath)
          .filter((candidate) => trackedPaths.has(candidate));
        const paths = findRepositoryRelatedTests(profile, validatedPaths);
        observation = {
          stage: phase,
          tool: action.tool,
          purpose: action.purpose,
          summary: `为 ${validatedPaths.length} 个已校验源码路径找到 ${paths.length} 个相关测试候选。`,
          paths,
          metadata: {
            requestedSources: action.paths.length,
            validatedSources: validatedPaths.length,
            tests: paths.length,
          },
        };
        break;
      }
    }

    this.onTrace({
      stage: phase,
      kind: "tool",
      title: `${formatToolName(observation.tool)}执行完成`,
      summary: observation.summary,
      tool: observation.tool,
      files: observation.paths,
      metadata: observation.metadata,
    });
    return observation;
  }

  private recordUnresolved(phase: EvidencePhase, value: string): void {
    const stage = this.state.stages[phase];
    if (!stage.unresolvedQuestions.includes(value)) {
      stage.unresolvedQuestions.push(value);
    }
    if (!this.state.unresolvedQuestions.includes(value)) {
      this.state.unresolvedQuestions.push(value);
    }
  }
}

function createInitialState(): HarnessState {
  const emptyStage = () => ({
    status: "pending" as const,
    plan: null,
    examinedPaths: [],
    skippedPaths: [],
    unresolvedQuestions: [],
    skillPolicy: null,
    stopReason: null,
  });
  return {
    repositoryProfile: null,
    repositoryOverview: null,
    evidence: [],
    observations: [],
    stages: {
      explorer: emptyStage(),
      mentor: emptyStage(),
      contributor: emptyStage(),
    },
    unresolvedQuestions: [],
    userFocus: [],
    budget: {
      ...HARNESS_BUDGET,
      usedEvidenceBatches: 0,
      filesRead: 0,
      evidenceBytes: 0,
    },
  };
}

function cloneStages(
  stages: HarnessState["stages"],
): HarnessState["stages"] {
  return Object.fromEntries(
    Object.entries(stages).map(([name, stage]) => [name, {
      ...stage,
      plan: stage.plan ? {
        ...stage.plan,
        files: stage.plan.files.map((file) => ({ ...file })),
      } : null,
      examinedPaths: [...stage.examinedPaths],
      skippedPaths: [...stage.skippedPaths],
      unresolvedQuestions: [...stage.unresolvedQuestions],
      skillPolicy: stage.skillPolicy ? cloneSkillPolicy(stage.skillPolicy) : null,
    }]),
  ) as HarnessState["stages"];
}

function cloneSkillPolicy(policy: HarnessSkillPolicy): HarnessSkillPolicy {
  return {
    ...policy,
    skillNames: [...policy.skillNames],
    allowedTools: [...policy.allowedTools],
    preferredTools: [...policy.preferredTools],
    recommendedQuestions: [...policy.recommendedQuestions],
    evidenceRequirements: [...policy.evidenceRequirements],
    stopConditions: [...policy.stopConditions],
  };
}

function normalizePath(value: string): string {
  return value.replaceAll("\\", "/").replace(/^\.\/+/, "");
}

function deduplicateRequests(requests: EvidencePlan["files"]): EvidencePlan["files"] {
  const seen = new Set<string>();
  const priorityRank = { high: 0, medium: 1, low: 2 } as const;
  return requests
    .map((request, index) => ({ request, index }))
    .sort((left, right) =>
      priorityRank[left.request.priority] - priorityRank[right.request.priority]
      || left.index - right.index
    )
    .map(({ request }) => request)
    .filter((request) => {
      const normalized = normalizePath(request.path);
      if (seen.has(normalized)) return false;
      seen.add(normalized);
      return true;
    });
}

function selectEvidenceRequests(
  directRequests: EvidencePlan["files"],
  discoveredRequests: EvidencePlan["files"],
  maxFiles: number,
): EvidencePlan["files"] {
  const direct = deduplicateRequests(directRequests);
  const directPaths = new Set(direct.map((request) => normalizePath(request.path)));
  const discovered = deduplicateRequests(discoveredRequests)
    .filter((request) => !directPaths.has(normalizePath(request.path)));
  if (!Number.isFinite(maxFiles)) {
    return deduplicateRequests([...direct, ...discovered]);
  }
  const boundedMax = Math.max(0, Math.floor(maxFiles));
  if (boundedMax === 0) return [];
  if (discovered.length === 0) return direct.slice(0, boundedMax);

  const discoverySlots = Math.min(
    discovered.length,
    Math.max(1, Math.min(3, Math.floor(boundedMax / 3))),
  );
  const selectedDiscoveries = discovered.slice(0, discoverySlots);
  const selectedDirect = direct.slice(0, Math.max(0, boundedMax - selectedDiscoveries.length));
  return deduplicateRequests([...selectedDirect, ...selectedDiscoveries]);
}

function formatToolName(tool: HarnessToolObservation["tool"]): string {
  switch (tool) {
    case "search_symbols": return "符号搜索";
    case "trace_module_dependencies": return "模块依赖追踪";
    case "find_related_tests": return "相关测试定位";
  }
}

function formatStage(stage: StageName | EvidencePhase): string {
  return stage.charAt(0).toUpperCase() + stage.slice(1);
}

function isCompleteCoverageFile(file: string): boolean {
  if (/(^|\/)(?:node_modules|vendor|dist|build|coverage|\.git)(\/|$)/i.test(file)) {
    return false;
  }
  const dot = file.lastIndexOf(".");
  return dot >= 0 && COMPLETE_COVERAGE_EXTENSIONS.has(file.slice(dot).toLowerCase());
}

function isCompleteCoverageIgnoredFile(file: string): boolean {
  const baseName = file.split("/").at(-1)?.toLowerCase() ?? "";
  if (/^(?:licen[cs]e|copying|notice)(?:\..*)?$/.test(baseName)) return true;
  if (/^\.(?:gitignore|gitattributes|editorconfig|npmignore)$/.test(baseName)) return true;
  const dot = baseName.lastIndexOf(".");
  return dot >= 0 && COMPLETE_COVERAGE_IGNORED_EXTENSIONS.has(baseName.slice(dot));
}

function deduplicateEvidenceGaps(gaps: EvidenceGap[]): EvidenceGap[] {
  const severityRank = { high: 2, medium: 1, low: 0 } as const;
  const deduplicated = new Map<string, EvidenceGap>();
  for (const gap of gaps) {
    const key = normalizePath(gap.subject).trim().toLowerCase();
    const existing = deduplicated.get(key);
    if (!existing || severityRank[gap.severity] > severityRank[existing.severity]) {
      deduplicated.set(key, gap);
    }
  }
  return [...deduplicated.values()];
}

function formatEvidenceOmission(
  path: string,
  reason: EvidenceBundle["omissions"][number]["reason"],
): string {
  switch (reason) {
    case "not_tracked": return `${path}: 计划读取的路径不属于当前提交的跟踪文件。`;
    case "file_limit": return `${path}: 受当前阶段的证据文件数量限制，未能读取该文件。`;
    case "budget_exhausted": return `${path}: 受当前阶段的证据字节预算限制，未能读取该文件。`;
    case "oversized": return `${path}: 文件超过安全扫描大小限制，未读取其内容。`;
    case "unreadable": return `${path}: 文件无法作为受支持的文本证据读取。`;
    case "duplicate_request": return `${path}: 同一证据计划中出现了重复读取请求。`;
    case "already_available": return `${path}: 已复用此前读取的证据。`;
  }
}
