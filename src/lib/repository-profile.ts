import fs from "node:fs/promises";
import path from "node:path";
import { simpleGit } from "simple-git";
import {
  decodeBoundedUtf8,
  prepareEvidenceContent,
  type PreparedEvidenceContent,
} from "./evidence-content.js";

import type {
  EvidenceBundle,
  EvidencePhase,
  EvidencePlan,
  ContributionEvidence,
  RepositoryContributionContext,
  RepositoryDirectoryStat,
  RepositoryFileExcerpt,
  RepositoryOverview,
  RepositoryProfile,
  TodoMarker,
} from "../types/index.js";

const MAX_TREE_ENTRIES = 200;
const MAX_FILE_INDEX_ENTRIES = 2_500;
const MAX_FILE_INDEX_BYTES = 80_000;
const MAX_DIRECTORY_STATS = 100;
const MAX_MANIFEST_FILES = 8;
const MAX_EXAMPLE_MANIFEST_FILES = 3;
const MAX_CONFIG_FILES = 10;
const MAX_GUIDANCE_FILES = 3;
const MAX_ENTRY_CANDIDATES = 20;
const MAX_TEST_CANDIDATES = 20;
const MAX_TODO_MARKERS = 20;
const MAX_TODO_SCAN_FILES = 500;
const MAX_TODO_SCAN_BYTES = 2_000_000;
const MAX_TODO_FILE_BYTES = 50_000;
const MAX_README_BYTES = 10_000;
const MAX_MANIFEST_BYTES = 4_000;
const MAX_CONFIG_BYTES = 3_000;
const MAX_GUIDANCE_BYTES = 4_000;
const MAX_MANIFEST_CONTENT_BYTES = 14_000;
const MAX_CONFIG_CONTENT_BYTES = 10_000;
const MAX_GUIDANCE_CONTENT_BYTES = 8_000;
const MAX_EXAMPLE_MANIFEST_CONTENT_BYTES = 7_000;
const MAX_EVIDENCE_FILES: Record<EvidencePhase, number> = {
  explorer: 10,
  mentor: 8,
};
const MAX_EVIDENCE_FILE_BYTES_WITH_SPARE_BUDGET = 24_000;
const MIN_EVIDENCE_FILE_BYTES = 4_000;
const MAX_CONTRIBUTION_EVIDENCE_FILES = 10;
const MAX_CONTRIBUTION_EVIDENCE_BYTES = 40_000;
const MAX_EVIDENCE_TOTAL_BYTES: Record<EvidencePhase, number> = {
  explorer: 48_000,
  mentor: 40_000,
};

const MANIFEST_NAMES = new Set([
  "package.json",
  "pyproject.toml",
  "cargo.toml",
  "go.mod",
  "pom.xml",
  "build.gradle",
  "build.gradle.kts",
  "requirements.txt",
  "setup.py",
  "setup.cfg",
  "composer.json",
  "gemfile",
  "mix.exs",
  "pubspec.yaml",
  "package.swift",
  "deno.json",
  "deno.jsonc",
]);

const LANGUAGE_BY_EXTENSION: Record<string, string> = {
  ".ts": "TypeScript",
  ".tsx": "TypeScript",
  ".js": "JavaScript",
  ".jsx": "JavaScript",
  ".mjs": "JavaScript",
  ".cjs": "JavaScript",
  ".py": "Python",
  ".go": "Go",
  ".rs": "Rust",
  ".java": "Java",
  ".kt": "Kotlin",
  ".kts": "Kotlin",
  ".rb": "Ruby",
  ".php": "PHP",
  ".cs": "C#",
  ".cpp": "C++",
  ".cc": "C++",
  ".c": "C",
  ".h": "C/C++",
  ".swift": "Swift",
  ".dart": "Dart",
  ".ex": "Elixir",
  ".exs": "Elixir",
  ".vue": "Vue",
  ".svelte": "Svelte",
  ".sol": "Solidity",
  ".sh": "Shell",
};

const ENTRY_BASENAMES = new Set([
  "index",
  "main",
  "app",
  "server",
  "cli",
  "__init__",
  "__main__",
  "mod",
  "lib",
]);

const ENTRY_EXTENSIONS = new Set([
  ".ts",
  ".tsx",
  ".js",
  ".jsx",
  ".mjs",
  ".cjs",
  ".py",
  ".go",
  ".rs",
  ".java",
  ".kt",
  ".kts",
  ".rb",
  ".php",
  ".cs",
  ".cpp",
  ".cc",
  ".c",
  ".swift",
  ".dart",
  ".ex",
  ".exs",
]);

