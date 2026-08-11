import "dotenv/config";
import { z } from "zod";

const envSchema = z.object({
  LLM_PROVIDER: z.enum(["anthropic-compatible", "openai-compatible"]).optional(),
  LLM_API_KEY: z.string().optional(),
  LLM_BASE_URL: z.string().optional(),
  LLM_MODEL: z.string().optional(),
  // Backward-compatible configuration for existing installations.
  ANTHROPIC_AUTH_TOKEN: z.string().optional(),
  ANTHROPIC_BASE_URL: z.string().optional(),
  ANTHROPIC_MODEL: z.string().optional(),
  MODEL_SETTINGS_PATH: z.string().default("./data/model-settings.json"),
  PORT: z.coerce.number().int().positive().default(3000),
  HOST: z.string().default("127.0.0.1"),
  CLONE_TIMEOUT_MS: z.coerce.number().int().positive().default(60000),
  CLONE_DEPTH: z.coerce.number().int().positive().default(1),
  MAX_REPO_SIZE_MB: z.coerce.number().int().positive().default(200),
  TASK_TOTAL_TIMEOUT_MS: z.coerce.number().int().positive().default(600000),
  INTERACTION_TIMEOUT_MS: z.coerce.number().int().positive().default(15000),
  SQLITE_PATH: z.string().default("./data/repomentor.db"),
  LOG_LEVEL: z.enum(["debug", "info", "warn", "error"]).default("info"),
  AGENT_RUNTIME_MODE: z.enum(["adaptive", "workflow", "agentic"]).default("adaptive"),
  MENTOR_SUBAGENTS_ENABLED: z.string().optional().transform((value) =>
    value?.trim().toLowerCase() === "true"
  ),
  AGENT_MAX_BUDGET_USD: z.coerce.number().positive().optional(),
});

export const config = envSchema.parse(process.env);
export type Config = z.infer<typeof envSchema>;
