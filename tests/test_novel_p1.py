"""Tests for Reflector, NovelPipeline, and AntiAI."""
import json

import pytest
from opensymphony.apps.novel.anti_ai import AntiAI
from opensymphony.apps.novel.observer import Observer
from opensymphony.apps.novel.reflector import Reflector
from opensymphony.apps.novel.templates import NovelPipeline
from opensymphony.apps.novel.truth_files import TruthFile, TruthFiles


@pytest.fixture
def story_dir(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "current_state.json").write_text(json.dumps({"chapter": 1, "location": "密林"}))
    (state_dir / "pending_hooks.json").write_text(json.dumps({
        "hooks": [
            {"hook_id": "h001", "content": "穿越原因", "status": "open", "start_chapter": 1},
        ]
    }))
    for tf in ["character_matrix", "particle_ledger", "chapter_summaries",
               "subplot_board", "emotional_arcs"]:
        (state_dir / f"{tf}.json").write_text("{}")
    return tmp_path


@pytest.fixture
def truth(story_dir):
    tf = TruthFiles(story_dir)
    tf.load()
    return tf


SAMPLE_CHAPTER = """张律拖着伤腿走进散修集市。三个天剑宗弟子正在勒索老头。张律站出来讲道理，引用了"拳头论"让弟子哑口无言。他发现这个世界没有法律保护弱者，决定要改变这一切。"""


# ── Reflector Tests ───────────────────────────────────────────────

class TestReflector:
    def test_reflect_basic(self, truth):
        obs = Observer()
        observation = obs.observe(2, SAMPLE_CHAPTER)
        reflector = Reflector(truth)
        result = reflector.reflect(observation)

        assert result.chapter == 2
        assert result.applied
        assert result.delta is not None

    def test_reflect_updates_state(self, truth):
        obs = Observer()
        observation = obs.observe(2, "张律的左肩伤口还在流血。他捡起了灵石。")
        reflector = Reflector(truth)
        reflector.reflect(observation)

        # Check current_state was updated (chapter field)
        state = truth.get(TruthFile.CURRENT_STATE)
        assert state.get("chapter") == 2

    def test_reflect_new_hooks(self, truth):
        # Create a fact that looks like a hook
        obs = Observer()
        observation = obs.observe(2, SAMPLE_CHAPTER)
        # Manually add a new hook observation
        observation.new_hooks.append({
            "hook_id": "h002", "type": "mystery",
            "content": "天剑宗的税务授权", "start_chapter": 2, "status": "open",
        })
        reflector = Reflector(truth)
        reflector.reflect(observation)

        hooks = truth.get(TruthFile.PENDING_HOOKS).get("hooks", [])
        hook_ids = [h["hook_id"] for h in hooks]
        assert "h002" in hook_ids

    def test_reflect_resolved_hooks(self, truth):
        obs = Observer()
        observation = obs.observe(2, SAMPLE_CHAPTER)
        observation.resolved_hooks = ["h001"]
        reflector = Reflector(truth)
        reflector.reflect(observation)

        hooks = truth.get(TruthFile.PENDING_HOOKS).get("hooks", [])
        h001 = next(h for h in hooks if h["hook_id"] == "h001")
        assert h001["status"] == "resolved"
        assert h001["resolved_chapter"] == 2

    def test_reflect_with_summary(self, truth):
        obs = Observer()
        observation = obs.observe(2, SAMPLE_CHAPTER)
        reflector = Reflector(truth)
        result = reflector.reflect_and_summarize(
            observation, chapter_title="散修集市", chapter_text=SAMPLE_CHAPTER
        )
        assert result.applied
        summaries = truth.get(TruthFile.CHAPTER_SUMMARIES)
        rows = summaries.get("rows", [])
        assert len(rows) >= 1
        assert rows[-1]["chapter"] == 2

    def test_validation_rejects_bad_status(self, truth):
        reflector = Reflector(truth)
        errors = reflector._validate_delta({
            "pending_hooks": {"hooks": [
                {"hook_id": "h003", "status": "invalid_status", "start_chapter": 3}
            ]}
        })
        assert len(errors) >= 1
        assert "invalid_status" in errors[0]

    def test_auto_fix_bad_status(self, truth):
        reflector = Reflector(truth)
        delta = {
            "pending_hooks": {"hooks": [
                {"hook_id": "h003", "status": "invalid_status", "start_chapter": 3}
            ]}
        }
        fixed = reflector._auto_fix(delta, reflector._validate_delta(delta))
        assert fixed["pending_hooks"]["hooks"][0]["status"] == "open"

    def test_snapshot_before_reflect(self, truth):
        obs = Observer()
        observation = obs.observe(2, SAMPLE_CHAPTER)
        reflector = Reflector(truth)
        truth.snapshot(2)

        reflector.reflect(observation)
        assert 2 in truth.list_snapshots()


