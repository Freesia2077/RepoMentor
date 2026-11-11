"""Transactional request reservations shared by all conditions of a local experiment."""

# 用 SQLite 记录请求预留和发送身份，跨进程中断后仍能核对实际调用成本。
# 发送结果不明的占位记录会保留，后续恢复需显式处理这类歧义。

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from .execution import HarnessError
from .records import write
from .runtimes.chat import fingerprint


class RequestLedger:
    def __init__(
        self, path, *, limit, experiment, context_windows=(8192,), batch_prefix=None, batch_limit=None
    ):
        self.path, self.limit, self.experiment = Path(path), limit, experiment
        self.context_windows = tuple(context_windows)
        self.batch_prefix, self.batch_limit = batch_prefix, batch_limit
        if not self.context_windows or any(not isinstance(w, int) or w < 512 for w in self.context_windows):
            raise ValueError("explicit context windows must be integer token budgets of at least 512")
        if (batch_prefix is None) != (batch_limit is None) or (batch_limit is not None and batch_limit < 1):
            raise ValueError("batch prefix and positive cap must be provided together")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS metadata (identity TEXT PRIMARY KEY, cap INTEGER)")
            db.execute("CREATE TABLE IF NOT EXISTS dispatches (ordinal INTEGER PRIMARY KEY, payload TEXT)")
            rows = db.execute("SELECT identity, cap FROM metadata").fetchall()
            if rows and rows != [(experiment, limit)]:
                raise ValueError("ledger identity/cap cannot change")
            db.execute("INSERT OR IGNORE INTO metadata VALUES (?, ?)", (experiment, limit))

    # 在真正发送前持久化占位；崩溃后的不确定请求仍占预算，避免静默重复发送。
    def reserve(self, settings, case, body, budget):
        if settings.backend != "ollama" or urlparse(settings.baseUrl).hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise HarnessError("local_only", "experiment forbids remote generation")
        if (
            not budget["fits"]
            or budget["context_window"] not in self.context_windows
            or budget.get("total", 0) > budget["context_window"]
            or getattr(settings, "context_window", budget["context_window"]) != budget["context_window"]
        ):
            raise HarnessError(
                "context_limit", "experiment requires an explicitly authorized input/output window"
            )
        if self.batch_prefix and not case.startswith(self.batch_prefix + "/"):
            raise HarnessError("suite_budget", "case is outside the reserved batch prefix")
        with sqlite3.connect(self.path, timeout=30) as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute("SELECT COUNT(*) FROM dispatches").fetchone()[0]
            if count >= self.limit:
                raise HarnessError("suite_budget", "global generation cap reached; no automatic retry")
            if self.batch_prefix:
                batch_count = sum(
                    json.loads(r[0])["case"].startswith(self.batch_prefix + "/")
                    for r in db.execute("SELECT payload FROM dispatches")
                )
                if batch_count >= self.batch_limit:
                    raise HarnessError("suite_budget", "diagnostic batch cap reached; no automatic retry")
            row = {
                "ordinal": count + 1,
                "case": case,
                "config_id": settings.config_id,
                "request_hash": fingerprint(body),
                "budget": budget,
                "reserved_at": datetime.now(timezone.utc).isoformat(),
                "status": "reserved_before_dispatch",
                "paid": False,
            }
            db.execute("INSERT INTO dispatches VALUES (?, ?)", (count + 1, json.dumps(row)))
        return row

    def rows(self):
        with sqlite3.connect(self.path) as db:
            return [json.loads(r[0]) for r in db.execute("SELECT payload FROM dispatches ORDER BY ordinal")]

    def export(self, path):
        rows = self.rows()
        write(
            path,
            {
                "experiment": self.experiment,
                "cap": self.limit,
                "reservations": rows,
                "reserved_requests": len(rows),
                "paid_requests": 0,
                "note": "Reservations include ambiguous interrupted dispatches, never reused or refunded.",
            },
        )
