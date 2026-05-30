"""Phase 2 tests: Governance layer (Voting, Precedent, Defense, HITL, Middleware)."""


import pytest
from opensymphony.governance.defense import Action, ActionDecision, DefenseLayer, RiskLevel
from opensymphony.governance.hitl import HITLManager, HITLStatus
from opensymphony.governance.middleware import GovernanceMiddleware
from opensymphony.governance.precedent import Precedent, PrecedentStore
from opensymphony.governance.voting import FinalDecision, Vote, VoteDecision, VotingMechanism
from opensymphony.kernel import SymphonyKernel
from opensymphony.llm.router import BaseProvider, LLMRouter


class MockProvider(BaseProvider):
    def supports_model(self, m): return True
    def chat(self, model, messages, max_tokens, temperature, **kw):
        return "test", {"total_tokens": 10}


@pytest.fixture
def mock_router():
    r = LLMRouter()
    r.register_provider("m", MockProvider())
    r.routing = {"chat": [("m", "mm")]}
    return r


@pytest.fixture
def tmp_souls_dir(tmp_path):
    (tmp_path / "alice.txt").write_text("be alice")
    (tmp_path / "bob.txt").write_text("be bob")
    return tmp_path


# ── Voting ──

class TestVoting:
    def test_approve_majority(self):
        vm = VotingMechanism(quorum=2, approval_threshold=0.6)
        votes = [
            Vote(voter_id="a", decision=VoteDecision.APPROVE),
            Vote(voter_id="b", decision=VoteDecision.APPROVE),
            Vote(voter_id="c", decision=VoteDecision.REJECT),
        ]
        result = vm.tally("p1", "test proposal", votes)
        assert result.is_approved()
        assert result.approve_count == 2

    def test_reject_minority(self):
        vm = VotingMechanism(quorum=2, approval_threshold=0.6)
        votes = [
            Vote(voter_id="a", decision=VoteDecision.APPROVE),
            Vote(voter_id="b", decision=VoteDecision.REJECT),
            Vote(voter_id="c", decision=VoteDecision.REJECT),
        ]
        result = vm.tally("p2", "bad proposal", votes)
        assert result.final_decision == FinalDecision.REJECTED

    def test_no_quorum(self):
        vm = VotingMechanism(quorum=3)
        votes = [Vote(voter_id="a", decision=VoteDecision.APPROVE)]
        result = vm.tally("p3", "lone proposal", votes)
        assert result.final_decision == FinalDecision.NO_QUORUM

    def test_veto_power(self):
        vm = VotingMechanism(quorum=2, veto_power={"crit"})
        votes = [
            Vote(voter_id="a", decision=VoteDecision.APPROVE),
            Vote(voter_id="crit", decision=VoteDecision.REJECT),
        ]
        result = vm.tally("p4", "vetoed proposal", votes)
        assert result.final_decision == FinalDecision.ESCALATED

    def test_abstain_doesnt_count(self):
        vm = VotingMechanism(quorum=2, approval_threshold=0.5)
        votes = [
            Vote(voter_id="a", decision=VoteDecision.APPROVE),
            Vote(voter_id="b", decision=VoteDecision.ABSTAIN),
            Vote(voter_id="c", decision=VoteDecision.ABSTAIN),
        ]
        result = vm.tally("p5", "mostly abstain", votes)
        assert result.is_approved()  # 1/1 decisive = 100% > 50%


# ── Precedent ──

class TestPrecedent:
    def test_store_and_search(self, tmp_path):
        store = PrecedentStore(tmp_path / "prec.db")
        store.store(Precedent(description="Allow read ops", category="security", approved=True))
        store.store(Precedent(description="Block delete ops", category="security", approved=False))

        results = store.search(query="read")
        assert len(results) == 1
        assert results[0].approved
        store.close()

    def test_citation(self, tmp_path):
        store = PrecedentStore(tmp_path / "prec.db")
        pid = store.store(Precedent(description="Test precedent"))
        store.cite(pid)
        results = store.search(query="Test")
        assert results[0].citation_count == 1
        store.close()

    def test_category_filter(self, tmp_path):
        store = PrecedentStore(tmp_path / "prec.db")
        store.store(Precedent(description="A", category="security"))
        store.store(Precedent(description="B", category="architecture"))
        assert len(store.search(category="security")) == 1
        store.close()


# ── Defense ──

class TestDefense:
    def test_low_risk_allowed(self):
        defense = DefenseLayer()
        action = Action(agent_id="a1", action_type="read", target="file.txt")
        result = defense.evaluate(action)
        assert result.decision == ActionDecision.ALLOW
        assert result.risk_level == RiskLevel.P2

    def test_high_risk_escalated(self):
        defense = DefenseLayer()
        action = Action(agent_id="a1", action_type="delete", target="important.txt")
        result = defense.evaluate(action)
        assert result.decision == ActionDecision.ESCALATE
        assert result.risk_level == RiskLevel.P0

    def test_blocked_agent_denied(self):
        defense = DefenseLayer(blocked_agents={"bad_actor"})
        action = Action(agent_id="bad_actor", action_type="read")
        result = defense.evaluate(action)
        assert result.decision == ActionDecision.DENY

    def test_custom_rule(self):
        defense = DefenseLayer()
        defense.add_rule("exec:dangerous", RiskLevel.P0, ActionDecision.DENY)
        action = Action(agent_id="a1", action_type="exec", target="dangerous")
        result = defense.evaluate(action)
        assert result.decision == ActionDecision.DENY


