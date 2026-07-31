import fs from "node:fs/promises";
import path from "node:path";
import { simpleGit } from "simple-git";
import type { RepositorySnapshot, SnapshotFile, TodoMarker } from "../types/index.js";

const MAX_TREE_ENTRIES = 200;
const MAX_MANIFEST_FILES = 8;
const MAX_EXAMPLE_MANIFEST_FILES = 3;
const MAX_GUIDANCE_FILES = 3;
const MAX_ENTRY_CANDIDATES = 20;
const MAX_TODO_MARKERS = 20;
const MAX_TODO_SCAN_FILES = 500;
const MAX_TODO_SCAN_BYTES = 2_000_000;
const MAX_TODO_FILE_BYTES = 50_000;
const MAX_README_BYTES = 10_000;
const MAX_MANIFEST_BYTES = 5_000;
const MAX_GUIDANCE_BYTES = 6_000;
const MAX_TOTAL_CONTENT_BYTES = 32_000;

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

export async function buildRepositorySnapshot(
  localPath: string,
  providedTrackedFiles?: string[],
): Promise<RepositorySnapshot> {
  const trackedFiles = providedTrackedFiles ?? await listTrackedFiles(localPath);
  const normalizedFiles = trackedFiles
    .map((file) => file.replaceAll("\\", "/"))
    .filter(Boolean)
    .sort((a, b) => a.localeCompare(b));

  const readmePath = selectReadme(normalizedFiles);
  const manifestPaths = selectManifests(normalizedFiles);
  const exampleManifestPaths = selectExampleManifests(normalizedFiles);
  const guidancePaths = selectGuidanceFiles(normalizedFiles);
  let remainingContentBytes = MAX_TOTAL_CONTENT_BYTES;

  const readme = readmePath
    ? await readSnapshotFile(
        localPath,
        readmePath,
        Math.min(MAX_README_BYTES, remainingContentBytes),
      )
    : null;
  if (readme) remainingContentBytes -= Buffer.byteLength(readme.content, "utf8");

  const manifests: SnapshotFile[] = [];
  for (const manifestPath of manifestPaths) {
    if (remainingContentBytes <= 0) break;
    const manifest = await readSnapshotFile(
      localPath,
      manifestPath,
      Math.min(MAX_MANIFEST_BYTES, remainingContentBytes),
    );
    if (!manifest) continue;
    manifests.push(manifest);
    remainingContentBytes -= Buffer.byteLength(manifest.content, "utf8");
  }

  const guidanceFiles: SnapshotFile[] = [];
  for (const guidancePath of guidancePaths) {
    if (remainingContentBytes <= 0) break;
    const guidance = await readSnapshotFile(
      localPath,
      guidancePath,
      Math.min(MAX_GUIDANCE_BYTES, remainingContentBytes),
    );
    if (!guidance) continue;
    guidanceFiles.push(guidance);
    remainingContentBytes -= Buffer.byteLength(guidance.content, "utf8");
  }

  const exampleManifests: SnapshotFile[] = [];
  for (const manifestPath of exampleManifestPaths) {
    if (remainingContentBytes <= 0) break;
    const manifest = await readSnapshotFile(
      localPath,
      manifestPath,
      Math.min(MAX_MANIFEST_BYTES, remainingContentBytes),
    );
    if (!manifest) continue;
    exampleManifests.push(manifest);
    remainingContentBytes -= Buffer.byteLength(manifest.content, "utf8");
  }

  const { entries: topLevelTree, truncated: treeTruncated } = buildTopLevelTree(normalizedFiles);

  return {
    fileCount: normalizedFiles.length,
    topLevelTree,
    treeTruncated,
    readme,
    manifests,
    exampleManifests,
    guidanceFiles,
    todoMarkers: await scanTodoMarkers(localPath, normalizedFiles),
    languageStats: buildLanguageStats(normalizedFiles),
    entryCandidates: selectEntryCandidates(normalizedFiles),
  };
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

async function readSnapshotFile(
  localPath: string,
  relativePath: string,
  maxBytes: number,
): Promise<SnapshotFile | null> {
  if (maxBytes <= 0) return null;
  const root = path.resolve(localPath);
  const absolutePath = path.resolve(root, ...relativePath.split("/"));
  if (absolutePath !== root && !absolutePath.startsWith(`${root}${path.sep}`)) return null;

  let handle: Awaited<ReturnType<typeof fs.open>> | undefined;
  try {
    const stat = await fs.lstat(absolutePath);
    if (!stat.isFile() || stat.isSymbolicLink()) return null;

    handle = await fs.open(absolutePath, "r");
    const bytesToRead = Math.min(maxBytes, stat.size);
    const buffer = Buffer.alloc(bytesToRead);
    const { bytesRead } = await handle.read(buffer, 0, bytesToRead, 0);
    const contentBuffer = buffer.subarray(0, bytesRead);
    if (contentBuffer.includes(0)) return null;

    return {
      path: relativePath,
      content: contentBuffer.toString("utf8"),
      truncated: stat.size > bytesRead,
    };
  } catch {
    return null;
  } finally {
    await handle?.close();
  }
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
    const source = await readSnapshotFile(localPath, file, maxBytes);
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
