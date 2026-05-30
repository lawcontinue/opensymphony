"""Defense — risk classification and action interception."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RiskLevel(str, Enum):
    P0 = "p0"  # critical — must have human approval
    P1 = "p1"  # high — should have approval
    P2 = "p2"  # low — auto-approve


class ActionDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ESCALATE = "escalate"  # needs human review
    MODIFY = "modify"  # allowed with modifications


@dataclass
class Action:
    """An action that an agent wants to perform."""
    agent_id: str
    action_type: str  # "write", "exec", "delete", "send", "tool_call"
    target: str = ""  # what's being acted upon
    parameters: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DefenseResult:
    decision: ActionDecision
    risk_level: RiskLevel
    reason: str = ""
    conditions: list[str] = field(default_factory=list)
    precedent_id: str | None = None


class DefenseLayer:
    """Intercepts agent actions and applies risk-based controls."""

    # Risk classification rules
    HIGH_RISK_ACTIONS = {"delete", "exec:rm", "exec:format", "send:bulk", "force_push"}
    MEDIUM_RISK_ACTIONS = {"write:config", "exec:sudo", "send", "git_push", "handoff"}
    LOW_RISK_ACTIONS = {"read", "search", "list", "health", "handoff:reflector", "handoff:default"}

    def __init__(self, blocked_agents: set[str] | None = None):
        self.blocked_agents = blocked_agents or set()
        self._action_rules: list[tuple[str, RiskLevel, ActionDecision]] = []
        self.audit_log: list[dict] = []

    def add_rule(self, action_pattern: str, risk: RiskLevel, decision: ActionDecision) -> None:
        """Add a custom rule. action_pattern matches against 'action_type:target'."""
        self._action_rules.append((action_pattern, risk, decision))

    def evaluate(self, action: Action) -> DefenseResult:
        """Evaluate an action through defense rules."""
        # Blocked agent check
        if action.agent_id in self.blocked_agents:
            return self._audit(action, ActionDecision.DENY, RiskLevel.P0, "Agent is blocked")

        action_key = f"{action.action_type}:{action.target}" if action.target else action.action_type

        # Check custom rules first
        for pattern, risk, decision in self._action_rules:
            if self._match(pattern, action_key) or self._match(pattern, action.action_type):
                return self._audit(action, decision, risk, f"Matched rule: {pattern}")

        # Default risk classification
        risk = self._classify_risk(action)
        decision = ActionDecision.ALLOW if risk == RiskLevel.P2 else ActionDecision.ESCALATE

        return self._audit(action, decision, risk, f"Default classification: {risk.value}")

    def _classify_risk(self, action: Action) -> RiskLevel:
        action_key = f"{action.action_type}:{action.target}" if action.target else action.action_type

        # Pass 1: Check specific patterns (action_type:target) across all risk levels
        for patterns, risk in [
            (self.LOW_RISK_ACTIONS, RiskLevel.P2),
            (self.HIGH_RISK_ACTIONS, RiskLevel.P0),
            (self.MEDIUM_RISK_ACTIONS, RiskLevel.P1),
        ]:
            for pattern in patterns:
                if self._match(pattern, action_key):
                    return risk

        # Pass 2: Check generic action_type
        for pattern in self.HIGH_RISK_ACTIONS:
            if self._match(pattern, action.action_type):
                return RiskLevel.P0
        for pattern in self.MEDIUM_RISK_ACTIONS:
            if self._match(pattern, action.action_type):
                return RiskLevel.P1

        return RiskLevel.P2

    def _match(self, pattern: str, value: str) -> bool:
        if pattern == value:
            return True
        if ":" in pattern:
            p_type, p_target = pattern.split(":", 1)
            v_type, v_target = value.split(":", 1) if ":" in value else (value, "")
            if p_type == v_type and (not p_target or p_target == "*"):
                return True
        return False

    def _audit(self, action: Action, decision: ActionDecision, risk: RiskLevel, reason: str) -> DefenseResult:
        entry = {
            "ts": time.time(),
            "agent_id": action.agent_id,
            "action": action.action_type,
            "decision": decision.value,
            "risk": risk.value,
            "reason": reason,
        }
        self.audit_log.append(entry)
        return DefenseResult(decision=decision, risk_level=risk, reason=reason)
