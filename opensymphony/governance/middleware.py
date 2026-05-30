"""Governance Middleware — the onion layer that all actions pass through."""

from __future__ import annotations

import logging
import time
from typing import Any

from .defense import Action, ActionDecision, DefenseLayer, DefenseResult, RiskLevel
from .hitl import HITLManager, HITLStatus
from .human_safety import HumanSafetyPolicy
from .precedent import Precedent, PrecedentStore
from .voting import Vote, VotingMechanism, VotingResult

logger = logging.getLogger("symphony.governance")


class GovernanceMiddleware:
    """All agent actions pass through this middleware (before + after).

    Flow: Action → Defense(risk classify) → Precedent(match) → Voting(if needed) → HITL(if P0) → Allow/Deny

    P0-7: HumanSafetyPolicy is integrated here as part of the governance layer.
    When sender_type is "human", the HumanSafetyPolicy is consulted for differentiated
    safety decisions. This ensures human safety goes through the governance pipeline
    rather than being a separate system.
    """

    def __init__(
        self,
        defense: DefenseLayer | None = None,
        voting: VotingMechanism | None = None,
        precedents: PrecedentStore | None = None,
        hitl: HITLManager | None = None,
    ):
        self.defense = defense or DefenseLayer()
        self.voting = voting or VotingMechanism()
        self.precedents = precedents
        self.hitl = hitl or HITLManager()
        self.human_safety = HumanSafetyPolicy()

    def before_action(self, action: Action) -> DefenseResult:
        """Pre-action interception. Returns allow/deny/escalate."""
        # P0-7: Check human safety for human sender types
        sender_type = getattr(action, 'sender_type', 'ai')
        if sender_type == 'human':
            risk_level_str = "low"  # Default
            if hasattr(action, 'risk_level') and action.risk_level:
                risk_level_str = action.risk_level.value if hasattr(action.risk_level, 'value') else str(action.risk_level)
            safety_decision = self.human_safety.check_action(
                sender_type="human",
                action_risk_level=risk_level_str,
                action_description=f"{action.action_type} on {action.target}",
            )
            if not safety_decision.allowed:
                result = DefenseResult(
                    decision=ActionDecision.DENY,
                    risk_level=RiskLevel.P2,
                    reason=f"Human safety policy denied: {safety_decision.reason}",
                )
                return result
        # 1. Defense layer check
        result = self.defense.evaluate(action)

        if result.decision == ActionDecision.DENY:
            logger.warning(f"DENIED action {action.action_type} by {action.agent_id}: {result.reason}")
            return result

        # 2. Precedent matching
        if self.precedents:
            matching = self.precedents.search(query=action.action_type, limit=3)
            for prec in matching:
                if prec.approved and prec.conditions:
                    # Check if conditions apply
                    result.conditions.extend(prec.conditions)
                    result.precedent_id = prec.id
                    self.precedents.cite(prec.id)
                    break

        # 3. Escalation for P0
        if result.risk_level == RiskLevel.P0 and result.decision != ActionDecision.DENY:
            approval = self.hitl.request_approval(
                agent_id=action.agent_id,
                action_type=action.action_type,
                description=f"{action.action_type} on {action.target}",
                risk_level="p0",
                context={"action": action.action_type, "target": action.target},
            )
            if approval.status == HITLStatus.APPROVED:
                result.decision = ActionDecision.ALLOW
                result.reason += " (HITL approved)"
            else:
                result.decision = ActionDecision.ESCALATE
                result.reason += f" (pending HITL approval: {approval.id})"

        return result

    def after_action(self, action: Action, result: Any, success: bool) -> None:
        """Post-action audit. Records outcome."""
        if self.precedents and success and action.action_type in ("write", "exec", "delete", "send"):
            prec = Precedent(
                description=f"{action.action_type}:{action.target}",
                category="auto_recorded",
                approved=True,
                reasoning=f"Action by {action.agent_id} completed successfully",
            )
            self.precedents.store(prec)

    def hold_vote(self, proposal: str, votes: list[Vote]) -> VotingResult:
        """Conduct a vote on a proposal."""
        proposal_id = f"vote_{int(time.time())}"
        result = self.voting.tally(proposal_id, proposal, votes)

        # Store as precedent
        if self.precedents and result.is_approved():
            prec = Precedent(
                description=proposal,
                category="vote",
                approved=True,
                reasoning=f"Vote: {result.approve_count}A/{result.reject_count}R/{result.abstain_count}Ab",
            )
            self.precedents.store(prec)

        return result

    def health(self) -> dict:
        return {
            "defense_audit_entries": len(self.defense.audit_log),
            "precedents_count": self.precedents.count() if self.precedents else 0,
            "hitl": self.hitl.get_stats(),
        }
