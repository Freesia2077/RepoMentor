import fs from "node:fs/promises";
import path from "node:path";

import type { RepositoryProfile } from "../types/index.js";

const SOURCE_EXTENSIONS = new Set([
  ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
  ".py", ".go", ".rs", ".java", ".kt", ".kts",
  ".rb", ".php", ".cs", ".cpp", ".cc", ".c", ".h",
  ".swift", ".dart", ".ex", ".exs", ".vue", ".svelte",
]);
const MAX_TOOL_FILE_BYTES = 50_000;
const MAX_SYMBOL_SCAN_FILES = 300;
const MAX_SYMBOL_SCAN_BYTES = 1_500_000;
const MAX_SYMBOL_RESULTS = 20;
const MAX_DEPENDENCY_SEEDS = 6;
const MAX_DEPENDENCY_RESULTS = 24;
const MAX_RELATED_TESTS = 12;

export interface SymbolSearchResult {
  path: string;
  line: number;
  excerpt: string;
}

export interface DependencyTraceResult {
  edges: Array<{ from: string; to: string }>;
  paths: string[];
}

export async function searchRepositorySymbols(
  localPath: string,
  profile: RepositoryProfile,
  query: string,
): Promise<SymbolSearchResult[]> {
  const term = query.trim();
  if (!term || term.length > 120) return [];
  const matcher = new RegExp(escapeRegExp(term), "i");
  const candidates = profile.fileIndex
    .filter(isSourceFile)
    .slice(0, MAX_SYMBOL_SCAN_FILES);
  const results: SymbolSearchResult[] = [];
  let scannedBytes = 0;

  for (const file of candidates) {
    if (results.length >= MAX_SYMBOL_RESULTS || scannedBytes >= MAX_SYMBOL_SCAN_BYTES) break;
    const content = await readBoundedTextFile(localPath, file);
    if (content === null) continue;
    scannedBytes += Buffer.byteLength(content, "utf8");
    const lines = content.split(/\r?\n/);
    for (let index = 0; index < lines.length; index++) {
      const line = lines[index]!;
      if (!matcher.test(line)) continue;
      results.push({ path: file, line: index + 1, excerpt: line.trim().slice(0, 200) });
      if (results.length >= MAX_SYMBOL_RESULTS) break;
    }
  }
  return results;
}

export async function traceRepositoryDependencies(
  localPath: string,
  profile: RepositoryProfile,
  seedPaths: string[],
): Promise<DependencyTraceResult> {
  const trackedSet = new Set(profile.fileIndex.map(normalizePath));
  const edges: DependencyTraceResult["edges"] = [];
  const related = new Set<string>();

  for (const rawSeed of seedPaths.slice(0, MAX_DEPENDENCY_SEEDS)) {
    const seed = normalizePath(rawSeed);
    if (!trackedSet.has(seed) || !isSourceFile(seed)) continue;
    const content = await readBoundedTextFile(localPath, seed);
    if (content === null) continue;
    for (const specifier of extractDependencySpecifiers(content)) {
      const resolved = resolveDependencyPath(seed, specifier, trackedSet);
      if (!resolved) continue;
      edges.push({ from: seed, to: resolved });
      related.add(resolved);
      if (edges.length >= MAX_DEPENDENCY_RESULTS) break;
    }
    if (edges.length >= MAX_DEPENDENCY_RESULTS) break;
  }

  return { edges, paths: [...related] };
}

export function findRepositoryRelatedTests(
  profile: RepositoryProfile,
  sourcePaths: string[],
): string[] {
  const tests = profile.fileIndex.filter(isTestFile);
  const scored = tests.map((testPath) => {
    let score = 0;
    for (const sourcePath of sourcePaths) {
      const sourceBase = basenameWithoutExtension(sourcePath)
        .replace(/^(index|main|mod|lib)$/i, path.posix.basename(path.posix.dirname(sourcePath)));
      const sourceDirectoryBase = path.posix.basename(path.posix.dirname(sourcePath));
      const testBase = basenameWithoutExtension(testPath).replace(/\.(test|spec)$/i, "");
      if (sourceBase && testBase.toLowerCase().includes(sourceBase.toLowerCase())) score += 10;
      if (
        sourceDirectoryBase
        && sourceDirectoryBase !== "."
        && testBase.toLowerCase().includes(sourceDirectoryBase.toLowerCase())
      ) {
        score += 8;
      }
      const sourceParts = new Set(path.posix.dirname(sourcePath).toLowerCase().split("/"));
      score += testPath.toLowerCase().split("/").filter((part) => sourceParts.has(part)).length;
    }
    return { path: testPath, score };
  });

  return scored
    .filter((item) => item.score > 0)
    .sort((left, right) => right.score - left.score || left.path.localeCompare(right.path))
    .slice(0, MAX_RELATED_TESTS)
    .map((item) => item.path);
}