const NON_PROJECT_MANIFEST_DIRECTORIES = new Set([
  "example",
  "examples",
  "test",
  "tests",
  "fixture",
  "fixtures",
  "docs",
  ".github",
]);

const CONFIG_FILE_PATTERNS = [
  /(^|\/)(?:tsconfig(?:\.[^/]+)?\.json|jsconfig\.json)$/i,
  /(^|\/)(?:eslint\.config\.[^/]+|\.eslintrc(?:\.[^/]+)?)$/i,
  /(^|\/)(?:prettier\.config\.[^/]+|\.prettierrc(?:\.[^/]+)?)$/i,
  /(^|\/)(?:vite|vitest|webpack|rollup|jest|playwright|cypress)\.config\.[^/]+$/i,
  /(^|\/)(?:turbo\.json|nx\.json|pnpm-workspace\.yaml|lerna\.json)$/i,
  /(^|\/)(?:makefile|dockerfile|docker-compose\.ya?ml)$/i,
  /^\.github\/workflows\/[^/]+\.ya?ml$/i,
];

export async function buildRepositoryProfile(
  localPath: string,
  providedTrackedFiles?: string[],
): Promise<RepositoryProfile> {
  const trackedFiles = providedTrackedFiles ?? await listTrackedFiles(localPath);
  const normalizedFiles = trackedFiles
    .map((file) => file.replaceAll("\\", "/"))
    .filter(Boolean)
    .sort((a, b) => a.localeCompare(b));

  const readmePath = selectReadme(normalizedFiles);
  const manifestPaths = selectManifests(normalizedFiles);
  const exampleManifestPaths = selectExampleManifests(normalizedFiles);
  const configPaths = selectConfigFiles(normalizedFiles);
  const guidancePaths = selectGuidanceFiles(normalizedFiles);
  const entryCandidates = selectEntryCandidates(normalizedFiles);
  const testCandidates = selectTestCandidates(normalizedFiles);
  const readme = readmePath
    ? await readRepositoryFile(
        localPath,
        readmePath,
        MAX_README_BYTES,
      )
    : null;

  const manifests: RepositoryFileExcerpt[] = [];
  let remainingManifestBytes = MAX_MANIFEST_CONTENT_BYTES;
  for (const manifestPath of manifestPaths) {
    if (remainingManifestBytes <= 0) break;
    const manifest = await readRepositoryFile(
      localPath,
      manifestPath,
      Math.min(MAX_MANIFEST_BYTES, remainingManifestBytes),
    );
    if (!manifest) continue;
    manifests.push(manifest);
    remainingManifestBytes -= Buffer.byteLength(manifest.content, "utf8");
  }

  const configFiles: RepositoryFileExcerpt[] = [];
  let remainingConfigBytes = MAX_CONFIG_CONTENT_BYTES;
  for (const configPath of configPaths) {
    if (remainingConfigBytes <= 0) break;
    const configFile = await readRepositoryFile(
      localPath,
      configPath,
      Math.min(MAX_CONFIG_BYTES, remainingConfigBytes),
    );
    if (!configFile) continue;
    configFiles.push(configFile);
    remainingConfigBytes -= Buffer.byteLength(configFile.content, "utf8");
  }

  const guidanceFiles: RepositoryFileExcerpt[] = [];
  let remainingGuidanceBytes = MAX_GUIDANCE_CONTENT_BYTES;
  for (const guidancePath of guidancePaths) {
    if (remainingGuidanceBytes <= 0) break;
    const guidance = await readRepositoryFile(
      localPath,
      guidancePath,
      Math.min(MAX_GUIDANCE_BYTES, remainingGuidanceBytes),
    );
    if (!guidance) continue;
    guidanceFiles.push(guidance);
    remainingGuidanceBytes -= Buffer.byteLength(guidance.content, "utf8");
  }

  const exampleManifests: RepositoryFileExcerpt[] = [];
  let remainingExampleManifestBytes = MAX_EXAMPLE_MANIFEST_CONTENT_BYTES;
  for (const manifestPath of exampleManifestPaths) {
    if (remainingExampleManifestBytes <= 0) break;
    const manifest = await readRepositoryFile(
      localPath,
      manifestPath,
      Math.min(MAX_MANIFEST_BYTES, remainingExampleManifestBytes),
    );
    if (!manifest) continue;
    exampleManifests.push(manifest);
    remainingExampleManifestBytes -= Buffer.byteLength(manifest.content, "utf8");
  }

  const { entries: topLevelTree, truncated: treeTruncated } = buildTopLevelTree(normalizedFiles);
  const { entries: fileIndex, truncated: fileIndexTruncated } = buildFileIndex(
    normalizedFiles,
    [
      ...entryCandidates,
      ...testCandidates,
      ...manifestPaths,
      ...exampleManifestPaths,
      ...configPaths,
      ...guidancePaths,
    ],
  );

  return {
    fileCount: normalizedFiles.length,
    fileIndex,
    fileIndexTruncated,
    topLevelTree,
    treeTruncated,
    directoryStats: buildDirectoryStats(normalizedFiles),
    readme,
    manifests,
    exampleManifests,
    configFiles,
    guidanceFiles,
    todoMarkers: await scanTodoMarkers(localPath, normalizedFiles),
    languageStats: buildLanguageStats(normalizedFiles),
    entryCandidates,
    testCandidates,
  };
}

