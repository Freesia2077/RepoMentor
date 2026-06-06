import { describe, it, expect, beforeEach, vi } from "vitest";

describe("config", () => {
  beforeEach(() => {
    vi.resetModules();
    delete process.env.DEEPSEEK_API_KEY;
    delete process.env.DEEPSEEK_BASE_URL;
    delete process.env.PORT;
    delete process.env.HOST;
    delete process.env.CLONE_TIMEOUT_MS;
    delete process.env.CLONE_DEPTH;
    delete process.env.MAX_REPO_SIZE_MB;
    delete process.env.TASK_TOTAL_TIMEOUT_MS;
    delete process.env.INTERACTION_TIMEOUT_MS;
    delete process.env.SQLITE_PATH;
    delete process.env.LOG_LEVEL;
  });

  it("throws when DEEPSEEK_API_KEY is missing", async () => {
    await expect(() => import("../../src/config.js")).rejects.toThrow();
  });

  it("parses valid environment with defaults", async () => {
    process.env.DEEPSEEK_API_KEY = "sk-test";
    const { config } = await import("../../src/config.js");
    expect(config.PORT).toBe(3000);
    expect(config.HOST).toBe("0.0.0.0");
    expect(config.CLONE_DEPTH).toBe(1);
    expect(config.DEEPSEEK_BASE_URL).toBe("https://api.deepseek.com");
    expect(config.LOG_LEVEL).toBe("info");
  });

  it("parses custom values", async () => {
    process.env.DEEPSEEK_API_KEY = "sk-test";
    process.env.PORT = "8080";
    process.env.MAX_REPO_SIZE_MB = "500";
    process.env.LOG_LEVEL = "debug";
    const { config } = await import("../../src/config.js");
    expect(config.PORT).toBe(8080);
    expect(config.MAX_REPO_SIZE_MB).toBe(500);
    expect(config.LOG_LEVEL).toBe("debug");
  });

  it("rejects invalid LOG_LEVEL", async () => {
    process.env.DEEPSEEK_API_KEY = "sk-test";
    process.env.LOG_LEVEL = "verbose";
    await expect(() => import("../../src/config.js")).rejects.toThrow();
  });
});
