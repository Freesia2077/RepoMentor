import type Database from "better-sqlite3";
import type { TaskRecord, TaskStatus, StageName, StageProgress, AnalysisResult, TaskError } from "../../types/index.js";

interface TaskRow {
  task_id: string;
  repo_url: string;
  branch: string;
  status: TaskStatus;
  current_stage: StageName | null;
  stage_progress: string;
  result: string | null;
  error: string | null;
  commit_hash: string | null;
  cached: number;
  created_at: string;
  completed_at: string | null;
}

export function save(db: Database.Database, task: TaskRecord): void {
  db.prepare(`
    INSERT INTO analysis_tasks (
      task_id, repo_url, branch, status, current_stage, stage_progress,
      result, error, commit_hash, cached, created_at, completed_at
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(task_id) DO UPDATE SET
      status = excluded.status,
      current_stage = excluded.current_stage,
      stage_progress = excluded.stage_progress,
      result = excluded.result,
      error = excluded.error,
      commit_hash = excluded.commit_hash,
      cached = excluded.cached,
      completed_at = excluded.completed_at
  `).run(
    task.taskId,
    task.repoUrl,
    task.branch,
    task.status,
    task.currentStage,
    JSON.stringify(task.stageProgress),
    task.result ? JSON.stringify(task.result) : null,
    task.error ? JSON.stringify(task.error) : null,
    task.commitHash,
    task.cached ? 1 : 0,
    task.createdAt,
    task.completedAt,
  );
}

export function findById(db: Database.Database, taskId: string): TaskRecord | undefined {
  const row = db.prepare(`
    SELECT * FROM analysis_tasks WHERE task_id = ?
  `).get(taskId) as TaskRow | undefined;

  return row ? fromRow(row) : undefined;
}

export function markInterruptedAsFailed(db: Database.Database): number {
  const error: TaskError = {
    category: "internal",
    message: "服务在任务执行期间重启，请重新发起分析",
    retryable: true,
  };

  const result = db.prepare(`
    UPDATE analysis_tasks
    SET status = 'failed',
        error = ?,
        completed_at = COALESCE(completed_at, datetime('now'))
    WHERE status IN ('cloning', 'analyzing')
  `).run(JSON.stringify(error));

  return result.changes;
}

function fromRow(row: TaskRow): TaskRecord {
  return {
    taskId: row.task_id,
    repoUrl: row.repo_url,
    branch: row.branch,
    status: row.status,
    currentStage: row.current_stage,
    stageProgress: JSON.parse(row.stage_progress) as StageProgress,
    result: row.result ? JSON.parse(row.result) as AnalysisResult : null,
    error: row.error ? JSON.parse(row.error) as TaskError : null,
    commitHash: row.commit_hash,
    cached: row.cached === 1,
    createdAt: row.created_at,
    completedAt: row.completed_at,
  };
}
