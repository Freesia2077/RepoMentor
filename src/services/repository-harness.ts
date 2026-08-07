import type {
  ContributionEvidence,
  CommitSummary,
  EvidenceBundle,
  EvidenceFirstOutput,
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

const HARNESS_BUDGET = {
  maxEvidenceBatches: 2,
  maxFilesRead: 18,
  maxEvidenceBytes: 88_000,
} as const;

export class HarnessInvariantError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "HarnessInvariantError";
  }
}

export class RepositoryHarness {
  private readonly state: HarnessState = createInitialState();
  private readonly verifiedClaimPaths = new Set<string>();

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
        `${action.tool}: Harness Skill policy did not permit this planned action`,
      );
    }
    const omittedFiles = deduplicateRequests(plan.files).slice(
      policy?.maxEvidenceFiles ?? plan.files.length,
    );
    for (const request of omittedFiles) {
      this.recordUnresolved(
        phase,
        `${request.path}: omitted by Harness Skill evidence-file limit`,
      );
    }

    this.recordEvidencePlan(phase, boundedPlan);
    const discoveredRequests: EvidencePlan["files"] = [];
    for (const action of boundedPlan.actions) {
      const observation = await this.executeDiscoveryAction(phase, action);
      this.state.observations.push(observation);
      if (observation.paths.length === 0) {
        this.recordUnresolved(phase, `${action.tool}: ${action.purpose}`);
      }
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
          `${request.path}: omitted after reserving Harness discovery evidence budget`,
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
    this.state.evidence.push(...bundle.files);
    stage.status = "evidence_ready";
    stage.examinedPaths = bundle.files.map((file) => file.path);
    stage.skippedPaths = bundle.skippedPaths;
    const skippedSet = new Set(bundle.skippedPaths);
    const unresolved = plan.files
      .filter((request) => skippedSet.has(normalizePath(request.path)))
      .map((request) => `${request.path}: ${request.purpose}`);
    stage.unresolvedQuestions.push(...unresolved);
    this.state.unresolvedQuestions.push(...unresolved);
    stage.stopReason = nextFilesRead >= this.state.budget.maxFilesRead
      || nextEvidenceBytes >= this.state.budget.maxEvidenceBytes
      ? "global_evidence_budget_exhausted"
      : bundle.files.length === 0
        ? "no_valid_evidence_selected"
        : stage.unresolvedQuestions.length > 0
          ? "evidence_gaps_recorded"
          : "evidence_batch_completed";
    this.onTrace({
      stage: phase,
      kind: "evidence",
      title: `${formatStage(phase)} 证据已收集`,
      summary: `在证据预算内读取了 ${bundle.files.length} 个仓库文件${bundle.skippedPaths.length > 0 ? `，跳过 ${bundle.skippedPaths.length} 个` : ""}。`,
      tool: "read_evidence_batch",
      files: bundle.files.map((file) => file.path),
      metadata: {
        filesRead: bundle.files.length,
        skippedFiles: bundle.skippedPaths.length,
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
    const contributionEvidence = buildContributionEvidence(evidence);

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

  getEvidenceBundle(): EvidenceBundle {
    return {
      files: [...this.state.evidence],
      skippedPaths: [
        ...this.state.stages.explorer.skippedPaths,
        ...this.state.stages.mentor.skippedPaths,
      ],
      totalBytes: this.state.budget.evidenceBytes,
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
    const gaps = [...output.evidenceCoverage.gaps];
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

    if (removedReferences > 0) {
      gaps.push(`${removedReferences} unsupported evidence reference(s) were removed by the Harness.`);
    }
    if (unsupportedClaims > 0) {
      gaps.push(`${unsupportedClaims} claim(s) remain report-level interpretations without file evidence.`);
    }
    gaps.push(...this.state.stages[stageName].unresolvedQuestions);

    const verified = {
      ...output,
      evidenceClaims,
      evidenceCoverage: {
        examinedFiles: [...allowedPaths],
        gaps: [...new Set(gaps)].slice(0, 8),
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
