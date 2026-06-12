"""Tests for native function calling (chat_with_fc)."""

import json
import pytest
from unittest.mock import MagicMock, patch

from opensymphony.agents.agent import Agent, AgentStatus
from opensymphony.agents.soul import Soul
from opensymphony.llm.router import LLMResponse, LLMRouter


# ── Fixtures ──────────────────────────────────────────────────────────

def _make_agent():
    """Create an agent with mocked router."""
    soul = Soul(id="test", name="Test", archetype="assistant")
    agent = Agent(soul=soul)
    agent.status = AgentStatus.INIT
    from opensymphony.session import Session
    agent._session = Session(agent_id=agent.id)
    return agent


def _make_router_with_fc(tool_calls_list=None, final_content="Done"):
    """Create a mock router that returns tool_calls then a final answer."""
    call_count = [0]
    tool_calls_list = tool_calls_list or []

    class MockRouter:
        def complete(self, messages, task_type="chat", tools=None, **kwargs):
            idx = call_count[0]
            call_count[0] += 1
            if idx < len(tool_calls_list):
                tc = tool_calls_list[idx]
                return LLMResponse(
                    content="", model="test", provider="test",
                    usage={"total_tokens": 10}, latency_ms=50.0,
                    tool_calls=tc,
                )
            return LLMResponse(
                content=final_content, model="test", provider="test",
                usage={"total_tokens": 20}, latency_ms=100.0,
                tool_calls=None,
            )
    return MockRouter()


def _register_tools():
    """Register minimal test tools in PRODUCTION_TOOLS."""
    from opensymphony.tools.production import PRODUCTION_TOOLS

    class FakeTool:
        name = "fake_tool"
        description = "A fake tool for testing"

        def execute(self, params):
            return {"success": True, "result": f"processed with {params}"}

    PRODUCTION_TOOLS["fake_tool"] = FakeTool()
    return PRODUCTION_TOOLS


# ── Tests ──────────────────────────────────────────────────────────────

def test_chat_with_fc_no_tools_returns_text():
    """When no tools enabled, should get plain text response."""
    agent = _make_agent()
    agent._router = _make_router_with_fc(final_content="Hello!")

    result = agent.chat_with_fc("Say hi")
    assert result["answer"] == "Hello!"
    assert result["tool_calls"] == 0
    assert result["iterations"] == 1


def test_chat_with_fc_single_tool_call():
    """Agent calls one tool then returns final answer."""
    tool_calls = [
        [{"id": "tc1", "type": "function",
          "function": {"name": "fake_tool", "arguments": '{"key": "value"}'}}],
    ]

    agent = _make_agent()
    agent._router = _make_router_with_fc(tool_calls, final_content="Processed!")

    with patch("opensymphony.tools.production.PRODUCTION_TOOLS", _register_tools()):
        result = agent.chat_with_fc("Use the tool", tool_names=["fake_tool"])

    assert result["answer"] == "Processed!"
    assert result["tool_calls"] == 1
    assert result["iterations"] == 2  # 1 tool call + 1 final
    assert len(result["steps"]) == 2
    assert result["steps"][0]["type"] == "tool_call"
    assert result["steps"][0]["tool"] == "fake_tool"
    assert result["steps"][1]["type"] == "final"


def test_chat_with_fc_max_iterations():
    """Should force stop at max_iterations."""
    # Return tool calls forever
    infinite_tc = [
        [{"id": f"tc{i}", "type": "function",
          "function": {"name": "fake_tool", "arguments": '{}'}}]
        for i in range(20)
    ]

    agent = _make_agent()
    agent._router = _make_router_with_fc(infinite_tc, final_content="forced")

    with patch("opensymphony.tools.production.PRODUCTION_TOOLS", _register_tools()):
        result = agent.chat_with_fc("Loop test", tool_names=["fake_tool"], max_iterations=3)

    assert result["truncated"] is True
    assert result["iterations"] == 3


def test_chat_with_fc_same_tool_streak():
    """Should break if same tool called 3+ times consecutively."""
    tc = [{"id": "tc1", "type": "function",
           "function": {"name": "fake_tool", "arguments": '{}'}}]

    # Return same tool call repeatedly
    agent = _make_agent()
    agent._router = _make_router_with_fc([tc] * 10, final_content="stopped")

    with patch("opensymphony.tools.production.PRODUCTION_TOOLS", _register_tools()):
        result = agent.chat_with_fc("Streak test", tool_names=["fake_tool"], max_iterations=10)

    # Should stop before 10 iterations due to streak detection
    assert any(s.get("error", "").find("consecutively") >= 0 for s in result["steps"])


def test_chat_with_fc_unknown_tool():
    """Should handle unknown tool gracefully."""
    tool_calls = [
        [{"id": "tc1", "type": "function",
          "function": {"name": "nonexistent_tool", "arguments": '{}'}}],
    ]

    agent = _make_agent()
    agent._router = _make_router_with_fc(tool_calls, final_content="OK")

    with patch("opensymphony.tools.production.PRODUCTION_TOOLS", _register_tools()):
        result = agent.chat_with_fc("Use unknown tool", tool_names=["fake_tool"])

    assert result["answer"] == "OK"
    # The tool call step should show "not found" in result
    tool_step = [s for s in result["steps"] if s["type"] == "tool_call"][0]
    assert "not found" in tool_step["result_preview"]


def test_llm_response_tool_calls_field():
    """LLMResponse should support tool_calls field."""
    resp = LLMResponse(content="test", model="m", provider="p",
                       tool_calls=[{"id": "1"}])
    assert resp.tool_calls == [{"id": "1"}]

    resp2 = LLMResponse(content="test", model="m", provider="p")
    assert resp2.tool_calls is None


def test_tool_to_schema():
    """Should generate OpenAI-format schema from tool."""
    class MockTool:
        description = "Does things"

    schema = Agent._tool_to_schema("my_tool", MockTool())
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "my_tool"
    assert schema["function"]["description"] == "Does things"
    assert "parameters" in schema["function"]


def test_http_chat_with_tools():
    """HTTP /chat endpoint should route to chat_with_fc when tools provided."""
    from opensymphony.gateway.http import ChatRequest

    req = ChatRequest(message="test", tools=["quality_check"])
    assert req.tools == ["quality_check"]

    req2 = ChatRequest(message="test")
    assert req2.tools is None
