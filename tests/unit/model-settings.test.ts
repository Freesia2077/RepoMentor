import fs from "node:fs";
import { afterAll, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => {
  const settingsPath = `${process.env.TEMP ?? process.env.TMP ?? "."}/repomentor-model-settings-${process.pid}-${Date.now()}.json`;
  return {
    settingsPath,
    testConfig: {
      LLM_PROVIDER: undefined as "anthropic-compatible" | "openai-compatible" | undefined,
      LLM_API_KEY: undefined as string | undefined,
      LLM_BASE_URL: undefined as string | undefined,
      LLM_MODEL: undefined as string | undefined,
      ANTHROPIC_AUTH_TOKEN: "legacy-key" as string | undefined,
      ANTHROPIC_BASE_URL: "https://api.deepseek.com/anthropic" as string | undefined,
      ANTHROPIC_MODEL: "deepseek-v4-flash" as string | undefined,
      MODEL_SETTINGS_PATH: settingsPath,
    },
  };
});
vi.mock("../../src/config.js", () => ({ config: mocks.testConfig }));

const { settingsPath, testConfig } = mocks;

import {
  getModelSettings,
  getPublicModelSettings,
  requireModelSettings,
  resetModelSettingsCache,
  saveModelSettings,
} from "../../src/services/model-settings.js";

describe("local model settings", () => {
  beforeEach(() => {
    if (fs.existsSync(settingsPath)) fs.unlinkSync(settingsPath);
    Object.assign(testConfig, {
      LLM_PROVIDER: undefined,
      LLM_API_KEY: undefined,
      LLM_BASE_URL: undefined,
      LLM_MODEL: undefined,
      ANTHROPIC_AUTH_TOKEN: "legacy-key",
      ANTHROPIC_BASE_URL: "https://api.deepseek.com/anthropic",
      ANTHROPIC_MODEL: "deepseek-v4-flash",
    });
    resetModelSettingsCache();
  });

  afterAll(() => {
    if (fs.existsSync(settingsPath)) fs.unlinkSync(settingsPath);
  });

  it("keeps legacy Anthropic-compatible environment settings working", () => {
    expect(requireModelSettings()).toMatchObject({
      provider: "anthropic-compatible",
      apiKey: "legacy-key",
      model: "deepseek-v4-flash",
      source: "environment",
    });
  });

  it("saves a local provider without returning the API key", () => {
    const result = saveModelSettings({
      provider: "openai-compatible",
      apiKey: "openai-secret",
      baseUrl: "https://api.example.com/v1/",
      model: "example-model",
    });
    expect(result).toEqual({
      provider: "openai-compatible",
      baseUrl: "https://api.example.com/v1",
      model: "example-model",
      hasApiKey: true,
      source: "file",
    });
    expect(result).not.toHaveProperty("apiKey");
    expect(JSON.parse(fs.readFileSync(settingsPath, "utf8"))).toMatchObject({
      apiKey: "openai-secret",
    });
  });

  it("requires a new key when switching provider protocols", () => {
    expect(() => saveModelSettings({
      provider: "openai-compatible",
      baseUrl: "https://api.example.com/v1",
      model: "example-model",
    })).toThrow("切换 Provider 时必须提供对应厂商的 API Key");
  });

  it("requires an explicit model for OpenAI-compatible environment settings", () => {
    Object.assign(testConfig, {
      LLM_PROVIDER: "openai-compatible",
      LLM_API_KEY: "openai-secret",
      ANTHROPIC_AUTH_TOKEN: undefined,
      ANTHROPIC_MODEL: undefined,
    });
    resetModelSettingsCache();
    expect(getModelSettings().baseUrl).toBe("https://api.openai.com/v1");
    expect(getPublicModelSettings().model).toBe("");
    expect(() => requireModelSettings()).toThrow("尚未配置模型 ID");
  });
});
