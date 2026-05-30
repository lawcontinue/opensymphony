"""End-to-end tests for Phase 0: HTTP → Agent(Soul) → LLM → Response."""


import pytest
from opensymphony.agents.agent import Agent, AgentStatus
from opensymphony.agents.soul import Soul, load_soul_from_text, load_souls_dir
from opensymphony.agents.soul_compiler import compile_and_check, compile_soul, estimate_tokens
from opensymphony.kernel import SymphonyKernel
from opensymphony.llm.router import BaseProvider, LLMRouter

# ── Fixtures ──

class MockProvider(BaseProvider):
    """Mock provider that echoes back the last user message."""

    def __init__(self):
        self.calls = []

    def supports_model(self, model: str) -> bool:
        return True

    def chat(self, model, messages, max_tokens, temperature, **kwargs):
        self.calls.append({"model": model, "messages": messages})
        last_user = [m["content"] for m in messages if m["role"] == "user"][-1]
        return f"[{model}] Response to: {last_user}", {"total_tokens": 50}


@pytest.fixture
def mock_router():
    router = LLMRouter()
    provider = MockProvider()
    router.register_provider("mock", provider)
    # Override routing to use mock
    router.routing = {
        "chat": [("mock", "mock-model")],
        "code_generation": [("mock", "mock-model")],
    }
    return router, provider


@pytest.fixture
def simple_soul():
    return Soul(
        id="test-soul",
        name="Test Soul",
        archetype="Test Agent",
        thinking_framework="You are a test agent. Be helpful.",
        communication_style="concise",
        values=["accuracy"],
        veto_conditions=["never lie"],
    )


@pytest.fixture
def tmp_souls_dir(tmp_path):
    """Create a temp directory with soul files."""
    (tmp_path / "alice.txt").write_text("You are Alice, a helpful assistant.", encoding="utf-8")
    (tmp_path / "bob.txt").write_text("You are Bob, a creative writer.", encoding="utf-8")
    return tmp_path


# ── Soul Tests ──

class TestSoul:
    def test_load_text(self, tmp_path):
        p = tmp_path / "test.txt"
        p.write_text("Be helpful.", encoding="utf-8")
        soul = load_soul_from_text(p)
        assert soul.id == "test"
        assert soul.thinking_framework == "Be helpful."
        assert soul.name == "Test"

    def test_compile_soul(self, simple_soul):
        prompt = compile_soul(simple_soul)
        assert "Test Soul" in prompt
        assert "Test Agent" in prompt
        assert "Be helpful" in prompt
        assert "accuracy" in prompt
        assert "never lie" in prompt

    def test_compile_empty_soul(self):
        soul = Soul(id="empty", name="Empty")
        prompt = compile_soul(soul)
        assert "Empty" in prompt

    def test_load_souls_dir(self, tmp_souls_dir):
        souls = load_souls_dir(tmp_souls_dir)
        assert len(souls) == 2
        assert "alice" in souls
        assert "bob" in souls
        assert "Alice" in souls["alice"].thinking_framework

    def test_estimate_tokens(self):
        text = "Hello world " * 100  # 1200 chars, ~300 tokens
        tokens = estimate_tokens(text)
        assert 200 < tokens < 400

    def test_compile_and_check_budget(self):
        big_soul = Soul(id="big", name="Big", thinking_framework="x" * 20000)
        prompt, tokens = compile_and_check(big_soul, max_tokens=4000)
        assert "[truncated" in prompt


# ── Agent Tests ──

class TestAgent:
    def test_lifecycle(self, mock_router, simple_soul):
        router, _ = mock_router
        agent = Agent(soul=simple_soul)
        assert agent.status == AgentStatus.CREATED

        agent.init(router)
        assert agent.status == AgentStatus.INIT
        assert agent._system_prompt != ""

    def test_chat(self, mock_router, simple_soul):
        router, _ = mock_router
        agent = Agent(soul=simple_soul)
        agent.init(router)

        response = agent.chat("Hello")
        assert "Response to: Hello" in response.content
        assert agent.status == AgentStatus.IDLE
        assert len(agent._session.l1.get()) == 2  # user + assistant

    def test_chat_session_persists(self, mock_router, simple_soul):
        router, _ = mock_router
        agent = Agent(soul=simple_soul)
        agent.init(router)

        agent.chat("First message")
        agent.chat("Second message")
        assert len(agent._session.l1.get()) == 4

    def test_chat_without_init_raises(self, simple_soul):
        agent = Agent(soul=simple_soul)
        with pytest.raises(RuntimeError, match="cannot chat"):
            agent.chat("Hello")

    def test_to_dict(self, mock_router, simple_soul):
        router, _ = mock_router
        agent = Agent(soul=simple_soul)
        agent.init(router)
        agent.chat("Test")

        d = agent.to_dict()
        assert d["id"] == agent.id
        assert d["name"] == "Test Soul"
        assert d["status"] in ("idle", "active")
        assert d["session"]["l1"]["messages_count"] == 2


