import { cp, mkdir } from "node:fs/promises";
import path from "node:path";

const projectRoot = process.cwd();
const sourceMigrations = path.join(projectRoot, "src", "db", "migrations");
const targetMigrations = path.join(projectRoot, "dist", "db", "migrations");

await mkdir(path.dirname(targetMigrations), { recursive: true });
await cp(sourceMigrations, targetMigrations, { recursive: true, force: true });
