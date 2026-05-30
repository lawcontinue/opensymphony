"""WebSocket Bridge — bidirectional real-time bridge between clients and agents."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("symphony.bridge")


@dataclass
class BridgeMessage:
    """Standard message format for client ↔ agent communication."""
    id: str = ""
    source: str = ""  # "client" or "server"
    target: str = ""  # "client" or "server" or specific agent_id
    type: str = ""    # "command", "event", "query", "response", "notification"
    action: str = ""  # "chat", "create_agent", "task_complete", "vote", "alert"
    payload: dict[str, Any] = field(default_factory=dict)
    reply_to: str = ""
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex[:12]

    def to_json(self) -> str:
        return json.dumps({
            "id": self.id, "source": self.source, "target": self.target,
            "type": self.type, "action": self.action, "payload": self.payload,
            "reply_to": self.reply_to, "timestamp": self.timestamp,
        }, ensure_ascii=False)

    @classmethod
    def from_json(cls, data: str) -> BridgeMessage:
        d = json.loads(data)
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class BridgeHandler:
    """Server-side handler for WebSocket connections on Symphony side."""

    def __init__(self, kernel: Any):
        self.kernel = kernel
        self._connections: dict[str, Any] = {}  # conn_id → websocket
        self._pending_responses: dict[str, asyncio.Future] = {}
        self._subscribers: dict[str, list[Callable]] = {}  # event_type → handlers
        self._message_log: list[BridgeMessage] = []
        self._max_log = 500

    async def on_connect(self, websocket, conn_id: str = "") -> str:
        if not conn_id:
            conn_id = uuid.uuid4().hex[:8]
        self._connections[conn_id] = websocket
        logger.info(f"Bridge client connected: {conn_id}")

        # Send welcome
        await self._send(websocket, BridgeMessage(
            source="server", target="client", type="event",
            action="connected", payload={"conn_id": conn_id, "version": "0.1.0"},
        ))

    async def on_disconnect(self, conn_id: str) -> None:
        self._connections.pop(conn_id, None)
        logger.info(f"Bridge client disconnected: {conn_id}")

    async def on_message(self, conn_id: str, raw: str) -> None:
        try:
            msg = BridgeMessage.from_json(raw)
        except Exception as e:
            logger.error(f"Invalid bridge message: {e}")
            return

        self._log(msg)
        logger.debug(f"Bridge [{msg.type}/{msg.action}] from {msg.source}")

        # Route to handler
        handler = getattr(self, f"_handle_{msg.action}", None)
        if handler:
            response = await handler(msg) if asyncio.iscoroutinefunction(handler) else handler(msg)
            if response and conn_id in self._connections:
                await self._send(self._connections[conn_id], response)
        else:
            logger.warning(f"Unknown bridge action: {msg.action}")

    async def broadcast_event(self, action: str, payload: dict) -> None:
        """Broadcast an event to all connected clients."""
        msg = BridgeMessage(
            source="server", target="client", type="event",
            action=action, payload=payload,
        )
        self._log(msg)
        for ws in list(self._connections.values()):
            try:
                await self._send(ws, msg)
            except Exception as e:
                logger.error(f"Broadcast failed: {e}")

    async def _send(self, websocket, msg: BridgeMessage) -> None:
        await websocket.send_text(msg.to_json())

    # ── Action Handlers ──

    def _handle_chat(self, msg: BridgeMessage) -> BridgeMessage:
        """Client sends a chat request to an agent."""
        soul_id = msg.payload.get("soul_id")
        agent_id = msg.payload.get("agent_id")
        message = msg.payload.get("message", "")

        if agent_id:
            agent = self.kernel.get_agent(agent_id)
        elif soul_id:
            agents = self.kernel._pool.find_by_soul(soul_id)
            agent = agents[0] if agents else None
            if not agent:
                agent = self.kernel.create_agent(soul_id=soul_id)
        else:
            agent = self.kernel.create_agent()

        if not agent:
            return BridgeMessage(
                source="server", target=msg.source, type="response",
                action="chat_error", payload={"error": "Agent not found"},
                reply_to=msg.id,
            )

        try:
            response = agent.chat(message).content
            return BridgeMessage(
                source="server", target=msg.source, type="response",
                action="chat_response", payload={
                    "agent_id": agent.id,
                    "soul_name": agent.soul.name if agent.soul else agent.id,
                    "response": response,
                },
                reply_to=msg.id,
            )
        except Exception as e:
            return BridgeMessage(
                source="server", target=msg.source, type="response",
                action="chat_error", payload={"error": str(e)},
                reply_to=msg.id,
            )

    def _handle_create_agent(self, msg: BridgeMessage) -> BridgeMessage:
        soul_id = msg.payload.get("soul_id")
        agent = self.kernel.create_agent(soul_id=soul_id)
        return BridgeMessage(
            source="server", target=msg.source, type="response",
            action="agent_created", payload=agent.to_dict(),
            reply_to=msg.id,
        )

    def _handle_list_agents(self, msg: BridgeMessage) -> BridgeMessage:
        return BridgeMessage(
            source="server", target=msg.source, type="response",
            action="agent_list", payload={"agents": self.kernel.list_agents()},
            reply_to=msg.id,
        )

    def _handle_list_souls(self, msg: BridgeMessage) -> BridgeMessage:
        return BridgeMessage(
            source="server", target=msg.source, type="response",
            action="soul_list", payload={"souls": self.kernel.list_souls()},
            reply_to=msg.id,
        )

    def _handle_health(self, msg: BridgeMessage) -> BridgeMessage:
        return BridgeMessage(
            source="server", target=msg.source, type="response",
            action="health", payload=self.kernel.health(),
            reply_to=msg.id,
        )

    def _handle_vote(self, msg: BridgeMessage) -> BridgeMessage:
        from ..governance.voting import Vote, VoteDecision
        votes_data = msg.payload.get("votes", [])
        votes = [Vote(voter_id=v["voter_id"], decision=VoteDecision(v["decision"]),
                       reasoning=v.get("reasoning", "")) for v in votes_data]
        result = self.kernel._governance.hold_vote(msg.payload.get("proposal", ""), votes)
        return BridgeMessage(
            source="server", target=msg.source, type="response",
            action="vote_result", payload=result.to_dict(),
            reply_to=msg.id,
        )

    def _handle_send_message(self, msg: BridgeMessage) -> BridgeMessage:
        """Send message from one agent to another (cross-system routing)."""
        msg.payload.get("from_agent", msg.source)
        to_agent = msg.payload.get("to_agent", "")
        content = msg.payload.get("content", "")

        agent = self.kernel.get_agent(to_agent)
        if not agent:
            return BridgeMessage(
                source="server", target=msg.source, type="response",
                action="message_error", payload={"error": f"Agent {to_agent} not found"},
                reply_to=msg.id,
            )

        results = agent.send_message(to_agent, content)
        return BridgeMessage(
            source="server", target=msg.source, type="response",
            action="message_delivered", payload={"results": [str(r) for r in results]},
            reply_to=msg.id,
        )

    def _log(self, msg: BridgeMessage) -> None:
        self._message_log.append(msg)
        if len(self._message_log) > self._max_log:
            self._message_log = self._message_log[-self._max_log:]

    def get_stats(self) -> dict:
        return {
            "connections": len(self._connections),
            "messages_logged": len(self._message_log),
        }


class BridgeClient:
    """Client-side connector for external client to connect via WebSocket."""

    def __init__(self, uri: str = "ws://localhost:8000/bridge"):
        self.uri = uri
        self._ws = None
        self._handlers: dict[str, list[Callable]] = {}
        self._pending: dict[str, asyncio.Future] = {}
        self._connected = False

    def on(self, action: str, handler: Callable) -> None:
        if action not in self._handlers:
            self._handlers[action] = []
        self._handlers[action].append(handler)

    async def connect(self) -> None:
        import websockets
        self._ws = await websockets.connect(self.uri)
        self._connected = True
        logger.info(f"Bridge connected to {self.uri}")

    async def send(self, action: str, payload: dict, msg_type: str = "command") -> BridgeMessage:
        msg = BridgeMessage(source="client", target="server", type=msg_type,
                            action=action, payload=payload)
        if self._ws:
            await self._ws.send(msg.to_json())
        return msg

    async def send_and_wait(self, action: str, payload: dict, timeout: float = 30.0) -> BridgeMessage | None:
        msg = await self.send(action, payload)
        future = asyncio.get_event_loop().create_future()
        self._pending[msg.id] = future
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(msg.id, None)
            return None

    async def listen(self) -> None:
        if not self._ws:
            return
        async for raw in self._ws:
            try:
                msg = BridgeMessage.from_json(raw)
            except Exception:
                continue

            # Check if it's a response to pending request
            if msg.reply_to and msg.reply_to in self._pending:
                future = self._pending.pop(msg.reply_to)
                if not future.done():
                    future.set_result(msg)
                continue

            # Dispatch to handlers
            for handler in self._handlers.get(msg.action, []):
                try:
                    if asyncio.iscoroutinefunction(handler):
                        await handler(msg)
                    else:
                        handler(msg)
                except Exception as e:
                    logger.error(f"Bridge handler error: {e}")

    async def disconnect(self) -> None:
        if self._ws:
            await self._ws.close()
            self._connected = False
