"""HITL — Human-in-the-Loop escalation and approval."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class HITLStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


@dataclass
class ApprovalRequest:
    id: str = ""
    agent_id: str = ""
    action_type: str = ""
    description: str = ""
    risk_level: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    status: HITLStatus = HITLStatus.PENDING
    created_at: float = 0.0
    resolved_at: float = 0.0
    resolver: str = ""
    resolution_note: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex[:12]
        if not self.created_at:
            self.created_at = time.time()


class HITLManager:
    """Manages human approval requests."""

    def __init__(self, auto_approve_p2: bool = True, expiry_seconds: float = 3600):
        """
        Args:
            auto_approve_p2: automatically approve P2 (low risk) actions
            expiry_seconds: how long before a pending request expires
        """
        self.auto_approve_p2 = auto_approve_p2
        self.expiry_seconds = expiry_seconds
        self._pending: dict[str, ApprovalRequest] = {}
        self._resolved: list[ApprovalRequest] = []
        self._callbacks: list[Callable[[ApprovalRequest], None]] = []

    def request_approval(self, agent_id: str, action_type: str, description: str,
                         risk_level: str = "p1", context: dict | None = None) -> ApprovalRequest:
        req = ApprovalRequest(
            agent_id=agent_id,
            action_type=action_type,
            description=description,
            risk_level=risk_level,
            context=context or {},
        )

        # Auto-approve P2
        if risk_level == "p2" and self.auto_approve_p2:
            req.status = HITLStatus.APPROVED
            req.resolver = "auto_p2"
            req.resolved_at = time.time()
            self._resolved.append(req)
            return req

        self._pending[req.id] = req
        # Notify callbacks
        for cb in self._callbacks:
            try:
                cb(req)
            except Exception:
                pass
        return req

    def approve(self, request_id: str, resolver: str = "human", note: str = "") -> ApprovalRequest | None:
        req = self._pending.pop(request_id, None)
        if not req:
            return None
        req.status = HITLStatus.APPROVED
        req.resolver = resolver
        req.resolution_note = note
        req.resolved_at = time.time()
        self._resolved.append(req)
        return req

    def reject(self, request_id: str, resolver: str = "human", note: str = "") -> ApprovalRequest | None:
        req = self._pending.pop(request_id, None)
        if not req:
            return None
        req.status = HITLStatus.REJECTED
        req.resolver = resolver
        req.resolution_note = note
        req.resolved_at = time.time()
        self._resolved.append(req)
        return req

    def expire_old(self) -> int:
        """Expire requests older than expiry_seconds. Returns count expired."""
        now = time.time()
        expired = [
            rid for rid, req in self._pending.items()
            if now - req.created_at > self.expiry_seconds
        ]
        for rid in expired:
            req = self._pending.pop(rid)
            req.status = HITLStatus.EXPIRED
            req.resolved_at = now
            self._resolved.append(req)
        return len(expired)

    def on_request(self, callback: Callable[[ApprovalRequest], None]) -> None:
        self._callbacks.append(callback)

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def list_pending(self) -> list[dict]:
        return [
            {"id": r.id, "agent_id": r.agent_id, "action": r.action_type,
             "risk": r.risk_level, "description": r.description, "age_seconds": int(time.time() - r.created_at)}
            for r in self._pending.values()
        ]

    def get_stats(self) -> dict:
        return {
            "pending": len(self._pending),
            "approved": sum(1 for r in self._resolved if r.status == HITLStatus.APPROVED),
            "rejected": sum(1 for r in self._resolved if r.status == HITLStatus.REJECTED),
            "expired": sum(1 for r in self._resolved if r.status == HITLStatus.EXPIRED),
        }
