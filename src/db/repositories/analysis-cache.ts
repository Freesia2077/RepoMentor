import type Database from "better-sqlite3";
import type { AnalysisCacheRecord, AnalysisResult } from "../../types/index.js";

export function findByCommit(
  db: Database.Database,
  owner: string,
  repo: string,
  branch: string,
  commitHash: string,
): AnalysisCacheRecord | undefined {
  return db.prepare(`
    SELECT * FROM analysis_cache
    WHERE owner = ? AND repo = ? AND branch = ? AND commit_hash = ?
  `).get(owner, repo, branch, commitHash) as AnalysisCacheRecord | undefined;
}

export function save(
  db: Database.Database,
  params: {
    owner: string;
    repo: string;
    branch: string;
    commitHash: string;
    result: AnalysisResult;
    projectTypePrimary: string;
    framework: string | null;
  },
): void {
  db.prepare(`
    INSERT OR REPLACE INTO analysis_cache
      (owner, repo, branch, commit_hash, result, project_type_primary, framework)
    VALUES (?, ?, ?, ?, ?, ?, ?)
  `).run(
    params.owner,
    params.repo,
    params.branch,
    params.commitHash,
    JSON.stringify(params.result),
    params.projectTypePrimary,
    params.framework,
  );
}
