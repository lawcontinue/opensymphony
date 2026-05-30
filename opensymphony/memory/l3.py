"""Memory L3 — Raw audit log (append-only file)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class L3Memory:
    """Append-only audit log. Never delete, never modify."""

    def __init__(self, log_dir: Path | str = "memory/audit"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def append(self, event_type: str, data: dict[str, Any], agent_id: str | None = None) -> None:
        entry = {
            "ts": time.time(),
            "type": event_type,
            "agent_id": agent_id,
            "data": data,
        }
        # One file per day
        filename = time.strftime("%Y-%m-%d", time.localtime()) + ".jsonl"
        path = self.log_dir / filename
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def read(self, date_str: str | None = None, event_type: str | None = None, limit: int = 50) -> list[dict]:
        if date_str is None:
            date_str = time.strftime("%Y-%m-%d", time.localtime())
        path = self.log_dir / f"{date_str}.jsonl"
        if not path.exists():
            return []

        entries = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                entry = json.loads(line.strip())
                if event_type and entry.get("type") != event_type:
                    continue
                entries.append(entry)
        return entries[-limit:]

    def count(self, date_str: str | None = None) -> int:
        if date_str is None:
            date_str = time.strftime("%Y-%m-%d", time.localtime())
        path = self.log_dir / f"{date_str}.jsonl"
        if not path.exists():
            return 0
        with open(path, encoding="utf-8") as f:
            return sum(1 for _ in f)
