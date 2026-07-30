import { describe, it, expect } from "vitest";
import {
  parseRepoUrl,
  isValidGithubUrl,
  getMaxRepoSizeKB,
  normalizeGithubUrl,
  isValidBranchName,
} from "../../src/lib/repo.js";

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
