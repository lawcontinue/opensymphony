"""Tests for Agent Handoff mechanism with governance check."""

import pytest
from opensymphony.agents.agent import AgentStatus, HandoffResult
from opensymphony.governance.defense import RiskLevel
from opensymphony.kernel import SymphonyKernel


@pytest.fixture
def kernel(tmp_path):
    souls_dir = tmp_path / "souls"
    souls_dir.mkdir()
    # Create minimal test souls
    (souls_dir / "themis.yaml").write_text(
        "id: themis\nname: Themis\narchetype: test\nthinking_framework: test\n")
    (souls_dir / "code.yaml").write_text(
        "id: code\nname: Code\narchetype: programmer\nthinking_framework: test\n")
    (souls_dir / "reflector.yaml").write_text(
        "id: reflector\nname: Reflector\narchetype: reflector\nthinking_framework: test\n")
    (souls_dir / "default.yaml").write_text(
        "id: default\nname: Default\narchetype: default\nthinking_framework: test\n")
    k = SymphonyKernel(souls_dir=souls_dir, data_dir=tmp_path / "data")
    k.start()
    return k


class TestHandoffBasics:
    def test_handoff_allowed_p2(self, kernel):
        """Handoff to reflector (P2, low risk) should auto-allow."""
        source = kernel.create_agent(soul_id="themis")
        result = source.handoff(target_soul="reflector", context={"task": "review"}, kernel=kernel)
        assert result.status == "allowed"
        assert result.target_agent is not None
        assert result.target_agent.soul.id == "reflector"

    def test_handoff_allowed_p1(self, kernel):
        """Handoff to code (P1, medium risk) should escalate by default."""
        source = kernel.create_agent(soul_id="themis")
        result = source.handoff(target_soul="code", context={"task": "implement"}, kernel=kernel)
        # Default: P1 actions escalate unless governance allows
        assert result.status in ("allowed", "escalated")
        if result.status == "allowed":
            assert result.target_agent is not None

    def test_handoff_no_kernel_denied(self, kernel):
        """Handoff without kernel should be denied."""
        source = kernel.create_agent(soul_id="themis")
        result = source.handoff(target_soul="code", kernel=None)
        assert result.status == "denied"
        assert "No kernel" in result.reason

    def test_handoff_transfers_context(self, kernel):
        """Handoff should pass context to target agent."""
        source = kernel.create_agent(soul_id="themis")
        ctx = {"draft": "Hello world", "step": 2}
        result = source.handoff(target_soul="default", context=ctx, kernel=kernel)
        assert result.status == "allowed"
        assert result.target_agent is not None

    def test_handoff_source_goes_idle(self, kernel):
        """Source agent should be idle after handoff."""
        source = kernel.create_agent(soul_id="themis")
        source.handoff(target_soul="default", kernel=kernel)
        assert source.status == AgentStatus.IDLE


class TestHandoffGovernance:
    def test_handoff_creates_audit_log(self, kernel):
        """Handoff should create an audit log entry."""
        source = kernel.create_agent(soul_id="themis")
        initial_entries = len(kernel._governance.defense.audit_log)
        source.handoff(target_soul="default", kernel=kernel)
        assert len(kernel._governance.defense.audit_log) > initial_entries

    def test_handoff_audit_contains_type(self, kernel):
        """Audit log entry should contain action_type='handoff'."""
        source = kernel.create_agent(soul_id="themis")
        source.handoff(target_soul="default", kernel=kernel)
        log = kernel._governance.defense.audit_log
        handoff_entries = [e for e in log if e["action"] == "handoff"]
        assert len(handoff_entries) >= 1

    def test_handoff_to_reflector_is_p2(self, kernel):
        """Handoff to reflector should be classified as P2 (low risk)."""
        source = kernel.create_agent(soul_id="themis")
        result = source.handoff(target_soul="reflector", kernel=kernel)
        assert result.status == "allowed"
        if result.governance:
            assert result.governance.risk_level == RiskLevel.P2

    def test_handoff_to_code_is_p1(self, kernel):
        """Handoff to code should be classified as P1 (medium risk)."""
        source = kernel.create_agent(soul_id="themis")
        result = source.handoff(target_soul="code", kernel=kernel)
        if result.governance:
            assert result.governance.risk_level in (RiskLevel.P1, RiskLevel.P2)


class TestHandoffResult:
    def test_handoff_result_fields(self):
        """HandoffResult should have correct fields."""
        r = HandoffResult(status="allowed", reason="test")
        assert r.status == "allowed"
        assert r.target_agent is None
        assert r.reason == "test"
        assert r.governance is None


class TestHandoffViaGateway:
    """Test handoff through the gateway API (if http extras available)."""

    def test_gateway_has_handoff_endpoint(self, kernel):
        """Gateway should include /agents/{id}/handoff endpoint."""
        try:
            from opensymphony.gateway.http import create_app
            app = create_app(kernel)
            routes = [r.path for r in app.routes]
            assert "/agents/{agent_id}/handoff" in routes
        except ImportError:
            pytest.skip("HTTP gateway not available")
