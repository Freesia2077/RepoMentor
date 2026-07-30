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
});
