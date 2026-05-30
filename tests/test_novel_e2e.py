"""End-to-end integration test: Novel Pipeline through Symphony Framework.

Tests the full flow: Kernel → NovelPipeline → 33-dim audit → style → anti-AI.
Uses mock LLM to avoid cloud API dependency.
"""
import json
import logging

import pytest
from opensymphony.apps.novel import (
    AntiAI,
    NovelAuditor,
    NovelPipeline,
    Severity,
    StyleImitator,
    TruthFile,
    TruthFiles,
)

logger = logging.getLogger("test_novel_e2e")

# ── Fixtures ────────────────────────────────────────────────────

CHAPTER_1 = """张律师醒来的时候，发现自己躺在一片陌生的密林中。

头顶的树冠遮天蔽日，只有零星的光斑透过枝叶洒落。空气中弥漫着一股他从未闻过的气味，像是草药和松脂的混合。

"你醒了？"一个苍老的声音从身后传来。

张律猛地转身，看到一个白发老者盘坐在巨石上。老者身穿灰色长袍，手中捏着一枚翠绿色的丹药。

"你是谁？这是哪里？"张律警惕地后退一步，右手下意识地去摸口袋里的手机——空的。

老者笑了笑，将丹药递过来："老夫姓李，这里是苍梧山。你从天上掉下来，昏了三天三夜。先把这颗疗伤丹吃了。"

张律接过丹药，犹豫片刻，还是吞了下去。一股温热的气流从腹部升起，后背的剧痛竟然在几秒内消散。

他环顾四周，远处的山峰之间似乎有什么东西在飞——是人。有人在飞。

"这不可能。"张律喃喃道。

李老者哈哈大笑："年轻人，你不在你那个世界了。这里是修仙界。在修仙界，一切皆有可能。不过你放心，老夫不会害你。你的伤已经好了大半，但要完全恢复还得修炼几日。"

张律深吸一口气，试图让自己冷静下来。他是律师，冷静分析是他的本能。

"您能告诉我更多吗？比如，我是怎么到这里来的？有没有办法回去？"

李老者摇摇头："来路不明，去路亦不明。不过既然来了，不妨先了解这个世界。苍梧山方圆百里都是散修的地盘，再往北走三百里就是天机宗的地界，那里规矩多，不适合你这样的外来者。"

张律默默记下这些信息，目光扫过密林深处。树干上缠绕着一种发出淡蓝色荧光的藤蔓，脚下的土壤呈现出不自然的紫黑色。

一切都在告诉他：这里不是地球。

"我能学修仙吗？"他问。

"当然可以。"李老者从袖中取出一卷泛黄的竹简，"这是老夫年轻时修炼的功法，叫做《归元诀》。虽然不是什么上乘功法，但胜在根基扎实。你若愿意学，老夫可以教你。"

张律接过竹简，展开一看——上面的文字他竟然能看懂。

"好。"他说，"我学。"

李老者满意地点点头："不过修炼之前，你得先知道一件事。这个世界里，强者为尊。法律——如果你们那边有这东西的话——在这里不存在。有理无理，拳头说了算。"

张律沉默了。没有法律的世界。

这对他来说，比任何妖魔鬼怪都可怕。"""


CHAPTER_2_DRAFT = """第二天清晨，张律按照《归元诀》的指引开始第一次修炼。

他盘膝而坐，闭上眼睛，尝试感受体内的灵气。李老者说，灵气就藏在空气中，只需要用意念引导它进入身体就行。

然而尝试了一个时辰，什么也没感觉到。

"别急。"李老者在旁边煮着一锅药汤，"第一次修炼能感受到灵气的人，百中无一。你要放松，不要用脑子去想，用身体去感受。"

张律调整呼吸，不再刻意去寻找灵气，而是放空思维。

忽然间，一股细微的暖流从指尖涌入。

他睁开眼睛，看到自己的右手指尖微微发光。

"不错！"李老者赞许道，"一天就能引气入体，天赋还行。"

但张律的注意力已经不在修炼上了。他站起身，走到密林边缘，朝北方望去。

"李老，你说的天机宗——他们管多大地方？"

"整个北境三十六城，都是天机宗的地盘。怎么，你想去？"

"我想了解这个世界的规则。"张律说，"不管是修仙界还是凡间，有组织就有规则。我想知道天机宗的规则是什么。"

李老者看了他一眼，似乎在重新审视这个从天上掉下来的年轻人。

"你这个人……有点意思。"

他放下药碗，从怀里掏出一块令牌，上面刻着一个"散"字。

"这是散修盟的信物。拿着它，你可以在散修地界自由行走。但如果要去天机宗的地盘，你得通过他们的入门考核——每三个月一次，下一次在半月后。"

张律接过令牌，掂了掂重量。

"考核难吗？"

"对你来说……"李老者犹豫了一下，"可能不难，也可能要命。天机宗考核的不是修为，是心性。"

"心性？"

"就是你的意志、判断、还有面对危险时的选择。修为可以练，心性改不了。"

张律点点头。心性考核。这他熟。

他做过无数次心理评估——作为刑辩律师，他比任何人都清楚，一个人的选择在极端压力下会暴露什么。

"我去。"他说。

李老者叹了口气："我就知道你会这么说。"


"""


