import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("dotenv/config", () => ({}));

describe("config", () => {
  beforeEach(() => {
    vi.resetModules();
    delete process.env.ANTHROPIC_AUTH_TOKEN;
    delete process.env.ANTHROPIC_BASE_URL;
    delete process.env.ANTHROPIC_MODEL;
    delete process.env.LLM_PROVIDER;
    delete process.env.LLM_API_KEY;
    delete process.env.LLM_BASE_URL;
    delete process.env.LLM_MODEL;
    delete process.env.MODEL_SETTINGS_PATH;
    delete process.env.PORT;
    delete process.env.HOST;
    delete process.env.CLONE_TIMEOUT_MS;
    delete process.env.CLONE_DEPTH;
    delete process.env.MAX_REPO_SIZE_MB;
    delete process.env.TASK_TOTAL_TIMEOUT_MS;
    delete process.env.INTERACTION_TIMEOUT_MS;
    delete process.env.SQLITE_PATH;
    delete process.env.LOG_LEVEL;
    delete process.env.AGENT_RUNTIME_MODE;
    delete process.env.MENTOR_SUBAGENTS_ENABLED;
    delete process.env.AGENT_MAX_BUDGET_USD;
  });

  it("allows startup before a model API key is configured", async () => {
    const { config } = await import("../../src/config.js");
    expect(config.ANTHROPIC_AUTH_TOKEN).toBeUndefined();
  });

  it("parses valid environment with defaults", async () => {
    process.env.ANTHROPIC_AUTH_TOKEN = "sk-test";
    const { config } = await import("../../src/config.js");
    expect(config.PORT).toBe(3000);
    expect(config.HOST).toBe("127.0.0.1");
    expect(config.CLONE_DEPTH).toBe(1);
    expect(config.ANTHROPIC_AUTH_TOKEN).toBe("sk-test");
    expect(config.LOG_LEVEL).toBe("info");
    expect(config.AGENT_RUNTIME_MODE).toBe("adaptive");
    expect(config.MENTOR_SUBAGENTS_ENABLED).toBe(false);
  });

  it("parses custom values", async () => {
    process.env.ANTHROPIC_AUTH_TOKEN = "sk-test";
    process.env.PORT = "8080";
    process.env.MAX_REPO_SIZE_MB = "500";
    process.env.LOG_LEVEL = "debug";
    const { config } = await import("../../src/config.js");
    expect(config.PORT).toBe(8080);
    expect(config.MAX_REPO_SIZE_MB).toBe(500);
    expect(config.LOG_LEVEL).toBe("debug");
  });

  it("parses generic multi-provider environment values", async () => {
    process.env.LLM_PROVIDER = "openai-compatible";
    process.env.LLM_API_KEY = "openai-test";
    process.env.LLM_BASE_URL = "https://api.openai.com/v1";
    process.env.LLM_MODEL = "example-model";
    const { config } = await import("../../src/config.js");
    expect(config.LLM_PROVIDER).toBe("openai-compatible");
    expect(config.LLM_API_KEY).toBe("openai-test");
    expect(config.LLM_MODEL).toBe("example-model");
  });

  it("rejects invalid LOG_LEVEL", async () => {
    process.env.ANTHROPIC_AUTH_TOKEN = "sk-test";
    process.env.LOG_LEVEL = "verbose";
    await expect(() => import("../../src/config.js")).rejects.toThrow();
  });

  it("parses bounded Agent Runtime switches", async () => {
    process.env.AGENT_RUNTIME_MODE = "agentic";
    process.env.MENTOR_SUBAGENTS_ENABLED = "true";
    process.env.AGENT_MAX_BUDGET_USD = "0.25";
    const { config } = await import("../../src/config.js");
    expect(config.AGENT_RUNTIME_MODE).toBe("agentic");
    expect(config.MENTOR_SUBAGENTS_ENABLED).toBe(true);
    expect(config.AGENT_MAX_BUDGET_USD).toBe(0.25);
  });
});
