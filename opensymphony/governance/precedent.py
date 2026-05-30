"""Precedent — lightweight precedent store (SQLite-backed)."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Precedent:
    """A stored decision precedent for future reference."""
    id: str = ""
    description: str = ""
    category: str = ""  # "security", "architecture", "resource", "workflow"
    approved: bool = True
    reasoning: str = ""
    conditions: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    weight: float = 1.0  # 0.0 - 5.0
    citation_count: int = 0
    created_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex[:12]
        if not self.created_at:
            self.created_at = time.time()


class PrecedentStore:
    """SQLite-backed precedent store with text search."""

    def __init__(self, db_path: Path | str = "data/precedents.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS precedents (
                id TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                category TEXT NOT NULL,
                approved INTEGER NOT NULL,
                reasoning TEXT,
                conditions TEXT,
                tags TEXT,
                weight REAL DEFAULT 1.0,
                citation_count INTEGER DEFAULT 0,
                created_at REAL NOT NULL,
                metadata TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_prec_category ON precedents(category);
            CREATE INDEX IF NOT EXISTS idx_prec_tags ON precedents(tags);
        """)

    def store(self, prec: Precedent) -> str:
        self._conn.execute(
            "INSERT OR REPLACE INTO precedents "
            "(id, description, category, approved, reasoning, conditions, tags, weight, "
            "citation_count, created_at, metadata) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (prec.id, prec.description, prec.category, int(prec.approved), prec.reasoning,
             json.dumps(prec.conditions), json.dumps(prec.tags), prec.weight,
             prec.citation_count, prec.created_at, json.dumps(prec.metadata)),
        )
        self._conn.commit()
        return prec.id

    def search(self, query: str | None = None, category: str | None = None,
               approved: bool | None = None, limit: int = 10) -> list[Precedent]:
        clauses: list[str] = []
        params: list[Any] = []

        if query:
            clauses.append("(description LIKE ? OR reasoning LIKE ? OR tags LIKE ?)")
            q = f"%{query}%"
            params.extend([q, q, q])
        if category:
            clauses.append("category = ?")
            params.append(category)
        if approved is not None:
            clauses.append("approved = ?")
            params.append(int(approved))

        where = " AND ".join(clauses) if clauses else "1=1"
        sql = f"SELECT * FROM precedents WHERE {where} ORDER BY weight DESC, created_at DESC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_prec(r) for r in rows]

    def cite(self, precedent_id: str) -> None:
        """Increment citation count."""
        self._conn.execute(
            "UPDATE precedents SET citation_count = citation_count + 1 WHERE id = ?",
            (precedent_id,),
        )
        self._conn.commit()

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM precedents").fetchone()
        return row[0]

    def _row_to_prec(self, row: sqlite3.Row) -> Precedent:
        return Precedent(
            id=row["id"],
            description=row["description"],
            category=row["category"],
            approved=bool(row["approved"]),
            reasoning=row["reasoning"] or "",
            conditions=json.loads(row["conditions"]) if row["conditions"] else [],
            tags=json.loads(row["tags"]) if row["tags"] else [],
            weight=row["weight"],
            citation_count=row["citation_count"],
            created_at=row["created_at"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        )

    def close(self):
        self._conn.close()
