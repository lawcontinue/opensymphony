"""Reflector — Convert observed facts into validated JSON deltas for truth files.

Takes Observer output (Fact list) → generates structured Delta → validates schema → applies to TruthFiles.
This is the "write-back" layer that keeps truth files in sync with chapter content.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .observer import FactCategory, ObservationResult
from .truth_files import Delta, TruthFile, TruthFiles

logger = logging.getLogger("symphony.apps.novel.reflector")


# ── Schema definitions for validation ────────────────────────────

HOOK_STATUSES = {"open", "progressing", "deferred", "resolved"}
SUBPLOT_STATUSES = {"open", "progressing", "resolved", "dormant"}

VALIDATION_RULES = {
    "pending_hooks": {
        "hooks": {
            "type": list,
            "item_schema": {
                "hook_id": {"type": str, "required": True},
                "status": {"type": str, "required": True, "allowed": HOOK_STATUSES},
                "start_chapter": {"type": int, "required": True},
                "content": {"type": str, "required": False},
                "type": {"type": str, "required": False},
            },
        }
    },
    "subplot_board": {
        "subplots": {
            "type": list,
            "item_schema": {
                "status": {"type": str, "required": True, "allowed": SUBPLOT_STATUSES},
                "name": {"type": str, "required": True},
            },
        }
    },
    "chapter_summaries": {
        "rows": {
            "type": list,
            "item_schema": {
                "chapter": {"type": int, "required": True},
                "title": {"type": str, "required": False},
            },
        }
    },
}


@dataclass
class ReflectionResult:
    """Result of reflecting observation into truth file deltas."""
    chapter: int
    delta: Delta | None = None
    validation_errors: list[str] = field(default_factory=list)
    applied: bool = False


class Reflector:
    """Convert observed facts into validated truth file deltas.

    Usage:
        reflector = Reflector(truth_files)
        result = reflector.reflect(observation)
        # result.delta contains the changes
        # result.applied is True if successfully applied
    """

    def __init__(self, truth: TruthFiles, llm_client=None):
        self.truth = truth
        self.llm_client = llm_client

    def reflect(self, observation: ObservationResult) -> ReflectionResult:
        """Generate and apply delta from observation.

        Args:
            observation: Observer output for a chapter.

        Returns:
            ReflectionResult with delta and validation status.
        """
        result = ReflectionResult(chapter=observation.chapter)

        # Build delta from facts
        delta_dict = self._build_delta(observation)

        if not delta_dict:
            logger.info(f"Chapter {observation.chapter}: no changes to reflect")
            return result

        # Validate delta
        errors = self._validate_delta(delta_dict)
        result.validation_errors = errors

        if errors:
            # Try to fix common issues
            delta_dict = self._auto_fix(delta_dict, errors)
            errors = self._validate_delta(delta_dict)
            result.validation_errors = errors

        if errors:
            logger.warning(f"Chapter {observation.chapter}: {len(errors)} validation errors remain")
            return result

        # Apply delta
        try:
            result.delta = self.truth.apply_delta(observation.chapter, delta_dict)
            result.applied = True
            logger.info(f"Chapter {observation.chapter}: reflected {len(delta_dict)} file changes")
        except ValueError as e:
            result.validation_errors.append(str(e))

        return result

    def reflect_and_summarize(self, observation: ObservationResult,
                               chapter_title: str = "",
                               chapter_text: str = "") -> ReflectionResult:
        """Reflect + generate chapter summary row.

        Convenience method that also adds a chapter_summaries entry.
        """
        result = self.reflect(observation)

        if result.applied and chapter_text:
            # Generate summary entry
            summary = self._generate_summary(observation.chapter, chapter_title, chapter_text)
            if summary:
                self.truth.apply_delta(observation.chapter, {
                    "chapter_summaries": {"rows": self.truth.get(TruthFile.CHAPTER_SUMMARIES).get("rows", []) + [summary]}
                })

        return result

    # ── Delta building ───────────────────────────────────────────

    def _build_delta(self, observation: ObservationResult) -> dict[str, Any]:
        """Convert observation into a truth file delta dict."""
        delta: dict[str, Any] = {}

        # Group facts by target truth file
        current_state_changes = {}
        character_changes = {}
        resource_changes = {}
        emotional_changes = {}
        hooks_resolved = observation.resolved_hooks

        for fact in observation.facts:
            if fact.category in (FactCategory.CHARACTER, FactCategory.INFORMATION,
                                  FactCategory.TIME, FactCategory.PHYSICAL):
                key = f"{fact.subject}_{fact.predicate}"
                current_state_changes[key] = {
                    "subject": fact.subject,
                    "predicate": fact.predicate,
                    "object": fact.object_,
                    "validFromChapter": observation.chapter,
                    "sourceChapter": observation.chapter,
                }

            elif fact.category == FactCategory.RELATIONSHIP:
                char_key = fact.subject
                if char_key not in character_changes:
                    character_changes[char_key] = []
                character_changes[char_key].append({
                    "relation": fact.object_,
                    "type": fact.predicate,
                    "chapter": observation.chapter,
                })

            elif fact.category == FactCategory.RESOURCE:
                if fact.subject not in resource_changes:
                    resource_changes[fact.subject] = []
                resource_changes[fact.subject].append({
                    "action": "获得" if "获" in fact.predicate or "得" in fact.predicate else "失去",
                    "item": fact.object_,
                    "chapter": observation.chapter,
                })

            elif fact.category == FactCategory.EMOTION:
                emotional_key = fact.subject
                if emotional_key not in emotional_changes:
                    emotional_changes[emotional_key] = []
                emotional_changes[emotional_key].append({
                    "emotion": fact.object_,
                    "chapter": observation.chapter,
                })

        # Build delta dict
        if current_state_changes:
            delta["current_state"] = {"facts": list(current_state_changes.values())}
            # Also update chapter number
            delta["current_state"]["chapter"] = observation.chapter

        if character_changes:
            delta["character_matrix"] = {"interactions": character_changes}

        if resource_changes:
            delta["particle_ledger"] = {"transactions": resource_changes}

        if emotional_changes:
            delta["emotional_arcs"] = {"changes": emotional_changes}

        # Handle hooks
        if observation.new_hooks:
            existing = self.truth.get(TruthFile.PENDING_HOOKS)
            existing_hooks = existing.get("hooks", [])
            existing_ids = {h.get("hook_id") for h in existing_hooks}
            for hook in observation.new_hooks:
                if hook["hook_id"] not in existing_ids:
                    existing_hooks.append(hook)
            delta["pending_hooks"] = {"hooks": existing_hooks}

        if hooks_resolved:
            existing = self.truth.get(TruthFile.PENDING_HOOKS)
            existing_hooks = existing.get("hooks", [])
            for hook in existing_hooks:
                if hook.get("hook_id") in hooks_resolved:
                    hook["status"] = "resolved"
                    hook["resolved_chapter"] = observation.chapter
            if "pending_hooks" not in delta:
                delta["pending_hooks"] = {"hooks": existing_hooks}

        return delta

    # ── Validation ───────────────────────────────────────────────

    def _validate_delta(self, delta: dict[str, Any]) -> list[str]:
        """Validate delta against schema rules."""
        errors = []

        for file_name, rules in VALIDATION_RULES.items():
            if file_name not in delta:
                continue
            data = delta[file_name]

            for field_name, field_rules in rules.items():
                if field_name not in data:
                    continue

                value = data[field_name]
                expected_type = field_rules.get("type")

                if expected_type and not isinstance(value, expected_type):
                    errors.append(f"{file_name}.{field_name}: expected {expected_type.__name__}, got {type(value).__name__}")
                    continue

                # Validate list items
                if isinstance(value, list) and "item_schema" in field_rules:
                    for i, item in enumerate(value):
                        if not isinstance(item, dict):
                            errors.append(f"{file_name}.{field_name}[{i}]: expected dict")
                            continue
                        for key, key_rules in field_rules["item_schema"].items():
                            if key_rules.get("required") and key not in item:
                                errors.append(f"{file_name}.{field_name}[{i}]: missing required '{key}'")
                            elif key in item:
                                if "allowed" in key_rules and item[key] not in key_rules["allowed"]:
                                    errors.append(f"{file_name}.{field_name}[{i}].{key}: '{item[key]}' not in {key_rules['allowed']}")

        return errors

    def _auto_fix(self, delta: dict[str, Any], errors: list[str]) -> dict[str, Any]:
        """Try to auto-fix common validation errors."""
        fixed = dict(delta)

        for error in errors:
            # Fix hook status values
            if "not in" in error and "status" in error:
                match = re.search(r"'(\w+)' not in", error)
                if match:
                    bad_status = match.group(1)
                    # Default to "open" if invalid status
                    for file_name in ("pending_hooks", "subplot_board"):
                        if file_name in fixed:
                            data = fixed[file_name]
                            for field in ("hooks", "subplots"):
                                if field in data and isinstance(data[field], list):
                                    for item in data[field]:
                                        if item.get("status") == bad_status:
                                            item["status"] = "open"

        return fixed

    # ── Summary generation ───────────────────────────────────────

    def _generate_summary(self, chapter: int, title: str, text: str) -> dict:
        """Generate a chapter summary entry."""
        # Extract first 200 chars as summary
        clean = re.sub(r'#.*\n', '', text).strip()
        summary_text = clean[:200] + "..." if len(clean) > 200 else clean

        # Extract character names (simple heuristic)
        characters = list(set(re.findall(r'[\u4e00-\u9fff]{2,4}(?=说|想|看|走|跑|站|坐|笑|叹|喊)', text)))[:5]

        return {
            "chapter": chapter,
            "title": title or f"第{chapter}章",
            "characters": ",".join(characters),
            "events": summary_text,
            "stateChanges": f"见第{chapter}章事实提取",
        }
