"""Session — manage agent sessions with cross-session memory recall."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from .memory.l1 import L1Memory
from .memory.l2 import Experience, L2Memory
from .memory.l3 import L3Memory


@dataclass
class Session:
    """A session ties an agent to persistent memory across conversations."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    agent_id: str = ""
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)

    # Memory tiers
    l1: L1Memory = field(default_factory=L1Memory)
    _l2: L2Memory | None = field(default=None, repr=False)
    _l3: L3Memory | None = field(default=None, repr=False)

    def attach_storage(self, l2: L2Memory, l3: L3Memory) -> None:
        """Attach persistent storage backends."""
        self._l2 = l2
        self._l3 = l3

    def add_message(self, role: str, content: str) -> None:
        """Add message to working memory and optionally persist."""
        self.l1.add(role, content)
        self.last_active = time.time()

        if self._l3:
            self._l3.append("message", {"role": role, "content": content}, agent_id=self.agent_id)

    def recall(self, query: str, limit: int = 5) -> list[Experience]:
        """Search past experiences relevant to a query."""
        if not self._l2:
            return []
        return self._l2.search(agent_id=self.agent_id, query=query, limit=limit)

    def save_experience(self, category: str, content: str, metadata: dict | None = None) -> str | None:
        """Save an experience for future recall."""
        if not self._l2:
            return None
        exp = Experience(
            id="",
            agent_id=self.agent_id,
            category=category,
            content=content,
            metadata=metadata,
        )
        exp_id = self._l2.store(exp)

        if self._l3:
            self._l3.append("experience_saved", {"exp_id": exp_id, "category": category}, agent_id=self.agent_id)

        return exp_id

    def get_context_messages(self, max_tokens: int = 4000) -> list[dict[str, str]]:
        """Get messages for LLM context, respecting token budget."""
        messages = self.l1.get()
        total = 0
        result = []
        for m in reversed(messages):
            cjk = sum(1 for c in m["content"] if "\u4e00" <= c <= "\u9fff")
            other = len(m["content"]) - cjk
            est = int(cjk / 2 + other / 4)
            if total + est > max_tokens and result:
                break
            result.insert(0, m)
            total += est
        return result

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "created_at": self.created_at,
            "last_active": self.last_active,
            "l1": self.l1.to_dict(),
            "l2_count": self._l2.count(self.agent_id) if self._l2 else 0,
        }