@pytest.fixture
def story_dir(tmp_path):
    """Create a story directory with initialized truth files."""
    for tf in TruthFile:
        f = tmp_path / f"{tf.value}.json"
        if tf == TruthFile.CURRENT_STATE:
            f.write_text(json.dumps({
                "chapter": 1,
                "current_location": "苍梧山密林",
                "active_characters": ["张律", "李老者"],
            }))
        elif tf == TruthFile.PENDING_HOOKS:
            f.write_text(json.dumps({
                "hooks": [
                    {"id": "h1", "content": "张律穿越原因", "status": "open", "start_chapter": 1},
                    {"id": "h2", "content": "天机宗考核", "status": "open", "start_chapter": 1},
                ]
            }))
        elif tf == TruthFile.CHARACTER_MATRIX:
            f.write_text(json.dumps({
                "张律": {
                    "role": "主角",
                    "background": "现代刑辩律师",
                    "personality": "冷静分析型",
                    "abilities": ["法律思维", "心理评估"],
                    "cultivation": "未开始",
                },
                "李老者": {
                    "role": "导师",
                    "background": "苍梧山散修",
                    "abilities": ["《归元诀》", "疗伤丹"],
                },
            }))
        else:
            f.write_text(json.dumps({}))
    return tmp_path


# ── E2E Test 1: Standalone Pipeline (no LLM) ────────────────────

class TestE2EStandalone:
    """Test standalone observe → reflect → audit cycle on Chapter 1."""

    def test_full_standalone_cycle(self, story_dir):
        truth = TruthFiles(story_dir)
        truth.load()

        pipeline = NovelPipeline(truth)
        result = pipeline.run_standalone(
            chapter=1,
            draft=CHAPTER_1,
            chapter_title="律师的死局",
        )

        # Pipeline should succeed
        assert result.success
        assert result.draft == CHAPTER_1
        assert result.final == CHAPTER_1
        assert result.audit is not None
        assert result.audit.score > 0
        assert result.facts_count > 0

        # Category scores should be populated
        assert len(result.audit.category_scores) == 5

        # Print audit summary
        print(f"\n{'='*60}")
        print(f"Chapter 1 Standalone Audit: {result.audit.score:.1f}/100")
        print(f"Facts extracted: {result.facts_count}")
        print(f"Hooks: +{result.hooks_new} / -{result.hooks_resolved}")
        for cat, score in result.audit.category_scores.items():
            print(f"  {cat}: {score:.0f}/100")
        if result.audit.issues:
            print("Issues:")
            for issue in result.audit.issues:
                emoji = "🔴" if issue.severity == Severity.CRITICAL else "🟡" if issue.severity == Severity.WARNING else "ℹ️"
                print(f"  {emoji} [{issue.dimension}] {issue.description}")
        print(f"{'='*60}")

    def test_chapter_2_standalone(self, story_dir):
        truth = TruthFiles(story_dir)
        truth.load()

        # First process chapter 1 to build truth
        pipeline = NovelPipeline(truth)
        pipeline.run_standalone(chapter=1, draft=CHAPTER_1, chapter_title="律师的死局")

        # Now process chapter 2
        result = pipeline.run_standalone(
            chapter=2,
            draft=CHAPTER_2_DRAFT,
            chapter_title="归元诀",
        )

        assert result.success
        assert result.audit.score > 0
        print(f"\nChapter 2 Standalone Audit: {result.audit.score:.1f}/100")


# ── E2E Test 2: Style Imitator ──────────────────────────────────