export function buildRepositoryOverview(
  profile: RepositoryProfile,
): RepositoryOverview {
  return {
    fileCount: profile.fileCount,
    topLevelTree: profile.topLevelTree,
    treeTruncated: profile.treeTruncated,
    directoryStats: profile.directoryStats,
    languageStats: profile.languageStats,
    entryCandidates: profile.entryCandidates,
    testCandidates: profile.testCandidates,
    projectFiles: {
      readme: profile.readme?.path ?? null,
      manifests: profile.manifests.map((file) => file.path),
      exampleManifests: profile.exampleManifests.map((file) => file.path),
      configFiles: profile.configFiles.map((file) => file.path),
      guidanceFiles: profile.guidanceFiles.map((file) => file.path),
    },
  };
}

export function buildRepositoryContributionContext(
  profile: RepositoryProfile,
): RepositoryContributionContext {
  return {
    fileCount: profile.fileCount,
    manifests: profile.manifests,
    exampleManifests: profile.exampleManifests,
    configFiles: profile.configFiles,
    guidanceFiles: profile.guidanceFiles,
    todoMarkers: profile.todoMarkers,
    languageStats: profile.languageStats,
    entryCandidates: profile.entryCandidates,
    testCandidates: profile.testCandidates,
  };
}

export function buildContributionEvidence(
  bundle: EvidenceBundle,
  referencedPaths: string[] = [],
): ContributionEvidence {
  const referenced = new Set(referencedPaths.map((value) => value.replaceAll("\\", "/")));
  const ordered = [...bundle.files].sort((left, right) => {
    const leftRank = referenced.has(left.path) ? 0 : left.phase === "mentor" ? 1 : 2;
    const rightRank = referenced.has(right.path) ? 0 : right.phase === "mentor" ? 1 : 2;
    return leftRank - rightRank;
  });
  const focusedFiles: EvidenceBundle["files"] = [];
  let focusedBytes = 0;
  for (const file of ordered) {
    const bytes = Buffer.byteLength(file.content, "utf8");
    if (
      focusedFiles.length >= MAX_CONTRIBUTION_EVIDENCE_FILES
      || focusedBytes + bytes > MAX_CONTRIBUTION_EVIDENCE_BYTES
    ) {
      continue;
    }
    focusedFiles.push(file);
    focusedBytes += bytes;
  }
  return {
    examinedFiles: bundle.files.map((file) => ({
      path: file.path,
      purpose: file.purpose,
      phase: file.phase,
      truncated: file.truncated,
    })),
    focusedFiles,
    totalBytes: focusedBytes,
  };
}

