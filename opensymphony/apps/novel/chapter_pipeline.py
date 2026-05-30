"""Chapter Pipeline — end-to-end chapter generation for the novel.

Orchestrates: outline → scene generation → draft → truth update → style/anti-AI → audit.

Usage (standalone with LLM):
    pipe = ChapterPipeline(truth_files, llm_fn=call_mimo)
    result = pipe.run(chapter=2, intent="张律在土地庙遇到苏晚...")

Usage (with Framework agent):
    pipe = ChapterPipeline(truth_files)
    result = pipe.run_standalone(chapter=2, draft="...", intent="...")
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .ability_updater import AbilityUpdater
from .anti_ai import AntiAI as AntiAIProcessor
from .auditor import NovelAuditor
from .observer import Observer
from .relationship_updater import RelationshipUpdater
from .style_imitator import StyleImitator
from .truth_files import TruthFiles
from .truth_updater import TruthUpdater

logger = logging.getLogger("symphony.apps.novel.chapter_pipeline")


@dataclass
class ChapterResult:
    """Result of a chapter pipeline run."""
    chapter: int
    intent: str = ""
    outline: str = ""
    draft: str = ""
    final: str = ""
    scenes: list[str] = field(default_factory=list)
    audit_score: float = 0.0
    audit_passed: bool = False
    facts_extracted: int = 0
    truth_updated: bool = False
    anti_ai_fixes: int = 0
    ability_changes: int = 0
    relationship_changes: int = 0
    elapsed_s: float = 0.0
    success: bool = False
    errors: list[str] = field(default_factory=list)


class ChapterPipeline:
    """End-to-end chapter generation pipeline.

    Flow:
        1. context: Load truth file context for this chapter
        2. outline: Generate chapter outline (beats) from intent + context
        3. scenes: Generate each scene (with character interaction if YAML available)
        4. assemble: Combine scenes into chapter draft
        5. truth_update: Extract facts, update truth files
        6. anti_ai: Post-process to remove AI-speak patterns
        7. audit: Quality check
        8. revise (optional): Fix audit issues
    """

    def __init__(
        self,
        truth: TruthFiles,
        llm_fn: Callable | None = None,
        characters_dir: Path | None = None,
        style_fingerprint: Any = None,
        world_setting_path: Path | None = None,
    ):
        """
        Args:
            truth: TruthFiles instance.
            llm_fn: Callable(prompt, max_tokens, temperature) -> str. None = standalone only.
            characters_dir: Directory with character YAML files.
            style_fingerprint: Optional StyleFingerprint for style-guided writing.
            world_setting_path: Path to world_setting.yaml for world constraints.
        """
        self.truth = truth
        self.llm_fn = llm_fn
        self.characters_dir = characters_dir
        self.style_fingerprint = style_fingerprint

        # Load world setting first (sets _world_context and _forbidden_words)
        self._world_context = ""
        self._forbidden_words: list[str] = []
        if world_setting_path and world_setting_path.exists():
            self._load_world_setting(world_setting_path)
        elif characters_dir:
            ws = characters_dir / "world_setting.yaml"
            if ws.exists():
                self._load_world_setting(ws)

        # Now init components that depend on _forbidden_words
        self.truth_updater = TruthUpdater(truth, Observer(llm_client=llm_fn))
        self.ability_updater = AbilityUpdater(truth)
        self.relationship_updater = RelationshipUpdater(truth)
        self.anti_ai = AntiAIProcessor()
        self.auditor = NovelAuditor(truth=truth, world_forbidden_words=self._forbidden_words)
        self.imitator = StyleImitator()
        if style_fingerprint:
            self.imitator.fingerprint = style_fingerprint

    def _load_world_setting(self, path: Path) -> None:
        """Load world_setting.yaml and build injection context."""
        try:
            import yaml
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except ImportError:
            # Fallback: simple line-based parsing
            data = {}
            content = path.read_text(encoding="utf-8")
            # Extract forbidden_words section
            in_forbidden = False
            for line in content.split("\n"):
                if "forbidden_words:" in line:
                    in_forbidden = True
                    continue
                if in_forbidden and line.strip().startswith("- "):
                    word = line.strip().lstrip("- ").strip('"').strip("'").strip()
                    if word and not word.startswith("#"):
                        self._forbidden_words.append(word)
                elif in_forbidden and not line.strip().startswith("-") and line.strip():
                    in_forbidden = False

        if not isinstance(data, dict):
            return

        world = data.get("world", data)

        # Build concise world context for LLM injection
        parts = []
        parts.append(f"世界：{world.get('name', '未知')}（{world.get('type', '')}）")

        hard_rules = world.get("hard_rules", [])
        if hard_rules:
            parts.append("【硬规则】")
            for r in hard_rules:
                parts.append(f"  - {r}")

        geo = world.get("geography", {})
        if geo:
            region = geo.get("current_region", "")
            parts.append(f"当前地区：{region}")
            for name, desc in geo.get("locations", {}).items():
                parts.append(f"  {name}：{desc}")

        factions = world.get("factions", {})
        if factions:
            parts.append("势力：")
            for faction, members in factions.items():
                if isinstance(members, list):
                    parts.append(f"  {faction}：{', '.join(members)}")

        tone = world.get("tone", {})
        if tone:
            parts.append(f"基调：{tone.get('core', '')}")
            fp = tone.get("forbidden_patterns", [])
            if fp:
                parts.append(f"禁止词汇：{', '.join(fp)}")

        self._world_context = "\n".join(parts)

        # Collect forbidden words for audit
        fw = world.get("forbidden_words", {})
        if isinstance(fw, dict):
            for category, words in fw.items():
                if isinstance(words, list):
                    self._forbidden_words.extend(words)

        logger.info(f"World setting loaded: {len(self._world_context)} chars, "
                     f"{len(self._forbidden_words)} forbidden words")

    @property
    def world_context(self) -> str:
        """World context string for LLM prompt injection."""
        return self._world_context

    @property
    def forbidden_words(self) -> list[str]:
        """List of forbidden modern-world words."""
        return self._forbidden_words

    def run(self, chapter: int, intent: str,
            max_revise_rounds: int = 1) -> ChapterResult:
        """Full LLM-integrated chapter generation.

        Args:
            chapter: Chapter number (1-indexed).
            intent: Chapter intent / outline description.
            max_revise_rounds: Max revision rounds.

        Returns:
            ChapterResult with final text and metadata.
        """
        t0 = time.time()
        result = ChapterResult(chapter=chapter, intent=intent)

        if not self.llm_fn:
            result.errors.append("No LLM function provided. Use run_standalone() instead.")
            result.elapsed_s = time.time() - t0
            return result

        try:
            # 1. Load context from truth files
            context = self.truth.context_for_chapter(chapter, max_chars=4000)

            # 2. Style guidance
            style_guide = ""
            if self.style_fingerprint:
                style_guide = self.imitator.generate_style_prompt()

            # 3. Generate outline
            result.outline = self._generate_outline(chapter, intent, context, style_guide)
            if not result.outline:
                result.errors.append("Outline generation failed")
                result.elapsed_s = time.time() - t0
                return result

            # 4. Generate scenes from outline
            scenes = self._generate_scenes(chapter, result.outline, context, style_guide)
            result.scenes = scenes

            # 5. Assemble draft
            result.draft = "\n\n".join(scenes) if scenes else ""

            if not result.draft:
                result.errors.append("Draft generation produced empty text")
                result.elapsed_s = time.time() - t0
                return result

            # 6. Truth update (extract facts)
            truth_result = self.truth_updater.update(chapter, result.draft)
            result.facts_extracted = truth_result.facts_extracted
            result.truth_updated = len(truth_result.delta_applied) > 0
            result.errors.extend(truth_result.errors)

            # 6b. Ability + Relationship updates
            if truth_result.facts_extracted > 0:
                observation = self.truth_updater.observer.observe(chapter, result.draft)
                ab_result = self.ability_updater.update(observation, text=result.draft)
                result.ability_changes = len(ab_result.changes)
                rel_result = self.relationship_updater.update(observation, text=result.draft)
                result.relationship_changes = len(rel_result.changes)

                # 6c. Update character YAML souls with chapter facts
                self._update_character_yamls(chapter, observation, result.draft)

            # 7. Anti-AI post-processing
            aa_result = self.anti_ai.process(result.draft)
            result.final = aa_result.cleaned
            result.anti_ai_fixes = aa_result.replacements_made

            # 8. Audit + optional revision loop
            current_text = result.final
            for round_idx in range(max_revise_rounds + 1):
                audit = self.auditor.audit(chapter, current_text)
                result.audit_score = audit.score
                result.audit_passed = audit.passed

                if audit.passed or round_idx >= max_revise_rounds:
                    result.final = current_text
                    break

                # Revise
                revised = self._revise(chapter, current_text, audit, context, style_guide)
                if revised:
                    current_text = revised
                else:
                    result.final = current_text
                    break

            result.success = result.audit_score >= 70 and len(result.final) > 500

        except Exception as e:
            result.errors.append(f"Pipeline error: {e}")
            logger.error(f"Chapter {chapter} pipeline error: {e}")

        result.elapsed_s = time.time() - t0
        logger.info(f"Chapter {chapter}: score={result.audit_score:.0f}, "
                     f"facts={result.facts_extracted}, "
                     f"anti_ai={result.anti_ai_fixes}, "
                     f"scenes={len(result.scenes)}, "
                     f"elapsed={result.elapsed_s:.1f}s")
        return result

    def run_standalone(self, chapter: int, draft: str,
                       intent: str = "") -> ChapterResult:
        """Run truth_update + anti_ai + audit on an existing draft (no LLM needed).

        Args:
            chapter: Chapter number.
            draft: Pre-written chapter text.
            intent: Optional intent description.

        Returns:
            ChapterResult with processed text and metadata.
        """
        t0 = time.time()
        result = ChapterResult(chapter=chapter, intent=intent, draft=draft)

        # Truth update
        truth_result = self.truth_updater.update(chapter, draft)
        result.facts_extracted = truth_result.facts_extracted
        result.truth_updated = len(truth_result.delta_applied) > 0
        result.errors.extend(truth_result.errors)

        # Ability + Relationship updates
        if truth_result.facts_extracted > 0:
            observation = self.truth_updater.observer.observe(chapter, draft)
            ab_result = self.ability_updater.update(observation, text=draft)
            result.ability_changes = len(ab_result.changes)
            rel_result = self.relationship_updater.update(observation, text=draft)
            result.relationship_changes = len(rel_result.changes)

        # Anti-AI post-processing
        aa_result = self.anti_ai.process(draft)
        result.final = aa_result.cleaned
        result.anti_ai_fixes = aa_result.replacements_made

        # Audit
        try:
            audit = self.auditor.audit(chapter, result.final)
            result.audit_score = audit.score
            result.audit_passed = audit.passed
        except Exception as e:
            result.errors.append(f"Audit failed: {e}")

        result.success = result.audit_score >= 70 and len(result.final) > 100
        result.elapsed_s = time.time() - t0
        return result

    # ── LLM Generation Helpers ───────────────────────────────────

    def _call_llm(self, prompt: str, max_tokens: int = 4096,
                  temperature: float = 0.8) -> str:
        """Call LLM with thinking tag cleanup."""
        if not self.llm_fn:
            return ""
        try:
            if hasattr(self.llm_fn, 'chat'):
                response = self.llm_fn.chat(prompt, max_tokens=max_tokens, temperature=temperature)
                content = getattr(response, 'content', '')
                if not content:
                    content = getattr(response, 'reasoning_content', '') or str(response)
            else:
                content = self.llm_fn(prompt, max_tokens=max_tokens, temperature=temperature)

            # Strip thinking tags
            content = re.sub(r'<think[^>]*>.*?</think\s*>', '', content, flags=re.DOTALL)
            return content.strip()
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            return ""

    def _generate_outline(self, chapter: int, intent: str,
                          context: str, style_guide: str) -> str:
        """Generate a chapter outline (3-5 scene beats)."""
        prompt = f"""你是一位小说策划。为第 {chapter} 章生成 3-5 个场景节拍（beats）。

