"""Relationship Updater — track character relationship changes across chapters.

Detects new relationships, trust changes, alliances, betrayals, etc.
Updates the character_matrix truth file.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from .observer import Fact, FactCategory, ObservationResult
from .truth_files import TruthFile, TruthFiles

logger = logging.getLogger("symphony.apps.novel.relationship_updater")


@dataclass
class RelationshipChange:
    """A single relationship change event."""
    character_a: str
    character_b: str
    change_type: str  # "new", "trust_up", "trust_down", "alliance", "betrayal", "conflict"
    detail: str = ""
    chapter: int = 0


@dataclass
class RelationshipUpdateResult:
    """Result of a relationship update."""
    chapter: int
    changes: list[RelationshipChange] = field(default_factory=list)
    total_relationships: int = 0
    updated: bool = False


class RelationshipUpdater:
    """Track relationship changes from chapter observations.

    Detects:
    - New character introductions
    - Trust changes (positive/negative interactions)
    - Alliances formed
    - Betrayals / conflicts
    - Power dynamics (superior/subordinate)

    Usage:
        updater = RelationshipUpdater(truth_files)
        result = updater.update(observation)
    """

    # Relationship indicators
    POSITIVE_SIGNALS = [
        r"帮助|包扎|救治|保护|信任|合作|联手|支持",
        r"点头|微笑|感激|答应",
    ]
    NEGATIVE_SIGNALS = [
        r"追杀|攻击|欺骗|背叛|威胁|恐吓|扣|扣押",
        r"冷笑|嘲讽|不屑|拒绝",
    ]
    ALLIANCE_SIGNALS = [
        r"结盟|合作|联手|契约|约定|一起",
    ]
    CONFLICT_SIGNALS = [
        r"对峙|冲突|争吵|矛盾|敌意|警惕|防备",
    ]

    # Blacklist: generic nouns that are not character names
    NAME_BLACKLIST = {
        "追杀者", "对方", "敌人", "某人", "主角", "少年", "少女", "老者",
        "男子", "女子", "青年", "中年", "管家", "管事", "弟子", "散修",
        "什么", "这个", "那个", "自己", "他们", "她们", "我们",
    }

    def __init__(self, truth_files: TruthFiles):
        self.truth_files = truth_files

    def update(self, observation: ObservationResult, text: str = "") -> RelationshipUpdateResult:
        """Scan observation and text for relationship changes and update truth files.

        Args:
            observation: The ObservationResult from Observer.
            text: Raw chapter text (scanned directly since rule-based Observer
                  may not extract relationship-category facts).

        Returns:
            RelationshipUpdateResult with detected changes.
        """
        result = RelationshipUpdateResult(chapter=observation.chapter)
        changes = []

        # 1. Scan raw text directly for relationship signals
        if text:
            changes.extend(self._scan_text(text, observation.chapter))

        # 2. Process relationship-category facts
        for fact in observation.facts:
            if fact.category == FactCategory.RELATIONSHIP:
                change = self._process_relationship_fact(fact)
                if change:
                    changes.append(change)

        # 3. Infer relationships from character interactions
        characters_in_chapter = set()
        for fact in observation.facts:
            if fact.category in (FactCategory.CHARACTER, FactCategory.EMOTION):
                characters_in_chapter.add(fact.subject)

        # Check for implied relationships between co-present characters
        if len(characters_in_chapter) >= 2:
            chars = list(characters_in_chapter)
            for i in range(len(chars)):
                for j in range(i + 1, len(chars)):
                    implied = self._infer_from_interaction(
                        chars[i], chars[j], observation)
                    if implied:
                        changes.append(implied)

        # Deduplicate
        seen = set()
        unique = []
        for c in changes:
            key = tuple(sorted([c.character_a, c.character_b]) + [c.change_type])
            if key not in seen:
                seen.add(key)
                unique.append(c)

        result.changes = unique

        # 3. Update truth files
        if unique:
            relationships = self.truth_files.get_field(
                TruthFile.CHARACTER_MATRIX, "relationships", {}
            )

            for change in unique:
                pair_key = f"{change.character_a}↔{change.character_b}"
                if pair_key not in relationships:
                    relationships[pair_key] = {
                        "characters": [change.character_a, change.character_b],
                        "history": [],
                    }
                relationships[pair_key]["history"].append({
                    "type": change.change_type,
                    "detail": change.detail,
                    "chapter": change.chapter,
                })
                # Update latest status
                relationships[pair_key]["latest"] = change.change_type
                relationships[pair_key]["latest_chapter"] = change.chapter

            delta = {"character_matrix": {"relationships": relationships}}
            self.truth_files.apply_delta(observation.chapter, delta)
            result.updated = True
            result.total_relationships = len(relationships)

            logger.info(f"Relationship update for chapter {observation.chapter}: "
                         f"{len(unique)} changes")

        return result

    def _scan_text(self, text: str, chapter: int) -> list[RelationshipChange]:
        """Scan raw text for relationship signals between named characters."""
        changes = []
        # Find character names (2-3 Chinese chars before/after action verbs)
        raw_names = re.findall(r'([\u4e00-\u9fff]{2,3})(?:帮助|包扎|救治|保护|告诉|追杀|攻击|扣押|说|看)', text)
        # Also find names as objects: verb + 了 + name
        raw_names += re.findall(r'(?:帮助|包扎|救治|保护|告诉|追杀|攻击|扣押)(?:了)?([\u4e00-\u9fff]{2,3})', text)
        char_names = [n for n in dict.fromkeys(raw_names) if n not in self.NAME_BLACKLIST][:5]
        if len(char_names) < 2:
            return changes  # Need at least 2 named characters

        for signals, change_type in [
            (self.POSITIVE_SIGNALS, "trust_up"),
            (self.NEGATIVE_SIGNALS, "trust_down"),
            (self.ALLIANCE_SIGNALS, "alliance"),
            (self.CONFLICT_SIGNALS, "conflict"),
        ]:
            for pat in signals:
                for m in re.finditer(pat, text):
                    start = max(0, m.start() - 30)
                    end = min(len(text), m.end() + 30)
                    context = text[start:end]
                    names_in_context = [n for n in char_names if n in context]
                    if len(names_in_context) >= 2:
                        changes.append(RelationshipChange(
                            character_a=names_in_context[0],
                            character_b=names_in_context[1],
                            change_type=change_type, detail=context[:100],
                            chapter=chapter,
                        ))
        return changes

    def _process_relationship_fact(self, fact: Fact) -> RelationshipChange | None:
        """Convert a relationship fact into a RelationshipChange."""
        text = f"{fact.predicate} {fact.object_}"

        # Determine change type
        for signals, change_type in [
            (self.ALLIANCE_SIGNALS, "alliance"),
            (self.CONFLICT_SIGNALS, "conflict"),
            (self.POSITIVE_SIGNALS, "trust_up"),
            (self.NEGATIVE_SIGNALS, "trust_down"),
        ]:
            for pat in signals:
                if re.search(pat, text):
                    return RelationshipChange(
                        character_a=fact.subject,
                        character_b=fact.object_,
                        change_type=change_type,
                        detail=text[:100],
                        chapter=fact.chapter,
                    )
        return None

    def _infer_from_interaction(self, char_a: str, char_b: str,
                                observation: ObservationResult) -> RelationshipChange | None:
        """Infer a relationship from co-occurring character actions."""
        # Check if both characters appear in the chapter's facts
        a_facts = [f for f in observation.facts if f.subject == char_a]
        b_facts = [f for f in observation.facts if f.subject == char_b]

        if not a_facts or not b_facts:
            return None

        # Check existing relationships
        relationships = self.truth_files.get_field(
            TruthFile.CHARACTER_MATRIX, "relationships", {}
        )
        pair_key = f"{char_a}↔{char_b}"
        if pair_key in relationships:
            return None  # Already tracked

        # New interaction detected — classify based on context
        all_text = " ".join(f"{f.predicate} {f.object_}" for f in a_facts + b_facts)

        for signals, change_type in [
            (self.POSITIVE_SIGNALS, "trust_up"),
            (self.NEGATIVE_SIGNALS, "trust_down"),
        ]:
            for pat in signals:
                if re.search(pat, all_text):
                    return RelationshipChange(
                        character_a=char_a,
                        character_b=char_b,
                        change_type=change_type,
                        detail=f"首次互动（{change_type}）",
                        chapter=observation.chapter,
                    )

        # Default: new neutral relationship
        return RelationshipChange(
            character_a=char_a,
            character_b=char_b,
            change_type="new",
            detail="首次同框",
            chapter=observation.chapter,
        )
