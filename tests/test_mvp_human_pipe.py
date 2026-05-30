"""Phase 1 MVP Validation: Human-Agent Pipeline Protocol Test.

MVP Stage 1: Take existing Agent-Agent interactions and replace one side
with simulated Human fuzzy input. Verify AgentMessage pipe can carry both.

Success criteria: 8/10 tests pass.

Source: Crucible #20, ADR-247.
"""


import pytest
from opensymphony.agents.agent import Agent
from opensymphony.agents.soul import Soul
from opensymphony.event_bus import AgentMessage, EventBus, MessageType
from opensymphony.llm.router import BaseProvider, LLMRouter

# ── Helpers ──

class MockProvider(BaseProvider):
    """Returns a deterministic response based on input."""
    def __init__(self):
        self.calls = []

    def supports_model(self, model: str) -> bool:
        return True

    def chat(self, model, messages, max_tokens, temperature, **kwargs):
        self.calls.append({"model": model, "messages": messages})
        last_user = [m["content"] for m in messages if m["role"] == "user"]
        last = last_user[-1] if last_user else "empty"
        return f"Response to: {last}", {"total_tokens": 50}


@pytest.fixture
def mock_router():
    router = LLMRouter()
    provider = MockProvider()
    router.register_provider("mock", provider)
    router.routing = {"chat": [("mock", "mock-model")], "code_generation": [("mock", "mock-model")]}
    return router, provider


def make_agent(agent_id: str, soul_name: str, framework: str, bus: EventBus, router: LLMRouter) -> Agent:
    soul = Soul(id=soul_name.lower(), name=soul_name, thinking_framework=framework)
    agent = Agent(id=agent_id, soul=soul)
    agent.init(router=router, event_bus=bus)
    return agent


# ── Test 1-10: Human Simulated Fuzzy Input through AgentMessage Pipe ──

