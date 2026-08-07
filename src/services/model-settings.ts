import fs from "node:fs";
import path from "node:path";
import { z } from "zod";

import { config } from "../config.js";
import type {
  ModelProviderId,
  PublicModelSettings,
  UpdateModelSettingsRequest,
} from "../types/index.js";

export interface ModelSettings {
  provider: ModelProviderId;
  apiKey: string;
  baseUrl: string;
  model: string;
  source: PublicModelSettings["source"];
}

const persistedSettingsSchema = z.object({
  provider: z.enum(["anthropic-compatible", "openai-compatible"]),
  apiKey: z.string().min(1),
  baseUrl: z.string().url().refine(isHttpUrl, "Base URL must use http or https"),
  model: z.string().trim().min(1),
});

const updateSettingsSchema = z.object({
  provider: z.enum(["anthropic-compatible", "openai-compatible"]),
  apiKey: z.string().trim().min(1).optional(),
  baseUrl: z.string().trim().url().refine(isHttpUrl, "Base URL must use http or https"),
  model: z.string().trim().min(1),
});

let cachedSettings: ModelSettings | undefined;

export function getModelSettings(): ModelSettings {
  if (cachedSettings) return { ...cachedSettings };

  const fromFile = readPersistedSettings();
  if (fromFile) {
    cachedSettings = { ...fromFile, source: "file" };
    return { ...cachedSettings };
  }

  const provider = config.LLM_PROVIDER ?? "anthropic-compatible";
  const apiKey = config.LLM_API_KEY ?? config.ANTHROPIC_AUTH_TOKEN ?? "";
  const hasEnvironmentSettings = Boolean(
    config.LLM_PROVIDER
    || config.LLM_API_KEY
    || config.LLM_BASE_URL
    || config.LLM_MODEL
    || config.ANTHROPIC_AUTH_TOKEN
    || config.ANTHROPIC_BASE_URL
    || config.ANTHROPIC_MODEL,
  );

  cachedSettings = {
    provider,
    apiKey,
    baseUrl: stripTrailingSlash(
      config.LLM_BASE_URL
      ?? (provider === "anthropic-compatible"
        ? config.ANTHROPIC_BASE_URL ?? "https://api.deepseek.com/anthropic"
        : "https://api.openai.com/v1"),
    ),
    model: config.LLM_MODEL
      ?? (provider === "anthropic-compatible"
        ? config.ANTHROPIC_MODEL ?? "deepseek-v4-flash"
        : ""),
    source: hasEnvironmentSettings ? "environment" : "default",
  };
  return { ...cachedSettings };
}

export function getPublicModelSettings(): PublicModelSettings {
  const settings = getModelSettings();
  return {
    provider: settings.provider,
    baseUrl: settings.baseUrl,
    model: settings.model,
    hasApiKey: Boolean(settings.apiKey),
    source: settings.source,
  };
}

export function requireModelSettings(): ModelSettings {
  const settings = getModelSettings();
  if (!settings.apiKey) {
    throw new ModelSettingsError(
      "model_not_configured",
      "尚未配置模型 API Key，请先在 Model Settings 中完成本地配置",
    );
  }
  if (!settings.model) {
    throw new ModelSettingsError(
      "invalid_model_settings",
      "尚未配置模型 ID，请先在 Model Settings 中填写厂商提供的准确模型名称",
    );
  }
  return settings;
}

export function saveModelSettings(input: UpdateModelSettingsRequest): PublicModelSettings {
  const parsed = updateSettingsSchema.parse(input);
  let apiKey = parsed.apiKey;
  if (!apiKey) {
    const existing = getModelSettings();
    if (existing.provider !== parsed.provider) {
      throw new ModelSettingsError(
        "model_not_configured",
        "切换 Provider 时必须提供对应厂商的 API Key",
      );
    }
    apiKey = existing.apiKey;
  }
  if (!apiKey) {
    throw new ModelSettingsError("model_not_configured", "首次配置时必须提供 API Key");
  }

  const persisted = persistedSettingsSchema.parse({
    ...parsed,
    apiKey,
    baseUrl: stripTrailingSlash(parsed.baseUrl),
  });
  const settingsPath = resolveSettingsPath();
  fs.mkdirSync(path.dirname(settingsPath), { recursive: true });
  fs.writeFileSync(settingsPath, `${JSON.stringify(persisted, null, 2)}\n`, {
    encoding: "utf8",
    mode: 0o600,
  });
  cachedSettings = { ...persisted, source: "file" };
  return getPublicModelSettings();
}

export function resetModelSettingsCache(): void {
  cachedSettings = undefined;
}

export class ModelSettingsError extends Error {
  constructor(
    public readonly code: "model_not_configured" | "invalid_model_settings",
    message: string,
  ) {
    super(message);
    this.name = "ModelSettingsError";
  }
}

function readPersistedSettings(): Omit<ModelSettings, "source"> | undefined {
  if (process.env.NODE_ENV === "test") return undefined;
  const settingsPath = resolveSettingsPath();
  if (!fs.existsSync(settingsPath)) return undefined;
  try {
    const parsed = JSON.parse(fs.readFileSync(settingsPath, "utf8"));
    const settings = persistedSettingsSchema.parse(parsed);
    return { ...settings, baseUrl: stripTrailingSlash(settings.baseUrl) };
  } catch (error) {
    throw new ModelSettingsError(
      "invalid_model_settings",
      `本地模型配置无效: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
}

function resolveSettingsPath(): string {
  return path.resolve(config.MODEL_SETTINGS_PATH);
}

function stripTrailingSlash(value: string): string {
  return value.replace(/\/+$/, "");
}

function isHttpUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:";
  } catch {
    return false;
  }
}
