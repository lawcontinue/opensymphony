"""Scheduler — priority-based task queue with resource limits."""

from __future__ import annotations

import heapq
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Task:
    id: str = ""
    description: str = ""
    agent_id: str | None = None
    soul_id: str | None = None
    priority: int = 0  # lower = higher priority
    status: TaskStatus = TaskStatus.PENDING
    result: Any = None
    error: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    completed_at: float = 0.0
    max_retries: int = 1
    retries: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex[:12]

    def __lt__(self, other: Task) -> bool:
        return self.priority < other.priority


class TaskScheduler:
    """Priority queue scheduler with retry and concurrency limits."""

    def __init__(self, max_concurrent: int = 5):
        self.max_concurrent = max_concurrent
        self._queue: list[Task] = []  # min-heap by priority
        self._running: dict[str, Task] = {}
        self._completed: list[Task] = []
        self._max_history = 100

    def submit(self, description: str, agent_id: str | None = None,
               soul_id: str | None = None, priority: int = 0, **kwargs: Any) -> Task:
        task = Task(description=description, agent_id=agent_id, soul_id=soul_id,
                    priority=priority, metadata=kwargs)
        heapq.heappush(self._queue, task)
        return task

    def next(self) -> Task | None:
        """Get next pending task if slot available."""
        if len(self._running) >= self.max_concurrent:
            return None
        while self._queue:
            task = heapq.heappop(self._queue)
            if task.status == TaskStatus.PENDING:
                task.status = TaskStatus.RUNNING
                task.started_at = time.time()
                self._running[task.id] = task
                return task
        return None

    def complete(self, task_id: str, result: Any = None) -> Task | None:
        task = self._running.pop(task_id, None)
        if not task:
            return None
        task.status = TaskStatus.COMPLETED
        task.result = result
        task.completed_at = time.time()
        self._completed.append(task)
        self._trim_history()
        return task

    def fail(self, task_id: str, error: str = "") -> Task | None:
        task = self._running.pop(task_id, None)
        if not task:
            return None
        if task.retries < task.max_retries:
            task.retries += 1
            task.status = TaskStatus.PENDING
            heapq.heappush(self._queue, task)
            return task
        task.status = TaskStatus.FAILED
        task.error = error
        task.completed_at = time.time()
        self._completed.append(task)
        self._trim_history()
        return task

    def cancel(self, task_id: str) -> bool:
        # Check running
        if task_id in self._running:
            task = self._running.pop(task_id)
            task.status = TaskStatus.CANCELLED
            return True
        # Check queue
        for i, t in enumerate(self._queue):
            if t.id == task_id:
                t.status = TaskStatus.CANCELLED
                self._queue.pop(i)
                heapq.heapify(self._queue)
                return True
        return False

    @property
    def pending_count(self) -> int:
        return len(self._queue)

    @property
    def running_count(self) -> int:
        return len(self._running)

    def stats(self) -> dict:
        return {
            "pending": len(self._queue),
            "running": len(self._running),
            "completed": len(self._completed),
            "max_concurrent": self.max_concurrent,
        }

    def _trim_history(self):
        if len(self._completed) > self._max_history:
            self._completed = self._completed[-self._max_history:]
