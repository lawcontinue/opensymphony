"""Phase 1 E2E tests: Memory + Session + Agent Communication."""


import pytest
from opensymphony.agents.agent import Agent
from opensymphony.agents.soul import Soul
from opensymphony.event_bus import AgentMessage, EventBus, MessageType
from opensymphony.kernel import SymphonyKernel
from opensymphony.llm.router import BaseProvider, LLMRouter
from opensymphony.memory.l1 import L1Memory
from opensymphony.memory.l2 import Experience, L2Memory
from opensymphony.memory.l3 import L3Memory
from opensymphony.session import Session


class MockProvider(BaseProvider):
    def __init__(self):
        self.calls = []

    def supports_model(self, model: str) -> bool:
        return True

    def chat(self, model, messages, max_tokens, temperature, **kwargs):
        self.calls.append({"model": model, "messages": messages})
        last_user = [m["content"] for m in messages if m["role"] == "user"][-1]
        return f"Response to: {last_user}", {"total_tokens": 50}


@pytest.fixture
def mock_router():
    router = LLMRouter()
    provider = MockProvider()
    router.register_provider("mock", provider)
    router.routing = {"chat": [("mock", "mock-model")], "code_generation": [("mock", "mock-model")]}
    return router, provider


@pytest.fixture
def tmp_souls_dir(tmp_path):
    (tmp_path / "alice.txt").write_text("You are Alice.", encoding="utf-8")
    (tmp_path / "bob.txt").write_text("You are Bob.", encoding="utf-8")
    return tmp_path


# ── L1 Memory ──

class TestL1Memory:
    def test_add_and_get(self):
        mem = L1Memory()
        mem.add("user", "hello")
        mem.add("assistant", "hi")
        assert len(mem.get()) == 2

    def test_trim(self):
        mem = L1Memory(max_messages=5)
        for i in range(10):
            mem.add("user", f"msg {i}")
        assert len(mem.get()) == 5
        assert mem.get()[0]["content"] == "msg 5"

    def test_system_preserved(self):
        mem = L1Memory(max_messages=4)
        mem.add("system", "you are alice")
        for i in range(5):
            mem.add("user", f"msg {i}")
        msgs = mem.get()
        assert msgs[0]["role"] == "system"
        assert len(msgs) == 4

    def test_last_n(self):
        mem = L1Memory()
        for i in range(10):
            mem.add("user", f"msg {i}")
        assert len(mem.get(last_n=3)) == 3


# ── L2 Memory ──

class TestL2Memory:
    def test_store_and_search(self, tmp_path):
        l2 = L2Memory(tmp_path / "test.db")
        exp = Experience(id="", agent_id="a1", category="conversation", content="discussed Python")
        l2.store(exp)
        results = l2.search(agent_id="a1", query="Python")
        assert len(results) == 1
        assert "Python" in results[0].content
        l2.close()

    def test_category_filter(self, tmp_path):
        l2 = L2Memory(tmp_path / "test.db")
        l2.store(Experience(id="", agent_id="a1", category="lesson", content="learned X"))
        l2.store(Experience(id="", agent_id="a1", category="decision", content="chose Y"))
        assert len(l2.search(category="lesson")) == 1
        assert len(l2.search(category="decision")) == 1
        l2.close()

    def test_count(self, tmp_path):
        l2 = L2Memory(tmp_path / "test.db")
        l2.store(Experience(id="", agent_id="a1", category="test", content="c1"))
        l2.store(Experience(id="", agent_id="a1", category="test", content="c2"))
        l2.store(Experience(id="", agent_id="a2", category="test", content="c3"))
        assert l2.count() == 3
        assert l2.count(agent_id="a1") == 2
        l2.close()


# ── L3 Memory ──

class TestL3Memory:
    def test_append_and_read(self, tmp_path):
        l3 = L3Memory(tmp_path / "audit")
        l3.append("test_event", {"key": "value"}, agent_id="a1")
        entries = l3.read()
        assert len(entries) == 1
        assert entries[0]["type"] == "test_event"
        assert entries[0]["data"]["key"] == "value"

    def test_filter_by_type(self, tmp_path):
        l3 = L3Memory(tmp_path / "audit")
        l3.append("type_a", {"x": 1})
        l3.append("type_b", {"x": 2})
        l3.append("type_a", {"x": 3})
        assert len(l3.read(event_type="type_a")) == 2


# ── Session ──

