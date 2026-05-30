"""HumanSafety — differentiated safety policies for human-in-the-loop.

P0-7: This module is called by GovernanceMiddleware, not used independently.
P1-2: sender_type is used only for audit logging, NOT for routing decisions.
      The safety layer is an exception to the pipeline philosophy — it does not
      perform permission checks at the pipeline layer.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger("symphony.human_safety")


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class DecisionType(str, Enum):
    AUTO = "auto"
    EXPLICIT = "explicit"
    DOUBLE_CONFIRM = "double_confirm"


@dataclass
class SafetyDecision:
    """Result of a safety policy check."""
    allowed: bool
    decision_type: DecisionType
    reason: str = ""
    confirm_token: str | None = None
    audit_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


class HumanSafetyPolicy:
    """Differentiated safety policies based on sender type and risk level.

    Strategies:
    - agent_to_agent: auto_authorize (fast path)
    - human_to_agent: explicit_authorize (audit log)
    - human_to_high_risk: double_confirm (confirmation token)
    """

    TIMEOUT_CONFIG: dict[str, int] = {
        "agent_vote": 5,
        "human_vote": 86400,
    }

    AUTH_STRATEGIES: dict[str, str] = {
        "agent_to_agent": "auto",
        "human_to_agent": "explicit",
        "human_to_high_risk": "double_confirm",
    }

    def __init__(self):
        self._pending_confirmations: dict[str, dict[str, Any]] = {}
        self._audit_log: list[dict[str, Any]] = []
        self._confirm_ttl = 300  # P1-5: 5 minutes
        self._audit_file: Path | None = None

    def check_action(
        self,
        sender_type: str,
        action_risk_level: str | RiskLevel = RiskLevel.LOW,
        action_description: str = "",
    ) -> SafetyDecision:
        """Check whether an action is allowed based on sender type and risk.

        Args:
            sender_type: "ai" or "human"
            action_risk_level: "low", "medium", "high", "critical"
            action_description: Human-readable description of the action.

        Returns:
            SafetyDecision with allow/deny and required confirmation level.
        """
        if isinstance(action_risk_level, str):
            action_risk_level = RiskLevel(action_risk_level)

        if sender_type == "ai":
            return self._check_ai_action(action_risk_level, action_description)
        elif sender_type == "human":
            return self._check_human_action(action_risk_level, action_description)
        else:
            return SafetyDecision(
                allowed=False,
                decision_type=DecisionType.EXPLICIT,
                reason=f"Unknown sender_type: {sender_type}",
            )

    def _check_ai_action(self, risk_level: RiskLevel, description: str) -> SafetyDecision:
        """AI agents: auto-authorize for low/medium, explicit for high+."""
        if risk_level in (RiskLevel.LOW, RiskLevel.MEDIUM):
            self._log_audit("ai", risk_level.value, DecisionType.AUTO, True, description)
            return SafetyDecision(
                allowed=True,
                decision_type=DecisionType.AUTO,
                reason="auto_authorize: agent_to_agent",
            )
        # High/critical from AI needs explicit approval
        self._log_audit("ai", risk_level.value, DecisionType.EXPLICIT, False, description)
        return SafetyDecision(
            allowed=False,
            decision_type=DecisionType.EXPLICIT,
            reason=f"AI agent action at {risk_level.value} risk requires explicit authorization",
        )

    def _check_human_action(self, risk_level: RiskLevel, description: str) -> SafetyDecision:
        """Human actions: explicit authorize for low/medium, double-confirm for high+."""
        if risk_level in (RiskLevel.LOW, RiskLevel.MEDIUM):
            self._log_audit("human", risk_level.value, DecisionType.EXPLICIT, True, description)
            return SafetyDecision(
                allowed=True,
                decision_type=DecisionType.EXPLICIT,
                reason="explicit_authorize: human_to_agent",
            )
        # High/critical from human requires double confirmation
        token = uuid.uuid4().hex[:16]
        self._pending_confirmations[token] = {
            "risk_level": risk_level.value,
            "description": description,
            "created_at": time.time(),
        }
        self._log_audit("human", risk_level.value, DecisionType.DOUBLE_CONFIRM, False, description)
        return SafetyDecision(
            allowed=False,
            decision_type=DecisionType.DOUBLE_CONFIRM,
            reason=f"double_confirm required for {risk_level.value} risk action",
            confirm_token=token,
        )

    def confirm_action(self, confirm_token: str) -> SafetyDecision:
        """Confirm a pending double-confirm action.

        Args:
            confirm_token: The token from the original SafetyDecision.

        Returns:
            SafetyDecision with allowed=True if token is valid and not expired.
        """
        self._cleanup_expired()  # P1-5
        pending = self._pending_confirmations.pop(confirm_token, None)
        if not pending:
            return SafetyDecision(
                allowed=False,
                decision_type=DecisionType.DOUBLE_CONFIRM,
                reason="Invalid or expired confirmation token",
            )
        # P1-5: Check TTL
        if time.time() - pending["created_at"] > self._confirm_ttl:
            return SafetyDecision(
                allowed=False,
                decision_type=DecisionType.DOUBLE_CONFIRM,
                reason="Confirmation token has expired",
            )
        self._log_audit("human", pending["risk_level"], DecisionType.DOUBLE_CONFIRM, True, pending["description"])
        return SafetyDecision(
            allowed=True,
            decision_type=DecisionType.DOUBLE_CONFIRM,
            reason="Action confirmed by human",
        )

    def _cleanup_expired(self) -> None:
        """P1-5: Remove expired confirmation tokens."""
        now = time.time()
        expired = [
            token for token, data in self._pending_confirmations.items()
            if now - data["created_at"] > self._confirm_ttl
        ]
        for token in expired:
            del self._pending_confirmations[token]

    def _log_audit(
        self, sender_type: str, risk_level: str, decision_type: DecisionType,
        allowed: bool, description: str,
    ) -> None:
        entry = {
            "sender_type": sender_type,
            "risk_level": risk_level,
            "decision_type": decision_type.value,
            "allowed": allowed,
            "description": description[:200],
            "timestamp": time.time(),
        }
        self._audit_log.append(entry)
        if len(self._audit_log) > 1000:
            self._audit_log = self._audit_log[-1000:]
        # P1-4: Persist to file
        self._persist_audit(entry)

    def _persist_audit(self, entry: dict[str, Any]) -> None:
        """P1-4: Write audit entry to file."""
        if self._audit_file is None:
            self._audit_file = Path("data/audit_human_safety.jsonl")
        try:
            self._audit_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._audit_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except Exception as e:
            logger.warning(f"Failed to persist audit: {e}")

    def get_audit_log(self, limit: int = 20) -> list[dict[str, Any]]:
        return self._audit_log[-limit:]

    def get_timeout(self, key: str) -> int:
        """Get timeout config value."""
        return self.TIMEOUT_CONFIG.get(key, 60)