# ── HITL ──

class TestHITL:
    def test_auto_approve_p2(self):
        hitl = HITLManager(auto_approve_p2=True)
        req = hitl.request_approval("a1", "read", "reading file", risk_level="p2")
        assert req.status == HITLStatus.APPROVED

    def test_p1_pending(self):
        hitl = HITLManager(auto_approve_p2=True)
        req = hitl.request_approval("a1", "write", "writing config", risk_level="p1")
        assert req.status == HITLStatus.PENDING
        assert hitl.pending_count == 1

    def test_approve_flow(self):
        hitl = HITLManager()
        req = hitl.request_approval("a1", "send", "sending email", risk_level="p0")
        approved = hitl.approve(req.id, resolver="human", note="looks good")
        assert approved.status == HITLStatus.APPROVED
        assert hitl.pending_count == 0

    def test_reject_flow(self):
        hitl = HITLManager()
        req = hitl.request_approval("a1", "delete", "deleting file", risk_level="p0")
        rejected = hitl.reject(req.id, resolver="human", note="too risky")
        assert rejected.status == HITLStatus.REJECTED

    def test_expiry(self):
        hitl = HITLManager(expiry_seconds=-1)  # already expired
        hitl.request_approval("a1", "exec", "run script", risk_level="p1")
        expired = hitl.expire_old()
        assert expired == 1
        assert hitl.pending_count == 0

    def test_callback(self):
        hitl = HITLManager()
        notifications = []
        hitl.on_request(lambda r: notifications.append(r.id))
        req = hitl.request_approval("a1", "send", "test", risk_level="p1")
        assert len(notifications) == 1
        assert notifications[0] == req.id


# ── Middleware Integration ──

class TestGovernanceMiddleware:
    def test_full_flow_low_risk(self, tmp_path):
        mw = GovernanceMiddleware(
            precedents=PrecedentStore(tmp_path / "prec.db"),
        )
        action = Action(agent_id="a1", action_type="read", target="data.txt")
        result = mw.before_action(action)
        assert result.decision == ActionDecision.ALLOW

    def test_full_flow_high_risk(self, tmp_path):
        mw = GovernanceMiddleware(
            precedents=PrecedentStore(tmp_path / "prec.db"),
        )
        action = Action(agent_id="a1", action_type="delete", target="important.txt")
        result = mw.before_action(action)
        # P0 should be escalated to HITL
        assert result.risk_level == RiskLevel.P0
        assert result.decision == ActionDecision.ESCALATE

    def test_vote_and_store_precedent(self, tmp_path):
        mw = GovernanceMiddleware(
            precedents=PrecedentStore(tmp_path / "prec.db"),
        )
        votes = [
            Vote(voter_id="alice", decision=VoteDecision.APPROVE),
            Vote(voter_id="bob", decision=VoteDecision.APPROVE),
        ]
        result = mw.hold_vote("Allow night deployments", votes)
        assert result.is_approved()
        # Should be stored as precedent
        precs = mw.precedents.search(query="night")
        assert len(precs) == 1
        assert precs[0].approved
        mw.precedents.close()

    def test_after_action_creates_precedent(self, tmp_path):
        mw = GovernanceMiddleware(
            precedents=PrecedentStore(tmp_path / "prec.db"),
        )
        action = Action(agent_id="a1", action_type="write", target="config.yaml")
        mw.after_action(action, result="ok", success=True)
        assert mw.precedents.count() == 1
        mw.precedents.close()


# ── Kernel + Governance Integration ──

class TestKernelGovernance:
    def test_kernel_starts_with_governance(self, mock_router, tmp_souls_dir, tmp_path):
        kernel = SymphonyKernel(souls_dir=tmp_souls_dir, router=mock_router, data_dir=tmp_path / "data")
        kernel.start()
        h = kernel.health()
        assert "governance" in h
        assert h["governance"]["precedents_count"] == 0
        kernel.stop()

    def test_governance_via_http(self, mock_router, tmp_souls_dir, tmp_path):
        kernel = SymphonyKernel(souls_dir=tmp_souls_dir, router=mock_router, data_dir=tmp_path / "data")
        kernel.start()

        from fastapi.testclient import TestClient
        from opensymphony.gateway.http import create_app
        client = TestClient(create_app(kernel))

        # Check governance health
        r = client.get("/governance/health")
        assert r.status_code == 200
        assert "defense_audit_entries" in r.json()

        # HITL pending
        r = client.get("/governance/hitl/pending")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

        kernel.stop()

    def test_three_agents_vote(self, mock_router, tmp_souls_dir, tmp_path):
        """Three agents vote on a proposal through governance middleware."""
        kernel = SymphonyKernel(souls_dir=tmp_souls_dir, router=mock_router, data_dir=tmp_path / "data")
        kernel.start()

        # Create 3 agents
        a1 = kernel.create_agent(soul_id="alice")
        a2 = kernel.create_agent(soul_id="bob")

        # Hold a vote
        votes = [
            Vote(voter_id=a1.id, decision=VoteDecision.APPROVE, reasoning="looks good"),
            Vote(voter_id=a2.id, decision=VoteDecision.APPROVE, reasoning="agreed"),
        ]
        result = kernel._governance.hold_vote("Deploy to production", votes)
        assert result.is_approved()

        # Verify precedent created
        assert kernel._governance.precedents.count() == 1

        kernel.stop()
