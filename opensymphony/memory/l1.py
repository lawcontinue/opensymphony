"""Memory L1 — Working memory (current session context, in-memory)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class L1Memory:
    """In-memory working memory — fast, volatile, per-session."""

    messages: list[dict[str, str]] = field(default_factory=list)
    max_messages: int = 100  # ~4K tokens worth

    def add(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})
        self._trim()

    def get(self, last_n: int | None = None) -> list[dict[str, str]]:
        if last_n:
            return self.messages[-last_n:]
        return list(self.messages)

    def clear(self) -> None:
        self.messages.clear()

    def _trim(self) -> None:
        if len(self.messages) > self.max_messages:
            # Keep system message (if any) + last N
            system = [m for m in self.messages if m["role"] == "system"]
            non_system = [m for m in self.messages if m["role"] != "system"]
            keep = self.max_messages - len(system)
            self.messages = system + non_system[-keep:]

    def token_estimate(self) -> int:
        text = " ".join(m["content"] for m in self.messages)
        cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        other = len(text) - cjk
        return int(cjk / 2 + other / 4)

    def to_dict(self) -> dict:
        return {
            "messages_count": len(self.messages),
            "estimated_tokens": self.token_estimate(),
        }
