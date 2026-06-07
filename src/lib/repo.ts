import { simpleGit, type SimpleGit } from "simple-git";
import fs from "node:fs/promises";
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
  // SSH 格式: git@github.com:owner/repo.git
  const sshMatch = url.match(/^git@([^:]+):(.+?)(?:\.git)?$/);
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
    const u = new URL(url.replace(/\.git$/, ""));
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
): Promise<{ localPath: string; git: SimpleGit; commitHash: string }> {
  await fs.mkdir(taskDir, { recursive: true });
  const git = simpleGit();

  await git.clone(url, taskDir, {
    "--depth": String(parseInt(process.env.CLONE_DEPTH ?? "1", 10)),
    "--single-branch": null,
  });

  const taskGit = simpleGit(taskDir);
  const commitHash = await taskGit.revparse(["HEAD"]);

  return { localPath: taskDir, git: taskGit, commitHash };
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
    const log = await git.log({ maxCount: 50 });
    const logEntries = log.all;

    const themes = logEntries
      .map((c: { message: string }) => c.message.split("\n")[0] ?? "")
      .filter(Boolean)
      .slice(0, 50);

    const contributors = new Set(
      logEntries.map((c: { author_email: string }) => c.author_email)
    );

    const fileFrequency = new Map<string, number>();
    try {
      const diffOutput = await git.raw([
        "diff-tree", "--no-commit-id", "--name-only", "-r",
        `HEAD~${Math.min(50, logEntries.length)}..HEAD`,
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