export async function buildEvidenceBundle(
  localPath: string,
  plan: EvidencePlan,
  phase: EvidencePhase,
  existingPaths: string[] = [],
  providedTrackedFiles?: string[],
): Promise<EvidenceBundle> {
  const trackedFiles = providedTrackedFiles ?? await listTrackedFiles(localPath);
  const trackedSet = new Set(
    trackedFiles.map((file) => file.replaceAll("\\", "/")).filter(Boolean),
  );
  const existingSet = new Set(
    existingPaths.map((file) => file.replaceAll("\\", "/").replace(/^\.\/+/, "")),
  );
  const seen = new Set<string>();
  const files: EvidenceBundle["files"] = [];
  const skippedPaths: string[] = [];
  const omissions: EvidenceBundle["omissions"] = [];
  const candidates: Array<{ request: EvidencePlan["files"][number]; path: string }> = [];

  for (const request of plan.files) {
    const relativePath = request.path.replaceAll("\\", "/").replace(/^\.\/+/, "");
    let omissionReason: EvidenceBundle["omissions"][number]["reason"] | null = null;
    if (seen.has(relativePath)) omissionReason = "duplicate_request";
    else {
      seen.add(relativePath);
      if (existingSet.has(relativePath)) omissionReason = "already_available";
      else if (!trackedSet.has(relativePath)) omissionReason = "not_tracked";
      else if (candidates.length >= MAX_EVIDENCE_FILES[phase]) omissionReason = "file_limit";
    }
    if (omissionReason) {
      skippedPaths.push(relativePath);
      omissions.push({ path: relativePath, reason: omissionReason });
      continue;
    }
    candidates.push({ request, path: relativePath });
  }

  const prepared: Array<{
    request: EvidencePlan["files"][number];
    file: PreparedEvidenceContent;
  }> = [];
  for (const candidate of candidates) {
    const result = await prepareEvidenceContent(
      localPath,
      candidate.path,
      MAX_EVIDENCE_FILE_BYTES_WITH_SPARE_BUDGET,
    );
    if (!result.file) {
      skippedPaths.push(candidate.path);
      omissions.push({ path: candidate.path, reason: result.reason });
      continue;
    }
    prepared.push({ request: candidate.request, file: result.file });
  }

  const budgets = allocateEvidenceBudgets(
    prepared.map(({ request, file }) => ({ request, targetBytes: Buffer.byteLength(file.content, "utf8") })),
    MAX_EVIDENCE_TOTAL_BYTES[phase],
  );
  for (let index = 0; index < prepared.length; index++) {
    const item = prepared[index]!;
    const budget = budgets[index] ?? 0;
    const targetBytes = Buffer.byteLength(item.file.content, "utf8");
    if (budget <= 0 && targetBytes > 0) {
      skippedPaths.push(item.file.path);
      omissions.push({ path: item.file.path, reason: "budget_exhausted" });
      continue;
    }
    const content = decodeBoundedUtf8(Buffer.from(item.file.content, "utf8"), budget);

    files.push({
      path: item.file.path,
      content,
      truncated: item.file.truncated
        || Buffer.byteLength(content, "utf8") < item.file.sourceBytes,
      purpose: item.request.purpose,
      phase,
    });
  }

  return {
    files,
    skippedPaths: [...new Set(skippedPaths)],
    omissions,
    totalBytes: files.reduce(
      (total, file) => total + Buffer.byteLength(file.content, "utf8"),
      0,
    ),
  };
}

function allocateEvidenceBudgets(
  candidates: Array<{
    request: EvidencePlan["files"][number];
    targetBytes: number;
  }>,
  totalBudget: number,
): number[] {
  const targets = candidates.map(({ targetBytes }) =>
    Math.min(targetBytes, MAX_EVIDENCE_FILE_BYTES_WITH_SPARE_BUDGET)
  );
  if (targets.reduce((total, value) => total + value, 0) <= totalBudget) return targets;

  const budgets = targets.map((target) => Math.min(target, MIN_EVIDENCE_FILE_BYTES));
  let remaining = Math.max(0, totalBudget - budgets.reduce((total, value) => total + value, 0));
  const priorityRank = { high: 0, medium: 1, low: 2 } as const;
  const order = candidates
    .map(({ request }, index) => ({ index, rank: priorityRank[request.priority] }))
    .sort((left, right) => left.rank - right.rank || left.index - right.index);
  for (const { index } of order) {
    if (remaining <= 0) break;
    const needed = targets[index]! - budgets[index]!;
    const granted = Math.min(needed, remaining);
    budgets[index] = budgets[index]! + granted;
    remaining -= granted;
  }
  return budgets;
}

async function listTrackedFiles(localPath: string): Promise<string[]> {
  try {
    const output = await simpleGit(localPath).raw(["ls-files", "-z"]);
    return output.split("\0").filter(Boolean);
  } catch {
    return [];
  }
}

function selectReadme(files: string[]): string | undefined {
  return files
    .filter((file) => !file.includes("/") && /^readme(?:\..+)?$/i.test(file))
    .sort((a, b) => a.length - b.length || a.localeCompare(b))[0];
}

