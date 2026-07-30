import { afterEach, describe, it, expect, vi } from "vitest";
import {
  parseRepoUrl,
  isValidGithubUrl,
  getMaxRepoSizeKB,
  normalizeGithubUrl,
  isValidBranchName,
  preflightGithubRepo,
} from "../../src/lib/repo.js";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("parseRepoUrl", () => {
  it("parses standard GitHub URL", () => {
    const result = parseRepoUrl("https://github.com/expressjs/express");
    expect(result).toEqual({ owner: "expressjs", repo: "express", isGitHub: true });
  });

  it("parses URL with .git suffix", () => {
    const result = parseRepoUrl("https://github.com/vuejs/core.git");
    expect(result).toEqual({ owner: "vuejs", repo: "core", isGitHub: true });
  });

  it("parses URL with tree subpath", () => {
    const result = parseRepoUrl("https://github.com/facebook/react/tree/main/packages");
    expect(result).toEqual({ owner: "facebook", repo: "react", isGitHub: true });
  });

  it("returns isGitHub false for non-GitHub URL", () => {
    const result = parseRepoUrl("https://gitlab.com/user/repo");
    expect(result).toEqual({ owner: "user", repo: "repo", isGitHub: false });
  });

  it("handles invalid URL gracefully", () => {
    const result = parseRepoUrl("not-a-url");
    expect(result.owner).toBe("");
  });

  it("parses SSH format", () => {
    const result = parseRepoUrl("git@github.com:user/repo.git");
    expect(result).toEqual({ owner: "user", repo: "repo", isGitHub: true });
  });

  it("parses owner/repo shorthand", () => {
    expect(parseRepoUrl("facebook/react")).toEqual({
      owner: "facebook",
      repo: "react",
      isGitHub: true,
    });
  });
});

describe("isValidGithubUrl", () => {
  it("returns true for valid GitHub URL", () => {
    expect(isValidGithubUrl("https://github.com/expressjs/express")).toBe(true);
  });

  it("returns false for invalid URL", () => {
    expect(isValidGithubUrl("not-a-url")).toBe(false);
  });

  it("returns false for non-GitHub URL", () => {
    expect(isValidGithubUrl("https://gitlab.com/user/repo")).toBe(false);
  });

  it("accepts owner/repo shorthand", () => {
    expect(isValidGithubUrl("facebook/react")).toBe(true);
  });
});

describe("normalizeGithubUrl", () => {
  it("normalizes supported inputs to a canonical clone URL", () => {
    expect(normalizeGithubUrl("facebook/react")).toBe("https://github.com/facebook/react.git");
    expect(normalizeGithubUrl("https://github.com/facebook/react/tree/main")).toBe(
      "https://github.com/facebook/react.git",
    );
  });
});

describe("isValidBranchName", () => {
  it("accepts common branch names", () => {
    expect(isValidBranchName("main")).toBe(true);
    expect(isValidBranchName("feature/sse-recovery")).toBe(true);
  });

  it("rejects unsafe ref names", () => {
    expect(isValidBranchName("../main")).toBe(false);
    expect(isValidBranchName("-danger")).toBe(false);
    expect(isValidBranchName("bad branch")).toBe(false);
  });
});

describe("getMaxRepoSizeKB", () => {
  it("returns 200MB in KB by default", () => {
    expect(getMaxRepoSizeKB()).toBe(200 * 1024);
  });
});

describe("preflightGithubRepo", () => {
  it("distinguishes a missing repository from a temporary API failure", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce({ ok: false, status: 404 })
      .mockResolvedValueOnce({ ok: false, status: 403 }));

    await expect(preflightGithubRepo("missing", "repo")).resolves.toEqual({
      status: "not_found",
    });
    await expect(preflightGithubRepo("rate-limited", "repo")).resolves.toEqual({
      status: "unavailable",
    });
  });

  it("returns repository size when GitHub confirms access", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ size: 2048 }),
    }));

    await expect(preflightGithubRepo("facebook", "react")).resolves.toEqual({
      status: "available",
      sizeKb: 2048,
    });
  });
});
