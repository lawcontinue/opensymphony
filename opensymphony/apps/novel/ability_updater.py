"""Ability Updater — track character ability changes across chapters.

Detects new abilities, level-ups, and ability usage from Observer facts.
Updates the character_matrix truth file.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from .observer import Fact, FactCategory, ObservationResult
from .truth_files import TruthFile, TruthFiles

logger = logging.getLogger("symphony.apps.novel.ability_updater")


@dataclass
class AbilityChange:
    """A single ability change event."""
    character: str
    ability: str
    change_type: str  # "new", "level_up", "used", "awakened"
    detail: str = ""
    chapter: int = 0


@dataclass
class AbilityUpdateResult:
    """Result of an ability update."""
    chapter: int
    changes: list[AbilityChange] = field(default_factory=list)
    total_abilities: int = 0
    updated: bool = False


class AbilityUpdater:
    """Track ability changes from chapter observations.

    Detects:
    - New abilities discovered (e.g., "灵根觉醒")
    - Level-ups (e.g., "练气三层→四层")
    - Ability usage (e.g., "使用符文")
    - Awakened abilities (e.g., "印记发热")

    Usage:
        updater = AbilityUpdater(truth_files)
        result = updater.update(observation)
    """

    # Patterns for detecting ability changes
    ABILITY_PATTERNS = [
        # Level-up: 练气X层→Y层
        (r"练气\s*[一二三四五六七八九十\d]+\s*层\s*[→到进]\s*[练气]*\s*[一二三四五六七八九十\d]+\s*层", "level_up"),
        # New ability — require the keyword pair within same clause
        (r"(?:获得|觉醒|领悟|习得|掌握)\s*了?\s*.{0,10}?(?:功法|法术|技能|灵根|能力)", "new"),
        # Ability used
        (r"(?:使用|施展|发动|释放|催动)\s*了?\s*.{0,10}?(?:法术|技能|符文|印记|能力)", "used"),
        # Awakened
        (r"(?:印记|符文).{0,5}(?:发光|发热|亮起|闪烁|脉动|激活)", "awakened"),
    ]

    # Blacklist for character name extraction (generic nouns, not names)
    NAME_BLACKLIST = {
        "追杀者", "对方", "敌人", "某人", "主角", "少年", "少女", "老者",
        "男子", "女子", "青年", "中年", "管家", "管事", "弟子", "散修",
        "加入仙门", "件中学",
    }

    def __init__(self, truth_files: TruthFiles):
        self.truth_files = truth_files

    def update(self, observation: ObservationResult, text: str = "") -> AbilityUpdateResult:
        """Scan observation facts and text for ability changes and update truth files.

        Args:
            observation: The ObservationResult from Observer.
            text: Raw chapter text (scanned directly since rule-based Observer
                  may not extract ability-category facts).

        Returns:
            AbilityUpdateResult with detected changes.
        """
        result = AbilityUpdateResult(chapter=observation.chapter)
        changes = []

        # 1. Scan raw text for ability patterns
        if text:
            for pat, change_type in self.ABILITY_PATTERNS:
                for m in re.finditer(pat, text):
                    snippet = m.group(0)
                    # Try to find character name near the match
                    start = max(0, m.start() - 20)
                    context = text[start:m.end() + 20]
                    char = self._extract_character(context)
                    changes.append(AbilityChange(
                        character=char, ability=snippet[:50],
                        change_type=change_type, chapter=observation.chapter,
                    ))

        # 2. Check facts for ability-related changes
        for fact in observation.facts:
            change = self._check_fact(fact)
            if change:
                changes.append(change)

        # 3. Scan state_changes
        for sc in observation.state_changes:
            obj = sc.get("fact", {}).get("object", "")
            subject = sc.get("fact", {}).get("subject", "")
            for pat, change_type in self.ABILITY_PATTERNS:
                if re.search(pat, obj):
                    changes.append(AbilityChange(
                        character=subject, ability=obj[:50],
                        change_type=change_type, chapter=observation.chapter,
                    ))

        # Deduplicate
        seen = set()
        unique = []
        for c in changes:
            key = (c.character, c.ability, c.change_type)
            if key not in seen:
                seen.add(key)
                unique.append(c)

        result.changes = unique

        # 3. Also scan raw text directly for ability patterns
        # (rule-based Observer may not extract these categories)
        if not unique and hasattr(observation, '_raw_text'):
            self._scan_text(observation._raw_text, observation.chapter, unique)

        # 4. Update truth files
        if unique:
            abilities = self.truth_files.get_field(
                TruthFile.CHARACTER_MATRIX, "abilities", {}
            )
            for change in unique:
                char_key = change.character
                if char_key not in abilities:
                    abilities[char_key] = []
                abilities[char_key].append({
                    "ability": change.ability,
                    "type": change.change_type,
                    "chapter": change.chapter,
                })

            delta = {"character_matrix": {"abilities": abilities}}
            self.truth_files.apply_delta(observation.chapter, delta)
            result.updated = True
            result.total_abilities = sum(len(v) for v in abilities.values())

            logger.info(f"Ability update for chapter {observation.chapter}: "
                         f"{len(unique)} changes for {set(c.character for c in unique)}")

        return result

    def _check_fact(self, fact: Fact) -> AbilityChange | None:
        """Check if a single fact represents an ability change."""
        text = f"{fact.predicate} {fact.object_}"

        if fact.category in (FactCategory.INFORMATION, FactCategory.CHARACTER):
            for pat, change_type in self.ABILITY_PATTERNS:
                if re.search(pat, text):
                    return AbilityChange(
                        character=fact.subject,
                        ability=fact.object_[:50],
                        change_type=change_type,
                        detail=fact.predicate,
                        chapter=fact.chapter,
                    )
        return None

    @staticmethod
    def _extract_character(context: str) -> str:
        """Extract a Chinese character name from context text."""
        # Look for name before 的/手腕/手 etc.
        m = re.search(r'([\u4e00-\u9fff]{2,3})(?=的|手腕|手|身体|灵根|符文|印记)', context)
        if m:
            name = m.group(1)
            if name not in AbilityUpdater.NAME_BLACKLIST:
                return name
        return "未知"
