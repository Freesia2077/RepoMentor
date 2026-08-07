import fs from "node:fs";
import path from "node:path";

const projectRoot = process.cwd();
const distDirectory = path.resolve(projectRoot, "dist");

if (
  path.basename(distDirectory) !== "dist"
  || path.dirname(distDirectory) !== path.resolve(projectRoot)
) {
  throw new Error(`Refusing to clean unexpected backend output path: ${distDirectory}`);
}

fs.rmSync(distDirectory, { recursive: true, force: true });
