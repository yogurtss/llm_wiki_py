from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from app.models import WikiProject


class SQLiteStore:
    def __init__(self, database: Path):
        self.database = database
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    path TEXT NOT NULL UNIQUE,
                    created_at REAL NOT NULL,
                    last_opened_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS review_items (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    resolved INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    label TEXT NOT NULL,
                    payload TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS search_index_meta (
                    project_id TEXT PRIMARY KEY,
                    indexed_at REAL NOT NULL,
                    document_count INTEGER NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );
                """
            )

    def upsert_project(self, project: WikiProject) -> None:
        now = time.time()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO projects (id, name, path, created_at, last_opened_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    path = excluded.path,
                    last_opened_at = excluded.last_opened_at
                """,
                (project.id, project.name, str(project.path), now, now),
            )

    def list_recent_projects(self, limit: int = 10) -> list[WikiProject]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, name, path FROM projects ORDER BY last_opened_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            WikiProject(id=row["id"], name=row["name"], path=Path(row["path"]))
            for row in rows
        ]

    def get_project(self, project_id: str) -> WikiProject | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id, name, path FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
        if row is None:
            return None
        return WikiProject(id=row["id"], name=row["name"], path=Path(row["path"]))

    def set_setting(self, key: str, value: Any) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, json.dumps(value), time.time()),
            )

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        return json.loads(row["value"])

    def update_search_meta(self, project_id: str, document_count: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO search_index_meta (project_id, indexed_at, document_count)
                VALUES (?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    indexed_at = excluded.indexed_at,
                    document_count = excluded.document_count
                """,
                (project_id, time.time(), document_count),
            )

    def get_search_meta(self, project_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT indexed_at, document_count FROM search_index_meta WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        if row is None:
            return None
        return {"indexed_at": row["indexed_at"], "document_count": row["document_count"]}

    def create_review_item(self, item_id: str, project_id: str, title: str, body: str) -> None:
        now = time.time()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO review_items (id, project_id, title, body, resolved, created_at, updated_at)
                VALUES (?, ?, ?, ?, 0, ?, ?)
                """,
                (item_id, project_id, title, body, now, now),
            )

    def list_review_items(self, project_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, title, body, resolved, created_at, updated_at
                FROM review_items WHERE project_id = ?
                ORDER BY resolved ASC, created_at DESC
                """,
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_task(self, task_id: str, project_id: str, kind: str, status: str, label: str, payload: dict[str, Any] | None = None) -> None:
        now = time.time()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO tasks (id, project_id, kind, status, label, payload, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    label = excluded.label,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (task_id, project_id, kind, status, label, json.dumps(payload or {}), now, now),
            )

    def list_tasks(self, project_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, kind, status, label, payload, created_at, updated_at
                FROM tasks WHERE project_id = ?
                ORDER BY created_at DESC
                """,
                (project_id,),
            ).fetchall()
        tasks = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item["payload"])
            tasks.append(item)
        return tasks

