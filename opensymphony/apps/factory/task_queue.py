"""Task Queue — Persistent task queue with atomic writes and state machine.

States: pending → planning → executing → reviewing → done / paused / failed
Priority: P0 (urgent) > P1 (normal) > P2 (low)

Persistence: JSON file with atomic write (tempfile + rename).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger("symphony.apps.factory.task_queue")


class TaskState(str, Enum):
    PENDING = "pending"
    PLANNING = "planning"
    EXECUTING = "executing"
    REVIEWING = "reviewing"
    DONE = "done"
    PAUSED = "paused"      # Awaiting human review
    FAILED = "failed"


class TaskTier(str, Enum):
    S = "S"  # Human material, 85+, needs review
    A = "A"  # Search-assisted, 75+ auto
    B = "B"  # Pure AI, 70+ auto, stockpile


class TaskType(str, Enum):
    ARTICLE = "article"           # 法律科普/技术博客
    NOVEL_CHAPTER = "novel_chapter"  # 网文章节
    IMAGE_SET = "image_set"       # 图片集（无文本）
    FULL_PACKAGE = "full_package"  # 文本+图片+视频


@dataclass
class Task:
    """A single content production task."""
    id: str
    task_type: TaskType = TaskType.ARTICLE
    tier: TaskTier = TaskTier.A
    state: TaskState = TaskState.PENDING
    priority: int = 1  # 0=P0, 1=P1, 2=P2

    # Task definition
    topic: str = ""          # 主题/标题
    genre: str = "general"   # 题材分类：legal/tech/novel/xianxia
    prompt: str = ""         # 生成 prompt
    seed_material: str = ""  # seed material
    soul_name: str = ""      # 指定 Soul（空=自动选择）
    keywords: list[str] = field(default_factory=list)
    target_length: int = 2000  # 目标字数

    # Output config
    output_dir: str = ""     # 输出目录
    download_media: bool = True  # 是否下载图片/视频到本地
    media_root: str = ""     # 媒体文件根目录（如 E:\novel_output）

    # Execution state
    progress: dict[str, Any] = field(default_factory=dict)  # 步骤完成状态
    result: dict[str, Any] = field(default_factory=dict)     # 最终结果
    error: str = ""
    score: float = 0.0
    retry_count: int = 0
    max_retries: int = 2

    # Timing
    created_at: float = 0.0
    started_at: float = 0.0
    finished_at: float = 0.0

    # AI content label (mandatory)
    ai_generated: bool = True

    def __post_init__(self):
        if not self.created_at:
            self.created_at = time.time()

    def to_dict(self) -> dict:
        return {
            "id": self.id, "task_type": self.task_type.value, "tier": self.tier.value,
            "state": self.state.value, "priority": self.priority,
            "topic": self.topic, "genre": self.genre, "prompt": self.prompt,
            "seed_material": self.seed_material, "soul_name": self.soul_name,
            "keywords": self.keywords, "target_length": self.target_length,
            "output_dir": self.output_dir, "download_media": self.download_media,
            "media_root": self.media_root,
            "progress": self.progress, "result": self.result,
            "error": self.error, "score": self.score,
            "retry_count": self.retry_count, "max_retries": self.max_retries,
            "created_at": self.created_at, "started_at": self.started_at,
            "finished_at": self.finished_at, "ai_generated": self.ai_generated,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Task:
        d = dict(d)
        d["task_type"] = TaskType(d.get("task_type", "article"))
        d["tier"] = TaskTier(d.get("tier", "A"))
        d["state"] = TaskState(d.get("state", "pending"))
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class TaskQueue:
    """Persistent task queue with atomic JSON writes."""

    def __init__(self, queue_file: Path):
        self.queue_file = Path(queue_file)
        self.queue_file.parent.mkdir(parents=True, exist_ok=True)
        self._tasks: dict[str, Task] = {}
        self._load()

    def _load(self):
        if self.queue_file.exists():
            try:
                data = json.loads(self.queue_file.read_text(encoding="utf-8"))
                for td in data.get("tasks", []):
                    t = Task.from_dict(td)
                    self._tasks[t.id] = t
                logger.info(f"Loaded {len(self._tasks)} tasks from {self.queue_file}")
            except (json.JSONDecodeError, Exception) as e:
                logger.error(f"Failed to load queue: {e}")
                # Try backup
                backup = self.queue_file.with_suffix(".bak")
                if backup.exists():
                    data = json.loads(backup.read_text(encoding="utf-8"))
                    for td in data.get("tasks", []):
                        t = Task.from_dict(td)
                        self._tasks[t.id] = t
                    logger.info(f"Recovered {len(self._tasks)} tasks from backup")

    def _atomic_write(self):
        """Atomic write: write to temp file, then rename."""
        data = {"tasks": [t.to_dict() for t in self._tasks.values()],
                "updated_at": time.time()}
        content = json.dumps(data, ensure_ascii=False, indent=2)

        fd, tmp_path = tempfile.mkstemp(
            dir=str(self.queue_file.parent), suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            # Atomic rename
            os.replace(tmp_path, str(self.queue_file))
        except Exception:
            os.unlink(tmp_path) if os.path.exists(tmp_path) else None
            raise

    def add(self, task: Task) -> None:
        self._tasks[task.id] = task
        self._atomic_write()
        logger.info(f"Task added: {task.id} ({task.topic})")

    def update(self, task: Task) -> None:
        self._tasks[task.id] = task
        self._atomic_write()

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def pop_next(self) -> Task | None:
        """Get next executable task (pending, lowest priority number first)."""
        pending = [t for t in self._tasks.values() if t.state == TaskState.PENDING]
        if not pending:
            return None
        pending.sort(key=lambda t: (t.priority, t.created_at))
        return pending[0]

    def get_retryable(self) -> Task | None:
        """Get a failed task that can be retried."""
        failed = [t for t in self._tasks.values()
                  if t.state == TaskState.FAILED and t.retry_count < t.max_retries]
        if not failed:
            return None
        failed.sort(key=lambda t: (t.priority, t.created_at))
        return failed[0]

    def stats(self) -> dict:
        from collections import Counter
        states = Counter(t.state.value for t in self._tasks.values())
        return {"total": len(self._tasks), **dict(states)}

    def all_tasks(self) -> list[Task]:
        return sorted(self._tasks.values(), key=lambda t: t.created_at)

    def clear_done(self, max_age_hours: int = 72) -> int:
        """Remove completed tasks older than max_age_hours."""
        cutoff = time.time() - max_age_hours * 3600
        to_remove = [tid for tid, t in self._tasks.items()
                     if t.state in (TaskState.DONE, TaskState.FAILED)
                     and t.finished_at and t.finished_at < cutoff]
        for tid in to_remove:
            del self._tasks[tid]
        if to_remove:
            self._atomic_write()
        return len(to_remove)
