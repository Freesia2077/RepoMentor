import fs from "node:fs/promises";
import path from "node:path";
import { StringDecoder } from "node:string_decoder";

export type EvidenceContentFailure = "unreadable" | "oversized";

export interface PreparedEvidenceContent {
  path: string;
  content: string;
  /** 有效文本的总字节数；可能大于 content，因为 content 受 maxBytes 约束。 */
  sourceBytes: number;
  truncated: boolean;
}

export type EvidenceContentResult =
  | { file: PreparedEvidenceContent; reason: null }
  | { file: null; reason: EvidenceContentFailure };

const MAX_NOTEBOOK_FILE_BYTES = 20_000_000;

/**
 * 所有仓库内容都从这一入口读取。Harness 和预算层只处理统一文本；需要
 * 内容归一化的容器格式在这里提供小型、隔离的适配器。
 */
export async function prepareEvidenceContent(
  localPath: string,
  relativePath: string,
  maxBytes: number,
): Promise<EvidenceContentResult> {
  if (maxBytes <= 0) return { file: null, reason: "unreadable" };
  const normalizedPath = relativePath.replaceAll("\\", "/").replace(/^\.\/+/, "");
  const root = path.resolve(localPath);
  const absolutePath = path.resolve(root, ...normalizedPath.split("/"));
  if (absolutePath !== root && !absolutePath.startsWith(`${root}${path.sep}`)) {
    return { file: null, reason: "unreadable" };
  }

  try {
    const stat = await fs.lstat(absolutePath);
    if (!stat.isFile() || stat.isSymbolicLink()) {
      return { file: null, reason: "unreadable" };
    }
    if (path.extname(normalizedPath).toLowerCase() === ".ipynb") {
      if (stat.size > MAX_NOTEBOOK_FILE_BYTES) {
        return { file: null, reason: "oversized" };
      }
      return {
        file: await extractNotebookContent(absolutePath, normalizedPath, maxBytes),
        reason: null,
      };
    }

    const handle = await fs.open(absolutePath, "r");
    try {
      const bytesToRead = Math.min(maxBytes, stat.size);
      const buffer = Buffer.alloc(bytesToRead);
      const { bytesRead } = await handle.read(buffer, 0, bytesToRead, 0);
      const contentBuffer = buffer.subarray(0, bytesRead);
      if (contentBuffer.includes(0)) return { file: null, reason: "unreadable" };
      const content = decodeBoundedUtf8(contentBuffer, maxBytes);
      return {
        file: {
          path: normalizedPath,
          content,
          sourceBytes: stat.size,
          truncated: stat.size > Buffer.byteLength(content, "utf8"),
        },
        reason: null,
      };
    } finally {
      await handle.close();
    }
  } catch {
    return { file: null, reason: "unreadable" };
  }
}

async function extractNotebookContent(
  absolutePath: string,
  relativePath: string,
  maxBytes: number,
): Promise<PreparedEvidenceContent> {
  const parsed = JSON.parse(await fs.readFile(absolutePath, "utf8")) as {
    cells?: unknown;
  };
  if (!Array.isArray(parsed.cells)) throw new Error("Notebook 缺少 cells 数组");

  let content = "";
  let retainedBytes = 0;
  let sourceBytes = 0;
  const appendBounded = (value: string) => {
    if (retainedBytes >= maxBytes) return;
    const bounded = decodeBoundedUtf8(Buffer.from(value, "utf8"), maxBytes - retainedBytes);
    content += bounded;
    retainedBytes += Buffer.byteLength(bounded, "utf8");
  };

  parsed.cells.forEach((value, index) => {
    if (!value || typeof value !== "object") return;
    const cell = value as { cell_type?: unknown; source?: unknown };
    if (cell.cell_type !== "code" && cell.cell_type !== "markdown") return;
    const source = typeof cell.source === "string"
      ? cell.source
      : Array.isArray(cell.source)
        ? cell.source.filter((item): item is string => typeof item === "string").join("")
        : "";
    if (!source) return;

    const prefix = sourceBytes > 0 ? "\n\n" : "";
    const normalized = `${prefix}## Cell ${index + 1} (${cell.cell_type})\n${source}`;
    sourceBytes += Buffer.byteLength(normalized, "utf8");
    appendBounded(normalized);
  });

  return {
    path: relativePath,
    content,
    sourceBytes,
    truncated: sourceBytes > retainedBytes,
  };
}

export function decodeBoundedUtf8(buffer: Buffer, maxBytes: number): string {
  if (maxBytes <= 0 || buffer.length === 0) return "";
  const decoder = new StringDecoder("utf8");
  return decoder.write(buffer.subarray(0, Math.min(maxBytes, buffer.length)));
}
