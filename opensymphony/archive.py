"""Archive & Compress — Layer 2.5 of Reverberate (回响).

"选择性压缩即智能" — Not all data is equal. This module:

1. Archives old telemetry into compressed summaries (daily → weekly → monthly)
2. Extracts and preserves high-signal patterns (errors, breakthroughs, anomalies)
3. Discards low-signal noise (routine successful calls with no learnings)

Retention policy:
  - Raw telemetry: 7 days → archive
  - Daily summaries: 30 days → weekly summary
  - Weekly summaries: 90 days → monthly summary
  - Skill candidates + approved skills: forever
  - Error examples: keep last 5 per pattern
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


class ArchiveEngine:
    """Selective compression for telemetry data."""

    def __init__(self, db_path: str | Path = "data/telemetry.db",
                 archive_path: str | Path = "data/archive"):
        self.db_path = Path(db_path)
        self.archive_path = Path(archive_path)
        self.archive_path.mkdir(parents=True, exist_ok=True)

    def run_daily_archive(self) -> dict:
        """Run daily archival: compress yesterday's raw data into summary."""
        yesterday = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))

        conn = sqlite3.connect(str(self.db_path))

        # Get yesterday's data range
        start_ts = time.mktime(time.strptime(f"{yesterday} 00:00:00", "%Y-%m-%d %H:%M:%S"))
        end_ts = start_ts + 86400

        # ── 1. Compress LLM calls into summary ──
        llm_summary = conn.execute("""
            SELECT soul_id, model, provider, task_type,
                   COUNT(*) as total_calls,
                   SUM(success) as successes,
                   AVG(latency_ms) as avg_latency,
                   MIN(latency_ms) as min_latency,
                   MAX(latency_ms) as max_latency,
                   SUM(tokens_in) as total_tokens_in,
                   SUM(tokens_out) as total_tokens_out
            FROM llm_calls WHERE timestamp BETWEEN ? AND ?
            GROUP BY soul_id, model, task_type
        """, (start_ts, end_ts)).fetchall()

        # ── 2. Extract high-signal: errors and anomalies ──
        errors = conn.execute("""
            SELECT soul_id, model, error_type, COUNT(*) as count,
                   AVG(latency_ms) as avg_latency
            FROM llm_calls
            WHERE timestamp BETWEEN ? AND ? AND success = 0
            GROUP BY soul_id, model, error_type
            ORDER BY count DESC
        """, (start_ts, end_ts)).fetchall()

        # Anomalies: latency > 2x average
        anomalies = conn.execute("""
            SELECT soul_id, model, latency_ms, response_preview
            FROM llm_calls
            WHERE timestamp BETWEEN ? AND ? AND success = 1
              AND latency_ms > (SELECT AVG(latency_ms) * 2 FROM llm_calls WHERE timestamp BETWEEN ? AND ?)
            ORDER BY latency_ms DESC LIMIT 10
        """, (start_ts, end_ts, start_ts, end_ts)).fetchall()

        # ── 3. Build archive document ──
        archive = {
            "date": yesterday,
            "type": "daily",
            "compressed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "llm_summary": [{
                "soul_id": r[0], "model": r[1], "provider": r[2], "task_type": r[3],
                "total_calls": r[4], "successes": r[5], "success_rate": round(r[5]/max(r[4],1), 3),
                "avg_latency_ms": round(r[6], 1), "min_latency_ms": round(r[7], 1),
                "max_latency_ms": round(r[8], 1),
                "tokens_in": r[9], "tokens_out": r[10],
            } for r in llm_summary],
            "errors": [{
                "soul_id": r[0], "model": r[1], "error_type": r[2],
                "count": r[3], "avg_latency_ms": round(r[4], 1),
            } for r in errors],
            "anomalies": [{
                "soul_id": r[0], "model": r[1], "latency_ms": round(r[2], 1),
                "preview": r[3][:100] if r[3] else "",
            } for r in anomalies],
            "tool_summary": self._compress_tools(conn, start_ts, end_ts),
        }

        # Calculate compression ratio
        raw_count = conn.execute(
            "SELECT COUNT(*) FROM llm_calls WHERE timestamp BETWEEN ? AND ?",
            (start_ts, end_ts)
        ).fetchone()[0]

        archive["meta"] = {
            "raw_records": raw_count,
            "compressed_entries": len(llm_summary) + len(errors) + len(anomalies),
            "compression_ratio": f"{len(llm_summary) + len(errors) + len(anomalies)}:{raw_count}",
        }

        # ── 4. Save archive ──
        archive_file = self.archive_path / f"daily_{yesterday}.json"
        archive_file.write_text(json.dumps(archive, ensure_ascii=False, indent=2), encoding="utf-8")

        # ── 5. Delete archived raw data (keep errors for 7 more days) ──
        # Only delete successful routine calls
        deleted = conn.execute("""
            DELETE FROM llm_calls
            WHERE timestamp BETWEEN ? AND ?
              AND success = 1
              AND latency_ms < (SELECT AVG(latency_ms) * 2 FROM llm_calls WHERE timestamp BETWEEN ? AND ?)
        """, (start_ts, end_ts, start_ts, end_ts))
        conn.commit()

        result = {
            "date": yesterday,
            "raw_records": raw_count,
            "deleted_routine": deleted.rowcount,
            "kept_errors": len(errors),
            "kept_anomalies": len(anomalies),
            "archive_file": str(archive_file),
            "compression_ratio": archive["meta"]["compression_ratio"],
        }

        conn.close()
        return result

    def _compress_tools(self, conn: sqlite3.Connection, start_ts: float, end_ts: float) -> list[dict]:
        """Compress tool calls into summary."""
        rows = conn.execute("""
            SELECT tool_name,
                   COUNT(*) as total_calls,
                   SUM(success) as successes,
                   AVG(latency_ms) as avg_latency,
                   MIN(latency_ms) as min_latency,
                   MAX(latency_ms) as max_latency
            FROM tool_calls WHERE timestamp BETWEEN ? AND ?
            GROUP BY tool_name
        """, (start_ts, end_ts)).fetchall()

        return [{
            "tool_name": r[0], "total_calls": r[1], "successes": r[2],
            "success_rate": round(r[2]/max(r[1],1), 3),
            "avg_latency_ms": round(r[3], 1), "min_latency_ms": round(r[4], 1),
            "max_latency_ms": round(r[5], 1),
        } for r in rows]

    def run_weekly_rollup(self, week_start: str = "") -> dict:
        """Roll up 7 daily archives into one weekly summary."""
        if not week_start:
            # Last Monday
            now = time.localtime()
            days_since_monday = now.tm_wday
            monday = time.localtime(time.time() - days_since_monday * 86400)
            week_start = time.strftime("%Y-%m-%d", monday)

        time.mktime(time.strptime(week_start, "%Y-%m-%d")) + 7 * 86400

        # Load all daily archives for this week
        daily_archives = []
        for i in range(7):
            day = time.strftime("%Y-%m-%d", time.localtime(
                time.mktime(time.strptime(week_start, "%Y-%m-%d")) + i * 86400))
            path = self.archive_path / f"daily_{day}.json"
            if path.exists():
                daily_archives.append(json.loads(path.read_text(encoding="utf-8")))

        if not daily_archives:
            return {"status": "no_data", "week": week_start}

        # Aggregate
        total_calls = sum(
            sum(s["total_calls"] for s in d.get("llm_summary", []))
            for d in daily_archives
        )
        all_errors = []
        for d in daily_archives:
            all_errors.extend(d.get("errors", []))

        # Group errors by soul+type
        error_groups: dict[str, int] = {}
        for e in all_errors:
            key = f"{e['soul_id']}:{e['error_type']}"
            error_groups[key] = error_groups.get(key, 0) + e["count"]

        weekly = {
            "week": week_start,
            "type": "weekly",
            "compressed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total_calls": total_calls,
            "daily_files": len(daily_archives),
            "top_errors": sorted(
                [{"pattern": k, "count": v} for k, v in error_groups.items()],
                key=lambda x: x["count"], reverse=True
            )[:10],
            "daily_summaries": [{
                "date": d["date"],
                "calls": sum(s["total_calls"] for s in d.get("llm_summary", [])),
                "errors": len(d.get("errors", [])),
            } for d in daily_archives],
        }

        out_path = self.archive_path / f"weekly_{week_start}.json"
        out_path.write_text(json.dumps(weekly, ensure_ascii=False, indent=2), encoding="utf-8")

        # Delete daily archives after weekly rollup
        for i in range(7):
            day = time.strftime("%Y-%m-%d", time.localtime(
                time.mktime(time.strptime(week_start, "%Y-%m-%d")) + i * 86400))
            path = self.archive_path / f"daily_{day}.json"
            if path.exists():
                path.unlink()

        return {
            "week": week_start,
            "total_calls": total_calls,
            "daily_files_consumed": len(daily_archives),
            "weekly_file": str(out_path),
        }

    def list_archives(self) -> dict:
        """List all archive files."""
        dailies = sorted(self.archive_path.glob("daily_*.json"))
        weeklies = sorted(self.archive_path.glob("weekly_*.json"))
        monthlies = sorted(self.archive_path.glob("monthly_*.json"))
        return {
            "dailies": [f.name for f in dailies],
            "weeklies": [f.name for f in weeklies],
            "monthlies": [f.name for f in monthlies],
        }
