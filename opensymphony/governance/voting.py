"""Voting — lightweight multi-agent voting mechanism."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class VoteDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    ABSTAIN = "abstain"


class FinalDecision(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    ESCALATED = "escalated"  # needs human review
    NO_QUORUM = "no_quorum"


@dataclass
class Vote:
    voter_id: str
    decision: VoteDecision
    reasoning: str = ""
    confidence: float = 1.0  # 0.0 - 1.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class VotingResult:
    proposal_id: str
    proposal: str
    votes: list[Vote] = field(default_factory=list)
    final_decision: FinalDecision = FinalDecision.NO_QUORUM
    approve_count: int = 0
    reject_count: int = 0
    abstain_count: int = 0
    timestamp: float = field(default_factory=time.time)

    def is_approved(self) -> bool:
        return self.final_decision == FinalDecision.APPROVED

    def to_dict(self) -> dict:
        return {
            "proposal_id": self.proposal_id,
            "final_decision": self.final_decision.value,
            "approve": self.approve_count,
            "reject": self.reject_count,
            "abstain": self.abstain_count,
            "votes": [{"voter": v.voter_id, "decision": v.decision.value} for v in self.votes],
        }


class VotingMechanism:
    """Simple majority voting with quorum and escalation."""

    def __init__(self, quorum: int = 2, approval_threshold: float = 0.6, veto_power: set[str] | None = None):
        """
        Args:
            quorum: minimum votes needed for a valid result
            approval_threshold: fraction of approve votes needed (excluding abstain)
            veto_power: set of agent IDs that have veto power (single reject = escalation)
        """
        self.quorum = quorum
        self.approval_threshold = approval_threshold
        self.veto_power = veto_power or set()

    def tally(self, proposal_id: str, proposal: str, votes: list[Vote]) -> VotingResult:
        result = VotingResult(proposal_id=proposal_id, proposal=proposal, votes=votes)

        result.approve_count = sum(1 for v in votes if v.decision == VoteDecision.APPROVE)
        result.reject_count = sum(1 for v in votes if v.decision == VoteDecision.REJECT)
        result.abstain_count = sum(1 for v in votes if v.decision == VoteDecision.ABSTAIN)

        decisive = result.approve_count + result.reject_count
        total = len(votes)

        # Check quorum
        if total < self.quorum:
            result.final_decision = FinalDecision.NO_QUORUM
            return result

        # Check veto
        for v in votes:
            if v.decision == VoteDecision.REJECT and v.voter_id in self.veto_power:
                result.final_decision = FinalDecision.ESCALATED
                return result

        # Majority threshold
        if decisive == 0:
            result.final_decision = FinalDecision.NO_QUORUM
            return result

        approval_rate = result.approve_count / decisive
        if approval_rate >= self.approval_threshold:
            result.final_decision = FinalDecision.APPROVED
        else:
            result.final_decision = FinalDecision.REJECTED

        return result