# ── Kernel Tests ──

class TestKernel:
    def test_start_stop(self, mock_router, tmp_souls_dir):
        router, _ = mock_router
        kernel = SymphonyKernel(souls_dir=tmp_souls_dir, router=router)
        assert not kernel.running

        kernel.start()
        assert kernel.running
        assert len(kernel._souls) == 2
        kernel.stop()
        assert not kernel.running

    def test_create_agent(self, mock_router, tmp_souls_dir):
        router, _ = mock_router
        kernel = SymphonyKernel(souls_dir=tmp_souls_dir, router=router)
        kernel.start()

        agent = kernel.create_agent(soul_id="alice")
        assert agent.soul is not None
        assert agent.soul.id == "alice"
        assert agent.status == AgentStatus.INIT

    def test_create_agent_unknown_soul(self, mock_router, tmp_souls_dir):
        router, _ = mock_router
        kernel = SymphonyKernel(souls_dir=tmp_souls_dir, router=router)
        kernel.start()

        agent = kernel.create_agent(soul_id="nonexistent")
        assert agent.soul is None  # No soul, but agent still created

    def test_health(self, mock_router, tmp_souls_dir):
        router, _ = mock_router
        kernel = SymphonyKernel(souls_dir=tmp_souls_dir, router=router)
        kernel.start()

        h = kernel.health()
        assert h["status"] == "running"
        assert h["souls_loaded"] == 2
        assert h["agents_total"] == 0

        kernel.create_agent(soul_id="alice")
        h = kernel.health()
        assert h["agents_total"] == 1


# ── HTTP Gateway Tests ──

class TestHTTPGateway:
    @pytest.fixture
    def client(self, mock_router, tmp_souls_dir):
        router, _ = mock_router
        kernel = SymphonyKernel(souls_dir=tmp_souls_dir, router=router)
        kernel.start()
        app = __import__("opensymphony.gateway.http", fromlist=["create_app"]).create_app(kernel)
        from fastapi.testclient import TestClient
        return TestClient(app)

    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "running"

    def test_list_souls(self, client):
        r = client.get("/souls")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 2
        ids = {s["id"] for s in data}
        assert "alice" in ids
        assert "bob" in ids

    def test_chat_new_agent(self, client):
        r = client.post("/chat", json={
            "message": "Hello from test",
            "soul_id": "alice",
        })
        assert r.status_code == 200
        data = r.json()
        assert "Response to: Hello from test" in data["response"]
        assert data["soul_name"] == "Alice"

    def test_chat_existing_agent(self, client):
        # Create agent first
        r1 = client.post("/chat", json={"message": "First", "soul_id": "alice"})
        agent_id = r1.json()["agent_id"]

        # Continue session
        r2 = client.post("/chat", json={"message": "Second", "agent_id": agent_id})
        assert r2.status_code == 200
        assert "Response to: Second" in r2.json()["response"]

    def test_chat_unknown_agent(self, client):
        r = client.post("/chat", json={"message": "Hi", "agent_id": "nonexistent"})
        assert r.status_code == 404

    def test_terminate_agent(self, client):
        r1 = client.post("/chat", json={"message": "Hi", "soul_id": "alice"})
        agent_id = r1.json()["agent_id"]

        r2 = client.delete(f"/agents/{agent_id}")
        assert r2.status_code == 200
        assert r2.json()["status"] == "terminated"

    def test_agents_list(self, client):
        client.post("/chat", json={"message": "Hi", "soul_id": "alice"})
        r = client.get("/agents")
        assert r.status_code == 200
        assert len(r.json()) >= 1


# ── Full E2E Test ──

class TestFullE2E:
    def test_full_chain(self, mock_router, tmp_souls_dir):
        """The Phase 0 acceptance test:
        HTTP request → Create Agent with Soul → LLM responds → Return response
        """
        router, provider = mock_router
        kernel = SymphonyKernel(souls_dir=tmp_souls_dir, router=router)
        kernel.start()

        # 1. Create agent with soul
        agent = kernel.create_agent(soul_id="alice")
        assert agent.soul is not None
        assert agent._system_prompt != ""
        assert "Alice" in agent._system_prompt

        # 2. Chat via agent
        response = agent.chat("What is 2+2?")
        assert "Response to: What is 2+2?" in response.content

        # 3. Verify system prompt was sent to LLM
        assert len(provider.calls) == 1
        messages = provider.calls[0]["messages"]
        roles = [m["role"] for m in messages]
        assert "system" in roles
        assert "user" in roles

        # 4. Session persists
        agent.chat("And what about 3+3?")
        assert len(provider.calls) == 2
        assert len(agent._session.l1.get()) == 4

        # 5. Health check
        h = kernel.health()
        assert h["status"] == "running"
        assert h["agents_total"] == 1

        kernel.stop()
