"""Independent SQLite run ledger plus append-only JSONL events; no web objects."""

import json
import re
import sqlite3
import threading
from pathlib import Path

from .contracts import RunEvent


class RunStore:
    def __init__(self, root: Path, run_id: str):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", run_id):
            raise ValueError("invalid run ID")
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.events_path = self.root / "runs" / run_id / "events.jsonl"
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.sequence = 0
        self.db = self.root / "repomentor-python.sqlite3"
        with sqlite3.connect(self.db) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, result_json TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS events (run_id TEXT, sequence INTEGER, event_json TEXT, PRIMARY KEY(run_id, sequence))"
            )
            self.sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM events WHERE run_id=?", (run_id,)
            ).fetchone()[0]

    def emit(self, stage, kind, data):
        with self._lock:
            self.sequence += 1
            event = RunEvent(run_id=self.run_id, sequence=self.sequence, stage=stage, kind=kind, data=data)
            raw = event.model_dump_json()
            with sqlite3.connect(self.db) as connection:
                connection.execute("INSERT INTO events VALUES (?, ?, ?)", (self.run_id, self.sequence, raw))
            with self.events_path.open("a", encoding="utf-8") as handle:
                handle.write(raw + "\n")
            return event

    def save(self, result):
        raw = result.model_dump_json(indent=2)
        with sqlite3.connect(self.db) as connection:
            connection.execute("INSERT OR REPLACE INTO runs VALUES (?, ?)", (self.run_id, raw))
        path = self.events_path.parent / "result.json"
        path.write_text(raw + "\n", encoding="utf-8")
        return path

    def load(self, run_id):
        return self.load_saved(self.root, run_id)

    @staticmethod
    def load_saved(root: Path, run_id: str):
        """Read an existing ledger without creating a database or a phantom run."""
        db = Path(root) / "repomentor-python.sqlite3"
        if not db.is_file():
            return None
        with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as connection:
            row = connection.execute("SELECT result_json FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return json.loads(row[0]) if row else None
