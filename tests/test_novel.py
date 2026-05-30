"""Tests for novel writing application — TruthFiles, Observer, Auditor."""
import json

import pytest
from opensymphony.apps.novel.auditor import NovelAuditor
from opensymphony.apps.novel.observer import FactCategory, Observer
from opensymphony.apps.novel.truth_files import TruthFile, TruthFiles

# ── Fixtures ─────────────────────────────────────────────────────

@pytest.fixture
def story_dir(tmp_path):
    """Create a minimal story directory with truth files."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    # current_state.json
    (state_dir / "current_state.json").write_text(json.dumps({
        "chapter": 0,
        "location": "青云城",
        "protagonist": {
            "name": "张律",
            "status": "健康",
            "position": "律所",
        },
    }, ensure_ascii=False))

    # pending_hooks.json
    (state_dir / "pending_hooks.json").write_text(json.dumps({
        "hooks": [
            {"hook_id": "h001", "content": "张正失踪", "status": "open", "start_chapter": 1},
            {"hook_id": "h002", "content": "符文印记", "status": "progressing", "start_chapter": 1},
        ]
    }, ensure_ascii=False))

    # character_matrix.json
    (state_dir / "character_matrix.json").write_text(json.dumps({
        "characters": [
            {"name": "张律", "role": "主角", "cultivation": "练气三层"},
            {"name": "苏瑶", "role": "助手", "cultivation": "练气六层"},
        ]
    }, ensure_ascii=False))

    # Empty files for the rest
    for tf in ["particle_ledger", "chapter_summaries", "subplot_board", "emotional_arcs"]:
        (state_dir / f"{tf}.json").write_text("{}")

    return tmp_path


@pytest.fixture
def truth(story_dir):
    """Create and load TruthFiles."""
    tf = TruthFiles(story_dir)
    tf.load()
    return tf


SAMPLE_CHAPTER = """# 第2章 散修集市

张律拖着伤腿走进散修集市。

集市不大，一条土路两旁摆着几十个摊位，卖丹药的、卖灵材的、卖二手法器的，吆喝声此起彼伏。空气里弥漫着廉价灵香的气味，混着泥土和汗味。

他穿过人群，注意到所有人都在刻意避开一个方向。顺着目光看去，三个穿着天剑宗内门弟子服的年轻修士正围着一个老头的摊位。

"说多少遍了，这个摊位的灵石税已经涨到每月三十颗了。"为首的弟子翘着二郎腿坐在摊位上，随手拿起一瓶丹药闻了闻，"你这破丹药能卖几个钱？交不起税就滚蛋。"

老头佝偻着身子，两只手攥着衣角，嘴唇哆嗦："大人，上个月还是二十颗……"

"涨价了，听不懂人话？"

周围的人低着头，没有人说话。有人拉着同伴快步走开，有人装作在看别的摊位。

张律站在人群里，看着这一幕。

上辈子他在法庭上见过太多类似的场景——强势一方用规则的名义欺压弱者。区别只在于，上辈子他还能引用法条、申请证据保全、要求法官回避。而在这里，他连一个可以投诉的地方都没有。

他深吸一口气，走向摊位。

"这位道友，请问天剑宗收取灵石税的依据是什么？"

三个弟子同时转头看向他。为首的那个上下打量了张律一眼，目光在他灰扑扑的麻衣和明显的伤口上停留了一秒，嘴角露出一丝不屑。

"又一个找死的散修。"

张律没理会他的语气，平静地说："我只是想了解一下，这笔税的法理基础在哪里。如果有明文规定，我会劝这位老丈按时缴纳。"

"法理？"那人像是听到了什么笑话，"修仙界，拳头就是法理。你一个练气三层的废物，跟我谈法理？"

张律注意到他说的"练气三层"——他确实能感知到对方的修为远高于自己，大概筑基期。

"那如果天剑宗的规矩是'拳头就是法理'，"张律的语气依然平稳，"请问现在是谁的拳头更大？天剑宗在青云城有数千弟子，但散修加起来有上万人。如果规矩真的是拳头，那上万人对几千人，谁应该交税给谁？"

集市突然安静了下来。

为首的弟子脸色变了。他没想到一个练气三层的散修会说出这种话。更让他不舒服的是——周围的人群里，有人开始点头。

"你——"

"我建议你们回去查一下，天剑宗到底有没有向散修收税的正式授权。"张律说，"如果有的话，我明天亲自上门道歉。如果没有的话……"

他没有把话说完，但意思已经很明显了。

三个弟子对视一眼，为首的那个站起身，拍了拍衣服上的灰，冷冷地说："你等着。"

然后转身走了。

周围的人依然安静了几秒，然后爆发出一阵低声议论。那个老头颤颤巍巍地走过来，拉住张律的袖子，嘴唇哆嗦着说不出话。

苏瑶从人群里挤出来，手里还攥着一串糖葫芦，杏眼瞪得圆圆的："你……你刚才说的话，好厉害。"

张律看了她一眼，忽然笑了。

"没什么厉害的，"他说，"只是讲道理而已。"