【本章意图】{intent}

【世界设定（必须遵守）】
{self._world_context}

【上下文】
{context[:2500]}

{style_guide}

输出格式（纯文本，每个 beat 一行）：
1. 场景名：场景描述（50字内）
2. ...

只输出场景列表，不要其他文字。"""

        return self._call_llm(prompt, max_tokens=1024, temperature=0.7)

    def _generate_scenes(self, chapter: int, outline: str,
                         context: str, style_guide: str) -> list[str]:
        """Generate the entire chapter in one LLM call (no scene-beat splitting).

        Why single-pass: Splitting into beats causes each beat to be treated
        as an independent story, producing 2-3 contradictory versions of the
        same event. Single-pass avoids this entirely.
        """
        # Build character state summary from truth files for injection
        char_state = self._build_character_state()

        prompt = f"""你是一位资深网络小说作家。撰写第 {chapter} 章的完整正文。

【本章大纲】
{outline}

【世界设定（必须遵守，违反即作废）】
{self._world_context}

【角色当前状态（必须遵守，违反即作废）】
{char_state}

【前文上下文】
{context[:3000]}

{style_guide}

【写作要求】
- 3000-5000 字，完整的一章
- 直接从第一个场景开始写，不要加章节标题
- 具体动作和感官描写，不用抽象概述
- 对话自然口语化，每个人物有自己的说话方式
- 不使用：不禁、竟然、忽然、微微、不是…而是…
- 章末留悬念
- 绝对禁止出现现代元素（手机、律师楼、法院、警察、汽车、安全屋等）
- 所有场景必须发生在修仙世界内
- 角色行为必须符合【角色当前状态】中的性格和约束
- 如果某角色在本章有重要变化（新能力、关系变化、获得物品），在正文中自然体现

