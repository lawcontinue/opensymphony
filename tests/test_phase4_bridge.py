"""Phase 4 tests: WebSocket Bridge, Soul YAML Spec, Gateway adapters."""

import pytest
from opensymphony.agents.soul import Soul, load_souls_dir
from opensymphony.agents.soul_compiler import compile_soul
from opensymphony.agents.soul_spec import estimate_soul_tokens, soul_to_yaml_dict, validate_soul_yaml
from opensymphony.gateway.bridge import BridgeHandler, BridgeMessage
from opensymphony.kernel import SymphonyKernel
from opensymphony.llm.router import BaseProvider, LLMRouter


class MockProvider(BaseProvider):
    def supports_model(self, m): return True
    def chat(self, model, messages, max_tokens, temperature, **kw):
        return "test response", {"total_tokens": 10}


@pytest.fixture
def mock_router():
    r = LLMRouter()
    r.register_provider("m", MockProvider())
    r.routing = {"chat": [("m", "mm")]}
    return r


@pytest.fixture
def tmp_souls_dir(tmp_path):
    (tmp_path / "alice.txt").write_text("You are Alice, a helpful assistant.", encoding="utf-8")
    (tmp_path / "bob.txt").write_text("You are Bob, a creative writer.", encoding="utf-8")
    return tmp_path


@pytest.fixture
def yaml_souls_dir(tmp_path):
    (tmp_path / "themis.yaml").write_text(
        "id: themis\nname: 忒弥斯\narchetype: 预见型架构师\n"
        "thinking_framework: 风险先于机会\ncommunication_style: 简洁\n"
        "values:\n  - 数据驱动\nveto_conditions:\n  - 不撒谎\ntools_whitelist:\n  - read\n",
        encoding="utf-8",
    )
    (tmp_path / "crit.yaml").write_text(
        "id: crit\nname: Crit\nthinking_framework: 每个假设都需要验证\n",
        encoding="utf-8",
    )
    return tmp_path


# ── BridgeMessage ──

class TestBridgeMessage:
    def test_serialization(self):
        msg = BridgeMessage(source="openclaw", target="symphony", type="command",
                            action="chat", payload={"message": "hello"})
        json_str = msg.to_json()
        parsed = BridgeMessage.from_json(json_str)
        assert parsed.source == "openclaw"
        assert parsed.action == "chat"
        assert parsed.payload["message"] == "hello"

    def test_auto_id(self):
        msg = BridgeMessage()
        assert len(msg.id) == 12


# ── BridgeHandler ──

class TestBridgeHandler:
    def _make_kernel(self, mock_router, tmp_souls_dir, tmp_path):
        kernel = SymphonyKernel(souls_dir=tmp_souls_dir, router=mock_router, data_dir=tmp_path / "data")
        kernel.start()
        return kernel

    def test_handle_health(self, mock_router, tmp_souls_dir, tmp_path):
        kernel = self._make_kernel(mock_router, tmp_souls_dir, tmp_path)
        handler = BridgeHandler(kernel)
        msg = BridgeMessage(source="openclaw", action="health")
        response = handler._handle_health(msg)
        assert response.payload["status"] == "running"
        kernel.stop()

    def test_handle_chat_create_agent(self, mock_router, tmp_souls_dir, tmp_path):
        kernel = self._make_kernel(mock_router, tmp_souls_dir, tmp_path)
        handler = BridgeHandler(kernel)
        msg = BridgeMessage(source="openclaw", action="chat",
                            payload={"soul_id": "alice", "message": "Hello"})
        response = handler._handle_chat(msg)
        assert response.action == "chat_response"
        assert "test response" in response.payload["response"]
        kernel.stop()

    def test_handle_chat_existing_agent(self, mock_router, tmp_souls_dir, tmp_path):
        kernel = self._make_kernel(mock_router, tmp_souls_dir, tmp_path)
        handler = BridgeHandler(kernel)
        agent = kernel.create_agent(soul_id="alice")

        msg = BridgeMessage(source="openclaw", action="chat",
                            payload={"agent_id": agent.id, "message": "Hi again"})
        response = handler._handle_chat(msg)
        assert response.action == "chat_response"
        kernel.stop()

    def test_handle_list_agents(self, mock_router, tmp_souls_dir, tmp_path):
        kernel = self._make_kernel(mock_router, tmp_souls_dir, tmp_path)
        handler = BridgeHandler(kernel)
        kernel.create_agent(soul_id="alice")

        msg = BridgeMessage(source="openclaw", action="list_agents")
        response = handler._handle_list_agents(msg)
        assert len(response.payload["agents"]) == 1
        kernel.stop()

    def test_handle_list_souls(self, mock_router, tmp_souls_dir, tmp_path):
        kernel = self._make_kernel(mock_router, tmp_souls_dir, tmp_path)
        handler = BridgeHandler(kernel)

        msg = BridgeMessage(source="openclaw", action="list_souls")
        response = handler._handle_list_agents(msg)
        assert response.action == "agent_list"
        kernel.stop()

    def test_handle_vote(self, mock_router, tmp_souls_dir, tmp_path):
        kernel = self._make_kernel(mock_router, tmp_souls_dir, tmp_path)
        handler = BridgeHandler(kernel)

        msg = BridgeMessage(source="openclaw", action="vote", payload={
            "proposal": "Deploy to production",
            "votes": [
                {"voter_id": "alice", "decision": "approve"},
                {"voter_id": "bob", "decision": "approve"},
            ],
        })
        response = handler._handle_vote(msg)
        assert response.payload["final_decision"] == "approved"
        kernel.stop()

    def test_stats(self, mock_router, tmp_souls_dir, tmp_path):
        kernel = self._make_kernel(mock_router, tmp_souls_dir, tmp_path)
        handler = BridgeHandler(kernel)
        stats = handler.get_stats()
        assert stats["connections"] == 0
        kernel.stop()


