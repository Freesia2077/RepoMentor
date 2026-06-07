import path from "node:path";

export class SandboxError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "SandboxError";
  }
}

export function resolvePath(taskWorkDir: string, requestedPath: string): string {
  const resolved = path.resolve(taskWorkDir, requestedPath);

  const normalizedWorkDir = taskWorkDir.endsWith(path.sep) ? taskWorkDir : taskWorkDir + path.sep;

  if (!resolved.startsWith(normalizedWorkDir) && resolved !== taskWorkDir) {
    throw new SandboxError("路径逃逸，拒绝访问");
  }

  return resolved;
}
