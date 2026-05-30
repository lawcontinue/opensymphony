"""Agent Pool — manage multiple agents with lifecycle and resource limits."""

from __future__ import annotations

import time
from dataclasses import dataclass

from ..agents.agent import Agent, AgentStatus


@dataclass
class PoolStats:
    total: int = 0
    active: int = 0
    idle: int = 0
    suspended: int = 0
    terminated: int = 0


class AgentPool:
    """Manages a pool of agents with resource tracking."""

    def __init__(self, max_agents: int = 20):
        self.max_agents = max_agents
        self._agents: dict[str, Agent] = {}

    def add(self, agent: Agent) -> None:
        if len(self._agents) >= self.max_agents:
            # Evict oldest idle agent
            idle = [a for a in self._agents.values() if a.status == AgentStatus.IDLE]
            if idle:
                oldest = min(idle, key=lambda a: a._created_at)
                oldest.status = AgentStatus.TERMINATED
                del self._agents[oldest.id]
            else:
                raise RuntimeError(f"Agent pool full ({self.max_agents}), no idle agents to evict")
        self._agents[agent.id] = agent

    def get(self, agent_id: str) -> Agent | None:
        return self._agents.get(agent_id)

    def remove(self, agent_id: str) -> Agent | None:
        agent = self._agents.pop(agent_id, None)
        if agent:
            agent.status = AgentStatus.TERMINATED
        return agent

    def find_by_soul(self, soul_id: str) -> list[Agent]:
        return [a for a in self._agents.values() if a.soul and a.soul.id == soul_id]

    def find_idle(self) -> list[Agent]:
        return [a for a in self._agents.values() if a.status == AgentStatus.IDLE]

    def suspend_idle(self, max_idle_seconds: float = 600) -> int:
        """Suspend agents idle for too long. Returns count suspended."""
        now = time.time()
        count = 0
        for a in self._agents.values():
            if a.status == AgentStatus.IDLE and a._session:
                if now - a._session.last_active > max_idle_seconds:
                    a.status = AgentStatus.SUSPENDED
                    count += 1
        return count

    def stats(self) -> PoolStats:
        s = PoolStats(total=len(self._agents))
        for a in self._agents.values():
            if a.status == AgentStatus.ACTIVE:
                s.active += 1
            elif a.status == AgentStatus.IDLE:
                s.idle += 1
            elif a.status == AgentStatus.SUSPENDED:
                s.suspended += 1
            elif a.status == AgentStatus.TERMINATED:
                s.terminated += 1
        return s

    def list_all(self) -> list[dict]:
        return [a.to_dict() for a in self._agents.values()]
