"""Observer — Extract structured facts from chapter text.

Uses LLM to extract 8 categories of facts from written chapters.
Outputs a list of Fact objects that the Reflector can apply to truth files.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("symphony.apps.novel.observer")


class FactCategory(str, Enum):
    CHARACTER = "character"        # 角色位置/状态变化
    RESOURCE = "resource"          # 物品/货币获得或失去
    RELATIONSHIP = "relationship"  # 关系变化
    HOOK = "hook"                  # 新伏笔或伏笔回收
    INFORMATION = "information"    # 新信息揭示
    EMOTION = "emotion"            # 情感变化
    TIME = "time"                  # 时间推进
    PHYSICAL = "physical"          # 物理状态（伤势/环境）


@dataclass
class Fact:
    """A single fact extracted from chapter text."""
    category: FactCategory
    subject: str          # Who/what this fact is about
    predicate: str        # What changed (e.g., "位置", "获得", "关系")
    object_: str          # The value or target
    chapter: int = 0
    source_text: str = ""  # Original text snippet (for verification)
    confidence: float = 1.0


@dataclass
class ObservationResult:
    """Result of observing a chapter."""
    chapter: int
    facts: list[Fact] = field(default_factory=list)
    new_hooks: list[dict] = field(default_factory=list)        # New foreshadowing
    resolved_hooks: list[str] = field(default_factory=list)    # Hook IDs resolved
    state_changes: list[dict] = field(default_factory=list)    # State delta suggestions


# ── Extraction prompt templates ──────────────────────────────────

EXTRACT_PROMPT = """你是一个小说事实提取器。从以下章节文本中提取所有事实变化。

提取以下 8 类事实：
- character: 角色位置变化、状态变化、能力变化
- resource: 物品获得/失去、货币变化
- relationship: 角色间关系变化（结盟/反目/信任变化）
- hook: 新的伏笔铺垫、旧伏笔回收
- information: 新信息揭示（秘密、真相、线索）
- emotion: 角色情感变化
- time: 时间推进（时间跳转、昼夜变化）
- physical: 物理状态变化（伤势、环境、天气）

输出纯 JSON 数组，每个事实格式：
{{"category":"character","subject":"张律","predicate":"位置","object":"密林深处","source":"原文片段","confidence":0.9}}

章节文本：
---
{text}
---

