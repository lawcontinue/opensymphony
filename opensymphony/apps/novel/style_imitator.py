"""Style Imitator — Extract writing style fingerprint and apply to generation.

Reads reference texts to build a style fingerprint:
  - Sentence length distribution
  - Paragraph length distribution
  - Favorite vocabulary/phrases
  - Punctuation habits
  - Dialogue ratio
  - Sensory preference
  - Sentence openers

Then generates a style prompt that can guide an LLM to match the style.
"""
from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field

logger = logging.getLogger("symphony.apps.novel.style_imitator")


@dataclass
class StyleFingerprint:
    """Quantified writing style profile extracted from reference texts."""
    # Sentence stats
    avg_sentence_len: float = 0.0
    sentence_len_std: float = 0.0
    short_ratio: float = 0.0     # <10 chars
    long_ratio: float = 0.0      # >30 chars

    # Paragraph stats
    avg_paragraph_len: float = 0.0
    paragraph_len_std: float = 0.0

    # Punctuation
    comma_period_ratio: float = 0.0
    excl_ratio: float = 0.0      # ! and ?
    ellipsis_ratio: float = 0.0

    # Dialogue
    dialogue_ratio: float = 0.0
    avg_dialogue_len: float = 0.0

    # Top vocabulary (excluding stop words)
    top_words: list[tuple[str, int]] = field(default_factory=list)

    # Sentence openers (first 2 chars after sentence break)
    top_openers: list[tuple[str, int]] = field(default_factory=list)

    # Sensory preference
    sensory_profile: dict[str, float] = field(default_factory=dict)

    # Paragraph count from reference
    sample_size: int = 0
    total_chars: int = 0


# Stop words for Chinese text
STOP_WORDS = {
    "的", "了", "在", "是", "我", "他", "她", "它", "有", "和", "都", "这",
    "那", "也", "就", "不", "人", "都", "一", "一个", "上", "也", "到",
    "说", "要", "去", "你", "会", "着", "没有", "看", "好", "自己",
    "这", "她", "他", "么", "什么", "那", "被", "从", "对", "把", "让",
    "很", "又", "与", "而", "但", "还", "已", "所", "于", "能", "得",
}

SENSORY_MAP = {
    "视觉": {"看到", "望去", "映入眼帘", "闪过", "闪烁", "光芒", "色彩", "阴影", "倒影", "轮廓",
              "望去", "目光", "注视", "凝视"},
    "听觉": {"听到", "传来", "响起", "回荡", "低语", "怒吼", "沉默", "喧嚣", "脚步声"},
    "触觉": {"冰冷", "灼热", "刺痛", "温热", "粗糙", "光滑", "颤抖", "酥麻"},
    "嗅觉": {"气味", "芬芳", "腐臭", "血腥味", "药香", "檀香"},
    "味觉": {"苦涩", "甘甜", "腥甜", "咸涩", "辛辣"},
}

SENTENCE_END = re.compile(r"[。！？]")
DIALOGUE_RE = re.compile(r'[\u201c\u201d"](.+?)[\u201c\u201d"]')


