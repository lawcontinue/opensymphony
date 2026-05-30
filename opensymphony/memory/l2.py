"""Memory L2 — Experience store (SQLite + optional vector search via BGE-M3)."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Experience:
    """A stored experience (conversation turn, decision, lesson)."""
    id: str
    agent_id: str
    category: str  # "conversation", "decision", "lesson", "tool_result"
    content: str
    metadata: dict[str, Any] | None = None
    embedding: list[float] | None = None
    created_at: float = 0.0

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex[:12]
        if not self.created_at:
            self.created_at = time.time()


class L2Memory:
    """SQLite-backed experience store with optional vector search."""

    def __init__(self, db_path: Path | str = "memory/experiences.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS experiences (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_agent ON experiences(agent_id);
            CREATE INDEX IF NOT EXISTS idx_category ON experiences(category);
            CREATE INDEX IF NOT EXISTS idx_time ON experiences(created_at);
        """)

    def store(self, exp: Experience) -> str:
        self._conn.execute(
            "INSERT OR REPLACE INTO experiences (id, agent_id, category, content, metadata, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (exp.id, exp.agent_id, exp.category, exp.content,
             json.dumps(exp.metadata) if exp.metadata else None, exp.created_at),
        )
        self._conn.commit()
        return exp.id

    def search(
        self,
        agent_id: str | None = None,
        category: str | None = None,
        query: str | None = None,
        limit: int = 10,
    ) -> list[Experience]:
        """Search experiences by filters. Text search is LIKE-based."""
        clauses: list[str] = []
        params: list[Any] = []

        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if category:
            clauses.append("category = ?")
            params.append(category)
        if query:
            clauses.append("content LIKE ?")
            params.append(f"%{query}%")

        where = " AND ".join(clauses) if clauses else "1=1"
        sql = f"SELECT * FROM experiences WHERE {where} ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_exp(r) for r in rows]

    def count(self, agent_id: str | None = None) -> int:
        if agent_id:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM experiences WHERE agent_id = ?", (agent_id,)
            ).fetchone()
        else:
            row = self._conn.execute("SELECT COUNT(*) FROM experiences").fetchone()
        return row[0]

    def _row_to_exp(self, row: sqlite3.Row) -> Experience:
        return Experience(
            id=row["id"],
            agent_id=row["agent_id"],
            category=row["category"],
            content=row["content"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else None,
            created_at=row["created_at"],
        )

    def close(self):
        self._conn.close()
