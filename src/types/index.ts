// ========== 任务生命周期 ==========

export type TaskStatus = "cloning" | "analyzing" | "completed" | "failed";

export type StageName = "explorer" | "mentor" | "contributor";

export type StageProgress = Record<StageName, "pending" | "running" | "done">;

// ========== 项目类型 ==========

export type KnownProjectType = "library" | "cli" | "web-framework" | "monorepo" | "unknown";
export type ProjectTypePrimary = string;

export interface ProjectType {
  primary: ProjectTypePrimary;
  secondary: ProjectTypePrimary[];
}

export interface TechStack {
  language: string | null;
  framework: string | null;
  buildTool: string | null;
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

export interface RepositoryFileExcerpt {
  path: string;
  content: string;
  truncated: boolean;
}

export interface TodoMarker {
  file: string;
  line: number;
  marker: "TODO" | "FIXME" | "HACK" | "XXX";
  excerpt: string;
}

export interface RepositoryDirectoryStat {
  path: string;
  files: number;
  sourceFiles: number;
  testFiles: number;
}

export interface RepositoryProfile {
  fileCount: number;
  fileIndex: string[];
  fileIndexTruncated: boolean;
  topLevelTree: string[];
  treeTruncated: boolean;
  directoryStats: RepositoryDirectoryStat[];
  readme: RepositoryFileExcerpt | null;
  manifests: RepositoryFileExcerpt[];
  exampleManifests: RepositoryFileExcerpt[];
  configFiles: RepositoryFileExcerpt[];
  guidanceFiles: RepositoryFileExcerpt[];
  todoMarkers: TodoMarker[];
  languageStats: Record<string, number>;
  entryCandidates: string[];
  testCandidates: string[];
}

export interface RepositoryOverview {
  fileCount: number;
  topLevelTree: string[];
  treeTruncated: boolean;
  directoryStats: RepositoryDirectoryStat[];
  languageStats: Record<string, number>;
  entryCandidates: string[];
  testCandidates: string[];
  projectFiles: {
    readme: string | null;
    manifests: string[];
    exampleManifests: string[];
    configFiles: string[];
    guidanceFiles: string[];
  };
}

export interface RepositoryContributionContext {
  fileCount: number;
  manifests: RepositoryFileExcerpt[];
  exampleManifests: RepositoryFileExcerpt[];
  configFiles: RepositoryFileExcerpt[];
  guidanceFiles: RepositoryFileExcerpt[];
  todoMarkers: TodoMarker[];
  languageStats: Record<string, number>;
  entryCandidates: string[];
  testCandidates: string[];
}

export interface EvidenceRequest {
  path: string;
  purpose: string;
  priority: "high" | "medium" | "low";
}

export interface EvidencePlan {
  rationale: string;
  files: EvidenceRequest[];
}

export interface EvidenceFile extends RepositoryFileExcerpt {
  purpose: string;
  phase: "explorer" | "mentor";
}

export interface EvidenceBundle {
  files: EvidenceFile[];
  skippedPaths: string[];
  totalBytes: number;
}

export interface ContributionEvidence {
  examinedFiles: Array<{
    path: string;
    purpose: string;
    phase: "explorer" | "mentor";
    truncated: boolean;
  }>;
  focusedFiles: EvidenceFile[];
  totalBytes: number;
}

export interface ExplorerInput {
  fileCount: number;
  repositoryProfile: RepositoryProfile;
  evidenceBundle: EvidenceBundle;
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
  repositoryOverview: RepositoryOverview;
  evidenceBundle: EvidenceBundle;
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
  repositoryContext: RepositoryContributionContext;
  contributionEvidence: ContributionEvidence;
  commitSummary: CommitSummary;
  userFocus?: string;
}

export interface GoodFirstIssue {
  area: string;
  difficulty: "easy" | "medium" | "hard";
  description: string;
}

export interface ContributionSetup {
  devEnv: string | null;
  build: string | null;
  test: string | null;
  lint?: string | null;
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

export type ErrorCategory = "clone_failed" | "llm_failed" | "parse_failed" | "timeout" | "internal";

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
  result: AnalysisResult;
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
  (
    | SSETaskCreatedEvent
    | SSETaskCompletedEvent
    | SSETaskErrorEvent
    | SSEStageStartEvent
    | SSEStageProgressEvent
    | SSEStageFieldEvent
    | SSEStageDoneEvent
    | SSEInteractAskEvent
    | SSEInteractTimeoutEvent
  ) & {
    timestamp?: string;
  };

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
