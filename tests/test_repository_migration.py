import sqlite3
from pathlib import Path

from app.task.repository import TaskRepository


def test_old_database_is_migrated_with_platform_column(tmp_path: Path):
    path = tmp_path / "tasks.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
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
        conn.commit()
    repo = TaskRepository(str(path))
    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
    assert "platform" in columns
    repo.create("x", "https://example.com/video")
    repo.update("x", platform="youtube")
    assert repo.get("x")["platform"] == "youtube"