直接输出正文（不要标题、不要分段说明）："""

        chapter_text = self._call_llm(prompt, max_tokens=8192, temperature=0.8)
        if chapter_text and len(chapter_text) > 200:
            # Fix truncation if needed
            chapter_text = self._fix_truncation(chapter_text, max_retries=3)
            return [chapter_text]

        return []

    def _build_character_state(self) -> str:
        """Build a concise character state summary from truth files + YAML souls.

        This gets injected into every chapter prompt to ensure character consistency.
        """
        lines = []

        # Load character YAML souls
        if self.characters_dir:
            try:
                import yaml
                for f in sorted(self.characters_dir.glob("*.yaml")):
                    if f.name == "world_setting.yaml":
                        continue
                    try:
                        data = yaml.safe_load(f.read_text(encoding="utf-8"))
                        if not data or "name" not in data:
                            continue
                        name = data["name"]
                        personality = data.get("personality", {})
                        core = personality.get("core", "")
                        speech = personality.get("speech_pattern", "")
                        hard = data.get("hard_constraints", [])
                        abilities = data.get("abilities", [])

                        line = f"**{name}**"
                        if core:
                            line += f"：{core}"
                        if speech:
                            line += f" | 说话：{speech}"

                        # Add ability levels
                        ability_strs = []
                        for ab in abilities:
                            if isinstance(ab, dict):
                                aid = ab.get("id", "")
                                lvl = ab.get("level", 0)
                                desc = ab.get("description", "")
                                if desc:
                                    ability_strs.append(f"{aid}(Lv{lvl})")
                        if ability_strs:
                            line += f" | 能力：{', '.join(ability_strs)}"

                        # Top 3 hard constraints
                        if hard:
                            line += f" | 禁止：{'; '.join(str(h)[:50] for h in hard[:3])}"

                        lines.append(line)
                    except Exception:
                        continue
            except ImportError:
                pass

        # Add relationship data from truth files if available
        try:
            matrix = self.truth.get("character_matrix")
            if matrix and isinstance(matrix, dict):
                characters = matrix.get("characters", {})
                for char_name, char_data in characters.items():
                    rels = char_data.get("relationships", {})
                    if rels:
                        rel_strs = []
                        for target, rel_info in rels.items():
                            if isinstance(rel_info, dict):
                                trust = rel_info.get("trust", 0)
                                rtype = rel_info.get("type", "")
                                rel_strs.append(f"{target}(信任{trust},{rtype})")
                        if rel_strs:
                            lines.append(f"  {char_name}的关系：{', '.join(rel_strs[:5])}")
        except Exception:
            pass

        return "\n".join(lines) if lines else "（暂无角色状态数据）"

    def _update_character_yamls(self, chapter: int, observation: Any, text: str) -> None:
        """Update character YAML souls using already-extracted observation data.

        No LLM call — pure deterministic code. Uses regex to find status changes
        and updates the YAML files on disk for next chapter's _build_character_state().
        """
        if not self.characters_dir:
            return

        try:
            import yaml
        except ImportError:
            return

        # Find all character YAMLs and check which ones are mentioned in this chapter
        for f in sorted(self.characters_dir.glob("*.yaml")):
            if f.name == "world_setting.yaml":
                continue
            try:
                data = yaml.safe_load(f.read_text(encoding="utf-8"))
                if not data or "name" not in data:
                    continue
                name = data["name"]
                mention_count = text.count(name)
                if mention_count == 0:
                    continue

                modified = False

                # Extract status hints via regex (deterministic)
                status_hints = []
                # Pattern: name + action verb within same sentence
                for pattern in [
                    rf'{name}([^。！？]{{0,80}})(受伤|吐血|倒下|昏迷|醒来|站起|死亡|逃脱)',
                    rf'{name}([^。！？]{{0,80}})(获得|得到|发现|找到|学会|掌握|觉醒)',
                    rf'{name}([^。！？]{{0,80}})(离开|到达|进入|被困|被救)',
                ]:
                    for m in re.finditer(pattern, text):
                        hint = m.group(0)
                        if len(hint) > 3:
                            status_hints.append(hint[:80])

                if status_hints:
                    data["status"] = f"[Ch{chapter}] {status_hints[-1]}"
                    modified = True

                # Always add chapter appearance to knowledge_state
                ks = data.setdefault("knowledge_state", {})
                known = ks.setdefault("known", [])
                chapter_ref = f"[Ch{chapter}] 本章出现({mention_count}次提及)"
                if not any(f"Ch{chapter}]" in k for k in known):
                    known.append(chapter_ref)
                    modified = True

                if modified:
                    with open(f, "w", encoding="utf-8") as wf:
                        yaml.dump(data, wf, allow_unicode=True,
                                  default_flow_style=False, sort_keys=False)
                    logger.info(f"Ch{chapter}: updated YAML for {name}")

            except Exception as e:
                logger.warning(f"Ch{chapter}: YAML update failed: {e}")


    def _extract_json(self, text: str) -> dict | list | None:
        """Extract JSON from LLM output with heavy fault tolerance.

        Handles: markdown code fences, leading/trailing text, truncated JSON,
        single quotes instead of double quotes, trailing commas, Chinese quotes,
        JSON embedded in explanatory text, etc.
        """
        if not text:
            return None

        s = text.strip()

        # Strategy 1: Direct parse
        try:
            return json.loads(s)
        except (json.JSONDecodeError, ValueError):
            pass

        # Strategy 2: Strip markdown code fences
        # Match ```json ... ``` or ``` ... ```
        m = re.search(r'```(?:json|JSON)?\s*\n?(.*?)\n?\s*```', s, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except (json.JSONDecodeError, ValueError):
                pass

        # Strategy 3: Find first { to last } — extract outermost JSON object
        first_brace = s.find('{')
        last_brace = s.rfind('}')
        if first_brace >= 0 and last_brace > first_brace:
            candidate = s[first_brace:last_brace + 1]
            try:
                return json.loads(candidate)
            except (json.JSONDecodeError, ValueError):
                # Strategy 3b: Fix common issues in the extracted candidate
                fixed = self._fix_json_string(candidate)
                try:
                    return json.loads(fixed)
                except (json.JSONDecodeError, ValueError):
                    # Strategy 3c: Truncated JSON recovery — close open braces/brackets
                    recovered = self._recover_truncated_json(fixed)
                    if recovered:
                        return recovered

        # Strategy 4: Find first [ to last ] — extract JSON array
        first_bracket = s.find('[')
        last_bracket = s.rfind(']')
        if first_bracket >= 0 and last_bracket > first_bracket:
            candidate = s[first_bracket:last_bracket + 1]
            try:
                return json.loads(candidate)
            except (json.JSONDecodeError, ValueError):
                pass

        # Strategy 5: Replace Chinese quotes and try again
        cn_replaced = s.replace('"', '"').replace('"', '"').replace(''', "'").replace(''', "'")
        first_brace = cn_replaced.find('{')
        last_brace = cn_replaced.rfind('}')
        if first_brace >= 0 and last_brace > first_brace:
            try:
                return json.loads(cn_replaced[first_brace:last_brace + 1])
            except (json.JSONDecodeError, ValueError):
                pass

        return None

    def _fix_json_string(self, s: str) -> str:
        """Fix common JSON formatting issues from LLM output."""
        # Remove trailing commas before } or ]
        s = re.sub(r',\s*([}\]])', r'\1', s)
        # Fix single quotes to double quotes (but not inside strings)
        # Simple approach: replace unescaped single quotes
        s = re.sub(r"(?<!\\)'", '"', s)
        # Fix missing quotes around keys: {key: ...} -> {"key": ...}
        s = re.sub(r'(\{|,)\s*([^"{\s:]+)\s*:', r'\1"\2":', s)
        return s

    def _recover_truncated_json(self, s: str) -> dict | None:
        """Try to recover a truncated JSON object by closing open structures.

        E.g. '{"a": {"b": "c"}, "d": {"e": "f' -> '{"a": {"b": "c"}, "d": {"e": "f"}}'
        """
        # Count open vs close for { and [
        open_braces = s.count('{') - s.count('}')
        open_brackets = s.count('[') - s.count(']')

        if open_braces < 0 or open_brackets < 0:
            return None

        # Try to close unclosed strings first
        # Check if we're inside a string (odd number of unescaped quotes)
        # Simple heuristic: just add closing characters
        recovery = s

        # Close potential unclosed string
        in_string = False
        for i, c in enumerate(recovery):
            if c == '"' and (i == 0 or recovery[i-1] != '\\'):
                in_string = not in_string
        if in_string:
            recovery += '"'

        # Close brackets first, then braces
        recovery += ']' * max(0, open_brackets)
        recovery += '}' * max(0, open_braces)

        try:
            result = json.loads(recovery)
            return result
        except (json.JSONDecodeError, ValueError):
            return None

    def _fix_truncation(self, text: str, max_retries: int = 2) -> str:
        """Detect and fix truncated text by checking if it ends mid-sentence.

        Signs of truncation:
        - Ends with comma, dash, or ellipsis followed by no closing punctuation
        - Ends mid-dialogue (no closing quote)
        - Ends with incomplete sentence (no 。！？)"
        """
        if not text or len(text) < 50:
            return text

        stripped = text.rstrip()
        last_char = stripped[-1] if stripped else ''

        # Check if text ends cleanly
        clean_endings = {'。', '！', '？', '"', '」', '…', '—', ')', '）', '`'}
        ends_cleanly = last_char in clean_endings

        # Check for unclosed dialogue
        open_quotes = stripped.count('"') + stripped.count('"')
        close_quotes = stripped.count('"') + stripped.count('"')
        unclosed_dialogue = open_quotes > close_quotes

        if ends_cleanly and not unclosed_dialogue:
            return text  # No truncation detected

        # Text appears truncated - try to continue
        for attempt in range(max_retries):
            continue_prompt = f"""上文在写作过程中被截断了。请续写接下来的内容（约500字），使段落自然收尾。

【截断位置】
...{stripped[-200:]}

【续写要求】
- 自然衔接上文
- 用1-2个段落收尾
- 留下章节悬念
- 直接输出续写内容（不要重复上文）："""

            continuation = self._call_llm(continue_prompt, max_tokens=1500, temperature=0.7)
            if continuation and len(continuation) > 20:
                text = stripped + continuation
                # Check if the continuation ended cleanly
                new_last = text.rstrip()[-1]
                if new_last in clean_endings:
                    break
            else:
                break

        return text

    def _revise(self, chapter: int, text: str, audit: Any,
                context: str, style_guide: str) -> str:
        """Revise chapter text based on audit feedback."""
        issues_text = ""
        for issue in getattr(audit, 'issues', [])[:5]:
            issues_text += f"- [{getattr(issue, 'dimension', '?')}] {getattr(issue, 'description', '')}\n"

        prompt = f"""你是编辑。根据审计反馈修改第 {chapter} 章。

【审计分数】{audit.score:.0f}/100

【问题】
{issues_text}

【原文】
{text[:12000]}

只修改有问题的部分，保留好的内容。直接输出修改后的完整正文。"""

        return self._call_llm(prompt, max_tokens=8192, temperature=0.7)
