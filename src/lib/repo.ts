import { simpleGit, type SimpleGit } from "simple-git";
import fs from "node:fs/promises";
import path from "node:path";
import type { CommitSummary } from "../types/index.js";

// Lazy: read env directly, don't import config (avoids test failures when DEEPSEEK_API_KEY not set)
export function getMaxRepoSizeKB(): number {
  const mb = parseInt(process.env.MAX_REPO_SIZE_MB ?? "200", 10);
  return mb * 1024;
}

interface ParsedRepo {
  owner: string;
  repo: string;
  isGitHub: boolean;
}

export function parseRepoUrl(url: string): ParsedRepo {
  const trimmed = url.trim();

  // 简写格式: owner/repo
  const shorthandMatch = trimmed.match(/^([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+?)(?:\.git)?$/);
  if (shorthandMatch) {
    return {
      owner: shorthandMatch[1]!,
      repo: shorthandMatch[2]!,
      isGitHub: true,
    };
  }

  // SSH 格式: git@github.com:owner/repo.git
  const sshMatch = trimmed.match(/^git@([^:]+):(.+?)(?:\.git)?$/);
  if (sshMatch) {
    const host = sshMatch[1]!;
    const parts = sshMatch[2]!.split("/");
    return {
      owner: parts[0] ?? "",
      repo: (parts[1] ?? "").replace(/\.git$/, ""),
      isGitHub: host === "github.com",
    };
  }

  // HTTPS 格式: https://github.com/owner/repo/...
  try {
    const u = new URL(trimmed.replace(/\.git$/, ""));
    const parts = u.pathname.replace(/\/$/, "").split("/").filter(Boolean);
    return {
      owner: parts[0] ?? "",
      repo: (parts[1] ?? "").replace(/\.git$/, ""),
      isGitHub: u.hostname === "github.com",
    };
  } catch {
    return { owner: "", repo: "", isGitHub: false };
  }
}

export function isValidGithubUrl(url: string): boolean {
  const parsed = parseRepoUrl(url);
  return parsed.isGitHub && parsed.owner !== "" && parsed.repo !== "";
}

export function normalizeGithubUrl(url: string): string {
  const parsed = parseRepoUrl(url);
  if (!parsed.isGitHub || !parsed.owner || !parsed.repo) return url.trim();
  return `https://github.com/${parsed.owner}/${parsed.repo}.git`;
}

export function isValidBranchName(branch: string): boolean {
  const value = branch.trim();
  return value.length > 0
    && value.length <= 255
    && !value.startsWith("-")
    && !value.startsWith("/")
    && !value.endsWith("/")
    && !value.endsWith(".")
    && !value.includes("..")
    && !value.includes("@{")
    && !/[\s~^:?*\[\\]/.test(value);
}

export async function fetchRepoSize(owner: string, repo: string): Promise<number | null> {
  try {
    const response = await fetch(`https://api.github.com/repos/${owner}/${repo}`, {
      headers: { Accept: "application/vnd.github+json" },
    });
    if (!response.ok) return null;
    const data = await response.json() as { size?: number };
    return data.size ?? null; // size 单位: KB
  } catch {
    return null;
  }
}

export async function cloneRepo(
  url: string,
  taskDir: string,
  branch = "main",
  options: { depth?: number; timeoutMs?: number } = {},
): Promise<{ localPath: string; git: SimpleGit; commitHash: string; cached: boolean }> {
  const previous = cloneLocks.get(taskDir) ?? Promise.resolve();
  const current = previous
    .catch(() => undefined)
    .then(() => cloneRepoUnlocked(url, taskDir, branch, options));
  cloneLocks.set(taskDir, current);

  try {
    return await current;
  } finally {
    if (cloneLocks.get(taskDir) === current) {
      cloneLocks.delete(taskDir);
    }
  }
}

const cloneLocks = new Map<string, Promise<{
  localPath: string;
  git: SimpleGit;
  commitHash: string;
  cached: boolean;
}>>();

async function cloneRepoUnlocked(
  url: string,
  taskDir: string,
  branch: string,
  options: { depth?: number; timeoutMs?: number },
): Promise<{ localPath: string; git: SimpleGit; commitHash: string; cached: boolean }> {
  const depth = options.depth ?? parseInt(process.env.CLONE_DEPTH ?? "1", 10);
  const timeoutMs = options.timeoutMs ?? parseInt(process.env.CLONE_TIMEOUT_MS ?? "60000", 10);
  const gitOptions = { timeout: { block: timeoutMs } };

  try {
    const stat = await fs.stat(taskDir);
    if (stat.isDirectory()) {
      const taskGit = simpleGit({ baseDir: taskDir, ...gitOptions });
      if (await taskGit.checkIsRepo()) {
        await taskGit.raw(["fetch", "origin", branch, "--depth", String(depth)]);
        await taskGit.raw(["checkout", "-B", branch, `origin/${branch}`]);
        await taskGit.reset(["--hard", `origin/${branch}`]);
        const commitHash = await taskGit.revparse(["HEAD"]);
        return { localPath: taskDir, git: taskGit, commitHash, cached: true };
      }
    }
    await fs.rm(taskDir, { recursive: true, force: true });
  } catch {
    // 缓存目录不存在时继续 clone
  }

  await fs.mkdir(path.dirname(taskDir), { recursive: true });
  const git = simpleGit(gitOptions);

  await git.clone(url, taskDir, {
    "--depth": String(depth),
    "--branch": branch,
    "--single-branch": null,
  });

  const taskGit = simpleGit({ baseDir: taskDir, ...gitOptions });
  const commitHash = await taskGit.revparse(["HEAD"]);

  return { localPath: taskDir, git: taskGit, commitHash, cached: false };
}

export async function getFileCount(localPath: string): Promise<number> {
  const git = simpleGit(localPath);
  try {
    const result = await git.raw(["ls-files"]);
    return result.split("\n").filter(Boolean).length;
  } catch {
    return 0;
  }
}

export async function extractCommitSummary(localPath: string): Promise<CommitSummary> {
  const git = simpleGit(localPath);

  try {
    const log = await git.log({ maxCount: 10 });
    const logEntries = log.all;

    const themes = logEntries
      .map((c: { message: string }) => c.message.split("\n")[0] ?? "")
      .filter(Boolean)
      .slice(0, 10);

    const contributors = new Set(
      logEntries.map((c: { author_email: string }) => c.author_email)
    );

    const fileFrequency = new Map<string, number>();
    try {
      const diffOutput = await git.raw([
        "diff-tree", "--no-commit-id", "--name-only", "-r",
        `HEAD~${Math.max(0, logEntries.length - 1)}..HEAD`,
      ]);
      const changedFiles = diffOutput.split("\n").filter(Boolean);
      for (const file of changedFiles) {
        fileFrequency.set(file, (fileFrequency.get(file) ?? 0) + 1);
      }
    } catch {
      // diff-tree 失败时降级为空
    }

    const frequentFiles = Array.from(fileFrequency.entries())
      .sort(([, a], [, b]) => b - a)
      .slice(0, 20)
      .map(([file, commits]) => ({ file, commits, recent: true }));

    return { frequentFiles, recentThemes: themes, contributorCount: contributors.size };
  } catch {
    return { frequentFiles: [], recentThemes: [], contributorCount: 0 };
  }
}

export async function cleanup(localPath: string): Promise<void> {
  try {
    await fs.rm(localPath, { recursive: true, force: true });
  } catch {
    // 清理失败不阻塞
  }
}
