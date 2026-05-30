"""Novel Pipeline Template — 7-step novel writing pipeline for Symphony.

Ported concept from InkOS: plan → compose → write → observe → reflect → audit → revise.
Designed to work with Symphony's Pipeline infrastructure.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .auditor import AuditResult, NovelAuditor
from .observer import Observer
from .reflector import Reflector
from .truth_files import TruthFiles

logger = logging.getLogger("symphony.apps.novel.templates")


# ── Novel Pipeline Template ──────────────────────────────────────

NOVEL_PIPELINE_STEPS = [
    {
        "id": "plan",
        "soul": "planner",
        "output_key": "chapter_plan",
        "description": "读取作者意图 + 当前焦点 + 真相文件，生成本章意图（must-keep / must-avoid）",
    },
    {
        "id": "compose",
        "soul": "composer",
        "input_key": "chapter_plan",
        "output_key": "context",
        "description": "从全量真相文件中按相关性选择上下文，编译规则栈",
    },
    {
        "id": "write",
        "soul": "writer",
        "input_key": "context",
        "output_key": "draft",
        "description": "基于编排后的精简上下文生成正文（字数治理 + 对话引导）",
    },
    {
        "id": "observe",
        "tool": "novel_observer",
        "input_key": "draft",
        "output_key": "observation",
        "description": "从正文中过度提取 8 类事实",
    },
    {
        "id": "reflect",
        "tool": "novel_reflector",
        "input_key": "observation",
        "output_key": "delta",
        "description": "将事实转为 JSON delta，校验后写入真相文件",
    },
    {
        "id": "audit",
        "tool": "novel_auditor",
        "input_key": "draft",
        "output_key": "audit_result",
        "description": "10 维质量审计（连续性 + AI 味 + 段落 + 结构）",
    },
    {
        "id": "revise",
        "soul": "reviser",
        "input_key": "audit_result",
        "output_key": "final",
        "condition": "audit_score < 85",
        "description": "修复审计发现的问题（仅当分数 < 85 时触发）",
        "retry": 1,
    },
]


@dataclass
class NovelPipelineResult:
    """Result of running the novel pipeline for one chapter."""
    chapter: int
    draft: str = ""
    final: str = ""
    audit: AuditResult | None = None
    facts_count: int = 0
    hooks_new: int = 0
    hooks_resolved: int = 0
    revised: bool = False
    success: bool = False


class NovelPipeline:
    """High-level novel pipeline that orchestrates the full write cycle.

    Can work in two modes:
    1. Standalone: Uses built-in Observer/Reflector/Auditor (no LLM needed for P0)
    2. Integrated: Uses Symphony Pipeline with soul agents for plan/compose/write/revise

    Usage (standalone):
        pipe = NovelPipeline(truth_files)
        result = pipe.run_standalone(chapter=2, prompt="张律到达散修集市...")
    """

    def __init__(self, truth: TruthFiles, llm_client=None):
        self.truth = truth
        self.llm_client = llm_client
        self.observer = Observer(llm_client=llm_client)
        self.reflector = Reflector(truth)
        self.auditor = NovelAuditor(truth=truth)

    def run_standalone(self, chapter: int, draft: str,
                        chapter_title: str = "") -> NovelPipelineResult:
        """Run the observe → reflect → audit cycle on an existing draft.

        This is the P0 standalone mode — you provide the draft text,
        and the pipeline handles fact extraction, truth file updates, and auditing.

        Args:
            chapter: Chapter number.
            draft: The chapter text to process.
            chapter_title: Optional chapter title for summary.

        Returns:
            NovelPipelineResult with audit score and status.
        """
        result = NovelPipelineResult(chapter=chapter, draft=draft)

        # Step 1: Snapshot truth files before changes
        self.truth.snapshot(chapter)

        # Step 2: Observe — extract facts
        observation = self.observer.observe(chapter, draft)
        result.facts_count = len(observation.facts)
        result.hooks_new = len(observation.new_hooks)
        result.hooks_resolved = len(observation.resolved_hooks)

        # Step 3: Reflect — write facts to truth files
        reflection = self.reflector.reflect_and_summarize(
            observation, chapter_title=chapter_title, chapter_text=draft
        )

        # Step 4: Audit
        result.audit = self.auditor.audit(chapter, draft, observation.facts)

        # Step 5: Final text (no revision in standalone mode)
        result.final = draft
        result.revised = False
        result.success = result.audit.passed or result.audit.score >= 70

        # Save truth files
        if reflection.applied:
            self.truth.save()

        logger.info(f"Chapter {chapter} pipeline: facts={result.facts_count}, "
                     f"score={result.audit.score:.0f}, "
                     f"hooks=+{result.hooks_new}/-{result.hooks_resolved}, "
                     f"pass={result.success}")
        return result

    def run_full_cycle(self, chapter: int, prompt: str,
                       max_revise_rounds: int = 2,
                       style_fingerprint: Any = None) -> NovelPipelineResult:
        """Full LLM-integrated write cycle with revision loop.

        Steps: plan → write draft → observe → reflect → audit → revise (if needed)

        Args:
            chapter: Chapter number.
            prompt: Chapter intent/outline (e.g., "张律到达散修集市，发现灵药被垄断").
            max_revise_rounds: Max revision rounds if audit score < 85.
            style_fingerprint: Optional StyleFingerprint for style-guided writing.

        Returns:
            NovelPipelineResult with final text, audit score, and revision count.
        """
        if not self.llm_client:
            raise RuntimeError("run_full_cycle requires an LLM client. Pass llm_client to NovelPipeline().")

        result = NovelPipelineResult(chapter=chapter)

        # Snapshot truth files before changes
        self.truth.snapshot(chapter)

        # ── Step 1: Build context from truth files ──
        context = self.truth.context_for_chapter(chapter, max_chars=4000)

        # ── Step 2: Build style guidance ──
        style_guide = ""
        if style_fingerprint:
            from .style_imitator import StyleImitator
            imitator = StyleImitator()
            imitator.fingerprint = style_fingerprint
            style_guide = imitator.generate_style_prompt()

        # ── Step 3: Generate draft via LLM ──
        draft_prompt = self._build_write_prompt(chapter, prompt, context, style_guide)
        draft = self._call_llm(draft_prompt, max_tokens=6000, temperature=0.8)
        if not draft:
            result.success = False
            logger.error(f"Chapter {chapter}: LLM returned empty draft")
            return result

        result.draft = draft
        current_text = draft

        # ── Step 4: Observe → Reflect → Audit → Revise loop ──
        for round_idx in range(max_revise_rounds + 1):
            # Observe
            observation = self.observer.observe(chapter, current_text)

            # Reflect (only on first round to avoid over-updating)
            if round_idx == 0:
                reflection = self.reflector.reflect_and_summarize(
                    observation, chapter_title=prompt[:20], chapter_text=current_text
                )
                if reflection.applied:
                    self.truth.save()

            # Audit
            audit = self.auditor.audit(chapter, current_text, observation.facts)
            result.audit = audit
            result.facts_count = len(observation.facts)
            result.hooks_new = len(observation.new_hooks)
            result.hooks_resolved = len(observation.resolved_hooks)

            logger.info(f"Chapter {chapter} round {round_idx}: score={audit.score:.0f}, "
                         f"criticals={len(audit.criticals)}, warnings={len(audit.warnings)}")

            # If passed, we're done
            if audit.passed:
                result.revised = round_idx > 0
                result.success = True
                result.final = current_text
                break

            # If not passed and we have revision rounds left, revise
            if round_idx < max_revise_rounds:
                revise_prompt = self._build_revise_prompt(
                    chapter, current_text, audit, context, style_guide
                )
                revised = self._call_llm(revise_prompt, max_tokens=6000, temperature=0.7)
                if revised:
                    current_text = revised
                else:
                    logger.warning(f"Chapter {chapter} revision round {round_idx}: LLM returned empty")
                    break
            else:
                # Last round: use what we have
                result.revised = True
                result.success = audit.score >= 70
                result.final = current_text

        if not result.final:
            result.final = current_text

        logger.info(f"Chapter {chapter} full cycle done: score={result.audit.score:.0f}, "
                     f"revised={result.revised}, success={result.success}")
        return result

    def _build_write_prompt(self, chapter: int, intent: str,
                            context: str, style_guide: str) -> str:
        """Build the LLM prompt for chapter generation."""
        parts = [
            f"你是一位网络小说作家。请根据以下信息撰写第 {chapter} 章。",
            "",
            f"【本章意图】{intent}",
            "",
            "【上下文（来自真相文件）】",
            context[:3000],
        ]
        if style_guide:
            parts.append("")
            parts.append(style_guide)

        parts.extend([
            "",
            "【写作要求】",
            "1. 字数 2000-4000 字",
            "2. 用具体动作和感官描写，不要抽象概述",
            "3. 对话自然口语化，每个角色有不同的说话方式",
            "4. 避免使用 AI 常见词汇（不禁、竟然、忽然、微微等）",
            "5. 章末留下悬念或反转",
            "6. 不要使用禁止句式（不是…而是…、不仅…而且…）",
            "",
            "直接输出正文，不要输出大纲或注释。",
        ])
        return "\n".join(parts)

    def _build_revise_prompt(self, chapter: int, current_text: str,
                             audit_result: Any, context: str,
                             style_guide: str) -> str:
        """Build the LLM prompt for revision based on audit issues."""
        parts = [
            f"你是一位网络小说编辑。请根据审计反馈修改第 {chapter} 章。",
            "",
            f"【审计分数】{audit_result.score:.0f}/100",
            "",
            "【需要修复的问题】",
        ]

        # List critical and warning issues (sanitized to prevent injection)
        for issue in audit_result.issues:
            if issue.severity.value in ("critical", "warning"):
                desc = self._sanitize_for_prompt(issue.description)
                parts.append(f"- [{issue.dimension}] {desc}")
                if issue.suggestion:
                    parts.append(f"  建议: {self._sanitize_for_prompt(issue.suggestion)}")

        parts.extend([
            "",
            "【当前正文】",
            current_text,
        ])

        if style_guide:
            parts.extend(["", style_guide])

        parts.extend([
            "",
            "【修改要求】",
            "1. 只修改有问题的部分，保留好的内容",
            "2. 修复所有 critical 和 warning 级别的问题",
            "3. 保持情节不变，只改善表达",
            "4. 直接输出修改后的完整正文",
        ])
        return "\n".join(parts)

    @staticmethod
    def _sanitize_for_prompt(text: str, max_len: int = 200) -> str:
        """Sanitize text before embedding in LLM prompt to prevent injection."""
        # Strip control characters and truncate
        cleaned = text.replace("\n", " ").strip()
        if len(cleaned) > max_len:
            cleaned = cleaned[:max_len] + "…"
        return cleaned

    def _call_llm(self, prompt: str, max_tokens: int = 4096,
                  temperature: float = 0.7, timeout: float = 120.0) -> str:
        """Call the LLM client and return the response text.

        Args:
            timeout: Seconds to wait before giving up (default 120s).
        """
        if not self.llm_client:
            raise RuntimeError("No LLM client configured")

        import threading

        result_holder = [None, None]  # [response, error]

        def _worker():
            try:
                if hasattr(self.llm_client, 'chat'):
                    response = self.llm_client.chat(
                        prompt, max_tokens=max_tokens, temperature=temperature
                    )
                    content = getattr(response, 'content', '')
                    # X-1: thinking models return empty content, fallback to reasoning_content
                    if not content:
                        content = getattr(response, 'reasoning_content', '') or str(response)
                    result_holder[0] = content
                elif callable(self.llm_client):
                    result_holder[0] = self.llm_client(
                        prompt, max_tokens=max_tokens, temperature=temperature
                    )
                elif hasattr(self.llm_client, 'generate'):
                    result_holder[0] = self.llm_client.generate(
                        prompt, max_tokens=max_tokens, temperature=temperature
                    )
                else:
                    result_holder[1] = RuntimeError(
                        f"Unsupported LLM client type: {type(self.llm_client)}"
                    )
            except Exception as e:
                result_holder[1] = e

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        thread.join(timeout=timeout)

        if thread.is_alive():
            logger.error(f"LLM call timed out after {timeout}s")
            return ""

        if result_holder[1]:
            raise result_holder[1]

        return result_holder[0] or ""

    @staticmethod
    def get_template() -> list[dict]:
        """Return the novel pipeline template for Symphony Pipeline integration."""
        return NOVEL_PIPELINE_STEPS.copy()
