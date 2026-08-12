from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import Any

from app.models import utcnow_iso

TERMINAL_STATUSES = {"succeeded", "failed", "canceled"}


class TaskRepository:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with closing(self._connect()) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    platform TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    transcript_source TEXT,
                    result_path TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                )
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
            if "platform" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN platform TEXT NOT NULL DEFAULT ''")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
            conn.commit()

    def create(self, task_id: str, url: str):
        with self._lock, closing(self._connect()) as conn:
            conn.execute(
                "INSERT INTO tasks(id,url,status,stage,progress,created_at) VALUES(?,?,?,?,?,?)",
                (task_id, url, "queued", "queued", 0, utcnow_iso()),
            )
            conn.commit()

    def update(self, task_id: str, **fields):
        if not fields:
            return
        allowed = {"title", "platform", "status", "stage", "progress", "transcript_source", "result_path", "error", "started_at", "finished_at"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        if not fields:
            return
        sql = "UPDATE tasks SET " + ", ".join(f"{k}=?" for k in fields) + " WHERE id=?"
        with self._lock, closing(self._connect()) as conn:
            conn.execute(sql, [*fields.values(), task_id])
            conn.commit()

    def get(self, task_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            return dict(row) if row else None

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
            return [dict(x) for x in rows]

    def list_finished_before(self, cutoff_iso: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status IN ('succeeded','failed','canceled') AND finished_at IS NOT NULL AND finished_at < ?",
                (cutoff_iso,),
            ).fetchall()
            return [dict(x) for x in rows]

    def recover_incomplete(self):
        with self._lock, closing(self._connect()) as conn:
            conn.execute(
                "UPDATE tasks SET status='failed', stage='failed', error=COALESCE(error, 'service restarted before task completed'), finished_at=? WHERE status IN ('queued','running')",
                (utcnow_iso(),),
            )
            conn.commit()

    def delete(self, task_id: str):
        with self._lock, closing(self._connect()) as conn:
            conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
            conn.commit()
