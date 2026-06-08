import "dotenv/config";
import { z } from "zod";

const envSchema = z.object({
  ANTHROPIC_AUTH_TOKEN: z.string(),
  ANTHROPIC_BASE_URL: z.string().default("https://api.deepseek.com/anthropic"),
  ANTHROPIC_MODEL: z.string().default("deepseek-v4-flash"),
  PORT: z.coerce.number().int().positive().default(3000),
  HOST: z.string().default("0.0.0.0"),
  CLONE_TIMEOUT_MS: z.coerce.number().int().positive().default(60000),
  CLONE_DEPTH: z.coerce.number().int().positive().default(1),
  MAX_REPO_SIZE_MB: z.coerce.number().int().positive().default(200),
  TASK_TOTAL_TIMEOUT_MS: z.coerce.number().int().positive().default(600000),
  INTERACTION_TIMEOUT_MS: z.coerce.number().int().positive().default(15000),
  SQLITE_PATH: z.string().default("./data/repomentor.db"),
  LOG_LEVEL: z.enum(["debug", "info", "warn", "error"]).default("info"),
});

export const config = envSchema.parse(process.env);
export type Config = z.infer<typeof envSchema>;
