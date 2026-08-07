import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import Fastify from "fastify";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  save: vi.fn(),
}));

vi.mock("../../src/services/model-settings.js", () => ({
  getPublicModelSettings: mocks.get,
  saveModelSettings: mocks.save,
  ModelSettingsError: class ModelSettingsError extends Error {
    code = "model_not_configured";
  },
}));

import { settingsRoutes } from "../../src/routes/settings.js";

describe("model settings API", () => {
  const app = Fastify({ logger: false });

  beforeAll(async () => {
    await app.register(settingsRoutes);
    await app.ready();
  });

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns only public settings metadata", async () => {
    mocks.get.mockReturnValue({
      provider: "anthropic-compatible",
      baseUrl: "https://example.com",
      model: "example-model",
      hasApiKey: true,
      source: "file",
    });
    const response = await app.inject({ method: "GET", url: "/api/settings/model" });
    expect(response.statusCode).toBe(200);
    expect(response.headers["cache-control"]).toBe("no-store");
    expect(response.json()).not.toHaveProperty("apiKey");
  });

  it("validates and saves local settings", async () => {
    mocks.save.mockReturnValue({
      provider: "openai-compatible",
      baseUrl: "https://api.example.com/v1",
      model: "example-model",
      hasApiKey: true,
      source: "file",
    });
    const response = await app.inject({
      method: "PUT",
      url: "/api/settings/model",
      payload: {
        provider: "openai-compatible",
        baseUrl: "https://api.example.com/v1",
        model: "example-model",
        apiKey: "secret",
      },
    });
    expect(response.statusCode).toBe(200);
    expect(mocks.save).toHaveBeenCalledWith(expect.objectContaining({ apiKey: "secret" }));
    expect(response.json()).not.toHaveProperty("apiKey");
  });

  it("rejects an unsupported provider", async () => {
    const response = await app.inject({
      method: "PUT",
      url: "/api/settings/model",
      payload: {
        provider: "unknown",
        baseUrl: "https://example.com",
        model: "model",
      },
    });
    expect(response.statusCode).toBe(400);
    expect(response.json().error).toBe("invalid_model_settings");
  });

  it("rejects model-setting writes from non-loopback clients", async () => {
    const response = await app.inject({
      method: "PUT",
      url: "/api/settings/model",
      remoteAddress: "192.168.1.25",
      payload: {
        provider: "openai-compatible",
        baseUrl: "https://attacker.example/v1",
        model: "capture-key",
      },
    });

    expect(response.statusCode).toBe(403);
    expect(response.json().error).toBe("local_access_required");
    expect(mocks.save).not.toHaveBeenCalled();
  });
});
