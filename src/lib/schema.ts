import { z } from "zod";

// Claude Agent SDK 0.3.224 delegates `outputFormat` to Claude Code's
// `--json-schema` validator, whose bundled Ajv accepts Draft-07 but does not
// register the Draft 2020-12 meta-schema emitted by Zod by default.
const CLAUDE_JSON_SCHEMA_OPTIONS = {
  target: "draft-07",
  unrepresentable: "any",
} as const;

// ===== Evidence Plan =====

export const evidencePlanSchema = z.object({
  goal: z.string().min(1).max(500),
  rationale: z.string().max(1000),
  questions: z.array(z.string().min(1).max(300)).max(6),
  actions: z.array(z.discriminatedUnion("tool", [
    z.object({
      tool: z.literal("search_symbols"),
      query: z.string().min(1).max(120),
      purpose: z.string().min(1).max(300),
    }),
    z.object({
      tool: z.literal("trace_module_dependencies"),
      paths: z.array(z.string().min(1)).min(1).max(6),
      purpose: z.string().min(1).max(300),
    }),
    z.object({
      tool: z.literal("find_related_tests"),
      paths: z.array(z.string().min(1)).min(1).max(6),
      purpose: z.string().min(1).max(300),
    }),
  ])).max(4),
  files: z.array(z.object({
    path: z.string().min(1),
    purpose: z.string().min(1).max(300),
    priority: z.enum(["high", "medium", "low"]),
  })).max(12),
  stopConditions: z.array(z.string().min(1).max(300)).min(1).max(4),
});

/**
 * Provider 原生结构化输出使用的 JSON Schema。它与上面的 Zod 契约保持同一
 * 边界，让 Orchestrator 在生成阶段就受到约束；Zod 仍作为最终可信校验层。
 */
export const EVIDENCE_PLAN_JSON_SCHEMA: Record<string, unknown> = {
  type: "object",
  additionalProperties: false,
  required: [
    "goal",
    "rationale",
    "questions",
    "actions",
    "files",
    "stopConditions",
  ],
  properties: {
    goal: { type: "string", minLength: 1, maxLength: 500 },
    rationale: { type: "string", maxLength: 1000 },
    questions: {
      type: "array",
      maxItems: 6,
      items: { type: "string", minLength: 1, maxLength: 300 },
    },
    actions: {
      type: "array",
      maxItems: 4,
      items: {
        anyOf: [
          {
            type: "object",
            additionalProperties: false,
            required: ["tool", "query", "purpose"],
            properties: {
              tool: { const: "search_symbols" },
              query: { type: "string", minLength: 1, maxLength: 120 },
              purpose: { type: "string", minLength: 1, maxLength: 300 },
            },
          },
          {
            type: "object",
            additionalProperties: false,
            required: ["tool", "paths", "purpose"],
            properties: {
              tool: {
                enum: ["trace_module_dependencies", "find_related_tests"],
              },
              paths: {
                type: "array",
                minItems: 1,
                maxItems: 6,
                items: { type: "string", minLength: 1 },
              },
              purpose: { type: "string", minLength: 1, maxLength: 300 },
            },
          },
        ],
      },
    },
    files: {
      type: "array",
      maxItems: 12,
      items: {
        type: "object",
        additionalProperties: false,
        required: ["path", "purpose", "priority"],
        properties: {
          path: { type: "string", minLength: 1 },
          purpose: { type: "string", minLength: 1, maxLength: 300 },
          priority: { enum: ["high", "medium", "low"] },
        },
      },
    },
    stopConditions: {
      type: "array",
      minItems: 1,
      maxItems: 4,
      items: { type: "string", minLength: 1, maxLength: 300 },
    },
  },
};

export function validateEvidencePlan(data: unknown) {
  return evidencePlanSchema.parse(data);
}

// ===== Explorer Output =====

export const MODULE_IMPORTANCE_VALUES = ["core", "support", "utility"] as const;

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

const moduleImportanceSchema = z.preprocess(
  (value) => value === "supporting" ? "support" : value,
  z.enum(MODULE_IMPORTANCE_VALUES),
);

const moduleInfoSchema = z.object({
  path: z.string(),
  responsibility: z.string().max(300),
  importance: moduleImportanceSchema,
  justification: z.string(),
});

const evidenceClaimSchema = z.object({
  claim: z.string().min(1).max(600),
  confidence: z.enum(["high", "medium", "low"]),
  evidence: z.array(z.object({
    path: z.string().min(1),
    supports: z.string().min(1).max(300),
  })).max(4),
});

const evidenceGapSchema = z.preprocess(
  (value) => {
    if (typeof value !== "string") return value;
    const subject = value.split(":", 1)[0]?.trim() || "legacy-gap";
    return {
      kind: "missing_evidence",
      subject: subject.slice(0, 200),
      summary: value,
      severity: "medium",
    };
  },
  z.object({
    kind: z.enum([
      "missing_evidence",
      "test_absent",
      "coverage_limit",
      "out_of_scope",
    ]),
    subject: z.string().min(1).max(200),
    summary: z.string().min(1).max(500),
    severity: z.enum(["high", "medium", "low"]),
  }),
);

const evidenceCoverageSchema = z.object({
  examinedFiles: z.array(z.string().min(1)).max(24),
  gaps: z.array(evidenceGapSchema).max(8),
});

export const explorerOutputSchema = z.object({
  projectType: projectTypeSchema,
  techStack: techStackSchema,
  fileCount: z.number().int().nonnegative(),
  entryPoints: z.array(entryPointSchema).max(10),
  moduleMap: z.array(moduleInfoSchema).max(6),
  directorySummary: z.string().max(1500),
  projectSummary: z.string().max(800),
  evidenceClaims: z.array(evidenceClaimSchema).max(8),
  evidenceCoverage: evidenceCoverageSchema,
});

export const EXPLORER_OUTPUT_JSON_SCHEMA = z.toJSONSchema(explorerOutputSchema, {
  ...CLAUDE_JSON_SCHEMA_OPTIONS,
}) as Record<string, unknown>;

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
  readingPath: z.array(readingStepSchema).max(5),
  keyPatterns: z.array(keyPatternSchema).max(10),
  codeConventions: z.array(codeConventionSchema).max(10),
  evidenceClaims: z.array(evidenceClaimSchema).max(8),
  evidenceCoverage: evidenceCoverageSchema,
});

export const MENTOR_OUTPUT_JSON_SCHEMA = z.toJSONSchema(mentorOutputSchema, {
  ...CLAUDE_JSON_SCHEMA_OPTIONS,
}) as Record<string, unknown>;

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
  goodFirstIssues: z.array(goodFirstIssueSchema).max(6),
  contributionSetup: contributionSetupSchema,
  entryFiles: z.array(entryFileSchema).max(10),
  notesForNewcomers: z.array(newcomerNoteSchema).max(10),
  evidenceClaims: z.array(evidenceClaimSchema).max(8),
  evidenceCoverage: evidenceCoverageSchema,
});

export const CONTRIBUTOR_OUTPUT_JSON_SCHEMA = z.toJSONSchema(contributorOutputSchema, {
  ...CLAUDE_JSON_SCHEMA_OPTIONS,
}) as Record<string, unknown>;

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
