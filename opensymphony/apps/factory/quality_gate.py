"""Quality Gate — Deterministic rules + AI-assisted scoring.

5 deterministic rules (100% reliable):
  1. Length check (min/max)
  2. Format check (required sections/elements)
  3. Cliche detection (forbidden patterns)
  4. Similarity check (vs recent outputs)
  5. Compliance check (AI label, no sensitive content)

AI scoring is advisory only, never blocks.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger("symphony.apps.factory.quality_gate")


# ── Cliche / forbidden patterns ─────────────────────────────────

AI_MARKER_WORDS = {
    "不禁", "竟然", "居然", "忽然", "猛地", "仿佛", "宛如",
    "一股", "一道", "一抹", "一丝", "微微",
    "倒吸一口凉气", "目光一凝", "嘴角微扬", "眼中闪过一丝",
    "缓缓", "淡淡", "静静", "默默", "轻轻",
    "综上所述", "值得注意的是", "总的来说", "不可否认",
    "显而易见", "众所周知", "由此可见",
}

FORBIDDEN_SENTENCE_PATTERNS = [
    (r"不是.{1,30}而是", "不是…而是…句式"),
    (r"不仅.{1,30}而且", "不仅…而且…句式"),
]

SENSITIVE_PATTERNS = [
    r"国家领导人",
    r"颠覆.*政权",
    r"(?:推翻|推翻).*政府",
]


@dataclass
class QualityResult:
    score: float = 0.0
    passed: bool = False
    tier: str = "B"  # S/A/B
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)

    def summary(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        lines = [f"Quality: {self.score:.0f}/100 ({status}, tier {self.tier})"]
        for i in self.issues:
            lines.append(f"  [ISSUE] {i}")
        for w in self.warnings:
            lines.append(f"  [WARN] {w}")
        return "\n".join(lines)


class QualityGate:
    """Deterministic quality gate for content factory."""

    def __init__(self, recent_hashes: list[str] | None = None,
                 similarity_threshold: float = 0.85):
        self.recent_hashes = recent_hashes or []
        self.recent_texts: list[str] = []  # For similarity check
        self.similarity_threshold = similarity_threshold

    def check(self, text: str, tier: str = "A",
              min_length: int = 500, max_length: int = 10000,
              keywords: list[str] = None,
              require_ai_label: bool = True) -> QualityResult:
        """Run all quality checks. Returns QualityResult."""
        result = QualityResult(tier=tier)
        score = 100.0
        issues = []
        warnings = []

        # ── Rule 1: Length check ──
        text_len = len(text)
        if text_len < min_length:
            deduction = min(30, (min_length - text_len) / min_length * 50)
            score -= deduction
            issues.append(f"文本过短: {text_len} 字 (最低 {min_length})")
        elif text_len > max_length:
            warnings.append(f"文本偏长: {text_len} 字 (上限 {max_length})")

        # ── Rule 2: Format check ──
        if keywords:
            missing = [kw for kw in keywords if kw not in text]
            if missing:
                score -= len(missing) * 3
                warnings.append(f"缺少关键词: {', '.join(missing[:5])}")

        # ── Rule 3: Cliche / AI pattern detection ──
        ai_counts = {w: text.count(w) for w in AI_MARKER_WORDS if w in text}
        threshold = max(1, text_len // 2000)
        heavy_ai = {w: c for w, c in ai_counts.items() if c > threshold}
        if heavy_ai:
            ai_deduction = sum(min(5, c) for c in heavy_ai.values())
            score -= ai_deduction
            warnings.append(f"AI标记词过密: {', '.join(f'{w}×{c}' for w, c in list(heavy_ai.items())[:5])}")

        # Forbidden sentence patterns
        for pattern, name in FORBIDDEN_SENTENCE_PATTERNS:
            matches = list(re.finditer(pattern, text))
            if matches:
                score -= len(matches) * 10
                issues.append(f"禁止句式: {name} (×{len(matches)})")

        # ── Rule 4: Similarity check (hash-based) ──
        text_hash = hashlib.md5(text.encode("utf-8")).hexdigest()
        if text_hash in self.recent_hashes:
            score -= 50
            issues.append("与最近产出完全相同（hash 重复）")
        else:
            # Simple n-gram similarity
            if self.recent_texts:
                max_sim = max(self._similarity(text, t) for t in self.recent_texts[-20:])
                if max_sim > self.similarity_threshold:
                    score -= (max_sim - self.similarity_threshold) * 100
                    warnings.append(f"与最近产出相似度 {max_sim:.0%}")

        # ── Rule 5: Compliance check ──
        for pat in SENSITIVE_PATTERNS:
            if re.search(pat, text):
                score -= 30
                issues.append(f"敏感内容检测: 匹配 {pat[:20]}")

        # AI label check
        if require_ai_label and text_len > 200:
            has_label = any(kw in text for kw in ["AI辅助", "AI生成", "人工智能", "AI"])
            if not has_label:
                warnings.append("缺少 AI 生成标识")

        # ── Finalize ──
        result.score = max(0, round(score, 1))
        result.issues = issues
        result.warnings = warnings
        result.details = {
            "length": text_len,
            "ai_word_count": sum(ai_counts.values()),
            "hash": text_hash,
        }

        # Tier-based pass threshold
        thresholds = {"S": 85, "A": 75, "B": 70}
        threshold_score = thresholds.get(tier, 75)
        result.passed = result.score >= threshold_score and len(issues) == 0

        # Store for future similarity checks
        self.recent_hashes.append(text_hash)
        self.recent_texts.append(text)
        if len(self.recent_hashes) > 100:
            self.recent_hashes = self.recent_hashes[-100:]
            self.recent_texts = self.recent_texts[-100:]

        return result

    @staticmethod
    def _similarity(text1: str, text2: str) -> float:
        """Simple character-level Jaccard similarity."""
        if not text1 or not text2:
            return 0.0
        # Use character bigrams
        def bigrams(t):
            return set(t[i:i+2] for i in range(len(t) - 1))
        b1, b2 = bigrams(text1[:2000]), bigrams(text2[:2000])
        if not b1 or not b2:
            return 0.0
        intersection = len(b1 & b2)
        union = len(b1 | b2)
        return intersection / union if union else 0.0
