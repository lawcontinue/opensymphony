"""Agent — core agent with soul, session, lifecycle, and event communication."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..event_bus import AgentMessage, EventBus, MessageType
from ..llm.router import LLMResponse, LLMRouter
from ..session import Session
from .soul import Soul
from .soul_compiler import compile_soul


@dataclass
class HandoffResult:
    """Result of an agent handoff operation."""
    status: str  # "allowed", "denied", "escalated"
    target_agent: Agent | None = None
    reason: str = ""
    governance: Any = None  # DefenseResult if governance checked


class AgentStatus(str, Enum):
    CREATED = "created"
    INIT = "init"
    ACTIVE = "active"
    IDLE = "idle"
    EVOLVING = "evolving"
    SUSPENDED = "suspended"
    TERMINATED = "terminated"


@dataclass
class AgentConfig:
    max_tokens_per_hour: int = 100_000
    max_tool_calls_per_hour: int = 200
    memory_limit_mb: int = 256
    agent_type: str = "ai"  # "ai" | "human_proxy"


@dataclass
class Agent:
    """An agent with soul, session memory, and event communication."""

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    soul: Soul | None = None
    status: AgentStatus = AgentStatus.CREATED
    permissions: int = 0
    config: AgentConfig = field(default_factory=AgentConfig)
    metadata: dict[str, Any] = field(default_factory=dict)

    # Runtime
    _router: LLMRouter | None = field(default=None, repr=False)
    _session: Session | None = field(default=None, repr=False)
    _event_bus: EventBus | None = field(default=None, repr=False)
    _system_prompt: str = field(default="", repr=False)
    _created_at: float = field(default_factory=time.time)
    _token_usage: int = 0

    def init(
        self,
        router: LLMRouter,
        session: Session | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._router = router
        self._echo_engine = None  # Set by kernel when EchoEngine is available
        if self.soul:
            self._system_prompt = compile_soul(self.soul)

        # Session setup
        if session:
            self._session = session
        else:
            self._session = Session(agent_id=self.id)
        self._session.agent_id = self.id

        # Event bus
        if event_bus:
            self._event_bus = event_bus
            aliases = [self.soul.name.lower()] if self.soul else []
            event_bus.subscribe(self.id, self._handle_message, aliases=aliases)

        self.status = AgentStatus.INIT

    def chat(self, user_message: str, **kwargs: Any) -> LLMResponse:
        """Send a message and get a response."""
        if self.status not in (AgentStatus.INIT, AgentStatus.ACTIVE, AgentStatus.IDLE):
            raise RuntimeError(f"Agent {self.id} is {self.status}, cannot chat")
        if not self._router:
            raise RuntimeError(f"Agent {self.id} not initialized")
        if not self._session:
            raise RuntimeError(f"Agent {self.id} has no session")

        self.status = AgentStatus.ACTIVE

        # Recall relevant experiences
        recalled = self._session.recall(user_message, limit=3)
        recalled_text = ""
        if recalled:
            recalled_text = "\n\n[Relevant past experiences]:\n" + "\n".join(
                f"- [{r.category}] {r.content[:200]}" for r in recalled
            )

        # Build messages from session context
        messages = self._session.get_context_messages(max_tokens=3500)
        if self._system_prompt:
            messages.insert(0, {"role": "system", "content": self._system_prompt + recalled_text})
        messages.append({"role": "user", "content": user_message})

        # For structured-output souls, prepend direct-output instruction to user message
        structured_souls = {"drama_director", "screenwriter", "reflector", "code"}
        if self.soul and self.soul.id in structured_souls:
            messages[-1]["content"] = "[直接输出最终结果，不要解释、不要分析过程、不要思考]\n\n" + messages[-1]["content"]

        # Echo Engine: apply active skills (pre-process)
        echo_skill_ids = []
        task_type = self.metadata.get("task_type", "chat")
        if self._echo_engine and self.soul:
            messages, echo_skill_ids = self._echo_engine.pre_process(
                soul_id=self.soul.id, model="", task_type=task_type, messages=messages)
            # Apply param overrides
            overrides = self._echo_engine.get_param_overrides(self.soul.id, "", task_type)
            if "max_tokens" in overrides:
                kwargs.setdefault("max_tokens", overrides["max_tokens"])

        # Call LLM
        response = self._router.complete(messages, task_type=task_type, **kwargs)

        # Post-process: extract JSON/code block from thinking noise
        content = response.content
        if content and self.soul and self.soul.id in structured_souls:
            content = self._extract_substantive(content)
            response = LLMResponse(
                content=content, model=response.model, provider=response.provider,
                usage=response.usage, latency_ms=response.latency_ms,
            )

        # Echo Engine: apply post-process
        if echo_skill_ids and content:
            content = self._echo_engine.post_process(
                self.soul.id if self.soul else "", response.model, content, echo_skill_ids)
            response = LLMResponse(
                content=content, model=response.model, provider=response.provider,
                usage=response.usage, latency_ms=response.latency_ms,
            )

        # Update session
        self._session.add_message("user", user_message)
        self._session.add_message("assistant", response.content)

        # Save experience
        self._session.save_experience("conversation", f"Q: {user_message[:100]} | A: {response.content[:100]}")

        self._token_usage += response.usage.get("total_tokens", 0)
        self.status = AgentStatus.IDLE
        return response

    @staticmethod
    def _extract_substantive(text: str) -> str:
        """Extract the substantive output from potentially noisy LLM response.

        Strategies:
        1. If text is already valid JSON, return as-is
        2. Extract content from ```json...``` code block
        3. Find first { or [ and extract to matching bracket
        """
        import json as _json
        import re

        # Strategy 0: Already valid JSON
        text = text.strip()
        try:
            _json.loads(text)
            return text
        except (_json.JSONDecodeError, ValueError):
            pass

        # Strategy 1: Extract from code block
        m = re.search(r'```(?:json)?\s*\n?(.*?)```', text, re.DOTALL)
        if m:
            extracted = m.group(1).strip()
            if len(extracted) > 20:
                try:
                    _json.loads(extracted)
                    return extracted
                except:
                    pass
                return extracted

        # Strategy 2: Find JSON-like structure
        for start_char, end_char in [('{', '}'), ('[', ']')]:
            idx = text.find(start_char)
            if idx >= 0:
                depth = 0
                for i in range(idx, len(text)):
                    if text[i] == start_char:
                        depth += 1
                    elif text[i] == end_char:
                        depth -= 1
                    if depth == 0:
                        candidate = text[idx:i+1].strip()
                        if len(candidate) > 20:
                            return candidate

        return text

    def handoff(self, target_soul: str, context: dict[str, Any] | None = None, kernel: Any = None) -> HandoffResult:
        """Transfer the current task to another agent via governance-checked handoff.

        Args:
            target_soul: Soul ID of the target agent (e.g. "code", "legal_writer").
            context: Optional dict with context to pass to the target agent.
            kernel: The SymphonyKernel (needed to create target agent and check governance).

        Returns:
            HandoffResult with status (allowed/denied/escalated) and target agent if allowed.
        """
        if not kernel:
            return HandoffResult(status="denied", reason="No kernel provided for handoff")

        # 1. Build governance action
        from ..governance.defense import Action
        action = Action(
            agent_id=self.id,
            action_type="handoff",
            target=target_soul,
            parameters=context or {},
            metadata={"source_soul": self.soul.id if self.soul else "unknown"},
        )

        # 2. Governance check
        gov_result = kernel._governance.before_action(action) if kernel._governance else None

        if gov_result and gov_result.decision.value == "deny":
            return HandoffResult(status="denied", reason=gov_result.reason, governance=gov_result)

        if gov_result and gov_result.decision.value == "escalate":
            self.status = AgentStatus.SUSPENDED
            return HandoffResult(status="escalated", reason=gov_result.reason, governance=gov_result)

        # 3. Create target agent
        target_agent = kernel.create_agent(soul_id=target_soul)

        # 4. Transfer context via event bus
        if context and self._event_bus:
            handoff_msg = AgentMessage(
                sender=self.id,
                receiver=target_agent.id,
                type=MessageType.REQUEST,
                content=context,
            )
            self._event_bus.publish(handoff_msg)

        # 5. Audit
        if kernel._governance:
            kernel._governance.after_action(action, {"target_agent": target_agent.id}, success=True)

        # 6. Update status
        self.status = AgentStatus.IDLE

        return HandoffResult(
            status="allowed",
            target_agent=target_agent,
            governance=gov_result,
        )

    def chat_with_tools(self, user_message: str, tools: dict[str, Any] | None = None,
                        max_iterations: int = 5, **kwargs: Any) -> dict[str, Any]:
        """ReAct loop: think → act (tool call) → observe → repeat until done.

        Args:
            user_message: The user's task.
            tools: Dict of {tool_name: tool_instance} with execute() method.
                   If None, no tools available (pure chat).
            max_iterations: Max ReAct iterations before forcing answer.
            **kwargs: Passed to LLMRouter.complete().

        Returns:
            {"answer": str, "tool_calls": int, "iterations": int, "steps": list}
        """
        if not self._router:
            raise RuntimeError(f"Agent {self.id} not initialized")
        if not self._session:
            raise RuntimeError(f"Agent {self.id} has no session")

        tools = tools or {}
        tool_schemas = self._format_tool_schemas(tools)

        # Build system prompt
        system = self._system_prompt or "You are a helpful assistant."
        if tools:
            system += (
                "\n\n你可以使用以下工具：\n" + tool_schemas +
                "\n\n使用工具时，严格按以下格式回复（每行一个动作）：\n"
                "TOOL_CALL: {\"name\": \"tool_name\", \"params\": {\"key\": \"value\"}}\n\n"
                "观察工具结果后继续思考。如果你已经得到最终答案，回复：\n"
                "FINAL_ANSWER: 你的回答\n\n"
                "规则：\n"
                "- 每轮最多调用一个工具\n"
                "- 仔细分析工具结果再决定下一步\n"
                f"- 最多使用 {max_iterations} 轮\n"
                "- 优先用工具获取信息，不要猜测文件内容"
            )

        messages = [{"role": "system", "content": system},
                     {"role": "user", "content": user_message}]

        steps = []
        tool_calls = 0

        for i in range(max_iterations):
            response = self._router.complete(messages, task_type="chat", max_tokens=1024, **kwargs)
            assistant_msg = response.content
            messages.append({"role": "assistant", "content": assistant_msg})
            self._token_usage += response.usage.get("total_tokens", 0)

            # Check for FINAL_ANSWER
            if "FINAL_ANSWER:" in assistant_msg:
                answer = assistant_msg.split("FINAL_ANSWER:", 1)[1].strip()
                steps.append({"iteration": i + 1, "type": "final", "content": answer[:200]})
                self._session.add_message("user", user_message)
                self._session.add_message("assistant", answer)
                return {"answer": answer, "tool_calls": tool_calls,
                        "iterations": i + 1, "steps": steps}

            # Check for TOOL_CALL
            tool_call = self._parse_tool_call(assistant_msg)
            if tool_call and tools:
                tool_name = tool_call["name"]
                tool_params = tool_call.get("params", {})
                tool = tools.get(tool_name)

                if tool:
                    try:
                        result = tool.execute(tool_params)
                        observation = json.dumps(result, ensure_ascii=False, default=str)[:3000]
                    except Exception as e:
                        observation = json.dumps({"success": False, "error": str(e)})
                    tool_calls += 1
                    steps.append({"iteration": i + 1, "type": "tool_call",
                                  "tool": tool_name, "params": tool_params,
                                  "result_preview": observation[:300]})
                    messages.append({"role": "user", "content": f"[Tool Result]\n{observation}"})
                else:
                    messages.append({"role": "user", "content": f"[Error] Tool '{tool_name}' not found. Available: {list(tools.keys())}"})
                    steps.append({"iteration": i + 1, "type": "error", "error": f"Tool '{tool_name}' not found"})
            else:
                # No tool call and no final answer — ask agent to decide
                if i < max_iterations - 1:
                    messages.append({"role": "user", "content": "请使用工具获取信息，或用 FINAL_ANSWER: 给出最终回答。"})
                steps.append({"iteration": i + 1, "type": "no_action", "content": assistant_msg[:200]})

        # Max iterations — force final answer
        forced = self._router.complete(messages, task_type="chat", max_tokens=512, **kwargs)
        answer = forced.content
        self._session.add_message("user", user_message)
        self._session.add_message("assistant", answer)
        return {"answer": answer, "tool_calls": tool_calls,
                "iterations": max_iterations, "steps": steps, "truncated": True}

    @staticmethod
    def _format_tool_schemas(tools: dict[str, Any]) -> str:
        """Format tool descriptions for system prompt."""
        lines = []
        for name, tool in tools.items():
            desc = getattr(tool, "description", "No description")
            lines.append(f"- {name}: {desc}")
        return "\n".join(lines)

    @staticmethod
    def _parse_tool_call(text: str) -> dict | None:
        """Extract TOOL_CALL JSON from assistant response."""
        marker = "TOOL_CALL:"
        idx = text.find(marker)
        if idx < 0:
            return None
        json_str = text[idx + len(marker):].strip()
        # Find the JSON object
        brace_start = json_str.find("{")
        if brace_start < 0:
            return None
        depth = 0
        for i in range(brace_start, len(json_str)):
            if json_str[i] == "{":
                depth += 1
            elif json_str[i] == "}":
                depth -= 1
            if depth == 0:
                try:
                    return json.loads(json_str[brace_start:i + 1])
                except (json.JSONDecodeError, ValueError):
                    return None
        return None

    def send_message(self, receiver: str, content: Any, msg_type: MessageType = MessageType.REQUEST) -> list[Any]:
        """Send a message to another agent via event bus."""
        if not self._event_bus:
            raise RuntimeError("No event bus configured")
        msg = AgentMessage(sender=self.id, receiver=receiver, type=msg_type, content=content)
        return self._event_bus.publish(msg)

    def _handle_message(self, message: AgentMessage) -> Any:
        """Handle incoming message from event bus."""
        if message.type == MessageType.REQUEST and message.content:
            if isinstance(message.content, str) and self._router:
                task_type = self.metadata.get("task_type", "chat")
                msgs = []
                if self._system_prompt:
                    msgs.append({"role": "system", "content": self._system_prompt})
                msgs.append({"role": "user", "content": message.content})
                resp = self._router.complete(msgs, task_type=task_type)
                return resp.content
        return None

    def to_dict(self) -> dict[str, Any]:
        session_info = self._session.to_dict() if self._session else {}
        return {
            "id": self.id,
            "name": self.soul.name if self.soul else self.id,
            "archetype": self.soul.archetype if self.soul else "",
            "status": self.status.value,
            "permissions": self.permissions,
            "token_usage": self._token_usage,
            "session": session_info,
            "agent_type": self.config.agent_type,
        }
