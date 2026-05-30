"""Echo Engine — Layer 5 of Reverberate. Active skill interceptor.

Before each LLM call, checks if any active skill matches the context.
If so, applies pre_action (e.g. prefix injection).
After each LLM call, applies post_action (e.g. JSON extraction).

Safety: Echo Engine can only apply pre/post actions defined in approved skills.
It cannot modify Soul prompts or code.
"""
from __future__ import annotations

import logging
import re

from .skill_registry import Skill, SkillRegistry

logger = logging.getLogger("symphony.echo")


class EchoEngine:
    """Intercepts LLM calls and applies active skills."""

    def __init__(self, registry: SkillRegistry):
        self.registry = registry
        self._active_cache: list[Skill] = []
        self._cache_ts: float = 0
        self._cache_ttl: float = 60.0  # Refresh cache every 60s

    def _get_active(self) -> list[Skill]:
        """Get cached active skills, refresh periodically."""
        import time
        now = time.time()
        if now - self._cache_ts > self._cache_ttl:
            self._active_cache = self.registry.get_active_skills()
            self._cache_ts = now
        return self._active_cache

    def pre_process(
        self, soul_id: str, model: str, task_type: str, messages: list[dict],
    ) -> tuple[list[dict], list[str]]:
        """Apply pre-actions before LLM call. Returns (modified_messages, matched_skill_ids)."""
        matched = []
        skill_ids = []

        for skill in self._get_active():
            if self._matches(skill, soul_id, model, task_type):
                matched.append(skill)
                skill_ids.append(skill.id)

        if not matched:
            return messages, []

        modified = list(messages)  # shallow copy
        for skill in matched:
            action = skill.pre_action
            if not action:
                continue

            if action.startswith("add_prefix:"):
                prefix = action[len("add_prefix:"):]
                if modified and modified[-1].get("role") == "user":
                    modified[-1] = dict(modified[-1])  # copy
                    modified[-1]["content"] = f"{prefix}\n\n{modified[-1]['content']}"
                logger.info(f"Echo: applied pre_action '{action[:50]}' (skill={skill.id})")

            elif action.startswith("set_param:"):
                # e.g. "set_param:max_tokens=4096" — handled by caller
                logger.info(f"Echo: noted param override '{action}' (skill={skill.id})")

            elif action.startswith("inject_system:"):
                text = action[len("inject_system:"):]
                modified.insert(0, {"role": "system", "content": text})
                logger.info(f"Echo: injected system message (skill={skill.id})")

        return modified, skill_ids

    def post_process(
        self, soul_id: str, model: str, content: str, skill_ids: list[str],
    ) -> str:
        """Apply post-actions after LLM call. Returns modified content."""
        if not skill_ids:
            return content

        for sid in skill_ids:
            skill = self._find_skill(sid)
            if not skill or not skill.post_action:
                continue

            action = skill.post_action

            if action == "extract_json":
                content = self._extract_json(content)
                logger.info(f"Echo: extracted JSON (skill={sid})")

            elif action == "strip_thinking":
                content = self._strip_thinking(content)
                logger.info(f"Echo: stripped thinking (skill={sid})")

            elif action.startswith("strip_prefix:"):
                prefix = action[len("strip_prefix:"):]
                if content.startswith(prefix):
                    content = content[len(prefix):].strip()

            # Record successful trigger
            self.registry.record_trigger(sid, success=True)

        return content

    def _matches(self, skill: Skill, soul_id: str, model: str, task_type: str) -> bool:
        """Check if a skill's trigger matches the current context."""
        trigger = skill.trigger
        conditions = [c.strip() for c in trigger.split("AND")]

        for cond in conditions:
            if cond.startswith("soul_id="):
                if soul_id != cond.split("=", 1)[1]:
                    return False
            elif cond.startswith("model="):
                if cond.split("=", 1)[1] not in model:
                    return False
            elif cond.startswith("task_type="):
                if task_type != cond.split("=", 1)[1]:
                    return False
            elif cond.startswith("error="):
                # Error matching happens in post_process context
                pass
        return True

    def _find_skill(self, skill_id: str) -> Skill | None:
        for s in self._active_cache:
            if s.id == skill_id:
                return s
        return None

    @staticmethod
    def _extract_json(text: str) -> str:
        """Extract JSON from potentially noisy output."""
        import json as _json
        text = text.strip()
        try:
            _json.loads(text)
            return text
        except (ValueError, Exception):
            pass
        # Try code block
        m = re.search(r'```(?:json)?\s*\n?(.*?)```', text, re.DOTALL)
        if m:
            extracted = m.group(1).strip()
            if len(extracted) > 20:
                return extracted
        # Try finding { or [
        for sc, ec in [('{', '}'), ('[', ']')]:
            idx = text.find(sc)
            if idx >= 0:
                depth = 0
                for i in range(idx, len(text)):
                    if text[i] == sc: depth += 1
                    elif text[i] == ec: depth -= 1
                    if depth == 0:
                        candidate = text[idx:i+1].strip()
                        if len(candidate) > 20:
                            return candidate
        return text

    @staticmethod
    def _strip_thinking(text: str) -> str:
        """Remove thinking chain from output."""
        if "</think" in text:
            idx = text.find("</think")
            text = text[idx + len("</think"):]
            gt = text.find(">")
            if gt >= 0:
                text = text[gt + 1:]
        return text.strip()

    def get_param_overrides(self, soul_id: str, model: str, task_type: str) -> dict:
        """Get parameter overrides from matching skills (e.g. max_tokens)."""
        overrides = {}
        for skill in self._get_active():
            if not self._matches(skill, soul_id, model, task_type):
                continue
            action = skill.pre_action
            if action and action.startswith("set_param:"):
                param_str = action[len("set_param:"):]
                if "=" in param_str:
                    key, val = param_str.split("=", 1)
                    try:
                        overrides[key] = int(val)
                    except ValueError:
                        overrides[key] = val
        return overrides
