"""Truth Updater — bridge between Observer (fact extraction) and TruthFiles (state management).

After each chapter is written, the TruthUpdater:
1. Observes the chapter text → extracts facts (Observer)
2. Converts facts → delta format
3. Applies delta → TruthFiles (snapshot + apply_delta)
4. Saves updated state to disk
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .observer import Fact, FactCategory, ObservationResult, Observer
from .truth_files import TruthFile, TruthFiles

logger = logging.getLogger("symphony.apps.novel.truth_updater")


@dataclass
class UpdateResult:
    """Result of a truth update operation."""
    chapter: int
    facts_extracted: int = 0
    delta_applied: dict[str, int] = field(default_factory=dict)  # {file_name: change_count}
    new_hooks: int = 0
    resolved_hooks: int = 0
    snapshot_created: bool = False
    errors: list[str] = field(default_factory=list)


class TruthUpdater:
    """Update truth files from chapter text.

    Usage:
        updater = TruthUpdater(truth_files, observer)
        result = updater.update(chapter=2, text=chapter_2_text)
    """

    def __init__(self, truth_files: TruthFiles, observer: Observer | None = None):
        self.truth_files = truth_files
        self.observer = observer or Observer()  # Default: rule-based extraction

    def update(self, chapter: int, text: str) -> UpdateResult:
        """Extract facts from chapter text and update truth files.

        Args:
            chapter: Chapter number (1-indexed).
            text: Full chapter text.

        Returns:
            UpdateResult with summary of changes.
        """
        result = UpdateResult(chapter=chapter)

        # 1. Observe chapter → extract facts
        try:
            observation = self.observer.observe(chapter, text)
            result.facts_extracted = len(observation.facts)
            result.new_hooks = len(observation.new_hooks)
            result.resolved_hooks = len(observation.resolved_hooks)
        except Exception as e:
            result.errors.append(f"Observation failed: {e}")
            logger.warning(f"Observation failed for chapter {chapter}: {e}")
            return result

        # 2. Convert facts → delta
        delta = self._build_delta(observation)

        # 3. Apply delta to truth files
        if delta:
            try:
                self.truth_files.apply_delta(chapter, delta)
                for file_name, changes in delta.items():
                    result.delta_applied[file_name] = len(changes) if isinstance(changes, dict) else 1
            except Exception as e:
                result.errors.append(f"Delta apply failed: {e}")
                logger.warning(f"Delta apply failed for chapter {chapter}: {e}")

        # 4. Snapshot AFTER applying delta (so snapshot reflects chapter state)
        try:
            self.truth_files.snapshot(chapter)
            result.snapshot_created = True
        except Exception as e:
            result.errors.append(f"Snapshot failed: {e}")
            logger.warning(f"Snapshot failed for chapter {chapter}: {e}")

        # 5. Save to disk
        try:
            self.truth_files.save()
        except Exception as e:
            result.errors.append(f"Save failed: {e}")

        logger.info(f"Truth updated for chapter {chapter}: "
                     f"{result.facts_extracted} facts, "
                     f"{len(result.delta_applied)} files changed, "
                     f"{result.new_hooks} new hooks, "
                     f"{result.resolved_hooks} resolved hooks")
        return result

    def _build_delta(self, observation: ObservationResult) -> dict[str, dict]:
        """Convert observation results into truth file delta format.

        Groups facts by their target truth file, then merges into
        structured updates per file.
        """
        delta: dict[str, dict] = {}

        # Group facts by target truth file
        facts_by_file: dict[str, list[Fact]] = {}
        for fact in observation.facts:
            target = self._fact_to_truth_file(fact)
            facts_by_file.setdefault(target, []).append(fact)

        # Build delta for each truth file
        for file_name, facts in facts_by_file.items():
            delta[file_name] = self._build_file_delta(file_name, facts)

        # Handle hooks
        if observation.new_hooks:
            existing_hooks = self.truth_files.get_field(
                TruthFile.PENDING_HOOKS, "hooks", [])
            existing_ids = {h.get("hook_id") for h in existing_hooks}
            new_unique = [h for h in observation.new_hooks
                          if h.get("hook_id") not in existing_ids]
            if new_unique:
                hooks_delta = delta.setdefault("pending_hooks", {})
                hooks_delta["hooks"] = existing_hooks + new_unique

        # Handle resolved hooks
        if observation.resolved_hooks:
            existing_hooks = self.truth_files.get_field(
                TruthFile.PENDING_HOOKS, "hooks", [])
            for hook in existing_hooks:
                if hook.get("hook_id") in observation.resolved_hooks:
                    hook["status"] = "resolved"
                    hook["resolved_chapter"] = observation.facts[0].chapter if observation.facts else 0
            hooks_delta = delta.setdefault("pending_hooks", {})
            hooks_delta["hooks"] = existing_hooks

        # Add chapter summary
        summaries = self.truth_files.get_field(
            TruthFile.CHAPTER_SUMMARIES, "rows", [])
        # Don't add duplicate
        if not any(r.get("chapter") == observation.chapter for r in summaries):
            summary = self._build_chapter_summary(observation)
            summaries.append(summary)
            delta.setdefault("chapter_summaries", {})["rows"] = summaries

        return delta

    def _build_file_delta(self, file_name: str, facts: list[Fact]) -> dict:
        """Build a delta dict for a specific truth file from its facts."""
        if file_name == "current_state":
            return self._build_current_state_delta(facts)
        elif file_name == "character_matrix":
            return self._build_character_delta(facts)
        elif file_name == "particle_ledger":
            return self._build_resource_delta(facts)
        elif file_name == "emotional_arcs":
            return self._build_emotion_delta(facts)
        else:
            # Generic: just list the facts
            return {"updates": [
                {"subject": f.subject, "predicate": f.predicate,
                 "object": f.object_, "category": f.category.value}
                for f in facts
            ]}

    def _build_current_state_delta(self, facts: list[Fact]) -> dict:
        """Build delta for current_state truth file."""
        delta: dict[str, Any] = {}
        for fact in facts:
            if fact.category == FactCategory.CHARACTER:
                key = f"{fact.subject}_location"
                delta[key] = fact.object_
            elif fact.category == FactCategory.TIME:
                delta["time"] = fact.object_
            elif fact.category == FactCategory.INFORMATION:
                infos = delta.get("revealed_information", [])
                infos.append({"subject": fact.subject, "detail": fact.object_})
                delta["revealed_information"] = infos
            elif fact.category == FactCategory.PHYSICAL:
                key = f"{fact.subject}_physical"
                delta[key] = fact.object_
        return delta

    def _build_character_delta(self, facts: list[Fact]) -> dict:
        """Build delta for character_matrix truth file."""
        delta: dict[str, Any] = {}
        for fact in facts:
            if fact.category == FactCategory.RELATIONSHIP:
                key = f"relationship_{fact.subject}"
                delta[key] = {"target": fact.object_, "change": fact.predicate}
        return delta

    def _build_resource_delta(self, facts: list[Fact]) -> dict:
        """Build delta for particle_ledger truth file."""
        delta: dict[str, Any] = {}
        for fact in facts:
            if fact.category == FactCategory.RESOURCE:
                key = f"resource_{fact.subject}"
                delta[key] = {"item": fact.object_, "action": fact.predicate}
        return delta

    def _build_emotion_delta(self, facts: list[Fact]) -> dict:
        """Build delta for emotional_arcs truth file."""
        delta: dict[str, Any] = {}
        for fact in facts:
            if fact.category == FactCategory.EMOTION:
                key = f"emotion_{fact.subject}"
                delta[key] = fact.object_
        return delta

    @staticmethod
    def _fact_to_truth_file(fact: Fact) -> str:
        """Map fact category to truth file name."""
        return Observer._fact_to_truth_file(fact)

    @staticmethod
    def _build_chapter_summary(observation: ObservationResult) -> dict:
        """Build a chapter summary entry from observation."""
        categories = {}
        for fact in observation.facts:
            cat = fact.category.value
            categories.setdefault(cat, []).append(
                f"{fact.subject} {fact.predicate} {fact.object_}")

        return {
            "chapter": observation.chapter,
            "fact_count": len(observation.facts),
            "categories": {k: len(v) for k, v in categories.items()},
            "highlights": [
                f"{fact.subject} {fact.predicate} {fact.object_}"
                for fact in observation.facts[:5]
            ],
            "new_hooks": len(observation.new_hooks),
            "resolved_hooks": len(observation.resolved_hooks),
        }