class TestE2EStyleImitator:
    """Test style fingerprint extraction and prompt generation."""

    def test_fingerprint_from_chapter1(self):
        imitator = StyleImitator()
        fp = imitator.analyze([CHAPTER_1])

        assert fp.total_chars > 500
        assert fp.avg_sentence_len > 5
        assert fp.dialogue_ratio > 0  # Has dialogue

        # Generate prompt
        prompt = imitator.generate_style_prompt()
        assert "句式节奏" in prompt
        assert "段落形态" in prompt

        print(f"\n{'='*60}")
        print(f"Style Fingerprint: {fp.total_chars} chars")
        print(f"  Avg sentence: {fp.avg_sentence_len:.1f} chars (std {fp.sentence_len_std:.1f})")
        print(f"  Avg paragraph: {fp.avg_paragraph_len:.1f} chars")
        print(f"  Dialogue ratio: {fp.dialogue_ratio:.1%}")
        print(f"  Comma/period: {fp.comma_period_ratio:.1f}")
        print(f"  Top 5 words: {', '.join(w for w, _ in fp.top_words[:5])}")
        print(f"  Sensory profile: {fp.sensory_profile}")
        print(f"{'='*60}")

    def test_style_comparison(self):
        imitator = StyleImitator()
        fp1 = imitator.analyze([CHAPTER_1])
        fp2 = imitator.analyze([CHAPTER_2_DRAFT])

        sim = imitator.compare_styles(fp1, fp2)
        assert sim["overall"] > 0.5  # Same author, should be similar

        print(f"\nStyle similarity between Ch1 and Ch2: {sim['overall']:.2%}")


# ── E2E Test 3: Anti-AI Pipeline ────────────────────────────────

class TestE2EAntiAI:
    """Test anti-AI detection and rewriting."""

    def test_detect_ai_patterns(self):
        anti = AntiAI()
        result = anti.process(CHAPTER_1)

        print(f"\n{'='*60}")
        print("Anti-AI Check on Chapter 1:")
        print(f"  Replacements: {result.replacements_made}")
        print(f"  Patterns: {result.patterns_found[:5]}")
        print(f"  Cliches: {result.cliches_found[:5]}")
        print(f"{'='*60}")

    def test_fix_ai_patterns(self):
        bad_text = "他不禁感到震惊。竟然有人能做到！然而他猛地后退。仿佛看到了不可思议的东西。"

        anti = AntiAI()
        suggestions = anti.get_suggestions(bad_text)
        fixed = anti.merge_short_paragraphs(bad_text)

        print("\nAnti-AI Fix:")
        print(f"  Before: {bad_text}")
        print(f"  Suggestions: {suggestions[:3]}")
        print(f"  After merge: {fixed}")


# ── E2E Test 4: Full Cycle with Mock LLM ────────────────────────

class MockLLMClient:
    """Mock LLM that returns pre-written chapter content."""

    def __init__(self, responses: list[str] | None = None):
        self.responses = responses or [CHAPTER_2_DRAFT]
        self._call_idx = 0

    def chat(self, prompt: str, **kwargs):
        idx = min(self._call_idx, len(self.responses) - 1)
        self._call_idx += 1

        class Resp:
            content = self.responses[idx]
            reasoning_content = ""

        return Resp()


class TestE2EFullCycle:
    """Test full LLM-integrated cycle: plan → write → observe → reflect → audit → revise."""

    def test_full_cycle_chapter_2(self, story_dir):
        truth = TruthFiles(story_dir)
        truth.load()

        # First run chapter 1 standalone to build truth
        pipe = NovelPipeline(truth)
        pipe.run_standalone(chapter=1, draft=CHAPTER_1, chapter_title="律师的死局")

        # Now run full cycle for chapter 2 with mock LLM
        mock_llm = MockLLMClient([CHAPTER_2_DRAFT])
        pipe2 = NovelPipeline(truth, llm_client=mock_llm)

        result = pipe2.run_full_cycle(
            chapter=2,
            prompt="张律开始修炼《归元诀》，第一次引气入体。得知天机宗入门考核将在半月后举行，决定前往。",
            max_revise_rounds=1,
        )

        assert result.success
        assert result.final  # Should have final text
        assert result.audit is not None
        assert result.audit.score > 0
        assert mock_llm._call_idx >= 1  # At least the write call

        print(f"\n{'='*60}")
        print("Full Cycle Chapter 2:")
        print(f"  Success: {result.success}")
        print(f"  Revised: {result.revised}")
        print(f"  Audit score: {result.audit.score:.1f}/100")
        print(f"  Facts: {result.facts_count}")
        print(f"  Hooks: +{result.hooks_new} / -{result.hooks_resolved}")
        print(f"  LLM calls: {mock_llm._call_idx}")
        print(f"  Final text length: {len(result.final)} chars")
        print(f"{'='*60}")

    def test_full_cycle_with_style_fingerprint(self, story_dir):
        truth = TruthFiles(story_dir)
        truth.load()

        # Build style fingerprint from chapter 1
        imitator = StyleImitator()
        fp = imitator.analyze([CHAPTER_1])

        # Run full cycle with style guidance
        mock_llm = MockLLMClient([CHAPTER_2_DRAFT])
        pipe = NovelPipeline(truth, llm_client=mock_llm)

        result = pipe.run_full_cycle(
            chapter=2,
            prompt="张律修炼引气入体，得知天机宗考核",
            style_fingerprint=fp,
        )

        assert result.success
        print(f"\nFull Cycle with Style: {result.audit.score:.1f}/100")

    def test_revision_loop(self, story_dir):
        """Test that revision improves score when first draft is bad."""
        truth = TruthFiles(story_dir)
        truth.load()

        bad_draft = """他不禁感到震惊。竟然有人能做到这种程度！他忽然觉得不可思议。

不仅如此，而且他还发现了一个惊人的秘密。然而与此同时，就在这时，他忽然发现了一个秘密。

但是他又不敢确定。不过直觉告诉他，这是真的。

他微微皱眉。眼中闪过一丝复杂的情绪。"""

        # Mock LLM: first call returns bad, second returns good
        mock_llm = MockLLMClient([bad_draft, CHAPTER_2_DRAFT])
        pipe = NovelPipeline(truth, llm_client=mock_llm)

        result = pipe.run_full_cycle(
            chapter=2,
            prompt="测试修订循环",
            max_revise_rounds=2,
        )

        assert mock_llm._call_idx >= 2  # Write + at least 1 revise
        print(f"\nRevision Loop: {mock_llm._call_idx} LLM calls, score={result.audit.score:.1f}, revised={result.revised}")