function selectManifests(files: string[]): string[] {
  return files
    .filter((file) => {
      const parts = file.split("/");
      const parentDirectories = parts.slice(0, -1).map((part) => part.toLowerCase());
      return parts.length <= 4
        && !parentDirectories.some((part) => NON_PROJECT_MANIFEST_DIRECTORIES.has(part))
        && MANIFEST_NAMES.has(parts.at(-1)!.toLowerCase());
    })
    .sort((a, b) => {
      const depthDifference = a.split("/").length - b.split("/").length;
      return depthDifference || a.localeCompare(b);
    })
    .slice(0, MAX_MANIFEST_FILES);
}

function selectExampleManifests(files: string[]): string[] {
  return files
    .filter((file) => {
      const parts = file.split("/");
      const parentDirectories = parts.slice(0, -1).map((part) => part.toLowerCase());
      return parts.length <= 6
        && parentDirectories.some((part) => NON_PROJECT_MANIFEST_DIRECTORIES.has(part))
        && MANIFEST_NAMES.has(parts.at(-1)!.toLowerCase());
    })
    .sort((a, b) => a.split("/").length - b.split("/").length || a.localeCompare(b))
    .slice(0, MAX_EXAMPLE_MANIFEST_FILES);
}

function selectGuidanceFiles(files: string[]): string[] {
  return files
    .filter((file) => {
      const parts = file.split("/");
      if (parts.length > 3) return false;
      const basename = parts.at(-1)!;
      return /^(contributing|developing|code_of_conduct|security)(?:\..+)?$/i.test(basename);
    })
    .sort((a, b) => a.split("/").length - b.split("/").length || a.localeCompare(b))
    .slice(0, MAX_GUIDANCE_FILES);
}

function selectConfigFiles(files: string[]): string[] {
  return files
    .filter((file) => file.split("/").length <= 4)
    .filter((file) => CONFIG_FILE_PATTERNS.some((pattern) => pattern.test(file)))
    .sort((a, b) => a.split("/").length - b.split("/").length || a.localeCompare(b))
    .slice(0, MAX_CONFIG_FILES);
}

async function readRepositoryFile(
  localPath: string,
  relativePath: string,
  maxBytes: number,
): Promise<RepositoryFileExcerpt | null> {
  const result = await prepareEvidenceContent(localPath, relativePath, maxBytes);
  if (!result.file) return null;
  return {
    path: result.file.path,
    content: result.file.content,
    truncated: result.file.truncated,
  };
}

function buildTopLevelTree(files: string[]): { entries: string[]; truncated: boolean } {
  const entries = new Set<string>();
  for (const file of files) {
    const parts = file.split("/");
    if (parts.length === 1) {
      entries.add(file);
      continue;
    }

    entries.add(`${parts[0]}/`);
    entries.add(parts.length === 2 ? file : `${parts[0]}/${parts[1]}/`);
  }

  const sorted = [...entries].sort((a, b) => a.localeCompare(b));
  return {
    entries: sorted.slice(0, MAX_TREE_ENTRIES),
    truncated: sorted.length > MAX_TREE_ENTRIES,
  };
}

function buildFileIndex(
  files: string[],
  priorityPaths: string[],
): { entries: string[]; truncated: boolean } {
  const fullIndexBytes = files.reduce(
    (total, file) => total + Buffer.byteLength(file, "utf8") + 1,
    0,
  );
  if (files.length <= MAX_FILE_INDEX_ENTRIES && fullIndexBytes <= MAX_FILE_INDEX_BYTES) {
    return { entries: files, truncated: false };
  }

  const entries: string[] = [];
  const selected = new Set<string>();
  let totalBytes = 0;

  const tryAdd = (file: string): boolean => {
    if (selected.has(file) || entries.length >= MAX_FILE_INDEX_ENTRIES) return false;
    const bytes = Buffer.byteLength(file, "utf8") + 1;
    if (totalBytes + bytes > MAX_FILE_INDEX_BYTES) return false;
    entries.push(file);
    selected.add(file);
    totalBytes += bytes;
    return true;
  };

  for (const file of priorityPaths) tryAdd(file);

  const groups = new Map<string, string[]>();
  for (const file of files) {
    if (selected.has(file)) continue;
    const parts = file.split("/");
    const group = parts.length > 2 ? parts.slice(0, 2).join("/") : (parts[0] ?? ".");
    const groupFiles = groups.get(group) ?? [];
    groupFiles.push(file);
    groups.set(group, groupFiles);
  }

  const queues = [...groups.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([, groupFiles]) => groupFiles);
  let offset = 0;
  let addedInRound = true;
  while (addedInRound && entries.length < MAX_FILE_INDEX_ENTRIES) {
    addedInRound = false;
    for (const queue of queues) {
      const file = queue[offset];
      if (file && tryAdd(file)) addedInRound = true;
    }
    offset += 1;
  }

  return { entries, truncated: entries.length < files.length };
}