# ── Soul YAML Spec ──

class TestSoulYAMLSpec:
    def test_validate_valid_yaml(self):
        data = {
            "id": "test", "name": "Test", "archetype": "Tester",
            "thinking_framework": "Be thorough and test everything. " * 5,
            "communication_style": "precise",
            "values": ["accuracy", "completeness"],
            "veto_conditions": ["never skip tests"],
            "tools_whitelist": ["read", "exec"],
        }
        errors, warnings = validate_soul_yaml(data)
        assert len(errors) == 0

    def test_validate_missing_required(self):
        data = {"id": "test"}
        errors, warnings = validate_soul_yaml(data)
        assert any("name" in e for e in errors)
        assert any("thinking_framework" in e for e in errors)

    def test_validate_wrong_type(self):
        data = {"id": "test", "name": "Test", "thinking_framework": 123}
        errors, warnings = validate_soul_yaml(data)
        assert any("str" in e for e in errors)

    def test_validate_list_wrong_item_type(self):
        data = {"id": "test", "name": "T", "thinking_framework": "ok", "values": [1, 2, 3]}
        errors, warnings = validate_soul_yaml(data)
        assert any("must be str" in e for e in errors)

    def test_warnings_for_missing_optional(self):
        data = {"id": "test", "name": "T", "thinking_framework": "short"}
        errors, warnings = validate_soul_yaml(data)
        assert any("values" in w for w in warnings)
        assert any("veto" in w for w in warnings)

    def test_soul_to_yaml_dict(self):
        soul = Soul(id="test", name="Test", archetype="TA",
                    thinking_framework="think", communication_style="cs",
                    values=["v1"], veto_conditions=["vc1"], tools_whitelist=["read"])
        d = soul_to_yaml_dict(soul)
        assert d["id"] == "test"
        assert d["values"] == ["v1"]

    def test_estimate_tokens(self):
        data = {"name": "Test", "thinking_framework": "x" * 1000}
        tokens = estimate_soul_tokens(data)
        assert 200 < tokens < 300

    def test_load_yaml_souls(self, yaml_souls_dir):
        souls = load_souls_dir(yaml_souls_dir)
        assert len(souls) == 2
        assert "themis" in souls
        assert souls["themis"].archetype == "预见型架构师"
        assert souls["themis"].values == ["数据驱动"]

    def test_yaml_soul_compiles(self, yaml_souls_dir):
        souls = load_souls_dir(yaml_souls_dir)
        prompt = compile_soul(souls["themis"])
        assert "忒弥斯" in prompt
        assert "风险先于机会" in prompt
        assert "数据驱动" in prompt


# ── Gateway with Bridge WebSocket ──

class TestGatewayWithBridge:
    def test_app_includes_bridge_route(self, mock_router, tmp_souls_dir, tmp_path):
        kernel = SymphonyKernel(souls_dir=tmp_souls_dir, router=mock_router, data_dir=tmp_path / "data")
        kernel.start()
        from opensymphony.gateway.http import create_app
        app = create_app(kernel)

        # Check bridge route exists
        routes = [r.path for r in app.routes]
        assert "/bridge" in routes
        kernel.stop()

    def test_governance_endpoints(self, mock_router, tmp_souls_dir, tmp_path):
        kernel = SymphonyKernel(souls_dir=tmp_souls_dir, router=mock_router, data_dir=tmp_path / "data")
        kernel.start()
        from fastapi.testclient import TestClient
        from opensymphony.gateway.http import create_app
        client = TestClient(create_app(kernel))

        # Governance health
        r = client.get("/governance/health")
        assert r.status_code == 200

        # HITL pending
        r = client.get("/governance/hitl/pending")
        assert r.status_code == 200
        kernel.stop()
