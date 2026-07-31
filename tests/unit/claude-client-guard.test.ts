import { describe, expect, it } from "vitest";
import path from "node:path";
import { createRepoToolGuard } from "../../src/services/claude-client.js";

describe("Agent repository tool guard", () => {
  const repoRoot = path.resolve("tmp", "guard-repo");
  const guard = createRepoToolGuard(repoRoot);

  it("allows repository-relative paths", async () => {
    await expect(guard("Read", { file_path: "src/index.ts" })).resolves.toMatchObject({
      behavior: "allow",
    });
  });

  it("denies direct and glob traversal outside the repository", async () => {
    await expect(guard("Read", { file_path: "../../secret.txt" })).resolves.toMatchObject({
      behavior: "deny",
    });
    await expect(guard("Glob", { pattern: "../**/*" })).resolves.toMatchObject({
      behavior: "deny",
    });
  });

  it("reserves output time by denying tools after the configured budget", async () => {
    const limitedGuard = createRepoToolGuard(repoRoot, 2);

    await expect(limitedGuard("Glob", { pattern: "*" })).resolves.toMatchObject({
      behavior: "allow",
    });
    await expect(limitedGuard("Read", { file_path: "README.md" })).resolves.toMatchObject({
      behavior: "allow",
    });
    await expect(limitedGuard("Read", { file_path: "package.json" })).resolves.toMatchObject({
      behavior: "deny",
      interrupt: false,
    });
  });

  it("denies an exact duplicate tool request without consuming useful work", async () => {
    const duplicateGuard = createRepoToolGuard(repoRoot, 2);

    await expect(duplicateGuard("Read", {
      file_path: "README.md",
      offset: 0,
    })).resolves.toMatchObject({ behavior: "allow" });
    await expect(duplicateGuard("Read", {
      offset: 0,
      file_path: "README.md",
    })).resolves.toMatchObject({
      behavior: "deny",
      message: expect.stringContaining("已经调用过"),
    });
    await expect(duplicateGuard("Read", {
      file_path: "package.json",
    })).resolves.toMatchObject({ behavior: "allow" });
  });
});
