"""Sandbox — resource limits for agent execution."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class ResourceLimits:
    max_tokens_per_hour: int = 100_000
    max_tool_calls_per_hour: int = 200
    max_memory_mb: int = 256
    max_concurrent_tasks: int = 3


@dataclass
class ResourceUsage:
    tokens_used: int = 0
    tool_calls: int = 0
    window_start: float = field(default_factory=time.time)

    def reset_if_expired(self, window_seconds: float = 3600) -> None:
        if time.time() - self.window_start > window_seconds:
            self.tokens_used = 0
            self.tool_calls = 0
            self.window_start = time.time()


class AgentSandbox:
    """Enforce resource limits on agent actions."""

    def __init__(self, limits: ResourceLimits | None = None):
        self.limits = limits or ResourceLimits()
        self._usage: dict[str, ResourceUsage] = {}

    def check(self, agent_id: str, tokens: int = 0, tool_call: bool = False) -> tuple[bool, str]:
        """Check if action is within limits. Returns (allowed, reason)."""
        if agent_id not in self._usage:
            self._usage[agent_id] = ResourceUsage()

        usage = self._usage[agent_id]
        usage.reset_if_expired()

        if tokens > 0:
            if usage.tokens_used + tokens > self.limits.max_tokens_per_hour:
                return False, f"Token limit exceeded: {usage.tokens_used + tokens}/{self.limits.max_tokens_per_hour}/h"
            usage.tokens_used += tokens

        if tool_call:
            if usage.tool_calls >= self.limits.max_tool_calls_per_hour:
                return False, f"Tool call limit exceeded: {usage.tool_calls}/{self.limits.max_tool_calls_per_hour}/h"
            usage.tool_calls += 1

        return True, "ok"

    def get_usage(self, agent_id: str) -> dict:
        usage = self._usage.get(agent_id)
        if not usage:
            return {"tokens_used": 0, "tool_calls": 0}
        return {"tokens_used": usage.tokens_used, "tool_calls": usage.tool_calls}

    def reset(self, agent_id: str) -> None:
        self._usage.pop(agent_id, None)