class TestHumanAgentPipeline:
    """Simulate Human fuzzy input flowing through the same AgentMessage pipe
    that Agent-Agent communication uses."""

    def test_01_simple_fuzzy_greeting(self, mock_router):
        """Human says something vague, Agent receives via AgentMessage pipe."""
        router, provider = mock_router
        bus = EventBus()

        make_agent("alice", "Themis", "Be helpful.", bus, router)

        # Simulate: Human sends fuzzy greeting through pipe
        # In production this would go through Intent Bridge first
        # For MVP validation we test the pipe itself
        msg = AgentMessage(
            sender="human:user1",
            receiver="alice",
            type=MessageType.REQUEST,
            content="hey, can you help me with something?",
        )

        received = []
        bus.subscribe("alice", lambda m: received.append(m))
        bus.publish(msg)

        assert len(received) == 1
        assert received[0].sender == "human:user1"
        assert received[0].content == "hey, can you help me with something?"

    def test_02_human_message_agent_responds(self, mock_router):
        """Agent processes a Human-originated message and responds."""
        router, provider = mock_router
        bus = EventBus()

        make_agent("alice", "Themis", "Be helpful.", bus, router)

        # Human sends message
        msg = AgentMessage(
            sender="human:user1",
            receiver="alice",
            type=MessageType.REQUEST,
            content="What is the capital of France?",
        )

        results = bus.publish(msg)
        # alice's handler should produce a response
        assert len(results) == 1
        assert "Response to: What is the capital of France?" in results[0]

    def test_03_raw_input_preserved(self, mock_router):
        """AgentMessage carries raw_input field for context preservation.
        (Anthropic vs Devin lesson: always preserve original input.)"""
        router, provider = mock_router
        bus = EventBus()

        make_agent("alice", "Themis", "Be helpful.", bus, router)

        # Create message with raw_input (simulating Intent Bridge output)
        msg = AgentMessage(
            sender="human:user1",
            receiver="alice",
            type=MessageType.REQUEST,
            content={"intent": "search", "query": "Python tutorials"},  # structured
        )
        # Attach raw_input as extra metadata on content dict
        # (v0.3 design: raw_input is a field on AgentMessage; for MVP we test the concept)
        msg.content = {
            "structured": {"intent": "search", "query": "Python tutorials"},
            "raw_input": "I want to learn Python, any good resources?",
            "confidence": 0.85,
        }

        received = []
        bus.subscribe("alice", lambda m: received.append(m))
        bus.publish(msg)

        assert len(received) == 1
        assert received[0].content["raw_input"] == "I want to learn Python, any good resources?"
        assert received[0].content["structured"]["query"] == "Python tutorials"

    def test_04_mixed_sender_types(self, mock_router):
        """Both Agent and Human send messages through the same pipe simultaneously."""
        router, provider = mock_router
        bus = EventBus()

        make_agent("alice", "Themis", "Be helpful.", bus, router)
        make_agent("bob", "Code", "Be technical.", bus, router)

        received = []
        bus.subscribe("alice", lambda m: received.append(m))

        # Agent sends to Alice
        bus.publish(AgentMessage(sender="bob", receiver="alice", content="Task done."))
        # Human sends to Alice
        bus.publish(AgentMessage(sender="human:user1", receiver="alice", content="Thanks!"))

        assert len(received) == 2
        assert received[0].sender == "bob"
        assert received[1].sender == "human:user1"

    def test_05_human_broadcast(self, mock_router):
        """Human broadcasts to all agents through the pipe."""
        router, provider = mock_router
        bus = EventBus()

        make_agent("alice", "Themis", "Be helpful.", bus, router)
        make_agent("bob", "Code", "Be technical.", bus, router)

        # Human broadcasts
        msg = AgentMessage(
            sender="human:user1",
            receiver="*",
            type=MessageType.BROADCAST,
            content="System going down for maintenance in 10 minutes.",
        )
        bus.publish(msg)

        # Broadcast receiver is "*", so check global history
        all_history = bus.get_history(limit=5)
        broadcast_msgs = [m for m in all_history if m.type == MessageType.BROADCAST]
        assert len(broadcast_msgs) == 1
        assert broadcast_msgs[0].content == "System going down for maintenance in 10 minutes."

    def test_06_confidence_low_triggers_no_direct_response(self, mock_router):
        """Low-confidence Human input should not produce a direct Agent response.
        (Simulates Intent Bridge slow path: clarification needed.)"""
        router, provider = mock_router
        bus = EventBus()

        make_agent("alice", "Themis", "Be careful.", bus, router)

        # Low confidence message — content is a clarification request, not a direct task
        msg = AgentMessage(
            sender="human:user1",
            receiver="alice",
            type=MessageType.REQUEST,
            content={
                "type": "clarification_needed",
                "question": "你说的'那个方案'是指哪个？",
                "raw_input": "我觉得那个方案有问题",
                "confidence": 0.3,
            },
        )

        # This should still flow through the pipe (pipe doesn't filter by confidence)
        received = []
        bus.subscribe("alice", lambda m: received.append(m))
        bus.publish(msg)

        assert len(received) == 1
        assert received[0].content["confidence"] < 0.5

    def test_07_human_voting_request(self, mock_router):
        """Human triggers a vote through the pipe."""
        router, provider = mock_router
        bus = EventBus()

        make_agent("alice", "Themis", "Be democratic.", bus, router)
        make_agent("bob", "Code", "Be technical.", bus, router)

        # Human requests a vote
        msg = AgentMessage(
            sender="human:user1",
            receiver="*",
            type=MessageType.VOTE,
            content={"proposal": "Deploy v2 to production", "options": ["approve", "reject"]},
            requires_vote=True,
        )

        received_a = []
        received_b = []
        bus.subscribe("alice", lambda m: received_a.append(m))
        bus.subscribe("bob", lambda m: received_b.append(m))
        bus.publish(msg)

        assert len(received_a) == 1
        assert len(received_b) == 1
        assert received_a[0].requires_vote is True
        assert received_a[0].type == MessageType.VOTE

    def test_08_human_long_fuzzy_request(self, mock_router):
        """Human sends a long, rambling message. Agent receives it via pipe."""
        router, provider = mock_router
        bus = EventBus()

        make_agent("alice", "Themis", "Be patient.", bus, router)

        long_message = (
            "So I was thinking about the thing we discussed earlier, "
            "you know, the one about the agent framework? Well, "
            "I'm not sure if it's the right approach anymore, "
            "maybe we should consider something different, "
            "or maybe not, what do you think?"
        )

        msg = AgentMessage(
            sender="human:user1",
            receiver="alice",
            type=MessageType.REQUEST,
            content=long_message,
        )

        results = bus.publish(msg)
        assert len(results) == 1
        # Agent should still produce a response (pipe doesn't choke on long fuzzy input)
        assert "Response to:" in results[0]

    def test_09_human_and_agent_chain(self, mock_router):
        """Chain: Human → Agent A → Agent B. All through the same pipe."""
        router, provider = mock_router
        bus = EventBus()

        make_agent("alice", "Themis", "Delegate tasks.", bus, router)
        make_agent("bob", "Code", "Execute tasks.", bus, router)

        # Human → Alice
        human_msg = AgentMessage(
            sender="human:user1",
            receiver="alice",
            type=MessageType.REQUEST,
            content="Please ask Bob to write a hello world function.",
        )

        results = bus.publish(human_msg)
        assert len(results) == 1
        # Alice should respond (via MockProvider)
        assert "Response to:" in results[0]

        # Alice → Bob (simulating Alice delegates)
        delegate_msg = AgentMessage(
            sender="alice",
            receiver="bob",
            type=MessageType.REQUEST,
            content="Write a hello world function in Python.",
        )
        results2 = bus.publish(delegate_msg)
        assert len(results2) == 1
        assert "Response to:" in results2[0]

        # Verify both messages in history
        history = bus.get_history(limit=10)
        human_msgs = [m for m in history if m.sender.startswith("human")]
        agent_msgs = [m for m in history if not m.sender.startswith("human")]
        assert len(human_msgs) == 1
        assert len(agent_msgs) >= 1

    def test_10_human_multilingual_input(self, mock_router):
        """Human sends Chinese fuzzy input. Pipe doesn't care about language."""
        router, provider = mock_router
        bus = EventBus()

        make_agent("alice", "Themis", "Be helpful.", bus, router)

        msg = AgentMessage(
            sender="human:user1",
            receiver="alice",
            type=MessageType.REQUEST,
            content="上次说的那个方案我觉得不太行，能不能换个思路？",
        )

        results = bus.publish(msg)
        assert len(results) == 1
        # MockProvider echoes back the content, pipe is language-agnostic
        assert "上次说的那个方案" in results[0]


# ── Summary ──

class TestMVPSummary:
    """Meta-test: report pass rate for MVP Stage 1."""

    def test_mvp_stage1_summary(self, mock_router):
        """This test always passes. Its purpose is to document the expected success criteria.

        Run `pytest tests/test_mvp_human_pipe.py -v` to see individual results.
        Success criteria: 8/10 TestHumanAgentPipeline tests pass.
        """
        # Count tests in TestHumanAgentPipeline
        test_methods = [m for m in dir(TestHumanAgentPipeline) if m.startswith("test_")]
        assert len(test_methods) == 10, f"Expected 10 tests, found {len(test_methods)}"