输出 JSON 数组（不要其他文字）："""


class Observer:
    """Extract structured facts from chapter text.

    Can work in two modes:
    1. LLM mode: Uses an agent/LLM to extract facts (preferred)
    2. Rule mode: Uses regex patterns for simple extractions (fallback)
    """

    def __init__(self, llm_client=None):
        """
        Args:
            llm_client: Optional callable that takes (prompt, max_tokens) -> str
        """
        self.llm_client = llm_client

    def observe(self, chapter: int, text: str) -> ObservationResult:
        """Extract facts from chapter text.

        Args:
            chapter: Chapter number.
            text: Full chapter text.

        Returns:
            ObservationResult with extracted facts and suggestions.
        """
        result = ObservationResult(chapter=chapter)

        # Try LLM extraction first
        if self.llm_client:
            facts = self._extract_via_llm(text)
        else:
            facts = self._extract_via_rules(text)

        result.facts = facts

        # Classify hooks
        for fact in facts:
            if fact.category == FactCategory.HOOK:
                if "回收" in fact.object_ or "揭示" in fact.object_ or "resolved" in fact.object_.lower():
                    result.resolved_hooks.append(fact.subject)
                else:
                    result.new_hooks.append({
                        "hook_id": f"{fact.subject[:20]}-{chapter}",
                        "type": fact.predicate,
                        "content": fact.object_,
                        "start_chapter": chapter,
                        "status": "open",
                    })

        # Build state change suggestions
        result.state_changes = self._build_state_changes(facts)

        logger.info(f"Chapter {chapter}: extracted {len(facts)} facts, "
                     f"{len(result.new_hooks)} new hooks, "
                     f"{len(result.resolved_hooks)} resolved hooks")
        return result

    def _extract_via_llm(self, text: str) -> list[Fact]:
        """Extract facts using LLM."""
        # Truncate text if too long
        input_text = text[:6000] if len(text) > 6000 else text

        prompt = EXTRACT_PROMPT.format(text=input_text)
        try:
            response = self.llm_client(prompt, max_tokens=2000)
            return self._parse_facts(response, 0)
        except Exception as e:
            logger.warning(f"LLM extraction failed: {e}, falling back to rules")
            return self._extract_via_rules(text)

    def _extract_via_rules(self, text: str) -> list[Fact]:
        """Extract facts using regex patterns (fallback)."""
        facts = []

        # Extract character movements
        move_patterns = [
            r"([^，。]{1,10})(?:往|朝|向|冲向|奔向|走到|跑向)([^，。]{1,20})",
            r"([^，。]{1,10})来到了([^，。]{1,20})",
        ]
        for pat in move_patterns:
            for m in re.finditer(pat, text):
                facts.append(Fact(
                    category=FactCategory.CHARACTER,
                    subject=m.group(1).strip(),
                    predicate="位置移动",
                    object_=m.group(2).strip(),
                    source_text=m.group(0),
                    confidence=0.7,
                ))

        # Extract injuries
        injury_patterns = [
            r"(?:伤|伤口|划伤|割伤|撕裂|骨折|断|肿|流血|出血).{0,20}([^，。]{1,15})",
        ]
        for pat in injury_patterns:
            for m in re.finditer(pat, text):
                facts.append(Fact(
                    category=FactCategory.PHYSICAL,
                    subject="主角",
                    predicate="受伤",
                    object_=m.group(0)[:50],
                    source_text=m.group(0),
                    confidence=0.8,
                ))

        # Extract resource acquisition
        resource_patterns = [
            r"([^，。]{1,10})(?:拿|取|收|捡|获得|得到|买了)([^，。]{1,20})",
            r"([^，。]{1,10})(?:灵石|丹药|法器|宝物|秘籍|卷轴)",
        ]
        for pat in resource_patterns:
            for m in re.finditer(pat, text):
                facts.append(Fact(
                    category=FactCategory.RESOURCE,
                    subject=m.group(1).strip() if m.lastindex >= 1 else "未知",
                    predicate="获得",
                    object_=m.group(0)[-30:],
                    source_text=m.group(0),
                    confidence=0.6,
                ))

        # Deduplicate
        seen = set()
        unique = []
        for f in facts:
            key = (f.category, f.subject, f.predicate, f.object_)
            if key not in seen:
                seen.add(key)
                f.chapter = 0  # Will be set by caller
                unique.append(f)

        return unique

    def _parse_facts(self, llm_output: str, chapter: int) -> list[Fact]:
        """Parse LLM output into Fact objects."""
        # Try to find JSON array
        match = re.search(r'\[.*\]', llm_output, re.DOTALL)
        if not match:
            logger.warning("No JSON array found in LLM output")
            return []

        try:
            items = json.loads(match.group())
        except json.JSONDecodeError:
            logger.warning("Failed to parse JSON from LLM output")
            return []

        facts = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                cat_str = item.get("category", "physical")
                cat = FactCategory(cat_str)
            except ValueError:
                cat = FactCategory.PHYSICAL

            facts.append(Fact(
                category=cat,
                subject=item.get("subject", "未知"),
                predicate=item.get("predicate", ""),
                object_=item.get("object", ""),
                chapter=chapter,
                source_text=item.get("source", ""),
                confidence=item.get("confidence", 0.8),
            ))

        return facts

    def _build_state_changes(self, facts: list[Fact]) -> list[dict]:
        """Build state change suggestions from extracted facts."""
        changes = []
        for fact in facts:
            change = {
                "file": self._fact_to_truth_file(fact),
                "fact": {
                    "subject": fact.subject,
                    "predicate": fact.predicate,
                    "object": fact.object_,
                },
            }
            changes.append(change)
        return changes

    @staticmethod
    def _fact_to_truth_file(fact: Fact) -> str:
        """Map a fact category to the most relevant truth file."""
        mapping = {
            FactCategory.CHARACTER: "current_state",
            FactCategory.RESOURCE: "particle_ledger",
            FactCategory.RELATIONSHIP: "character_matrix",
            FactCategory.HOOK: "pending_hooks",
            FactCategory.INFORMATION: "current_state",
            FactCategory.EMOTION: "emotional_arcs",
            FactCategory.TIME: "current_state",
            FactCategory.PHYSICAL: "current_state",
        }
        return mapping.get(fact.category, "current_state")
