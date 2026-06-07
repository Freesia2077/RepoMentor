import { describe, it, expect } from "vitest";
import { resolvePath } from "../../src/lib/sandbox.js";
import path from "node:path";

describe("resolvePath", () => {
  const workDir = path.resolve("/tmp/task123");

  it("resolves a normal path within work dir", () => {
    const result = resolvePath(workDir, "src/index.ts");
    expect(result).toBe(path.resolve("/tmp/task123/src/index.ts"));
  });

  it("resolves nested path", () => {
    const result = resolvePath(workDir, "src/core/middleware.ts");
    expect(result).toBe(path.resolve("/tmp/task123/src/core/middleware.ts"));
  });

  it("throws on path traversal with ..", () => {
    expect(() => resolvePath(workDir, "../../etc/passwd")).toThrow("路径逃逸");
  });

  it("throws on absolute path outside work dir", () => {
    expect(() => resolvePath(workDir, "/etc/passwd")).toThrow("路径逃逸");
  });

  it("throws on deep traversal", () => {
    expect(() => resolvePath(workDir, "src/../../../var/log")).toThrow("路径逃逸");
  });

  it("resolves empty string to work dir", () => {
    const result = resolvePath(workDir, "");
    expect(result).toBe(workDir);
  });
});