# ── NovelPipeline Tests ───────────────────────────────────────────

class TestNovelPipeline:
    def test_standalone_pipeline(self, truth):
        truth.snapshot(1)
        pipe = NovelPipeline(truth)
        result = pipe.run_standalone(chapter=2, draft=SAMPLE_CHAPTER)

        assert result.chapter == 2
        assert result.facts_count >= 1
        assert result.audit is not None
        assert result.audit.score > 0

    def test_standalone_saves_truth(self, truth, story_dir):
        pipe = NovelPipeline(truth)
        pipe.run_standalone(chapter=2, draft=SAMPLE_CHAPTER)

        # Reload and verify persistence
        tf2 = TruthFiles(story_dir)
        tf2.load()
        assert tf2.get_field(TruthFile.CURRENT_STATE, "chapter") == 2

    def test_get_template(self):
        template = NovelPipeline.get_template()
        assert len(template) == 7
        assert template[0]["id"] == "plan"
        assert template[-1]["id"] == "revise"
        assert template[-1].get("condition") == "audit_score < 85"

    def test_pipeline_result_fields(self, truth):
        pipe = NovelPipeline(truth)
        result = pipe.run_standalone(chapter=2, draft=SAMPLE_CHAPTER)

        assert result.draft == SAMPLE_CHAPTER
        assert result.final == SAMPLE_CHAPTER
        assert result.revised is False


# ── AntiAI Tests ──────────────────────────────────────────────────

class TestAntiAI:
    def test_detect_forbidden_patterns(self):
        anti = AntiAI()
        result = anti.process("这不是普通的力量，而是来自灵魂的觉醒。")
        assert len(result.patterns_found) >= 1

    def test_detect_cliches(self):
        anti = AntiAI()
        result = anti.process("他如临大敌，难以置信地瞪大了眼睛。")
        assert len(result.cliches_found) >= 1

    def test_aggressive_replacement(self):
        anti = AntiAI(aggressive=True)
        result = anti.process("他不禁止不住倒吸一口凉气。竟然出现了！")
        assert result.replacements_made >= 1
        assert "倒吸一口凉气" not in result.cleaned
        assert "不禁" not in result.cleaned

    def test_no_replacement_in_non_aggressive(self):
        anti = AntiAI(aggressive=False)
        result = anti.process("他不禁笑了起来。")
        assert result.replacements_made == 0
        assert "不禁" in result.cleaned

    def test_get_suggestions(self):
        anti = AntiAI()
        text = "这不是梦，而是现实。他不禁倒吸一口凉气，如临大敌。竟然出现了。"
        suggestions = anti.get_suggestions(text)
        assert len(suggestions) >= 2  # At least pattern + cliche or word freq

    def test_merge_short_paragraphs(self):
        anti = AntiAI()
        text = "短的。\n\n也很短。\n\n这是一段正常长度的文字，有足够的描述和内容来撑起一个完整的段落。"
        merged = anti.merge_short_paragraphs(text, min_length=35)
        # Short paragraphs should be merged
        assert len(merged) <= len(text) + 5  # Allow for comma joiners

    def test_preserve_headers(self):
        anti = AntiAI()
        text = "# 第1章 标题\n\n短段。\n\n另一短段。"
        merged = anti.merge_short_paragraphs(text)
        assert "# 第1章 标题" in merged

    def test_clean_text_no_issues(self):
        anti = AntiAI()
        result = anti.process("他走过去拿起了杯子。")
        assert result.replacements_made == 0
        assert len(result.patterns_found) == 0
