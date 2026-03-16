"""SQLite-backed chat & message persistence."""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from .utils import text_preview, utc_now_iso


class ChatStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS chats (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    pinned INTEGER NOT NULL DEFAULT 0,
                    archived INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    chat_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_messages_chat_created
                ON messages(chat_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_chats_updated
                ON chats(pinned, archived, updated_at DESC);
            """)

    @staticmethod
    def _row_to_chat(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "title": row["title"],
            "pinned": bool(row["pinned"]),
            "archived": bool(row["archived"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_message_preview": row["last_message_preview"],
        }

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> dict[str, Any]:
        metadata = row["metadata"]
        try:
            chat_id = row["chat_id"]
        except (KeyError, IndexError):
            chat_id = None
        return {
            "id": row["id"],
            "chat_id": chat_id,
            "role": row["role"],
            "content": row["content"],
            "created_at": row["created_at"],
            "metadata": json.loads(metadata) if metadata else None,
        }

    def create_chat(self, title: str = "New Chat", chat_id: str | None = None) -> dict[str, Any]:
        cid = chat_id or str(uuid.uuid4())
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO chats(id, title, pinned, archived, created_at, updated_at) VALUES(?, ?, 0, 0, ?, ?)",
                (cid, title, now, now),
            )
            row = conn.execute(
                "SELECT c.*, NULL AS last_message_preview FROM chats c WHERE c.id = ?",
                (cid,),
            ).fetchone()
        return self._row_to_chat(row)

    def list_chats(self, include_archived: bool = True) -> list[dict[str, Any]]:
        where = "" if include_archived else "WHERE c.archived = 0"
        query = f"""
            SELECT c.*,
                (SELECT m.content FROM messages m WHERE m.chat_id = c.id ORDER BY m.created_at DESC LIMIT 1)
                AS last_message_preview
            FROM chats c {where}
            ORDER BY c.pinned DESC, c.updated_at DESC
        """
        with self._connect() as conn:
            rows = conn.execute(query).fetchall()
        chats = []
        for row in rows:
            chat = self._row_to_chat(row)
            if chat["last_message_preview"]:
                chat["last_message_preview"] = text_preview(chat["last_message_preview"])
            chats.append(chat)
        return chats

    def get_chat(self, chat_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT c.*,
                    (SELECT m.content FROM messages m WHERE m.chat_id = c.id ORDER BY m.created_at DESC LIMIT 1)
                    AS last_message_preview
                FROM chats c WHERE c.id = ?""",
                (chat_id,),
            ).fetchone()
        if not row:
            return None
        chat = self._row_to_chat(row)
        if chat["last_message_preview"]:
            chat["last_message_preview"] = text_preview(chat["last_message_preview"])
        return chat

    def update_chat(self, chat_id: str, *, title: str | None = None, pinned: bool | None = None, archived: bool | None = None) -> dict[str, Any] | None:
        chat = self.get_chat(chat_id)
        if not chat:
            return None
        new_title = title if title is not None else chat["title"]
        new_pinned = int(pinned if pinned is not None else chat["pinned"])
        new_archived = int(archived if archived is not None else chat["archived"])
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                "UPDATE chats SET title=?, pinned=?, archived=?, updated_at=? WHERE id=?",
                (new_title, new_pinned, new_archived, now, chat_id),
            )
        return self.get_chat(chat_id)

    def delete_chat(self, chat_id: str) -> bool:
        with self._connect() as conn:
            deleted = conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,)).rowcount
        return deleted > 0

    def add_message(self, chat_id: str, role: str, content: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        message_id = str(uuid.uuid4())
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO messages(id, chat_id, role, content, metadata, created_at) VALUES(?, ?, ?, ?, ?, ?)",
                (message_id, chat_id, role, content, json.dumps(metadata) if metadata else None, now),
            )
            conn.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (now, chat_id))
            row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        return self._row_to_message(row)

    def list_messages(self, chat_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE chat_id = ? ORDER BY created_at ASC",
                (chat_id,),
            ).fetchall()
        return [self._row_to_message(row) for row in rows]