function buildDirectoryStats(files: string[]): RepositoryDirectoryStat[] {
  const stats = new Map<string, RepositoryDirectoryStat>();
  for (const file of files) {
    const parts = file.split("/");
    const directories = parts.length === 1
      ? ["."]
      : Array.from(
          { length: Math.min(parts.length - 1, 3) },
          (_, index) => parts.slice(0, index + 1).join("/"),
        );
    const isSource = Boolean(LANGUAGE_BY_EXTENSION[path.posix.extname(file).toLowerCase()]);
    const isTest = isTestFile(file);

    for (const directory of directories) {
      const current = stats.get(directory) ?? {
        path: directory,
        files: 0,
        sourceFiles: 0,
        testFiles: 0,
      };
      current.files += 1;
      if (isSource) current.sourceFiles += 1;
      if (isTest) current.testFiles += 1;
      stats.set(directory, current);
    }
  }

  return [...stats.values()]
    .sort((left, right) => right.files - left.files || left.path.localeCompare(right.path))
    .slice(0, MAX_DIRECTORY_STATS);
}

function buildLanguageStats(files: string[]): Record<string, number> {
  const counts = new Map<string, number>();
  for (const file of files) {
    const extension = path.posix.extname(file).toLowerCase();
    const language = LANGUAGE_BY_EXTENSION[extension];
    if (!language) continue;
    counts.set(language, (counts.get(language) ?? 0) + 1);
  }

  return Object.fromEntries(
    [...counts.entries()]
      .sort(([, left], [, right]) => right - left)
      .slice(0, 10),
  );
}

async function scanTodoMarkers(localPath: string, files: string[]): Promise<TodoMarker[]> {
  const candidates = files
    .filter((file) => LANGUAGE_BY_EXTENSION[path.posix.extname(file).toLowerCase()])
    .filter((file) => !/(^|\/)(vendor|node_modules|dist|build|coverage)(\/|$)/i.test(file))
    .slice(0, MAX_TODO_SCAN_FILES);
  const markers: TodoMarker[] = [];
  let remainingBytes = MAX_TODO_SCAN_BYTES;

  for (const file of candidates) {
    if (remainingBytes <= 0 || markers.length >= MAX_TODO_MARKERS) break;
    const maxBytes = Math.min(MAX_TODO_FILE_BYTES, remainingBytes);
    const source = await readRepositoryFile(localPath, file, maxBytes);
    if (!source) continue;
    remainingBytes -= Buffer.byteLength(source.content, "utf8");

    const lines = source.content.split(/\r?\n/);
    for (let index = 0; index < lines.length; index++) {
      const line = lines[index]!;
      const match = line.match(/\b(TODO|FIXME|HACK|XXX)\b/i);
      if (!match) continue;
      markers.push({
        file,
        line: index + 1,
        marker: match[1]!.toUpperCase() as TodoMarker["marker"],
        excerpt: line.trim().slice(0, 200),
      });
      if (markers.length >= MAX_TODO_MARKERS) break;
    }
  }

  return markers;
}

function selectEntryCandidates(files: string[]): string[] {
  return files
    .filter((file) => {
      const parts = file.split("/");
      if (parts.length > 6) return false;
      const extension = path.posix.extname(file).toLowerCase();
      if (!ENTRY_EXTENSIONS.has(extension)) return false;
      const basename = path.posix.basename(file, extension).toLowerCase();
      return ENTRY_BASENAMES.has(basename);
    })
    .sort((a, b) => {
      const depthDifference = a.split("/").length - b.split("/").length;
      return depthDifference || a.localeCompare(b);
    })
    .slice(0, MAX_ENTRY_CANDIDATES);
}

function selectTestCandidates(files: string[]): string[] {
  return files
    .filter(isTestFile)
    .filter((file) => ENTRY_EXTENSIONS.has(path.posix.extname(file).toLowerCase()))
    .sort((a, b) => {
      const depthDifference = a.split("/").length - b.split("/").length;
      return depthDifference || a.localeCompare(b);
    })
    .slice(0, MAX_TEST_CANDIDATES);
}

function isTestFile(file: string): boolean {
  return /(^|\/)(?:test|tests|spec|specs|__tests__)(\/|$)/i.test(file)
    || /\.(?:test|spec)\.[^/]+$/i.test(file);
}
