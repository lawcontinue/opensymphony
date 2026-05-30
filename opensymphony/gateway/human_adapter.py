"""HumanAdapter — bridge Human HTTP requests into the Agent system."""

from __future__ import annotations

import inspect
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from ..event_bus import AgentMessage, MessageType

logger = logging.getLogger("symphony.human_adapter")

# P1-11: user_id validation
_USER_ID_RE = re.compile(r'^[a-zA-Z0-9_]{1,64}$')


def _validate_user_id(user_id: str) -> str:
    """Validate and sanitize user_id. Raises ValueError on invalid input."""
    if not _USER_ID_RE.match(user_id):
        raise ValueError(f"Invalid user_id: must be 1-64 alphanumeric/underscore chars, got '{user_id[:20]}'")
    return user_id


class HumanAdapter:
    """Receives Human requests, translates via IntentBridge, routes to Agents."""

    def __init__(self, kernel: Any, intent_bridge: Any | None = None):
        self.kernel = kernel
        self.intent_bridge = intent_bridge
        self._audit_log: list[dict[str, Any]] = []
        self._audit_file: Path | None = None
    def _persist_audit(self, entry: dict[str, Any]) -> None:
        """Persist audit entry to file."""
        # Always use file-based audit (L3 may not have store method)
        if self._audit_file is None:
            self._audit_file = Path("data/audit_human.jsonl")
        try:
            self._audit_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._audit_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except Exception as e:
            logger.warning(f"Failed to persist audit: {e}")

    async def handle_message(
        self,
        user_id: str,
        message: str,
        target_agent: str | None = None,
    ) -> dict[str, Any]:
        """Process a human message through IntentBridge → Agent → response.

        Args:
            user_id: Human user identifier.
            message: Raw natural language input.
            target_agent: Optional agent ID or soul name to target.

        Returns:
            {"response": str, "confidence": float, "intent": str}
        """
        # P1-11: Validate user_id
        try:
            user_id = _validate_user_id(user_id)
        except ValueError as e:
            logger.warning(f"Invalid user_id rejected: {e}")
            return {
                "response": "Invalid user identifier.",
                "confidence": 0.0,
                "intent": "other",
                "status": "error",
            }

        # 1. IntentBridge parse (P1-3: use parse_async if available)
        intent_result = None
        confidence = 1.0
        intent = "other"

        if self.intent_bridge:
            try:
                # P1-3: use parse_async if it's a real async method
                if hasattr(self.intent_bridge, 'parse_async') and inspect.iscoroutinefunction(self.intent_bridge.parse_async):
                    intent_result = await self.intent_bridge.parse_async(message)
                else:
                    intent_result = self.intent_bridge.parse(message)
                confidence = intent_result.confidence
                intent = intent_result.intent
            except Exception as e:
                logger.warning(f"IntentBridge parse failed: {e}")

        # 2. Low confidence → return clarification request
        if intent_result and intent_result.confidence < 0.5 and intent_result.clarification:
            self._log_audit(user_id, message, intent, confidence, "clarification_needed")
            return {
                "response": intent_result.clarification,
                "confidence": confidence,
                "intent": intent,
                "status": "clarification_needed",
            }

        # 3. Resolve target agent (P1-1: don't create soulless agents)
        agent = None
        if target_agent:
            agent = self.kernel.get_agent(target_agent)

        if not agent:
            # P0-3: Prefer default/themis soul, then any available
            agents_dict = getattr(self.kernel, '_agents', {})
            if isinstance(agents_dict, dict) and agents_dict:
                # Try to find a default agent first
                for preferred_id in ("default", "themis"):
                    for a in agents_dict.values():
                        if a.soul and a.soul.id == preferred_id:
                            agent = a
                            break
                    if agent:
                        break
                if not agent:
                    agent = next(iter(agents_dict.values()))
            else:
                # Fallback: create agent only if we have a soul to give it
                souls = getattr(self.kernel, '_souls', {})
                if souls:
                    # P0-3: Prefer default, then themis, then first available
                    soul_id = None
                    for p in ("default", "themis"):
                        if p in souls:
                            soul_id = p
                            break
                    if not soul_id:
                        soul_id = next(iter(souls))
                    try:
                        agent = self.kernel.create_agent(soul_id=soul_id)
                    except Exception as e:
                        logger.error(f"Failed to create agent: {e}")
                if not agent:
                    return {
                        "response": "No agents available. Please initialize the system first.",
                        "confidence": 0.0,
                        "intent": intent,
                        "status": "error",
                    }

        # 4. Build AgentMessage with human metadata (P1-7: use correct content)
        content = message
        if intent_result and isinstance(intent_result.content, str):
            content = intent_result.content

        agent_msg = AgentMessage(
            sender=f"human:{user_id}",
            receiver=agent.id,
            type=MessageType.REQUEST,
            content=content,
            raw_input=message,
            confidence=confidence,
            sender_type="human",
        )

        # 5. Send to agent
        # P0-2: Temporarily set human-mode system prompt for this call only.
        # Safe in asyncio's cooperative multitasking: the handler runs
        # synchronously within publish_async, so no interleaving occurs.
        original_prompt = agent._system_prompt
        try:
            if agent.soul:
                from ..agents.soul_compiler import compile_soul
                human_prompt = compile_soul(agent.soul, output_mode="human")
                # Preserve system_context (P0-1 fix)
                system_ctx = getattr(self.kernel, '_system_context', '')
                agent._system_prompt = (system_ctx + "\n\n" + human_prompt) if system_ctx else human_prompt
            results = await self.kernel._event_bus.publish_async(agent_msg)
        finally:
            agent._system_prompt = original_prompt

        try:
            response_text = None
            if results:
                response_text = results[0] if isinstance(results[0], str) else str(results[0])
        except Exception as e:
            logger.error(f"Response processing failed: {e}")
            response_text = "An error occurred processing your request."  # P0-6

        # 6. Audit log
        self._log_audit(user_id, message, intent, confidence, "completed")

        return {
            "response": response_text or "",
            "confidence": confidence,
            "intent": intent,
            "agent_id": agent.id,
            "status": "completed",
        }

    def _log_audit(self, user_id: str, message: str, intent: str,
                   confidence: float, status: str) -> None:
        entry = {
            "user_id": user_id,
            "message_preview": message[:100],
            "intent": intent,
            "confidence": confidence,
            "status": status,
            "timestamp": time.time(),
        }
        self._audit_log.append(entry)
        if len(self._audit_log) > 1000:
            self._audit_log = self._audit_log[-1000:]
        # P1-4: persist audit
        self._persist_audit(entry)

    def get_audit_log(self, limit: int = 20) -> list[dict[str, Any]]:
        return self._audit_log[-limit:]
