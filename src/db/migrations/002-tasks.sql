CREATE TABLE IF NOT EXISTS analysis_tasks (
  task_id TEXT PRIMARY KEY,
  repo_url TEXT NOT NULL,
  branch TEXT NOT NULL,
  status TEXT NOT NULL,
  current_stage TEXT,
  stage_progress TEXT NOT NULL,
  result TEXT,
  error TEXT,
  commit_hash TEXT,
  cached INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_created_at
  ON analysis_tasks(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_tasks_status
  ON analysis_tasks(status);
