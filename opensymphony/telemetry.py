"""Telemetry — Layer 1 of Reverberate (回响) self-evolution architecture.

Records every LLM call and tool execution for pattern mining.
Storage: SQLite (zero external dependencies).

Schema:
  llm_calls: soul_id, model, provider, task_type, latency_ms, tokens_in, tokens_out,
             success, error_type, timestamp
  tool_calls: tool_name, params_hash, latency_ms, success, error_type, timestamp
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path


class Telemetry:
    """Lightweight telemetry store for Symphony."""

    def __init__(self, db_path: str | Path = "data/telemetry.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self):
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS llm_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                soul_id TEXT DEFAULT '',
                model TEXT NOT NULL,
                provider TEXT NOT NULL,
                task_type TEXT DEFAULT 'chat',
                latency_ms REAL NOT NULL,
                tokens_in INTEGER DEFAULT 0,
                tokens_out INTEGER DEFAULT 0,
                success INTEGER NOT NULL DEFAULT 1,
                error_type TEXT DEFAULT '',
                response_preview TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS tool_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                tool_name TEXT NOT NULL,
                params_hash TEXT DEFAULT '',
                latency_ms REAL NOT NULL,
                success INTEGER NOT NULL DEFAULT 1,
                error_type TEXT DEFAULT '',
                result_preview TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_llm_ts ON llm_calls(timestamp);
            CREATE INDEX IF NOT EXISTS idx_llm_soul ON llm_calls(soul_id);
            CREATE INDEX IF NOT EXISTS idx_tool_ts ON tool_calls(timestamp);
            CREATE INDEX IF NOT EXISTS idx_tool_name ON tool_calls(tool_name);
        """)
        conn.commit()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        return self._conn

    def record_llm(
        self,
        model: str,
        provider: str,
        latency_ms: float,
        soul_id: str = "",
        task_type: str = "chat",
        tokens_in: int = 0,
        tokens_out: int = 0,
        success: bool = True,
        error_type: str = "",
        response_preview: str = "",
    ) -> None:
        """Record an LLM API call."""
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO llm_calls (timestamp, soul_id, model, provider, task_type, latency_ms, tokens_in, tokens_out, success, error_type, response_preview) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), soul_id, model, provider, task_type, latency_ms, tokens_in, tokens_out, int(success), error_type, response_preview[:500]),
        )
        conn.commit()

    def record_tool(
        self,
        tool_name: str,
        latency_ms: float,
        params_hash: str = "",
        success: bool = True,
        error_type: str = "",
        result_preview: str = "",
    ) -> None:
        """Record a tool execution."""
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO tool_calls (timestamp, tool_name, params_hash, latency_ms, success, error_type, result_preview) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (time.time(), tool_name, params_hash, latency_ms, int(success), error_type, result_preview[:300]),
        )
        conn.commit()

    # ── Query methods for Pattern Miner ──────────────────────────────

    def get_daily_summary(self, date_str: str = "") -> dict:
        """Get summary stats for a given date (YYYY-MM-DD). Default: today."""
        conn = self._get_conn()
        if not date_str:
            date_str = time.strftime("%Y-%m-%d")

        start_ts = time.mktime(time.strptime(f"{date_str} 00:00:00", "%Y-%m-%d %H:%M:%S"))
        end_ts = start_ts + 86400

        # LLM summary
        llm = conn.execute("""
            SELECT soul_id, model, provider, task_type,
                   COUNT(*) as calls,
                   SUM(success) as successes,
                   AVG(latency_ms) as avg_latency,
                   SUM(tokens_in) as total_tokens_in,
                   SUM(tokens_out) as total_tokens_out
            FROM llm_calls WHERE timestamp BETWEEN ? AND ?
            GROUP BY soul_id, model
            ORDER BY calls DESC
        """, (start_ts, end_ts)).fetchall()

        # Tool summary
        tools = conn.execute("""
            SELECT tool_name,
                   COUNT(*) as calls,
                   SUM(success) as successes,
                   AVG(latency_ms) as avg_latency
            FROM tool_calls WHERE timestamp BETWEEN ? AND ?
            GROUP BY tool_name
            ORDER BY calls DESC
        """, (start_ts, end_ts)).fetchall()

        # Error clustering
        errors = conn.execute("""
            SELECT soul_id, model, error_type, COUNT(*) as count
            FROM llm_calls WHERE timestamp BETWEEN ? AND ? AND success = 0
            GROUP BY soul_id, error_type
            ORDER BY count DESC
        """, (start_ts, end_ts)).fetchall()

        return {
            "date": date_str,
            "llm": [{"soul_id": r[0], "model": r[1], "provider": r[2], "task_type": r[3],
                      "calls": r[4], "successes": r[5], "avg_latency_ms": round(r[6], 1),
                      "tokens_in": r[7], "tokens_out": r[8]} for r in llm],
            "tools": [{"tool_name": r[0], "calls": r[1], "successes": r[2],
                        "avg_latency_ms": round(r[3], 1)} for r in tools],
            "errors": [{"soul_id": r[0], "model": r[1], "error_type": r[2], "count": r[3]} for r in errors],
        }

    def get_skill_candidates(self, min_repeats: int = 3, days: int = 7) -> list[dict]:
        """Find error patterns that repeat enough to warrant a Skill candidate."""
        conn = self._get_conn()
        cutoff = time.time() - days * 86400

        patterns = conn.execute("""
            SELECT soul_id, error_type, COUNT(*) as count,
                   MIN(timestamp) as first_seen, MAX(timestamp) as last_seen
            FROM llm_calls
            WHERE timestamp > ? AND success = 0 AND error_type != ''
            GROUP BY soul_id, error_type
            HAVING count >= ?
            ORDER BY count DESC
        """, (cutoff, min_repeats)).fetchall()

        candidates = []
        for r in patterns:
            soul_id, error_type, count, first, last = r
            # Get example errors
            examples = conn.execute("""
                SELECT timestamp, model, response_preview
                FROM llm_calls
                WHERE soul_id = ? AND error_type = ? AND success = 0
                ORDER BY timestamp DESC LIMIT 3
            """, (soul_id, error_type)).fetchall()

            candidates.append({
                "trigger": f"{soul_id} encounters {error_type}",
                "pattern": error_type,
                "soul_id": soul_id,
                "repeat_count": count,
                "first_seen": time.strftime("%Y-%m-%d %H:%M", time.localtime(first)),
                "last_seen": time.strftime("%Y-%m-%d %H:%M", time.localtime(last)),
                "examples": [time.strftime("%Y-%m-%d", time.localtime(e[0])) for e in examples],
                "confidence": min(count / min_repeats, 1.0),
                "status": "candidate",  # candidate → approved → archived
            })

        return candidates

    def get_total_records(self) -> dict:
        conn = self._get_conn()
        llm_count = conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
        tool_count = conn.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0]
        return {"llm_calls": llm_count, "tool_calls": tool_count}

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
