import { z } from "zod";

// ===== Explorer Output =====

const projectTypeSchema = z.object({
  primary: z.string().min(1),
  secondary: z.array(z.string()),
});

const techStackSchema = z.object({
  language: z.string().nullable(),
  framework: z.string().nullable(),
  buildTool: z.string().nullable(),
});

const entryPointSchema = z.object({
  file: z.string(),
  role: z.string(),
});

const moduleInfoSchema = z.object({
  path: z.string(),
  responsibility: z.string().max(300),
  importance: z.enum(["core", "support", "utility"]),
  justification: z.string(),
});

export const explorerOutputSchema = z.object({
  projectType: projectTypeSchema,
  techStack: techStackSchema,
  fileCount: z.number().int().nonnegative(),
  entryPoints: z.array(entryPointSchema).max(10),
  moduleMap: z.array(moduleInfoSchema).max(6),
  directorySummary: z.string().max(1500),
  projectSummary: z.string().max(800),
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
  architectureOverview: z.string().max(3000),
  dependencyGraph: z.record(z.string(), z.array(z.string())),
  readingPath: z.array(readingStepSchema).max(10),
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
  devEnv: z.string().nullable(),
  build: z.string().nullable(),
  test: z.string().nullable(),
  lint: z.string().nullable().optional(),
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

// ===== Commit Summary (Orchestrator 层生成) =====

export const commitSummarySchema = z.object({
  frequentFiles: z.array(z.object({
    file: z.string(),
    commits: z.number(),
    recent: z.boolean(),
  })),
  recentThemes: z.array(z.string()),
  contributorCount: z.number().int().nonnegative(),
});
