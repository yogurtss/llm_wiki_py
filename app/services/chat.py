from __future__ import annotations

import time
import uuid

from app.storage import SQLiteStore


class ChatStore:
    def __init__(self, store: SQLiteStore):
        self.store = store

    def create_conversation(self, project_id: str, title: str = "New chat") -> str:
        conversation_id = str(uuid.uuid4())
        now = time.time()
        with self.store.connect() as conn:
            conn.execute(
                """
                INSERT INTO conversations (id, project_id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (conversation_id, project_id, title, now, now),
            )
        return conversation_id

    def list_conversations(self, project_id: str) -> list[dict[str, object]]:
        with self.store.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, title, created_at, updated_at
                FROM conversations WHERE project_id = ?
                ORDER BY updated_at DESC
                """,
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_message(self, conversation_id: str, role: str, content: str) -> str:
        message_id = str(uuid.uuid4())
        now = time.time()
        with self.store.connect() as conn:
            conn.execute(
                """
                INSERT INTO messages (id, conversation_id, role, content, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (message_id, conversation_id, role, content, now),
            )
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )
        return message_id

    def list_messages(self, conversation_id: str) -> list[dict[str, object]]:
        with self.store.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, role, content, created_at, metadata
                FROM messages WHERE conversation_id = ?
                ORDER BY created_at ASC
                """,
                (conversation_id,),
            ).fetchall()
        return [dict(row) for row in rows]