async function readBoundedTextFile(
  localPath: string,
  relativePath: string,
): Promise<string | null> {
  const root = path.resolve(localPath);
  const absolutePath = path.resolve(root, ...normalizePath(relativePath).split("/"));
  if (absolutePath !== root && !absolutePath.startsWith(`${root}${path.sep}`)) return null;

  let handle: Awaited<ReturnType<typeof fs.open>> | undefined;
  try {
    const stat = await fs.lstat(absolutePath);
    if (!stat.isFile() || stat.isSymbolicLink()) return null;
    handle = await fs.open(absolutePath, "r");
    const size = Math.min(stat.size, MAX_TOOL_FILE_BYTES);
    const buffer = Buffer.alloc(size);
    const { bytesRead } = await handle.read(buffer, 0, size, 0);
    const content = buffer.subarray(0, bytesRead);
    if (content.includes(0)) return null;
    return content.toString("utf8");
  } catch {
    return null;
  } finally {
    await handle?.close();
  }
}

function extractDependencySpecifiers(content: string): string[] {
  const specifiers = new Set<string>();
  const javascriptPattern = /(?:import|export)\s+(?:type\s+)?(?:[^"'`;\n]+?\s+from\s+)?["']([^"']+)["']|require\(\s*["']([^"']+)["']\s*\)|import\(\s*["']([^"']+)["']\s*\)/g;
  for (const match of content.matchAll(javascriptPattern)) {
    const value = match[1] ?? match[2] ?? match[3];
    if (value) specifiers.add(value);
  }
  const pythonPattern = /^\s*from\s+([A-Za-z0-9_.]+)\s+import\s+|^\s*import\s+([A-Za-z0-9_.]+)/gm;
  for (const match of content.matchAll(pythonPattern)) {
    const value = match[1] ?? match[2];
    if (value) specifiers.add(value);
  }
  return [...specifiers];
}

function resolveDependencyPath(
  importer: string,
  specifier: string,
  trackedSet: Set<string>,
): string | undefined {
  const candidates: string[] = [];
  if (specifier.startsWith(".")) {
    const relative = path.posix.normalize(path.posix.join(path.posix.dirname(importer), specifier));
    candidates.push(relative);
    if (/\.(?:js|mjs|cjs)$/i.test(relative)) {
      candidates.push(relative.replace(/\.(?:js|mjs|cjs)$/i, ""));
    }
  } else if (specifier.includes(".")) {
    candidates.push(specifier.replaceAll(".", "/"));
  } else {
    return undefined;
  }

  const extensions = ["", ...SOURCE_EXTENSIONS];
  for (const base of candidates) {
    for (const extension of extensions) {
      const direct = `${base}${extension}`;
      if (trackedSet.has(direct)) return direct;
      const indexPath = `${base}/index${extension}`;
      if (trackedSet.has(indexPath)) return indexPath;
      const initPath = `${base}/__init__${extension}`;
      if (trackedSet.has(initPath)) return initPath;
    }
  }
  return undefined;
}

function isSourceFile(file: string): boolean {
  return SOURCE_EXTENSIONS.has(path.posix.extname(file).toLowerCase())
    && !/(^|\/)(dist|build|coverage|vendor|node_modules)(\/|$)/i.test(file);
}

function isTestFile(file: string): boolean {
  return /(^|\/)(test|tests|spec|specs|__tests__)(\/|$)/i.test(file)
    || /\.(test|spec)\.[^/]+$/i.test(file);
}

function basenameWithoutExtension(file: string): string {
  return path.posix.basename(file, path.posix.extname(file));
}

function normalizePath(value: string): string {
  return value.replaceAll("\\", "/").replace(/^\.\/+/, "");
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