class StyleImitator:
    """Extract style fingerprint from reference texts and generate style prompts."""

    def __init__(self):
        self.fingerprint: StyleFingerprint | None = None

    def analyze(self, texts: list[str]) -> StyleFingerprint:
        """Analyze one or more reference texts to build a style fingerprint.

        Args:
            texts: List of reference chapter/paragraph texts.

        Returns:
            StyleFingerprint with quantified style metrics.
        """
        fp = StyleFingerprint()
        all_text = "\n".join(texts)
        fp.total_chars = len(all_text)
        fp.sample_size = len(texts)

        # Sentence analysis
        sentences = [s.strip() for s in SENTENCE_END.split(all_text) if s.strip()]
        if sentences:
            lengths = [len(s) for s in sentences]
            fp.avg_sentence_len = sum(lengths) / len(lengths)
            fp.sentence_len_std = math.sqrt(
                sum((l - fp.avg_sentence_len) ** 2 for l in lengths) / len(lengths)
            ) if len(lengths) > 1 else 0
            fp.short_ratio = sum(1 for l in lengths if l < 10) / len(lengths)
            fp.long_ratio = sum(1 for l in lengths if l > 30) / len(lengths)

        # Paragraph analysis
        paragraphs = []
        for text in texts:
            paragraphs.extend(
                p.strip() for p in text.split("\n")
                if p.strip() and not p.startswith("#") and len(p.strip()) > 5
            )
        if paragraphs:
            p_lengths = [len(p) for p in paragraphs]
            fp.avg_paragraph_len = sum(p_lengths) / len(p_lengths)
            fp.paragraph_len_std = math.sqrt(
                sum((l - fp.avg_paragraph_len) ** 2 for l in p_lengths) / len(p_lengths)
            ) if len(p_lengths) > 1 else 0

        # Punctuation analysis
        total_punct = max(1, all_text.count("。") + all_text.count("，") +
                          all_text.count("！") + all_text.count("？") +
                          all_text.count("…") + all_text.count("——"))
        fp.comma_period_ratio = all_text.count("，") / max(1, all_text.count("。"))
        fp.excl_ratio = (all_text.count("！") + all_text.count("？")) / total_punct
        fp.ellipsis_ratio = (all_text.count("…") + all_text.count("——")) / total_punct

        # Dialogue analysis
        all_dialogues = []
        for text in texts:
            all_dialogues.extend(DIALOGUE_RE.findall(text))
        if all_dialogues:
            fp.dialogue_ratio = sum(len(d) for d in all_dialogues) / max(1, fp.total_chars)
            fp.avg_dialogue_len = sum(len(d) for d in all_dialogues) / len(all_dialogues)

        # Top vocabulary (2-4 char words, excluding stop words)
        # Simple: extract all 2-char and 3-char segments
        word_counts: Counter = Counter()
        for text in texts:
            # Extract Chinese words (2-4 chars)
            # Extract from continuous Chinese segments
            chinese_segments = re.findall(r"[\u4e00-\u9fff]{4,}", text)
            for seg in chinese_segments:
                for i in range(len(seg) - 1):
                    w = seg[i:i+2]
                    if w not in STOP_WORDS:
                        word_counts[w] += 1
                for i in range(len(seg) - 2):
                    w = seg[i:i+3]
                    if w not in STOP_WORDS:
                        word_counts[w] += 1

        fp.top_words = word_counts.most_common(30)

        # Sentence openers
        opener_counts: Counter = Counter()
        for text in texts:
            # First 2 chars of each paragraph
            for line in text.split("\n"):
                line = line.strip()
                if line and not line.startswith("#") and len(line) >= 2:
                    opener = line[:2]
                    if opener not in STOP_WORDS:
                        opener_counts[opener] += 1
        fp.top_openers = opener_counts.most_common(15)

        # Sensory profile
        for sense, words in SENSORY_MAP.items():
            count = sum(all_text.count(w) for w in words)
            fp.sensory_profile[sense] = count / max(1, len(all_text)) * 10000  # per 10k chars

        self.fingerprint = fp
        logger.info(f"Style fingerprint built: {fp.total_chars} chars, "
                     f"avg sent={fp.avg_sentence_len:.1f}, avg para={fp.avg_paragraph_len:.1f}, "
                     f"dialogue={fp.dialogue_ratio:.1%}")
        return fp

    def generate_style_prompt(self, fp: StyleFingerprint = None) -> str:
        """Generate a prompt that instructs an LLM to match the fingerprinted style.

        Args:
            fp: Style fingerprint (uses self.fingerprint if None).

        Returns:
            A prompt string for style-guided generation.
        """
        fp = fp or self.fingerprint
        if not fp:
            return ""

        lines = ["请严格模仿以下文风特征写作：", ""]

        # Sentence rhythm
        lines.append("【句式节奏】")
        lines.append(f"- 平均句长 {fp.avg_sentence_len:.0f} 字（标准差 {fp.sentence_len_std:.0f}）")
        if fp.short_ratio > 0.2:
            lines.append(f"- 短句（<10字）占比 {fp.short_ratio:.0%}，善用短句制造节奏感")
        if fp.long_ratio > 0.2:
            lines.append(f"- 长句（>30字）占比 {fp.long_ratio:.0%}，善用长句铺垫氛围")

        # Paragraph shape
        lines.append("\n【段落形态】")
        lines.append(f"- 平均段落 {fp.avg_paragraph_len:.0f} 字")

        # Punctuation
        lines.append("\n【标点习惯】")
        lines.append(f"- 逗号/句号比 {fp.comma_period_ratio:.1f}")
        if fp.excl_ratio > 0.1:
            lines.append(f"- 感叹号/问号使用较多（{fp.excl_ratio:.0%}）")
        if fp.ellipsis_ratio > 0.1:
            lines.append(f"- 省略号/破折号使用较多（{fp.ellipsis_ratio:.0%}）")

        # Dialogue
        if fp.dialogue_ratio > 0.05:
            lines.append("\n【对话风格】")
            lines.append(f"- 对话占比 {fp.dialogue_ratio:.0%}，平均对话 {fp.avg_dialogue_len:.0f} 字")

        # Top vocabulary
        if fp.top_words:
            lines.append("\n【高频用词（请自然使用）】")
            top = fp.top_words[:15]
            lines.append("- " + "、".join(w for w, _ in top))

        # Sensory preference
        if fp.sensory_profile:
            dominant = sorted(fp.sensory_profile.items(), key=lambda x: -x[1])
            top_senses = [s for s, v in dominant if v > 0][:3]
            if top_senses:
                lines.append(f"\n【感官偏好】优先使用 {'、'.join(top_senses)} 描写")

        # Openers
        if fp.top_openers:
            openers = "、".join(w for w, _ in fp.top_openers[:8])
            lines.append(f"\n【段落开头常用词】{openers}")

        lines.append("\n【禁忌】不要使用 AI 常用词（不禁、竟然、忽然等），保持自然。")

        return "\n".join(lines)

    def compare_styles(self, fp1: StyleFingerprint, fp2: StyleFingerprint) -> dict:
        """Compare two style fingerprints and return similarity metrics."""
        def _cosine_sim(v1: float, v2: float) -> float:
            """Simple ratio-based similarity."""
            if v1 == 0 and v2 == 0:
                return 1.0
            return min(v1, v2) / max(v1, v2) if max(v1, v2) > 0 else 0.0

        metrics = {
            "sentence_len_sim": _cosine_sim(fp1.avg_sentence_len, fp2.avg_sentence_len),
            "paragraph_len_sim": _cosine_sim(fp1.avg_paragraph_len, fp2.avg_paragraph_len),
            "dialogue_ratio_sim": _cosine_sim(fp1.dialogue_ratio, fp2.dialogue_ratio),
            "comma_period_sim": _cosine_sim(fp1.comma_period_ratio, fp2.comma_period_ratio),
            "excl_sim": _cosine_sim(fp1.excl_ratio, fp2.excl_ratio),
        }
        metrics["overall"] = sum(metrics.values()) / len(metrics)
        return metrics
