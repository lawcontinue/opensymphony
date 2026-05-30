"""Skill Registry — manages skill candidates, approved skills, and archived skills.

All skills stored in SQLite. Only approved skills are loaded by Echo Engine.
Skills can only be promoted via file-level operations, never via API.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Skill:
    id: str = ""
    trigger: str = ""          # e.g. "soul_id=drama_director AND error=thinking_leak"
    pattern: str = ""          # Human-readable pattern description
    fix_type: str = ""         # P2_param / P1_prompt / P0_code
    suggestion: str = ""       # What to do
    pre_action: str = ""       # Auto-applied before LLM call (e.g. "add_prefix:Respond directly")
    post_action: str = ""      # Auto-applied after LLM call (e.g. "extract_json")
    evidence: list[str] = field(default_factory=list)
    confidence: float = 0.0
    status: str = "candidate"  # candidate → approved → active → archived → degraded
    created_at: float = 0.0
    approved_at: float = 0.0
    trigger_count: int = 0     # How many times this skill was matched
    last_triggered: float = 0.0
    fail_count: int = 0        # How many times applying this skill failed

    def __post_init__(self):
        if not self.id:
            self.id = f"SK-{time.strftime('%Y%m%d')}-{int(time.time())%10000:04d}"
        if not self.created_at:
            self.created_at = time.time()


class SkillRegistry:
    """SQLite-backed skill registry with lifecycle management."""

    def __init__(self, db_path: str | Path = "data/skills.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self):
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS skills (
                id TEXT PRIMARY KEY,
                trigger TEXT NOT NULL,
                pattern TEXT NOT NULL,
                fix_type TEXT NOT NULL DEFAULT 'P2_param',
                suggestion TEXT DEFAULT '',
                pre_action TEXT DEFAULT '',
                post_action TEXT DEFAULT '',
                evidence TEXT DEFAULT '[]',
                confidence REAL DEFAULT 0.0,
                status TEXT NOT NULL DEFAULT 'candidate',
                created_at REAL NOT NULL,
                approved_at REAL DEFAULT 0,
                trigger_count INTEGER DEFAULT 0,
                last_triggered REAL DEFAULT 0,
                fail_count INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_status ON skills(status);
            CREATE INDEX IF NOT EXISTS idx_trigger ON skills(trigger);
        """)
        conn.commit()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        return self._conn

    def add(self, skill: Skill) -> str:
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO skills (id, trigger, pattern, fix_type, suggestion, pre_action, post_action, evidence, confidence, status, created_at, approved_at, trigger_count, last_triggered, fail_count) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (skill.id, skill.trigger, skill.pattern, skill.fix_type, skill.suggestion,
             skill.pre_action, skill.post_action, json.dumps(skill.evidence),
             skill.confidence, skill.status, skill.created_at, skill.approved_at,
             skill.trigger_count, skill.last_triggered, skill.fail_count),
        )
        conn.commit()
        return skill.id

    def approve(self, skill_id: str) -> bool:
        """Approve a candidate skill. Sets status to 'active'."""
        conn = self._get_conn()
        cur = conn.execute(
            "UPDATE skills SET status = 'active', approved_at = ? WHERE id = ? AND status IN ('candidate', 'degraded')",
            (time.time(), skill_id),
        )
        conn.commit()
        return cur.rowcount > 0

    def reject(self, skill_id: str) -> bool:
        conn = self._get_conn()
        cur = conn.execute("UPDATE skills SET status = 'archived' WHERE id = ? AND status = 'candidate'", (skill_id,))
        conn.commit()
        return cur.rowcount > 0

    def record_trigger(self, skill_id: str, success: bool = True) -> None:
        """Record that a skill was triggered. Track failures for auto-degradation."""
        conn = self._get_conn()
        if success:
            conn.execute(
                "UPDATE skills SET trigger_count = trigger_count + 1, last_triggered = ?, fail_count = 0 WHERE id = ?",
                (time.time(), skill_id),
            )
        else:
            conn.execute(
                "UPDATE skills SET trigger_count = trigger_count + 1, last_triggered = ?, fail_count = fail_count + 1 WHERE id = ?",
                (time.time(), skill_id),
            )
            # Auto-degrade after 3 consecutive failures
            conn.execute(
                "UPDATE skills SET status = 'degraded' WHERE id = ? AND fail_count >= 3 AND status = 'active'",
                (skill_id,),
            )
        conn.commit()

    def get_active_skills(self) -> list[Skill]:
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM skills WHERE status = 'active'").fetchall()
        return [self._row_to_skill(r) for r in rows]

    def get_candidates(self) -> list[Skill]:
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM skills WHERE status = 'candidate' ORDER BY confidence DESC").fetchall()
        return [self._row_to_skill(r) for r in rows]

    def get_stale_skills(self, days: int = 30) -> list[Skill]:
        """Get active skills not triggered in N days (candidates for archival)."""
        cutoff = time.time() - days * 86400
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM skills WHERE status = 'active' AND last_triggered > 0 AND last_triggered < ?",
            (cutoff,),
        ).fetchall()
        return [self._row_to_skill(r) for r in rows]

    def list_all(self) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute("SELECT id, trigger, pattern, fix_type, status, confidence, trigger_count, fail_count FROM skills ORDER BY created_at DESC").fetchall()
        return [{"id": r[0], "trigger": r[1], "pattern": r[2], "fix_type": r[3],
                 "status": r[4], "confidence": r[5], "triggers": r[6], "fails": r[7]} for r in rows]

    def _row_to_skill(self, r) -> Skill:
        return Skill(
            id=r[0], trigger=r[1], pattern=r[2], fix_type=r[3],
            suggestion=r[4], pre_action=r[5], post_action=r[6],
            evidence=json.loads(r[7]) if r[7] else [],
            confidence=r[8], status=r[9], created_at=r[10], approved_at=r[11],
            trigger_count=r[12], last_triggered=r[13], fail_count=r[14],
        )
