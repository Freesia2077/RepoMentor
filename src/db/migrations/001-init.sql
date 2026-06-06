CREATE TABLE IF NOT EXISTS analysis_cache (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner TEXT NOT NULL,
  repo TEXT NOT NULL,
  branch TEXT NOT NULL DEFAULT 'main',
  commit_hash TEXT NOT NULL,
  result TEXT NOT NULL,
  project_type_primary TEXT NOT NULL,
  framework TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(owner, repo, branch, commit_hash)
);

CREATE INDEX IF NOT EXISTS idx_cache_lookup
  ON analysis_cache(owner, repo, branch, commit_hash);

CREATE TABLE IF NOT EXISTS experiences (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner TEXT NOT NULL,
  repo TEXT NOT NULL,
  primary_type TEXT NOT NULL,
  framework TEXT,
  secondary_type TEXT NOT NULL DEFAULT '[]',
  content TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_experiences_match
  ON experiences(primary_type, framework);

CREATE INDEX IF NOT EXISTS idx_experiences_owner
  ON experiences(owner);
