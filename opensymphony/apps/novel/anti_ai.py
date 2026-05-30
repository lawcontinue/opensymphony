"""Anti-AI Writing System — Detect and fix AI writing patterns.

Separate from the auditor (which flags issues), this module provides
actionable fixes: replacement suggestions, paragraph restructuring,
and style injection.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ── Replacement dictionaries ─────────────────────────────────────

# AI high-frequency words → suggested replacements
WORD_REPLACEMENTS = {
    "不禁": ["下意识", "没忍住", "不由自主"],
    "竟然": ["没想到", "出乎意料地", "哪知道"],
    "居然": ["谁能想到", "偏偏", "谁料"],
    "忽然": ["突然间", "冷不丁", "毫无预兆地"],
    "猛地": ["一", "陡然间", "倏地"],
    "仿佛": ["像是", "好似", "跟...似的"],
    "宛如": ["如同", "就像", "跟...一模一样"],
    "倒吸一口凉气": ["瞳孔一缩", "背后一紧", "心往下沉"],
    "目光一凝": ["眼神暗了暗", "眉头一皱", "视线定住"],
    "嘴角微扬": ["嘴角一勾", "扯了扯嘴角", "脸上浮起一丝笑意"],
    "眼中闪过一丝": ["眼里划过", "目光里一闪", "瞳孔里一晃"],
    "一股": ["一阵", "一缕", "一团"],
    "一道": ["一束", "一条", "一线"],
    "一抹": ["一丝", "一点", "几分"],
    "不可思议": ["超出想象", "匪夷所思", "怎么也想不通"],
}

# Forbidden sentence patterns → rewrite suggestions
PATTERN_REWRITES = {
    r"不是.{1,30}而是": "避免'不是A而是B'句式，直接说B或用对比描述",
    r"不仅.{1,30}而且": "避免'不仅A而且B'句式，用递进或并列描述",
    r"无论.{1,30}都": "避免'无论A都B'句式，用条件句或直接陈述",
}

# Cliché phrases → fresher alternatives
CLICHE_FIXES = {
    "如临大敌": ["像碰上了硬茬", "绷紧了神经", "打起了十二分精神"],
    "登时": ["当场", "那一下", "瞬间"],
    "霎时间": ["一眨眼", "瞬间", "电光火石间"],
    "刹那间": ["一瞬", "眨眼间", "转瞬"],
    "难以置信": ["怎么都不敢信", "眼珠子差点瞪出来", "愣是没反应过来"],
    "一股凌厉的杀气": ["一股冷意贴着后脊梁爬上来", "空气里的温度骤然降了几度"],
}


@dataclass
class AntiAIResult:
    """Result of anti-AI processing."""
    original: str
    cleaned: str
    replacements_made: int = 0
    patterns_found: list[str] = None
    cliches_found: list[str] = None

    def __post_init__(self):
        if self.patterns_found is None:
            self.patterns_found = []
        if self.cliches_found is None:
            self.cliches_found = []


class AntiAI:
    """Detect and fix AI writing patterns in Chinese text.

    Usage:
        anti = AntiAI()
        result = anti.process(text)
        print(result.cleaned)  # Text with replacements applied
        print(result.replacements_made)  # Count of changes
    """

    def __init__(self, aggressive: bool = False):
        """
        Args:
            aggressive: If True, apply all replacements. If False, only flag.
        """
        self.aggressive = aggressive

    def process(self, text: str) -> AntiAIResult:
        """Process text for AI writing patterns.

        Args:
            text: Chinese text to process.

        Returns:
            AntiAIResult with cleaned text and statistics.
        """
        result = AntiAIResult(original=text, cleaned=text)

        # 1. Check forbidden patterns (report only, don't auto-rewrite)
        for pattern, suggestion in PATTERN_REWRITES.items():
            matches = list(re.finditer(pattern, text))
            if matches:
                result.patterns_found.append(f"{matches[0].group(0)[:30]}... → {suggestion}")

        # 2. Check clichés
        for cliché, alternatives in CLICHE_FIXES.items():
            if cliché in text:
                result.cliches_found.append(f"「{cliché}」→ 建议: {alternatives[0]}")

        # 3. Auto-replace high-frequency words (if aggressive mode)
        if self.aggressive:
            cleaned = text
            for word, replacements in WORD_REPLACEMENTS.items():
                count = cleaned.count(word)
                if count > 0:
                    # Use first replacement
                    cleaned = cleaned.replace(word, replacements[0])
                    result.replacements_made += count
            result.cleaned = cleaned

        return result

    def get_suggestions(self, text: str) -> list[str]:
        """Get a list of human-readable suggestions without modifying text."""
        result = self.process(text)
        suggestions = []

        for p in result.patterns_found:
            suggestions.append(f"🔴 {p}")
        for c in result.cliches_found:
            suggestions.append(f"🟡 {c}")

        # Check word frequency
        for word, replacements in WORD_REPLACEMENTS.items():
            count = text.count(word)
            if count >= 2:
                suggestions.append(f"🟡「{word}」出现 {count} 次，建议替换为: {', '.join(replacements[:3])}")

        return suggestions

    def merge_short_paragraphs(self, text: str, min_length: int = 35) -> str:
        """Merge consecutive short paragraphs.

        Args:
            text: Input text with paragraphs separated by newlines.
            min_length: Minimum paragraph length.

        Returns:
            Text with short paragraphs merged.
        """
        lines = text.split("\n")
        result = []
        buffer = ""

        for line in lines:
            stripped = line.strip()

            # Preserve headers
            if stripped.startswith("#"):
                if buffer:
                    result.append(buffer)
                    buffer = ""
                result.append(line)
                continue

            # Preserve empty lines between long paragraphs
            if not stripped:
                if buffer:
                    result.append(buffer)
                    buffer = ""
                result.append(line)
                continue

            # If buffer + current line would make a decent paragraph, merge
            if buffer and len(buffer) + len(stripped) < min_length * 2:
                buffer = buffer.rstrip("。！？") + "，" + stripped
            elif len(stripped) < min_length:
                if buffer:
                    result.append(buffer)
                buffer = stripped
            else:
                if buffer:
                    result.append(buffer)
                    buffer = ""
                result.append(line)

        if buffer:
            result.append(buffer)

        return "\n".join(result)