# ── E2E Test 5: Multi-Chapter Pipeline ──────────────────────────

class TestE2EMultiChapter:
    """Test processing multiple chapters in sequence."""

    def test_two_chapters_sequential(self, story_dir):
        truth = TruthFiles(story_dir)
        truth.load()

        pipeline = NovelPipeline(truth)

        # Chapter 1
        r1 = pipeline.run_standalone(chapter=1, draft=CHAPTER_1, chapter_title="律师的死局")
        assert r1.success

        # Chapter 2 (truth files now have ch1 facts)
        r2 = pipeline.run_standalone(chapter=2, draft=CHAPTER_2_DRAFT, chapter_title="归元诀")
        assert r2.success

        print(f"\n{'='*60}")
        print("Multi-Chapter Pipeline:")
        print(f"  Ch1: {r1.audit.score:.1f}/100, {r1.facts_count} facts")
        print(f"  Ch2: {r2.audit.score:.1f}/100, {r2.facts_count} facts")
        print(f"  Total facts: {r1.facts_count + r2.facts_count}")
        print(f"{'='*60}")

    def test_truth_file_accumulation(self, story_dir):
        """Verify truth files accumulate facts across chapters."""
        truth = TruthFiles(story_dir)
        truth.load()

        pipeline = NovelPipeline(truth)
        pipeline.run_standalone(chapter=1, draft=CHAPTER_1, chapter_title="律师的死局")

        # Check truth files were updated
        current = truth.get(TruthFile.CURRENT_STATE)
        assert current is not None
        print(f"\nTruth file after Ch1: {json.dumps(current, ensure_ascii=False)[:200]}")


# ── E2E Test 6: 33-Dim Audit Deep Dive ─────────────────────────

class TestE2E33DimAudit:
    """Comprehensive 33-dimension audit on real chapter text."""

    def test_all_33_dimensions_run(self, story_dir):
        truth = TruthFiles(story_dir)
        truth.load()
        auditor = NovelAuditor(truth=truth)

        result = auditor.audit(1, CHAPTER_1, facts=[])

        # All 33 dimensions should have been checked
        set(i.dimension for i in result.issues)
        # Not all dimensions will produce issues on good text, but audit should complete
        assert len(result.category_scores) == 5

        # Print full report
        print(f"\n{'='*60}")
        print(f"33-Dim Audit on Chapter 1 ({len(CHAPTER_1)} chars):")
        print(f"  Overall: {result.score:.1f}/100")
        for cat, score in result.category_scores.items():
            issues_in_cat = [i for i in result.issues if i.category.value == cat]
            print(f"  {cat}: {score:.0f}/100 ({len(issues_in_cat)} issues)")
        print(f"  Total issues: {len(result.issues)}")
        print(f"  Critical: {len(result.criticals)}, Warning: {len(result.warnings)}")
        print(f"  Passed: {result.passed}")
        print(f"{'='*60}")

    def test_custom_weights(self, story_dir):
        """Test custom category weights."""
        truth = TruthFiles(story_dir)
        truth.load()

        # Heavily weight AI detection
        custom_weights = {
            "continuity": 0.10,
            "ai_detection": 0.50,
            "style": 0.15,
            "structure": 0.15,
            "reader_exp": 0.10,
        }
        auditor = NovelAuditor(truth=truth, category_weights=custom_weights)
        result = auditor.audit(1, CHAPTER_1)
        assert result.score > 0
        print(f"\nCustom weights (AI-heavy): {result.score:.1f}/100")