但他的心脏跳得很快。不是因为恐惧，是因为他发现——在这个没有法律的世界，讲道理本身就是一种武器。

而他恰好是这方面的专家。
"""


# ── TruthFiles Tests ─────────────────────────────────────────────

class TestTruthFiles:
    def test_load(self, truth):
        state = truth.get(TruthFile.CURRENT_STATE)
        assert "protagonist" in state
        assert state["protagonist"]["name"] == "张律"

    def test_get_field(self, truth):
        loc = truth.get_field(TruthFile.CURRENT_STATE, "location")
        assert loc == "青云城"

    def test_get_missing_field(self, truth):
        val = truth.get_field(TruthFile.CURRENT_STATE, "nonexistent", "default")
        assert val == "default"

    def test_apply_delta(self, truth):
        delta = truth.apply_delta(2, {
            "current_state": {"location": "散修集市", "protagonist": {"status": "受伤恢复中"}},
        })
        assert delta.chapter == 2
        assert truth.get_field(TruthFile.CURRENT_STATE, "location") == "散修集市"
        assert truth.get_field(TruthFile.CURRENT_STATE, "protagonist")["status"] == "受伤恢复中"

    def test_apply_delta_invalid_file(self, truth):
        with pytest.raises(ValueError, match="Invalid truth file"):
            truth.apply_delta(2, {"nonexistent_file": {"key": "val"}})

    def test_apply_delta_non_dict(self, truth):
        with pytest.raises(ValueError, match="must be a dict"):
            truth.apply_delta(2, {"current_state": "not a dict"})

    def test_snapshot_and_rollback(self, truth):
        # Snapshot at chapter 1
        truth.snapshot(1)
        assert 1 in truth.list_snapshots()

        # Make changes
        truth.apply_delta(2, {"current_state": {"location": "新地点"}})
        assert truth.get_field(TruthFile.CURRENT_STATE, "location") == "新地点"

        # Rollback to chapter 1
        truth.rollback(1)
        assert truth.get_field(TruthFile.CURRENT_STATE, "location") == "青云城"

    def test_rollback_nonexistent_snapshot(self, truth):
        with pytest.raises(ValueError, match="No snapshot"):
            truth.rollback(999)

    def test_save_and_reload(self, truth, story_dir):
        truth.apply_delta(3, {"current_state": {"location": "天道城"}})
        truth.save()

        # Reload from disk
        tf2 = TruthFiles(story_dir)
        tf2.load()
        assert tf2.get_field(TruthFile.CURRENT_STATE, "location") == "天道城"

    def test_context_for_chapter(self, truth):
        truth.apply_delta(1, {"current_state": {"chapter": 1}})
        ctx = truth.context_for_chapter(2)
        assert "张律" in ctx
        assert "当前世界状态" in ctx

    def test_context_truncation(self, truth):
        ctx = truth.context_for_chapter(2, max_chars=50)
        assert len(ctx) <= 80  # 50 + truncation message

    def test_pending_hooks_context(self, truth):
        ctx = truth.context_for_chapter(1)
        assert "活跃伏笔" in ctx
        assert "张正失踪" in ctx


# ── Observer Tests ────────────────────────────────────────────────

class TestObserver:
    def test_rule_extraction_movements(self):
        obs = Observer()
        result = obs.observe(1, "张律拖着伤腿走进散修集市。苏瑶从人群里挤出来。")
        facts = result.facts
        # Regex may catch "走进散修集市" or "挤出来" — at minimum we should get some facts
        assert len(facts) >= 1
        # Check that at least some facts were extracted (any category)
        categories = {f.category for f in facts}
        assert len(categories) >= 1

    def test_rule_extraction_injuries(self):
        obs = Observer()
        result = obs.observe(1, "左肩撕裂的伤口还在渗血，膝盖磕在石头上")
        physical = [f for f in result.facts if f.category == FactCategory.PHYSICAL]
        assert len(physical) >= 1

    def test_rule_extraction_resources(self):
        obs = Observer()
        result = obs.observe(1, "张律捡起地上散落的灵石，数了数有三颗")
        resources = [f for f in result.facts if f.category == FactCategory.RESOURCE]
        assert len(resources) >= 1

    def test_observe_full_chapter(self):
        obs = Observer()
        result = obs.observe(2, SAMPLE_CHAPTER)
        assert len(result.facts) >= 3
        categories = {f.category for f in result.facts}
        assert FactCategory.CHARACTER in categories

    def test_state_changes_generated(self):
        obs = Observer()
        result = obs.observe(2, SAMPLE_CHAPTER)
        assert len(result.state_changes) >= 1

    def test_llm_fallback_to_rules(self):
        """If LLM client fails, fall back to rules."""
        def bad_llm(prompt, max_tokens=1000):
            raise RuntimeError("LLM unavailable")

        obs = Observer(llm_client=bad_llm)
        # Use text with injury/resource keywords that rules can detect
        result = obs.observe(1, "张律的左肩伤口还在流血。他捡起地上的灵石。")
        assert len(result.facts) >= 1  # Should detect injury or resource

    def test_parse_facts_json(self):
        obs = Observer()
        json_output = '[{"category":"character","subject":"张律","predicate":"位置","object":"散修集市","source":"原文","confidence":0.9}]'
        facts = obs._parse_facts(json_output, 2)
        assert len(facts) == 1
        assert facts[0].subject == "张律"
        assert facts[0].chapter == 2

    def test_parse_facts_invalid_json(self):
        obs = Observer()
        facts = obs._parse_facts("not json at all", 1)
        assert len(facts) == 0

    def test_deduplication(self):
        obs = Observer()
        text = "张律往东走。张律往东走。"
        result = obs.observe(1, text)
        # Should deduplicate identical facts
        keys = [(f.category, f.subject, f.predicate, f.object_) for f in result.facts]
        assert len(keys) == len(set(keys))


# ── Auditor Tests ─────────────────────────────────────────────────

class TestNovelAuditor:
    def test_perfect_chapter(self):
        auditor = NovelAuditor()
        result = auditor.audit(2, SAMPLE_CHAPTER)
        assert result.score > 0
        assert result.chapter == 2

    def test_forbidden_pattern_detected(self):
        auditor = NovelAuditor()
        text = "这不是普通的力量，而是来自灵魂深处的觉醒。这种力量不仅强大无比，而且持久永恒。"
        result = auditor.audit(1, text)
        criticals = result.criticals
        assert len(criticals) >= 1
        assert any("不是" in c.description or "不仅" in c.description for c in criticals)

    def test_ai_tell_detected(self):
        auditor = NovelAuditor()
        text = "他不禁止不住倒吸一口凉气。竟然出现了！他仿佛看见了什么不可思议的东西。宛如梦幻。猛地回头，忽然发现居然有危险。不禁心中一惊。"
        result = auditor.audit(1, text)
        ai_issues = [i for i in result.issues if i.dimension == "ai_tell"]
        assert len(ai_issues) >= 1  # At least cliché or high-freq word detected

    def test_paragraph_shape(self):
        auditor = NovelAuditor()
        # Many short paragraphs
        text = "\n".join(["短的。" for _ in range(20)])
        result = auditor.audit(1, text)
        shape_issues = [i for i in result.issues if i.dimension == "paragraph_shape"]
        assert len(shape_issues) >= 1

    def test_sudden_healing(self):
        auditor = NovelAuditor()
        text = "他的手臂骨折了。三天后，伤口痊愈，恢复如初。"
        result = auditor.audit(1, text)
        state_issues = [i for i in result.issues if i.dimension == "character_state"]
        assert len(state_issues) >= 1

    def test_chapter_structure_short_opening(self):
        auditor = NovelAuditor()
        text = "短。\n\n这是一段正常的正文内容，足够长，有足够的描述和对话来支撑一个段落。"
        result = auditor.audit(1, text)
        struct_issues = [i for i in result.issues if i.dimension == "chapter_structure"]
        assert len(struct_issues) >= 1

    def test_passing_score(self):
        auditor = NovelAuditor()
        result = auditor.audit(2, SAMPLE_CHAPTER)
        # SAMPLE_CHAPTER should score reasonably well
        assert result.score >= 70

    def test_result_summary(self):
        auditor = NovelAuditor()
        result = auditor.audit(2, SAMPLE_CHAPTER)
        summary = result.summary()
        assert "Chapter 2" in summary

    def test_hook_consistency_with_truth(self, truth):
        # Set current chapter high to trigger dormant hook warning
        truth.apply_delta(15, {"current_state": {"chapter": 15}})
        auditor = NovelAuditor(truth=truth)
        result = auditor.audit(15, "一些普通的正文内容。")
        hook_issues = [i for i in result.issues if i.dimension == "hook_consistency"]
        assert len(hook_issues) >= 1
        assert "伏笔" in hook_issues[0].description

    def test_no_truth_no_hook_check(self):
        auditor = NovelAuditor(truth=None)
        result = auditor.audit(1, "正文内容")
        hook_issues = [i for i in result.issues if i.dimension == "hook_consistency"]
        assert len(hook_issues) == 0

    def test_word_fatigue(self):
        auditor = NovelAuditor()
        text = "他走了。他看了看他。他想了想。他知道他要做什么。他转身。他走了。他停下了。他回头。他笑了笑。他叹了口气。他继续走。他看到了什么。他不敢相信。他沉默了。他开口了。他闭上了眼睛。他深呼吸。他做出了决定。他知道机会来了。他不再犹豫。"
        result = auditor.audit(1, text)
        fatigue = [i for i in result.issues if i.dimension == "word_fatigue"]
        assert len(fatigue) >= 1

    def test_score_deduction(self):
        auditor = NovelAuditor()
        # Perfect text (no issues)
        good_result = auditor.audit(2, SAMPLE_CHAPTER)
        # Terrible text (many issues)
        bad_result = auditor.audit(1, "不是他而是别人。不禁竟然猛地。")
        assert good_result.score > bad_result.score
