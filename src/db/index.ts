import Database from "better-sqlite3";
import { config } from "../config.js";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

let db: Database.Database | undefined;

export function getDb(): Database.Database {
  if (!db) {
    const dir = path.dirname(config.SQLITE_PATH);
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }

    db = new Database(config.SQLITE_PATH);
    db.pragma("journal_mode = WAL");
    db.pragma("foreign_keys = ON");
    runMigrations(db);
  }
  return db;
}

function runMigrations(database: Database.Database): void {
  const migrationsDir = path.join(__dirname, "migrations");
  if (!fs.existsSync(migrationsDir)) return;

  const files = fs.readdirSync(migrationsDir)
    .filter(f => f.endsWith(".sql"))
    .sort();

  for (const file of files) {
    const sql = fs.readFileSync(path.join(migrationsDir, file), "utf-8");
    database.exec(sql);
  }
}

export function createMemoryDb(): Database.Database {
  const memDb = new Database(":memory:");
  memDb.pragma("journal_mode = WAL");
  const migrationsDir = path.join(__dirname, "migrations");
  if (fs.existsSync(migrationsDir)) {
    const files = fs.readdirSync(migrationsDir)
      .filter(f => f.endsWith(".sql"))
      .sort();
    for (const file of files) {
      const sql = fs.readFileSync(path.join(migrationsDir, file), "utf-8");
      memDb.exec(sql);
    }
  }
  return memDb;
}

export function closeDb(): void {
  if (db) {
    db.close();
    db = undefined;
  }
}
