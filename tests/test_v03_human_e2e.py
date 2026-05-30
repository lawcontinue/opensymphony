"""Tests for v0.3 dual-function architecture (Human ↔ Agent)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from opensymphony.agents.agent import Agent, AgentConfig
from opensymphony.agents.soul import Soul
from opensymphony.agents.soul_compiler import compile_soul
from opensymphony.event_bus import AgentMessage, EventBus
from opensymphony.gateway.human_adapter import HumanAdapter
from opensymphony.governance.human_safety import (
    DecisionType,
    HumanSafetyPolicy,
    RiskLevel,
)

# ── Fixtures ────────────────────────────────────────────────────

def _make_soul(**kwargs):
    defaults = {"id": "test_soul", "name": "TestSoul", "archetype": "helper"}
    defaults.update(kwargs)
    return Soul(**defaults)


def _make_kernel_mock():
    """Create a mock kernel with event bus."""
    kernel = MagicMock()
    kernel._event_bus = EventBus()
    kernel.get_agent = MagicMock(return_value=None)
    kernel.create_agent = MagicMock()
    kernel._souls = {"test_soul": _make_soul()}  # P1-1: provide souls so HumanAdapter can create agents

    # create_agent returns a real agent with soul
    def _create(soul_id=None, **kw):
        soul = _make_soul() if soul_id else None
        agent = Agent(soul=soul, config=AgentConfig())
        agent._system_prompt = compile_soul(soul) if soul else ""
        kernel._agents = {agent.id: agent}
        kernel.get_agent = MagicMock(return_value=agent)
        return agent

    kernel.create_agent.side_effect = _create
    return kernel


# ── 1. AgentMessage new fields ──────────────────────────────────

class TestAgentMessageV03:
    def test_raw_input_field_default(self):
        msg = AgentMessage(sender="ai", content="hello")
        assert msg.raw_input is None

    def test_raw_input_field_set(self):
        msg = AgentMessage(sender="human:u1", raw_input="你好世界", content="parsed")
        assert msg.raw_input == "你好世界"

    def test_confidence_field_default(self):
        msg = AgentMessage()
        assert msg.confidence == 1.0

    def test_confidence_field_set(self):
        msg = AgentMessage(confidence=0.65)
        assert msg.confidence == 0.65

    def test_sender_type_field_default(self):
        msg = AgentMessage()
        assert msg.sender_type == "ai"

    def test_sender_type_field_human(self):
        msg = AgentMessage(sender_type="human", sender="human:u1")
        assert msg.sender_type == "human"


# ── 2. Agent.agent_type ────────────────────────────────────────

class TestAgentType:
    def test_default_agent_type(self):
        config = AgentConfig()
        assert config.agent_type == "ai"

    def test_human_proxy_agent_type(self):
        config = AgentConfig(agent_type="human_proxy")
        assert config.agent_type == "human_proxy"

    def test_to_dict_includes_agent_type(self):
        agent = Agent(config=AgentConfig(agent_type="human_proxy"))
        d = agent.to_dict()
        assert d["agent_type"] == "human_proxy"

    def test_to_dict_default_agent_type(self):
        agent = Agent(config=AgentConfig())
        d = agent.to_dict()
        assert d["agent_type"] == "ai"


# ── 3. Soul.ambiguity_strategy ─────────────────────────────────

class TestSoulAmbiguityStrategy:
    def test_default_ambiguity_strategy(self):
        soul = Soul(id="s", name="S")
        assert soul.ambiguity_strategy == "balanced"

    def test_custom_ambiguity_strategy(self):
        soul = Soul(id="s", name="S", ambiguity_strategy="conservative")
        assert soul.ambiguity_strategy == "conservative"

    def test_soul_compiler_ignores_strategy(self):
        """Soul compiler should still work with the new field."""
        soul = Soul(id="s", name="TestSoul", archetype="helper",
                    thinking_framework="Think step by step",
                    ambiguity_strategy="aggressive")
        prompt = compile_soul(soul)
        assert "Think step by step" in prompt


# ── 4. HumanAdapter ────────────────────────────────────────────

class TestHumanAdapter:
    def test_init(self):
        kernel = _make_kernel_mock()
        adapter = HumanAdapter(kernel)
        assert adapter.kernel is kernel
        assert adapter.intent_bridge is None

    def test_init_with_bridge(self):
        kernel = _make_kernel_mock()
        bridge = MagicMock()
        adapter = HumanAdapter(kernel, intent_bridge=bridge)
        assert adapter.intent_bridge is bridge

    @pytest.mark.asyncio
    async def test_handle_message_no_bridge(self):
        """Without IntentBridge, message passes through directly."""
        kernel = _make_kernel_mock()
        adapter = HumanAdapter(kernel)

        # Monkey-patch create_agent to also subscribe handler
        original_create = kernel.create_agent.side_effect
        def _create_with_handler(soul_id=None, **kw):
            agent = original_create(soul_id=soul_id, **kw)
            async def handler(msg):
                return "AI response"
            kernel._event_bus.subscribe(agent.id, handler)
            return agent
        kernel.create_agent.side_effect = _create_with_handler

        result = await adapter.handle_message("user1", "hello")
        assert result["status"] == "completed"
        assert result["confidence"] == 1.0
        assert result["intent"] == "other"

    @pytest.mark.asyncio
    async def test_handle_message_with_bridge(self):
        kernel = _make_kernel_mock()
        bridge = MagicMock()
        bridge.parse.return_value = MagicMock(
            intent="question", confidence=0.9, content={"topic": "test"},
            clarification=None,
        )
        adapter = HumanAdapter(kernel, intent_bridge=bridge)

        original_create = kernel.create_agent.side_effect
        def _create_with_handler(soul_id=None, **kw):
            agent = original_create(soul_id=soul_id, **kw)
            async def handler(msg):
                return "AI answer"
            kernel._event_bus.subscribe(agent.id, handler)
            return agent
        kernel.create_agent.side_effect = _create_with_handler

        result = await adapter.handle_message("user1", "What is AI?")
        assert result["intent"] == "question"
        assert result["confidence"] == 0.9
        bridge.parse.assert_called_once_with("What is AI?")

    @pytest.mark.asyncio
    async def test_low_confidence_returns_clarification(self):
        kernel = _make_kernel_mock()
        bridge = MagicMock()
        bridge.parse.return_value = MagicMock(
            intent="other", confidence=0.3,
            content={"text": "hmm"},
            clarification="你能说得更具体一些吗？",
        )
        adapter = HumanAdapter(kernel, intent_bridge=bridge)

        result = await adapter.handle_message("user1", "hmm")
        assert result["status"] == "clarification_needed"
        assert "更具体" in result["response"]

    @pytest.mark.asyncio
    async def test_audit_log(self):
        kernel = _make_kernel_mock()
        adapter = HumanAdapter(kernel)

        original_create = kernel.create_agent.side_effect
        def _create_with_handler(soul_id=None, **kw):
            agent = original_create(soul_id=soul_id, **kw)
            async def handler(msg):
                return "response"
            kernel._event_bus.subscribe(agent.id, handler)
            return agent
        kernel.create_agent.side_effect = _create_with_handler

        await adapter.handle_message("user1", "hello")
        log = adapter.get_audit_log()
        assert len(log) == 1
        assert log[0]["user_id"] == "user1"

    @pytest.mark.asyncio
    async def test_message_has_human_metadata(self):
        """Verify the AgentMessage sent to the bus has human fields."""
        kernel = _make_kernel_mock()
        adapter = HumanAdapter(kernel)
        captured_msg = None

        original_create = kernel.create_agent.side_effect
        def _create_with_handler(soul_id=None, **kw):
            agent = original_create(soul_id=soul_id, **kw)
            async def handler(msg):
                nonlocal captured_msg
                captured_msg = msg
                return "response"
            kernel._event_bus.subscribe(agent.id, handler)
            return agent
        kernel.create_agent.side_effect = _create_with_handler

        await adapter.handle_message("user1", "hello")
        assert captured_msg is not None
        assert captured_msg.sender == "human:user1"
        assert captured_msg.sender_type == "human"
        assert captured_msg.raw_input == "hello"
        assert captured_msg.confidence == 1.0


# ── 5. HumanSafetyPolicy ───────────────────────────────────────

class TestHumanSafetyPolicy:
    def test_ai_low_risk_auto(self):
        policy = HumanSafetyPolicy()
        decision = policy.check_action("ai", "low")
        assert decision.allowed is True
        assert decision.decision_type == DecisionType.AUTO

    def test_ai_medium_risk_auto(self):
        policy = HumanSafetyPolicy()
        decision = policy.check_action("ai", "medium")
        assert decision.allowed is True
        assert decision.decision_type == DecisionType.AUTO

    def test_ai_high_risk_explicit(self):
        policy = HumanSafetyPolicy()
        decision = policy.check_action("ai", "high")
        assert decision.allowed is False
        assert decision.decision_type == DecisionType.EXPLICIT

    def test_human_low_risk_explicit(self):
        policy = HumanSafetyPolicy()
        decision = policy.check_action("human", "low")
        assert decision.allowed is True
        assert decision.decision_type == DecisionType.EXPLICIT

    def test_human_medium_risk_explicit(self):
        policy = HumanSafetyPolicy()
        decision = policy.check_action("human", "medium")
        assert decision.allowed is True
        assert decision.decision_type == DecisionType.EXPLICIT

    def test_human_high_risk_double_confirm(self):
        policy = HumanSafetyPolicy()
        decision = policy.check_action("human", "high")
        assert decision.allowed is False
        assert decision.decision_type == DecisionType.DOUBLE_CONFIRM
        assert decision.confirm_token is not None

    def test_human_critical_double_confirm(self):
        policy = HumanSafetyPolicy()
        decision = policy.check_action("human", "critical")
        assert decision.allowed is False
        assert decision.confirm_token is not None

    def test_confirm_action_success(self):
        policy = HumanSafetyPolicy()
        decision = policy.check_action("human", "high", "delete all data")
        token = decision.confirm_token
        confirm = policy.confirm_action(token)
        assert confirm.allowed is True
        assert "confirmed" in confirm.reason.lower()

    def test_confirm_action_invalid_token(self):
        policy = HumanSafetyPolicy()
        confirm = policy.confirm_action("nonexistent")
        assert confirm.allowed is False

    def test_unknown_sender_type_denied(self):
        policy = HumanSafetyPolicy()
        decision = policy.check_action("unknown", "low")
        assert decision.allowed is False

    def test_timeout_config(self):
        policy = HumanSafetyPolicy()
        assert policy.get_timeout("agent_vote") == 5
        assert policy.get_timeout("human_vote") == 86400

    def test_audit_log(self):
        policy = HumanSafetyPolicy()
        policy.check_action("ai", "low", "test action")
        log = policy.get_audit_log()
        assert len(log) == 1
        assert log[0]["sender_type"] == "ai"
        assert log[0]["allowed"] is True

    def test_risk_level_enum(self):
        policy = HumanSafetyPolicy()
        decision = policy.check_action("ai", RiskLevel.LOW)
        assert decision.allowed is True


# ── 6. Integration: HumanAdapter + SafetyPolicy ─────────────────

class TestHumanIntegration:
    @pytest.mark.asyncio
    async def test_full_flow_with_safety(self):
        """Human message → IntentBridge → AgentMessage → Agent → response."""
        kernel = _make_kernel_mock()
        bridge = MagicMock()
        bridge.parse.return_value = MagicMock(
            intent="question", confidence=0.85,
            content={"topic": "test"},
            clarification=None,
        )
        adapter = HumanAdapter(kernel, intent_bridge=bridge)

        original_create = kernel.create_agent.side_effect
        def _create_with_handler(soul_id=None, **kw):
            agent = original_create(soul_id=soul_id, **kw)
            async def handler(msg):
                return "This is the AI response"
            kernel._event_bus.subscribe(agent.id, handler)
            return agent
        kernel.create_agent.side_effect = _create_with_handler

        result = await adapter.handle_message("user1", "What is AI?")
        assert result["status"] == "completed"
        assert result["response"] == "This is the AI response"
        assert result["intent"] == "question"

        # Safety check
        safety = HumanSafetyPolicy()
        decision = safety.check_action("human", "low")
        assert decision.allowed is True

    @pytest.mark.asyncio
    async def test_human_safety_blocks_high_risk(self):
        """High-risk human action should require double confirmation."""
        safety = HumanSafetyPolicy()
        decision = safety.check_action("human", "high", "delete all data")
        assert not decision.allowed
        assert decision.confirm_token

        # Confirm it
        confirmed = safety.confirm_action(decision.confirm_token)
        assert confirmed.allowed
