"""EventBus — Agent communication via publish/subscribe."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("symphony.events")


class MessageType(str, Enum):
    REQUEST = "request"
    RESPONSE = "response"
    BROADCAST = "broadcast"
    VOTE = "vote"
    NOTIFICATION = "notification"


@dataclass
class AgentMessage:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    sender: str = ""
    receiver: str | list[str] = ""  # agent_id, name, or "*" for broadcast
    type: MessageType = MessageType.REQUEST
    content: Any = None
    priority: int = 0
    requires_vote: bool = False
    timestamp: float = field(default_factory=time.time)
    reply_to: str | None = None
    raw_input: str | None = None
    confidence: float = 1.0
    sender_type: str = "ai"  # "ai" | "human"


MessageHandler = Callable[[AgentMessage], Any]


class EventBus:
    """Pub/sub event bus with name alias support."""

    def __init__(self):
        self._subscribers: dict[str, list[MessageHandler]] = {}
        self._aliases: dict[str, str] = {}  # name → agent_id
        self._history: list[AgentMessage] = []
        self._max_history = 1000

    def subscribe(self, agent_id: str, handler: MessageHandler, aliases: list[str] | None = None) -> None:
        if agent_id not in self._subscribers:
            self._subscribers[agent_id] = []
        self._subscribers[agent_id].append(handler)
        if aliases:
            for alias in aliases:
                self._aliases[alias.lower()] = agent_id

    def unsubscribe(self, agent_id: str) -> None:
        self._subscribers.pop(agent_id, None)
        self._aliases = {k: v for k, v in self._aliases.items() if v != agent_id}

    def _resolve(self, name: str) -> str | None:
        if name in self._subscribers:
            return name
        return self._aliases.get(name.lower())

    def publish(self, message: AgentMessage) -> list[Any]:
        self._history.append(message)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        results = []
        receivers = message.receiver if isinstance(message.receiver, list) else [message.receiver]

        resolved_ids = set()
        for name in receivers:
            if name == "*":
                resolved_ids = set(self._subscribers.keys())
                break
            rid = self._resolve(name)
            if rid:
                resolved_ids.add(rid)

        for rid in resolved_ids:
            for handler in self._subscribers.get(rid, []):
                try:
                    results.append(handler(message))
                except Exception as e:
                    logger.error(f"Handler error for {rid}: {e}")
                    results.append(e)

        return results

    async def publish_async(self, message: AgentMessage) -> list[Any]:
        self._history.append(message)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        results = []
        receivers = message.receiver if isinstance(message.receiver, list) else [message.receiver]

        resolved_ids = set()
        for name in receivers:
            if name == "*":
                resolved_ids = set(self._subscribers.keys())
                break
            rid = self._resolve(name)
            if rid:
                resolved_ids.add(rid)

        for rid in resolved_ids:
            for handler in self._subscribers.get(rid, []):
                try:
                    if asyncio.iscoroutinefunction(handler):
                        results.append(await handler(message))
                    else:
                        results.append(handler(message))
                except Exception as e:
                    logger.error(f"Async handler error for {rid}: {e}")
                    results.append(e)

        return results

    def get_history(self, agent_id: str | None = None, limit: int = 20) -> list[AgentMessage]:
        if agent_id:
            resolved = self._resolve(agent_id) or agent_id
            filtered = [
                m for m in self._history
                if m.sender == resolved or m.sender == agent_id
                or self._resolve(m.receiver) == resolved
                or (isinstance(m.receiver, str) and m.receiver == agent_id)
                or (isinstance(m.receiver, list) and (agent_id in m.receiver or "*" in m.receiver))
            ]
            return filtered[-limit:]
        return self._history[-limit:]
