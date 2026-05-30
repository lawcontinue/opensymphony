"""Novel Auditor — 33-dimension continuity and quality audit.

Expanded from InkOS-inspired 10-dim P0 to full 33-dim coverage.
Organized into 5 categories:
  A. Continuity (10 dims) — character/world state consistency
  B. AI Detection (5 dims) — AI writing pattern detection
  C. Style & Rhythm (8 dims) — prose quality
  D. Structure & Pacing (5 dims) — chapter/narrative structure
  E. Reader Experience (5 dims) — engagement & readability
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from enum import Enum

from .observer import Fact, FactCategory
from .truth_files import TruthFile, TruthFiles

logger = logging.getLogger("symphony.apps.novel.auditor")


class Severity(str, Enum):
    CRITICAL = "critical"  # Must fix — breaks continuity
    WARNING = "warning"    # Should fix — quality issue
    INFO = "info"          # Nice to fix — style suggestion


class AuditCategory(str, Enum):
    CONTINUITY = "continuity"      # A. 连续性
    AI_DETECTION = "ai_detection"  # B. AI 检测
    STYLE = "style"                # C. 文风节奏
    STRUCTURE = "structure"        # D. 结构节奏
    READER_EXP = "reader_exp"      # E. 读者体验


@dataclass
class AuditIssue:
    """A single audit finding."""
    dimension: str         # e.g., "character_memory", "ai_tell"
    category: AuditCategory = AuditCategory.STYLE
    severity: Severity = Severity.INFO
    description: str = ""  # Human-readable description
    location: str = ""     # Relevant text snippet
    suggestion: str = ""   # How to fix


@dataclass
class AuditResult:
    """Complete audit result for a chapter."""
    chapter: int
    score: float = 0.0
    issues: list[AuditIssue] = field(default_factory=list)
    passed: bool = False
    category_scores: dict[str, float] = field(default_factory=dict)

    @property
    def criticals(self) -> list[AuditIssue]:
        return [i for i in self.issues if i.severity == Severity.CRITICAL]

    @property
    def warnings(self) -> list[AuditIssue]:
        return [i for i in self.issues if i.severity == Severity.WARNING]

    def summary(self) -> str:
        lines = [f"Chapter {self.chapter} audit: {self.score:.0f}/100 ({'PASS' if self.passed else 'FAIL'})"]
        if self.category_scores:
            for cat, s in self.category_scores.items():
                lines.append(f"  {cat}: {s:.0f}/100")
        for issue in self.issues:
            if issue.severity == Severity.CRITICAL:
                lines.append(f"  🔴 [{issue.dimension}] {issue.description}")
            elif issue.severity == Severity.WARNING:
                lines.append(f"  🟡 [{issue.dimension}] {issue.description}")
        return "\n".join(lines)


# ── 33 Dimension Definitions ────────────────────────────────────

DIMENSIONS = {
    # A. Continuity (10)
    "character_position":   ("角色位置连续性",     AuditCategory.CONTINUITY),
    "character_state":      ("角色状态连续性",     AuditCategory.CONTINUITY),
    "character_voice":      ("角色语言一致性",     AuditCategory.CONTINUITY),
    "resource_continuity":  ("道具/资源连续性",    AuditCategory.CONTINUITY),
    "information_boundary": ("信息边界",          AuditCategory.CONTINUITY),
    "hook_consistency":     ("伏笔一致性",        AuditCategory.CONTINUITY),
    "timeline_consistency": ("时间线一致性",       AuditCategory.CONTINUITY),
    "world_rule":           ("世界观规则一致性",    AuditCategory.CONTINUITY),
    "world_anachronism":    ("世界观时代违禁词",    AuditCategory.CONTINUITY),
    "naming_consistency":   ("命名一致性",         AuditCategory.CONTINUITY),
    "relationship_track":   ("人物关系追踪",       AuditCategory.CONTINUITY),
    # B. AI Detection (5)
    "ai_tell":              ("AI 高频标记词",      AuditCategory.AI_DETECTION),
    "ai_sentence_rhythm":   ("AI 句式节奏",        AuditCategory.AI_DETECTION),
    "ai_emotion_flat":      ("情感均匀度（太平）",  AuditCategory.AI_DETECTION),
    "ai_listification":     ("AI 列举式描写",      AuditCategory.AI_DETECTION),
    "ai_transition":        ("AI 过渡词过密",       AuditCategory.AI_DETECTION),
    # C. Style & Rhythm (8)
    "paragraph_shape":      ("段落形态",          AuditCategory.STYLE),
    "word_fatigue":         ("词汇疲劳",          AuditCategory.STYLE),
    "forbidden_patterns":   ("禁止句式",          AuditCategory.STYLE),
    "sentence_variety":     ("句长多样性",         AuditCategory.STYLE),
    "sensory_density":      ("感官描写密度",       AuditCategory.STYLE),
    "dialogue_naturalness": ("对话自然度",         AuditCategory.STYLE),
    "metaphor_freshness":   ("比喻新鲜度",         AuditCategory.STYLE),
    "punctuation_balance":  ("标点平衡",          AuditCategory.STYLE),
    # D. Structure & Pacing (5)
    "chapter_structure":    ("章节结构",          AuditCategory.STRUCTURE),
    "pacing_curve":         ("节奏曲线",          AuditCategory.STRUCTURE),
    "scene_balance":        ("场景平衡",          AuditCategory.STRUCTURE),
    "tension_arc":          ("张力弧线",          AuditCategory.STRUCTURE),
    "info_density":         ("信息密度",          AuditCategory.STRUCTURE),
    # E. Reader Experience (5)
    "opening_hook":         ("开头吸引力",         AuditCategory.READER_EXP),
    "ending_cliffhanger":   ("结尾悬念",          AuditCategory.READER_EXP),
    "reader_confusion":     ("读者困惑点",         AuditCategory.READER_EXP),
    "emotional_resonance":  ("情感共鸣",          AuditCategory.READER_EXP),
    "readability":          ("可读性",            AuditCategory.READER_EXP),
}


class NovelAuditor:
    """33-dimension novel chapter auditor.

    Organized into 5 categories (A-E), 33 dimensions total.
    Each check returns AuditIssue with category + severity + suggestion.
    """

    # ── AI writing markers ───────────────────────────────────────
    AI_HIGH_FREQ_WORDS = {
        "不禁", "竟然", "居然", "忽然", "猛地", "仿佛", "宛如",
        "一股", "一道", "一抹", "一丝", "微微",
        "倒吸一口凉气", "目光一凝", "嘴角微扬", "眼中闪过一丝",
        "缓缓", "淡淡", "静静", "默默", "轻轻", "深深",
    }

    FORBIDDEN_PATTERNS = [
        (r"不是.{1,30}而是", "不是…而是…句式"),
        (r"不仅.{1,30}而且", "不仅…而且…句式"),
        (r"无论.{1,30}都", "无论…都…句式（过度使用）"),
    ]

    CLICHE_PHRASES = {
        "一股凌厉的杀气", "如临大敌", "不可思议", "难以置信",
        "登时", "霎时间", "刹那间", "不怒自威", "宛若天仙",
        "气宇轩昂", "心旷神怡", "惨绝人寰", "义愤填膺",
    }

    # AI sentence rhythm markers: short uniform sentences
    AI_SENTENCE_ENDERS = re.compile(r"[。！？]")
    AI_TRANSITION_WORDS = {
        "然而", "但是", "不过", "与此同时", "就在这时", "忽然间",
        "与此同时", "话说回来", "另一方面", "此时此刻",
    }

    # Sensory words
    SENSORY_MAP = {
        "视觉": {"看到", "望去", "映入眼帘", "闪过", "闪烁", "光芒", "色彩", "阴影", "倒影", "轮廓"},
        "听觉": {"听到", "传来", "响起", "回荡", "低语", "怒吼", "沉默", "喧嚣", "脚步声", "风声"},
        "触觉": {"冰冷", "灼热", "刺痛", "温热", "粗糙", "光滑", "颤抖", "酥麻", "沉甸甸", "轻盈"},
        "嗅觉": {"气味", "芬芳", "腐臭", "血腥味", "药香", "炊烟味", "檀香", "异香"},
        "味觉": {"苦涩", "甘甜", "腥甜", "铁锈味", "咸涩", "辛辣"},
    }

    # Emotional markers
    EMOTION_MARKERS = {
        "紧张": {"心跳", "屏住", "握紧", "冷汗", "后退一步", "紧张"},
        "愤怒": {"怒", "愤", "咬牙", "攥拳", "怒目", "暴喝", "低吼"},
        "悲伤": {"泪", "哭", "哽咽", "颤抖", "黯然", "泪光", "红着眼"},
        "喜悦": {"笑", "喜", "欢", "激动", "兴奋", "欣慰"},
        "恐惧": {"恐惧", "骇然", "战栗", "发抖", "寒意", "惊骇"},
    }

    # Default category weights for overall score
    DEFAULT_WEIGHTS = {
        "continuity": 0.30,
        "ai_detection": 0.20,
        "style": 0.20,
        "structure": 0.15,
        "reader_exp": 0.15,
    }

    def __init__(self, truth: TruthFiles | None = None,
                 category_weights: dict[str, float] | None = None,
                 world_forbidden_words: list[str] | None = None):
        self.truth = truth
        self.category_weights = category_weights or self.DEFAULT_WEIGHTS
        self._world_forbidden_words = world_forbidden_words or []

    def audit(self, chapter: int, text: str, facts: list[Fact] = None) -> AuditResult:
        """Run all 33 audit dimensions on a chapter."""
        result = AuditResult(chapter=chapter)
        facts = facts or []

        # Run all checks, grouped by category
        check_methods = [
            # A. Continuity (10)
            (self._check_character_position,   (text, facts)),
            (self._check_character_state,       (text, facts)),
            (self._check_character_voice,       (text, facts)),
            (self._check_resource_continuity,   (text, facts)),
            (self._check_information_boundary,  (text,)),
            (self._check_hook_consistency,      (text, facts)),
            (self._check_timeline_consistency,  (text, facts)),
            (self._check_world_rule,            (text, facts)),
            (self._check_world_anachronism,     (text,)),
            (self._check_naming_consistency,    (text, facts)),
            (self._check_relationship_track,    (text, facts)),
            # B. AI Detection (5)
            (self._check_ai_tell,               (text,)),
            (self._check_ai_sentence_rhythm,    (text,)),
            (self._check_ai_emotion_flat,       (text,)),
            (self._check_ai_listification,      (text,)),
            (self._check_ai_transition,         (text,)),
            # C. Style & Rhythm (8)
            (self._check_paragraph_shape,       (text,)),
            (self._check_word_fatigue,          (text,)),
            (self._check_forbidden_patterns,    (text,)),
            (self._check_sentence_variety,      (text,)),
            (self._check_sensory_density,       (text,)),
            (self._check_dialogue_naturalness,  (text,)),
            (self._check_metaphor_freshness,    (text,)),
            (self._check_punctuation_balance,   (text,)),
            # D. Structure & Pacing (5)
            (self._check_chapter_structure,     (text,)),
            (self._check_pacing_curve,          (text,)),
            (self._check_scene_balance,         (text,)),
            (self._check_tension_arc,           (text,)),
            (self._check_info_density,          (text,)),
            # E. Reader Experience (5)
            (self._check_opening_hook,          (text,)),
            (self._check_ending_cliffhanger,    (text,)),
            (self._check_reader_confusion,      (text,)),
            (self._check_emotional_resonance,   (text,)),
            (self._check_readability,           (text,)),
        ]

        all_issues: list[AuditIssue] = []
        for method, args in check_methods:
            try:
                all_issues.extend(method(*args))
            except Exception as e:
                logger.warning(f"Audit check {method.__name__} failed: {e}")

        result.issues = all_issues

        # Calculate per-category scores
        cat_issues: dict[str, list[AuditIssue]] = {}
        for issue in all_issues:
            cat = issue.category.value
            cat_issues.setdefault(cat, []).append(issue)

        for cat_enum in AuditCategory:
            issues_in_cat = cat_issues.get(cat_enum.value, [])
            sum(1 for d, (_, c) in DIMENSIONS.items() if c == cat_enum)
            cat_score = 100.0
            for issue in issues_in_cat:
                if issue.severity == Severity.CRITICAL:
                    cat_score -= 15
                elif issue.severity == Severity.WARNING:
                    cat_score -= 5
                elif issue.severity == Severity.INFO:
                    cat_score -= 1
            result.category_scores[cat_enum.value] = max(0, cat_score)

        # Overall score: weighted average of category scores
        total = sum(result.category_scores.get(k, 100) * v
                    for k, v in self.category_weights.items())
        result.score = round(max(0, total), 1)
        result.passed = result.score >= 85 and len(result.criticals) == 0

        logger.info(f"Chapter {chapter} audit: {result.score:.0f}/100, "
                     f"{len(result.criticals)} critical, {len(result.warnings)} warnings")
        return result

    # ═══════════════════════════════════════════════════════════════
    # A. Continuity (10 dimensions)
    # ═══════════════════════════════════════════════════════════════

    def _check_character_position(self, text: str, facts: list[Fact]) -> list[AuditIssue]:
        issues = []
        if not self.truth:
            return issues
        char_facts = [f for f in facts if f.category == FactCategory.CHARACTER
                      and f.predicate in ("位置", "位置移动")]
        if len(char_facts) >= 2:
            for i in range(1, len(char_facts)):
                prev, curr = char_facts[i - 1], char_facts[i]
                if prev.subject == curr.subject and prev.object_ == curr.object_:
                    issues.append(AuditIssue(
                        dimension="character_position", category=AuditCategory.CONTINUITY,
                        severity=Severity.WARNING,
                        description=f"{prev.subject} 位置重复: {prev.object_}",
                        suggestion="检查是否有不必要的重复描述",
                    ))
        return issues

    def _check_character_state(self, text: str, facts: list[Fact]) -> list[AuditIssue]:
        issues = []
        injury_words = {"伤口", "骨折", "断裂", "大出血"}
        heal_words = {"痊愈", "恢复如初", "完好无损", "伤口愈合"}
        if any(w in text for w in injury_words) and any(w in text for w in heal_words):
            issues.append(AuditIssue(
                dimension="character_state", category=AuditCategory.CONTINUITY,
                severity=Severity.WARNING,
                description="受伤后突然痊愈，缺少治疗过程",
                suggestion="加入治疗/休息描写，或调整时间线",
            ))
        return issues

    def _check_character_voice(self, text: str, facts: list[Fact]) -> list[AuditIssue]:
        """Check character speech style consistency (dialogue tone)."""
        issues = []
        # Extract dialogue lines
        dialogues = re.findall(r'[“”"](.+?)[“”"]', text)
        if len(dialogues) < 2:
            return issues

        # Check if all dialogues have similar length (AI flattening)
        lengths = [len(d) for d in dialogues]
        if lengths:
            avg = sum(lengths) / len(lengths)
            variance = sum((l - avg) ** 2 for l in lengths) / len(lengths)
            if variance < 10 and len(dialogues) >= 4:
                issues.append(AuditIssue(
                    dimension="character_voice", category=AuditCategory.CONTINUITY,
                    severity=Severity.WARNING,
                    description=f"所有对话长度趋同（平均 {avg:.0f} 字，方差 {variance:.0f}），角色声音不够分化",
                    suggestion="给不同角色不同的说话风格（长短/用词/语气）",
                ))
        return issues

    def _check_resource_continuity(self, text: str, facts: list[Fact]) -> list[AuditIssue]:
        issues = []
        # Check acquired items are later referenced or used
        for fact in facts:
            if fact.category == FactCategory.RESOURCE and fact.object_:
                # If something is acquired, check it appears again later
                pass  # Requires multi-chapter tracking, P2 feature
        return issues

    def _check_information_boundary(self, text: str) -> list[AuditIssue]:
        issues = []
        patterns = [r"([^，。]{2,6})(?:早就|一直|心中|暗自)(?:知道|明白|清楚|了解|察觉)"]
        for pat in patterns:
            for m in re.finditer(pat, text):
                issues.append(AuditIssue(
                    dimension="information_boundary", category=AuditCategory.CONTINUITY,
                    severity=Severity.INFO,
                    description=f"验证 {m.group(1)} 是否有渠道获得此信息",
                    location=m.group(0),
                    suggestion="确保信息获取有合理的伏笔",
                ))
        return issues

    def _check_hook_consistency(self, text: str, facts: list[Fact]) -> list[AuditIssue]:
        issues = []
        if not self.truth:
            return issues
        hooks = self.truth.get(TruthFile.PENDING_HOOKS)
        if not hooks or "hooks" not in hooks:
            return issues
        for hook in hooks["hooks"]:
            if hook.get("status") == "open":
                start = hook.get("start_chapter", 0)
                current_ch = self.truth.get_field(TruthFile.CURRENT_STATE, "chapter", 0)
                if isinstance(current_ch, int) and current_ch - start > 10:
                    issues.append(AuditIssue(
                        dimension="hook_consistency", category=AuditCategory.CONTINUITY,
                        severity=Severity.WARNING,
                        description=f"伏笔「{hook.get('content', '?')[:30]}」已闲置 {current_ch - start} 章",
                        suggestion="考虑推进或回收此伏笔",
                    ))
        return issues

    def _check_timeline_consistency(self, text: str, facts: list[Fact]) -> list[AuditIssue]:
        """Check time references don't contradict."""
        issues = []
        time_words = {"天前", "小时前", "昨天", "前天", "上周", "刚才", "片刻前", "昨天夜里"}
        time_markers = [(w, text.count(w)) for w in time_words if w in text]
        # Check multiple conflicting time references
        if len(time_markers) >= 3:
            issues.append(AuditIssue(
                dimension="timeline_consistency", category=AuditCategory.CONTINUITY,
                severity=Severity.WARNING,
                description=f"出现 {len(time_markers)} 种时间引用，检查是否有矛盾",
                location=", ".join(f"{w}×{c}" for w, c in time_markers),
                suggestion="理清本章时间线，确保不矛盾",
            ))
        return issues

    def _check_world_rule(self, text: str, facts: list[Fact]) -> list[AuditIssue]:
        """Check world-building rules are not violated."""
        issues = []
        if not self.truth:
            return issues
        # Check power system consistency (if defined in truth files)
        world = self.truth.get(TruthFile.CURRENT_STATE)  # World rules stored in current_state
        if not world:
            return issues
        # Check if "impossible" power level jumps happen
        level_patterns = re.findall(r"(\w+?)(?:突破了?|晋升到?|达到了?)(.{2,10}?境)", text)
        for who, level in level_patterns:
            issues.append(AuditIssue(
                dimension="world_rule", category=AuditCategory.CONTINUITY,
                severity=Severity.INFO,
                description=f"{who} 晋升到 {level}，验证是否符合修炼体系规则",
                suggestion="对比世界观设定中的修炼等级和突破条件",
            ))
        return issues

    def _check_world_anachronism(self, text: str) -> list[AuditIssue]:
        """Check for modern-world words that violate the xianxia setting."""
        issues = []
        forbidden = self._world_forbidden_words
        if not forbidden:
            return issues
        found = set()
        for word in forbidden:
            w = word.strip().strip('"').strip("'")
            if w and w in text:
                # Find context
                idx = text.find(w)
                start = max(0, idx - 15)
                end = min(len(text), idx + len(w) + 15)
                context = text[start:end]
                found.add(w)
                issues.append(AuditIssue(
                    dimension="world_anachronism",
                    category=AuditCategory.CONTINUITY,
                    severity=Severity.CRITICAL,
                    description=f"现代词汇「{w}」出现在修仙世界中",
                    location=context,
                    suggestion="替换为修仙世界对应词汇（如：合同→契约，签名→画押，警察→巡天司）",
                ))
        if found:
            logger.warning(f"World anachronism detected: {found}")
        return issues

    def _check_naming_consistency(self, text: str, facts: list[Fact]) -> list[AuditIssue]:
        """Check character/place names are used consistently."""
        issues = []
        # Detect name variants: e.g., "张律师" vs "张律" vs "老张"
        char_names: dict[str, list[str]] = {}
        for fact in facts:
            if fact.category == FactCategory.CHARACTER and fact.subject:
                base = fact.subject[:2]  # First 2 chars as base
                char_names.setdefault(base, []).append(fact.subject)

        for base, variants in char_names.items():
            unique = set(variants)
            if len(unique) > 1:
                issues.append(AuditIssue(
                    dimension="naming_consistency", category=AuditCategory.CONTINUITY,
                    severity=Severity.INFO,
                    description=f"同角色可能有多种称呼: {', '.join(unique)}",
                    suggestion="确认是否为同一人的不同称呼，还是不同角色",
                ))
        return issues

    def _check_relationship_track(self, text: str, facts: list[Fact]) -> list[AuditIssue]:
        """Track character relationship changes."""
        issues = []
        relation_words = {"师父", "徒弟", "师兄", "师姐", "师弟", "敌人", "盟友", "朋友", "对手"}
        found = [(w, text.count(w)) for w in relation_words if w in text]
        # Flag if many relationship terms but no context
        if sum(c for _, c in found) > 10:
            issues.append(AuditIssue(
                dimension="relationship_track", category=AuditCategory.CONTINUITY,
                severity=Severity.INFO,
                description=f"关系词出现频率高（{', '.join(f'{w}×{c}' for w, c in found[:5])}），确认关系变化有铺垫",
                suggestion="检查关系转变是否有足够的互动铺垫",
            ))
        return issues

    # ═══════════════════════════════════════════════════════════════
    # B. AI Detection (5 dimensions)
    # ═══════════════════════════════════════════════════════════════

    def _check_ai_tell(self, text: str) -> list[AuditIssue]:
        issues = []
        counts = {w: text.count(w) for w in self.AI_HIGH_FREQ_WORDS if w in text}
        threshold = max(1, len(text) // 3000)
        for word, count in counts.items():
            if count > threshold:
                issues.append(AuditIssue(
                    dimension="ai_tell", category=AuditCategory.AI_DETECTION,
                    severity=Severity.WARNING if count > 2 else Severity.INFO,
                    description=f"AI标记词「{word}」出现 {count} 次",
                    suggestion="替换为更具体的描写",
                ))
        for phrase in self.CLICHE_PHRASES:
            if phrase in text:
                issues.append(AuditIssue(
                    dimension="ai_tell", category=AuditCategory.AI_DETECTION,
                    severity=Severity.INFO,
                    description=f"陈词滥调:「{phrase}」",
                    suggestion="用原创表达替换",
                ))
        return issues

    def _check_ai_sentence_rhythm(self, text: str) -> list[AuditIssue]:
        """Detect uniform sentence lengths (AI hallmark)."""
        issues = []
        sentences = [s.strip() for s in self.AI_SENTENCE_ENDERS.split(text) if s.strip()]
        if len(sentences) < 5:
            return issues
        lengths = [len(s) for s in sentences]
        avg = sum(lengths) / len(lengths)
        std = math.sqrt(sum((l - avg) ** 2 for l in lengths) / len(lengths))
        # Low std dev = uniform length = AI-ish
        if std < 5 and len(sentences) >= 8:
            issues.append(AuditIssue(
                dimension="ai_sentence_rhythm", category=AuditCategory.AI_DETECTION,
                severity=Severity.WARNING,
                description=f"句长标准差仅 {std:.1f}（平均 {avg:.0f} 字），句子太均匀",
                suggestion="穿插长短句，短句制造紧张感，长句铺垫氛围",
            ))
        return issues

    def _check_ai_emotion_flat(self, text: str) -> list[AuditIssue]:
        """Detect emotionally flat prose (AI tends to be even-tempered)."""
        issues = []
        paragraphs = [p.strip() for p in text.split("\n") if p.strip() and not p.startswith("#")]
        if len(paragraphs) < 4:
            return issues

        # Count emotion intensity per paragraph
        intensities = []
        for p in paragraphs:
            score = 0
            for markers in self.EMOTION_MARKERS.values():
                score += sum(p.count(m) for m in markers)
            intensities.append(score)

        # If most paragraphs have similar (low) intensity, flag flatness
        nonzero = [i for i in intensities if i > 0]
        if len(nonzero) < len(paragraphs) * 0.3:
            issues.append(AuditIssue(
                dimension="ai_emotion_flat", category=AuditCategory.AI_DETECTION,
                severity=Severity.WARNING,
                description=f"仅 {len(nonzero)}/{len(paragraphs)} 段含情感标记，情感太平",
                suggestion="关键段落加浓情感描写，平静段落保持淡，制造起伏",
            ))
        return issues

    def _check_ai_listification(self, text: str) -> list[AuditIssue]:
        """Detect AI tendency to list/enumerate (编号式描写)."""
        issues = []
        # Check for numbered items in narrative
        list_patterns = [
            r"(?:第一|第二|第三|第四|第五).{0,8}?(?:，|：)",
            r"[一是二三].{2,6}，[二是三四].{2,6}，[三是四五].{2,6}",
        ]
        for pat in list_patterns:
            matches = list(re.finditer(pat, text))
            if len(matches) >= 2:
                issues.append(AuditIssue(
                    dimension="ai_listification", category=AuditCategory.AI_DETECTION,
                    severity=Severity.WARNING,
                    description=f"发现 {len(matches)} 处列举式描写",
                    location=matches[0].group(0),
                    suggestion="用叙事融合代替列举，让信息自然散布在情节中",
                ))
                break
        return issues

    def _check_ai_transition(self, text: str) -> list[AuditIssue]:
        """Check for overuse of transition words."""
        issues = []
        counts = {w: text.count(w) for w in self.AI_TRANSITION_WORDS if w in text}
        total = sum(counts.values())
        if total > 5:
            top = sorted(counts.items(), key=lambda x: -x[1])[:3]
            issues.append(AuditIssue(
                dimension="ai_transition", category=AuditCategory.AI_DETECTION,
                severity=Severity.WARNING,
                description=f"过渡词过密（{total} 次）: {', '.join(f'{w}×{c}' for w, c in top)}",
                suggestion="减少过渡词，用动作/场景切换自然过渡",
            ))
        return issues

    # ═══════════════════════════════════════════════════════════════
    # C. Style & Rhythm (8 dimensions)
    # ═══════════════════════════════════════════════════════════════

    def _check_paragraph_shape(self, text: str) -> list[AuditIssue]:
        issues = []
        paragraphs = [p.strip() for p in text.split("\n") if p.strip() and not p.startswith("#")]
        if not paragraphs:
            return issues

        short_count = sum(1 for p in paragraphs if len(p) < 35)
        short_ratio = short_count / len(paragraphs)

        if short_ratio > 0.6:
            issues.append(AuditIssue(
                dimension="paragraph_shape", category=AuditCategory.STYLE,
                severity=Severity.WARNING,
                description=f"{len(paragraphs)} 段中 {short_count} 段不足 35 字（{short_ratio:.0%}）",
                suggestion="合并短段，避免堆砌感",
            ))

        # Consecutive short paragraphs
        max_consec = current = 0
        for p in paragraphs:
            if len(p) < 35:
                current += 1
                max_consec = max(max_consec, current)
            else:
                current = 0
        if max_consec >= 5:
            issues.append(AuditIssue(
                dimension="paragraph_shape", category=AuditCategory.STYLE,
                severity=Severity.WARNING,
                description=f"连续 {max_consec} 个短段（<35字）",
                suggestion="在短段之间加入长段打破节奏",
            ))
        return issues

    def _check_word_fatigue(self, text: str) -> list[AuditIssue]:
        issues = []
        ta_count = text.count("他")
        chars_per_ta = len(text) / max(1, ta_count)
        if ta_count > 20 and chars_per_ta < 150:
            issues.append(AuditIssue(
                dimension="word_fatigue", category=AuditCategory.STYLE,
                severity=Severity.INFO,
                description=f"「他」出现 {ta_count} 次（每 {chars_per_ta:.0f} 字一次）",
                suggestion="用角色名或具体描写替代部分代词",
            ))
        return issues

    def _check_forbidden_patterns(self, text: str) -> list[AuditIssue]:
        issues = []
        for pattern, name in self.FORBIDDEN_PATTERNS:
            for m in re.finditer(pattern, text):
                issues.append(AuditIssue(
                    dimension="forbidden_patterns", category=AuditCategory.STYLE,
                    severity=Severity.CRITICAL,
                    description=f"禁止句式: {name}",
                    location=m.group(0),
                    suggestion="改写为其他表达方式",
                ))
        return issues

    def _check_sentence_variety(self, text: str) -> list[AuditIssue]:
        """Check mix of sentence lengths."""
        issues = []
        sentences = [s.strip() for s in self.AI_SENTENCE_ENDERS.split(text) if s.strip()]
        if len(sentences) < 8:
            return issues

        lengths = [len(s) for s in sentences]
        short = sum(1 for l in lengths if l < 10)
        sum(1 for l in lengths if 10 <= l < 30)
        long_ = sum(1 for l in lengths if l >= 30)

        # Check if one type dominates
        total = len(lengths)
        if short / total > 0.7:
            issues.append(AuditIssue(
                dimension="sentence_variety", category=AuditCategory.STYLE,
                severity=Severity.WARNING,
                description=f"短句占比 {short/total:.0%}，缺少中长句",
                suggestion="适当加入长句营造氛围",
            ))
        elif long_ / total > 0.7:
            issues.append(AuditIssue(
                dimension="sentence_variety", category=AuditCategory.STYLE,
                severity=Severity.INFO,
                description=f"长句占比 {long_/total:.0%}，节奏偏慢",
                suggestion="穿插短句加速节奏",
            ))
        return issues

    def _check_sensory_density(self, text: str) -> list[AuditIssue]:
        """Check sensory description coverage across 5 senses."""
        issues = []
        found_senses = {}
        for sense, words in self.SENSORY_MAP.items():
            count = sum(text.count(w) for w in words)
            if count > 0:
                found_senses[sense] = count

        if len(text) > 2000 and len(found_senses) < 2:
            issues.append(AuditIssue(
                dimension="sensory_density", category=AuditCategory.STYLE,
                severity=Severity.WARNING,
                description=f"仅使用 {len(found_senses)} 种感官描写（{', '.join(found_senses) or '无'}），太单一",
                suggestion="加入听觉/触觉/嗅觉描写丰富场景",
            ))
        return issues

    def _check_dialogue_naturalness(self, text: str) -> list[AuditIssue]:
        """Check dialogue sounds natural, not overly formal."""
        issues = []
        dialogues = re.findall(r'[“”"](.+?)[“”"]', text)
        if not dialogues:
            return issues

        formal_markers = ["因此", "故而", "由此可见", "综上所述", "综上所述", "毋庸置疑"]
        formal_dialogues = [d for d in dialogues if any(m in d for m in formal_markers)]
        if len(formal_dialogues) >= 2:
            issues.append(AuditIssue(
                dimension="dialogue_naturalness", category=AuditCategory.STYLE,
                severity=Severity.WARNING,
                description=f"{len(formal_dialogues)} 句对话过于书面化",
                location=formal_dialogues[0][:40],
                suggestion="口语化：缩短、用语气词、允许不完整句子",
            ))
        return issues

    def _check_metaphor_freshness(self, text: str) -> list[AuditIssue]:
        """Check for overused metaphors/similes."""
        issues = []
        stale_metaphors = {
            "如同利剑", "宛如星辰", "仿佛烈火", "如同寒冰",
            "如同一座山", "宛如猛虎", "仿佛蛟龙", "如同鬼魅",
            "如履薄冰", "势如破竹", "如鱼得水",
        }
        found = [m for m in stale_metaphors if m in text]
        if len(found) >= 2:
            issues.append(AuditIssue(
                dimension="metaphor_freshness", category=AuditCategory.STYLE,
                severity=Severity.INFO,
                description=f"老套比喻: {', '.join(found)}",
                suggestion="用独特的、与世界观相关的意象替换",
            ))
        return issues

    def _check_punctuation_balance(self, text: str) -> list[AuditIssue]:
        """Check punctuation distribution."""
        issues = []
        comma_count = text.count("，")
        period_count = text.count("。")
        excl_count = text.count("！") + text.count("？")
        text.count("…") + text.count("——")

        if period_count > 0 and comma_count / period_count > 8:
            issues.append(AuditIssue(
                dimension="punctuation_balance", category=AuditCategory.STYLE,
                severity=Severity.INFO,
                description=f"逗号/句号比 {comma_count/period_count:.1f}，句子太长",
                suggestion="适当断句，减少长句中的逗号堆叠",
            ))

        if len(text) > 2000 and excl_count < 2:
            issues.append(AuditIssue(
                dimension="punctuation_balance", category=AuditCategory.STYLE,
                severity=Severity.INFO,
                description="全章无感叹号或问号，情绪平淡",
                suggestion="在关键冲突/转折处使用！或？加强语气",
            ))
        return issues

    # ═══════════════════════════════════════════════════════════════
    # D. Structure & Pacing (5 dimensions)
    # ═══════════════════════════════════════════════════════════════

    def _check_chapter_structure(self, text: str) -> list[AuditIssue]:
        issues = []
        paragraphs = [p.strip() for p in text.split("\n") if p.strip() and not p.startswith("#")]
        if not paragraphs:
            return issues

        opening = paragraphs[0]
        if len(opening) < 20:
            issues.append(AuditIssue(
                dimension="chapter_structure", category=AuditCategory.STRUCTURE,
                severity=Severity.WARNING,
                description=f"开头太短（{len(opening)}字）",
                suggestion="用动作/感官/对话开头吸引读者",
            ))
        return issues

    def _check_pacing_curve(self, text: str) -> list[AuditIssue]:
        """Check if pacing has variation (not monotonically flat)."""
        issues = []
        paragraphs = [p.strip() for p in text.split("\n") if p.strip() and not p.startswith("#")]
        if len(paragraphs) < 6:
            return issues

        # Use paragraph length as pacing proxy
        lengths = [len(p) for p in paragraphs]
        # Split into thirds
        third = len(lengths) // 3
        beg_avg = sum(lengths[:third]) / max(1, third)
        mid_avg = sum(lengths[third:2*third]) / max(1, third)
        end_avg = sum(lengths[2*third:]) / max(1, len(lengths) - 2*third)

        # Flag if all thirds are very similar
        diffs = [abs(beg_avg - mid_avg), abs(mid_avg - end_avg), abs(beg_avg - end_avg)]
        if all(d < 15 for d in diffs) and beg_avg > 0:
            issues.append(AuditIssue(
                dimension="pacing_curve", category=AuditCategory.STRUCTURE,
                severity=Severity.INFO,
                description=f"节奏均匀（起{beg_avg:.0f}→承{mid_avg:.0f}→转{end_avg:.0f}），缺少起伏",
                suggestion="开头/高潮段落用短段加速，铺垫段落用长段舒缓",
            ))
        return issues

    def _check_scene_balance(self, text: str) -> list[AuditIssue]:
        """Check balance of action, dialogue, and description."""
        issues = []
        # Rough classification
        dialogue_chars = sum(len(m) for m in re.findall(r'[“”"].*?[“”"]', text))
        action_words = {"打", "砍", "冲", "跑", "跳", "闪", "抓", "挥", "刺", "挡", "逃"}
        sum(len(line) for line in text.split("\n")
                          if any(w in line for w in action_words))
        total = max(1, len(text))

        dialogue_ratio = dialogue_chars / total
        if dialogue_ratio > 0.6:
            issues.append(AuditIssue(
                dimension="scene_balance", category=AuditCategory.STRUCTURE,
                severity=Severity.INFO,
                description=f"对话占比 {dialogue_ratio:.0%}，可能缺少动作/环境描写",
                suggestion="在对话间穿插动作和表情描写",
            ))
        elif dialogue_ratio < 0.1 and total > 2000:
            issues.append(AuditIssue(
                dimension="scene_balance", category=AuditCategory.STRUCTURE,
                severity=Severity.INFO,
                description="对话极少，大段叙述可能枯燥",
                suggestion="适当加入对话增加活力",
            ))
        return issues

    def _check_tension_arc(self, text: str) -> list[AuditIssue]:
        """Check if there's a tension peak in the chapter."""
        issues = []
        paragraphs = [p.strip() for p in text.split("\n") if p.strip() and not p.startswith("#")]
        if len(paragraphs) < 5:
            return issues

        # Score tension per paragraph
        tension_high = {"！", "？", "杀", "死", "危", "逃", "急", "惊", "怒", "血"}
        tension_scores = []
        for p in paragraphs:
            score = sum(p.count(w) for w in tension_high)
            tension_scores.append(score)

        peak = max(tension_scores)
        if peak == 0:
            issues.append(AuditIssue(
                dimension="tension_arc", category=AuditCategory.STRUCTURE,
                severity=Severity.INFO,
                description="全章无明显张力高峰",
                suggestion="设置至少一个冲突/危机/反转点",
            ))
        return issues

    def _check_info_density(self, text: str) -> list[AuditIssue]:
        """Check if too much info is dumped at once."""
        issues = []
        paragraphs = [p.strip() for p in text.split("\n") if p.strip() and not p.startswith("#")]
        for p in paragraphs:
            if len(p) > 300:
                # Long paragraph: check info density
                names = re.findall(r"[\u4e00-\u9fff]{2,4}(?:说|道|的|是)", p)
                if len(names) > 8:
                    issues.append(AuditIssue(
                        dimension="info_density", category=AuditCategory.STRUCTURE,
                        severity=Severity.INFO,
                        description=f"单段超 300 字且信息密集（{len(names)} 个实体）",
                        suggestion="拆分为多个段落或用对话分散信息",
                    ))
                    break  # Only report once
        return issues

    # ═══════════════════════════════════════════════════════════════
    # E. Reader Experience (5 dimensions)
    # ═══════════════════════════════════════════════════════════════

    def _check_opening_hook(self, text: str) -> list[AuditIssue]:
        """Check if opening grabs attention."""
        issues = []
        paragraphs = [p.strip() for p in text.split("\n") if p.strip() and not p.startswith("#")]
        if not paragraphs:
            return issues

        opening = paragraphs[0]
        hook_elements = {"？", "！", "——", "…", "却", "但", "突然", "竟然"}
        has_hook = any(h in opening for h in hook_elements)
        is_dialogue = opening.startswith(("\"", "“"))

        if not has_hook and not is_dialogue and len(opening) < 50:
            issues.append(AuditIssue(
                dimension="opening_hook", category=AuditCategory.READER_EXP,
                severity=Severity.WARNING,
                description="开头缺乏吸引力（无悬念/对话/冲突）",
                suggestion="用悬念句、对话、或动作场景开场",
            ))
        return issues

    def _check_ending_cliffhanger(self, text: str) -> list[AuditIssue]:
        """Check if ending creates desire to continue reading."""
        issues = []
        paragraphs = [p.strip() for p in text.split("\n") if p.strip() and not p.startswith("#")]
        if not paragraphs:
            return issues

        ending = paragraphs[-1]
        cliffhanger_indicators = {"？", "！", "…", "——", "却", "但", "然而", "突然", "只见", "就在"}
        has_cliff = any(c in ending for c in cliffhanger_indicators)

        if not has_cliff and len(ending) > 20:
            issues.append(AuditIssue(
                dimension="ending_cliffhanger", category=AuditCategory.READER_EXP,
                severity=Severity.INFO,
                description="章末缺少悬念/反转",
                suggestion="在结尾留下未解之谜或意外转折",
            ))
        return issues

    def _check_reader_confusion(self, text: str) -> list[AuditIssue]:
        """Detect potential reader confusion points."""
        issues = []
        # Many new character names in short span
        name_pattern = re.compile(r"([\u4e00-\u9fff]{2,4})(?:说|道|笑|怒|叹|喊)")
        names = name_pattern.findall(text)
        unique_names = set(names)

        if len(unique_names) > 6 and len(text) < 3000:
            issues.append(AuditIssue(
                dimension="reader_confusion", category=AuditCategory.READER_EXP,
                severity=Severity.WARNING,
                description=f"短篇幅出现 {len(unique_names)} 个角色，读者可能混淆",
                suggestion="减少出场角色或给每个角色一个鲜明标签",
            ))
        return issues

    def _check_emotional_resonance(self, text: str) -> list[AuditIssue]:
        """Check if text evokes emotion (not just describes events)."""
        issues = []
        paragraphs = [p.strip() for p in text.split("\n") if p.strip() and not p.startswith("#")]
        if len(paragraphs) < 3:
            return issues

        # Count paragraphs with any emotional content
        emotional_paras = 0
        for p in paragraphs:
            for markers in self.EMOTION_MARKERS.values():
                if any(m in p for m in markers):
                    emotional_paras += 1
                    break

        ratio = emotional_paras / len(paragraphs)
        if ratio < 0.2 and len(text) > 2000:
            issues.append(AuditIssue(
                dimension="emotional_resonance", category=AuditCategory.READER_EXP,
                severity=Severity.INFO,
                description=f"仅 {ratio:.0%} 的段落含情感元素",
                suggestion="在关键场景加入角色的情感反应（内心独白/身体反应）",
            ))
        return issues

    def _check_readability(self, text: str) -> list[AuditIssue]:
        """General readability checks."""
        issues = []
        # Check for overly long sentences (>80 chars without punctuation)
        long_sentences = re.findall(r"[^。！？\n]{80,}", text)
        if len(long_sentences) >= 3:
            issues.append(AuditIssue(
                dimension="readability", category=AuditCategory.READER_EXP,
                severity=Severity.INFO,
                description=f"{len(long_sentences)} 个超长句（>80字无句号）",
                suggestion="拆分长句，提高可读性",
            ))

        # Check total length
        if len(text) < 800:
            issues.append(AuditIssue(
                dimension="readability", category=AuditCategory.READER_EXP,
                severity=Severity.INFO,
                description=f"章节仅 {len(text)} 字，偏短",
                suggestion="一般网文章节 2000-4000 字",
            ))
        return issues