class TestSession:
    def test_add_and_recall(self, tmp_path):
        l2 = L2Memory(tmp_path / "exp.db")
        l3 = L3Memory(tmp_path / "audit")
        session = Session(agent_id="test")
        session.attach_storage(l2, l3)

        session.save_experience("lesson", "Python generators are lazy")
        session.save_experience("lesson", "Rust ownership is strict")

        results = session.recall("Python")
        assert len(results) == 1
        assert "Python" in results[0].content
        l2.close()

    def test_context_budget(self):
        session = Session(agent_id="test")
        session.add_message("system", "short")
        session.add_message("user", "x" * 500)
        session.add_message("assistant", "y" * 500)
        session.add_message("user", "z" * 500)

        msgs = session.get_context_messages(max_tokens=300)
        # Should trim older messages to fit budget
        assert len(msgs) <= 4


# ── EventBus ──

class TestEventBus:
    def test_publish_subscribe(self):
        bus = EventBus()
        received = []
        bus.subscribe("agent_a", lambda m: received.append(m))

        msg = AgentMessage(sender="agent_b", receiver="agent_a", content="hello")
        bus.publish(msg)
        assert len(received) == 1
        assert received[0].content == "hello"

    def test_broadcast(self):
        bus = EventBus()
        received_a = []
        received_b = []
        bus.subscribe("agent_a", lambda m: received_a.append(m))
        bus.subscribe("agent_b", lambda m: received_b.append(m))

        msg = AgentMessage(sender="system", receiver="*", type=MessageType.BROADCAST, content="ping")
        bus.publish(msg)
        assert len(received_a) == 1
        assert len(received_b) == 1

    def test_history(self):
        bus = EventBus()
        bus.subscribe("a", lambda m: None)
        bus.publish(AgentMessage(sender="b", receiver="a", content="msg1"))
        bus.publish(AgentMessage(sender="b", receiver="a", content="msg2"))

        history = bus.get_history("a", limit=2)
        assert len(history) == 2


# ── Agent Communication ──

class TestAgentCommunication:
    def test_agent_to_agent(self, mock_router):
        router, provider = mock_router
        bus = EventBus()

        alice = Agent(id="alice", soul=Soul(id="alice", name="Alice", thinking_framework="Be Alice."))
        bob = Agent(id="bob", soul=Soul(id="bob", name="Bob", thinking_framework="Be Bob."))
        alice.init(router=router, event_bus=bus)
        bob.init(router=router, event_bus=bus)

        # Alice sends message to Bob
        results = alice.send_message("bob", "What is 2+2?")
        assert len(results) == 1
        assert "Response to: What is 2+2?" in results[0]

    def test_cross_session_memory(self, mock_router, tmp_path):
        """Agent remembers past conversations across sessions."""
        router, _ = mock_router
        l2 = L2Memory(tmp_path / "exp.db")
        l3 = L3Memory(tmp_path / "audit")

        # Session 1: Alice learns something
        session1 = Session(agent_id="alice")
        session1.attach_storage(l2, l3)
        session1.save_experience("lesson", "User prefers concise answers")

        # Session 2: Alice recalls
        session2 = Session(agent_id="alice")
        session2.attach_storage(l2, l3)
        results = session2.recall("concise")
        assert len(results) == 1
        assert "concise" in results[0].content
        l2.close()


# ── Kernel Integration ──

class TestKernelPhase1:
    def test_full_stack(self, mock_router, tmp_souls_dir, tmp_path):
        """Full stack: kernel → create agents → chat → memory → communication."""
        router, provider = mock_router
        kernel = SymphonyKernel(souls_dir=tmp_souls_dir, router=router, data_dir=tmp_path / "data")
        kernel.start()

        # Create two agents
        alice = kernel.create_agent(soul_id="alice")
        kernel.create_agent(soul_id="bob")

        # Alice chats
        r1 = alice.chat("What is Python?")
        assert "Response to: What is Python?" in r1.content

        # Alice sends to Bob
        results = alice.send_message("bob", "Tell Bob about Python")
        assert "Response to: Tell Bob about Python" in results[0]

        # Check memory persisted
        assert kernel._l2.count() > 0

        # Health check
        h = kernel.health()
        assert h["agents_total"] == 2
        assert h["l2_experiences"] > 0
        assert h["event_bus_subscribers"] == 2

        kernel.stop()

    def test_http_with_memory(self, mock_router, tmp_souls_dir, tmp_path):
        """HTTP gateway works with session/memory."""
        router, _ = mock_router
        kernel = SymphonyKernel(souls_dir=tmp_souls_dir, router=router, data_dir=tmp_path / "data")
        kernel.start()

        from fastapi.testclient import TestClient
        from opensymphony.gateway.http import create_app
        client = TestClient(create_app(kernel))

        # Chat
        r = client.post("/chat", json={"message": "Hello", "soul_id": "alice"})
        assert r.status_code == 200
        agent_id = r.json()["agent_id"]

        # Continue session
        r2 = client.post("/chat", json={"message": "Follow up", "agent_id": agent_id})
        assert r2.status_code == 200

        # Verify memory
        assert kernel._l2.count(agent_id=agent_id) >= 1

        kernel.stop()
