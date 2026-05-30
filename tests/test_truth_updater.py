"""Tests for TruthUpdater — Observer → TruthFiles bridge."""
import json

from opensymphony.apps.novel.observer import Observer
from opensymphony.apps.novel.truth_files import TruthFile, TruthFiles
from opensymphony.apps.novel.truth_updater import TruthUpdater

TF = TruthFile


class TestTruthUpdaterRules:
    """Test truth_updater with rule-based Observer (no LLM)."""

    def test_basic_update(self, tmp_path):
        """Update truth files from chapter text with rule-based extraction."""
        story_dir = tmp_path / "story"
        story_dir.mkdir()

        tf = TruthFiles(story_dir)
        tf.load()
        updater = TruthUpdater(tf)

        text = "张律往密林深处奔去，手里拿着一块灵石。苏晚走到了青云城门口。"
        result = updater.update(chapter=1, text=text)

        assert result.chapter == 1
        assert result.facts_extracted > 0
        assert result.snapshot_created is True
        assert len(result.errors) == 0

    def test_snapshot_before_update(self, tmp_path):
        """Snapshot is created before delta is applied."""
        story_dir = tmp_path / "story"
        story_dir.mkdir()
        tf = TruthFiles(story_dir)
        tf.load()

        # Pre-populate
        tf.apply_delta(0, {"current_state": {"location": "起始村"}})
        tf.save()

        updater = TruthUpdater(tf)
        text = "张律向青云城走去。"
        result = updater.update(chapter=1, text=text)

        assert result.snapshot_created is True
        assert 1 in tf.list_snapshots()

    def test_character_location_updated(self, tmp_path):
        """Character location changes are captured in current_state."""
        story_dir = tmp_path / "story"
        story_dir.mkdir()
        tf = TruthFiles(story_dir)
        tf.load()

        updater = TruthUpdater(tf)
        text = "张律往密林深处奔去。"
        updater.update(chapter=1, text=text)

        state = tf.get_field(TF.CURRENT_STATE, "张律_location")
        assert "密林深处" in state

    def test_resource_extraction(self, tmp_path):
        """Resource acquisition is captured in particle_ledger."""
        story_dir = tmp_path / "story"
        story_dir.mkdir()
        tf = TruthFiles(story_dir)
        tf.load()

        updater = TruthUpdater(tf)
        text = "张律获得了一颗灵石。"
        updater.update(chapter=1, text=text)

        ledger = tf.get(TF.PARTICLE_LEDGER)
        assert any("灵石" in str(v) for v in ledger.values())

    def test_chapter_summary_added(self, tmp_path):
        """A chapter summary entry is added to chapter_summaries."""
        story_dir = tmp_path / "story"
        story_dir.mkdir()
        tf = TruthFiles(story_dir)
        tf.load()

        updater = TruthUpdater(tf)
        text = "张律来到了城门口。"
        updater.update(chapter=1, text=text)

        summaries = tf.get_field(TF.CHAPTER_SUMMARIES, "rows", [])
        assert len(summaries) == 1
        assert summaries[0]["chapter"] == 1

    def test_no_duplicate_summary(self, tmp_path):
        """Running update twice for same chapter doesn't duplicate summary."""
        story_dir = tmp_path / "story"
        story_dir.mkdir()
        tf = TruthFiles(story_dir)
        tf.load()

        updater = TruthUpdater(tf)
        text = "张律来到了城门口。"
        updater.update(chapter=1, text=text)
        updater.update(chapter=1, text=text)

        summaries = tf.get_field(TF.CHAPTER_SUMMARIES, "rows", [])
        assert len(summaries) == 1

    def test_persistence(self, tmp_path):
        """Truth files are saved to disk after update."""
        story_dir = tmp_path / "story"
        story_dir.mkdir()
        tf = TruthFiles(story_dir)
        tf.load()

        updater = TruthUpdater(tf)
        text = "张律往密林深处奔去。"
        updater.update(chapter=1, text=text)

        # Reload from disk
        tf2 = TruthFiles(story_dir)
        tf2.load()
        state = tf2.get_field(TF.CURRENT_STATE, "张律_location")
        assert "密林深处" in state

    def test_multi_chapter(self, tmp_path):
        """Multiple chapters build up truth state."""
        story_dir = tmp_path / "story"
        story_dir.mkdir()
        tf = TruthFiles(story_dir)
        tf.load()

        updater = TruthUpdater(tf)

        updater.update(chapter=1, text="张律来到了青云城门口。")
        updater.update(chapter=2, text="张律冲向密林深处。苏晚走到了河边。")

        # Both characters should have locations
        state = tf.get(TF.CURRENT_STATE)
        assert "张律_location" in state
        assert "苏晚_location" in state

        # Snapshots for both chapters
        assert tf.list_snapshots() == [1, 2]

    def test_rollback_after_bad_update(self, tmp_path):
        """Can rollback to previous chapter snapshot."""
        story_dir = tmp_path / "story"
        story_dir.mkdir()
        tf = TruthFiles(story_dir)
        tf.load()

        updater = TruthUpdater(tf)

        updater.update(chapter=1, text="张律来到了青云城。")
        state_ch1 = dict(tf.get(TF.CURRENT_STATE))

        updater.update(chapter=2, text="张律冲向密林深处。")

        # Rollback to chapter 1
        tf.rollback(1)
        state_after = tf.get(TF.CURRENT_STATE)

        # Should match chapter 1 state
        assert state_after.get("张律_location") == state_ch1.get("张律_location")


class TestTruthUpdaterWithMockLLM:
    """Test with mock LLM observer."""

    def test_llm_observer_integration(self, tmp_path):
        """TruthUpdater works with LLM-based observer."""
        story_dir = tmp_path / "story"
        story_dir.mkdir()
        tf = TruthFiles(story_dir)
        tf.load()

        # Mock LLM that returns structured facts
        def mock_llm(prompt, max_tokens=1000):
            return json.dumps([
                {"category": "character", "subject": "张律", "predicate": "位置",
                 "object": "天衡宗大殿", "source": "张律走进天衡宗大殿", "confidence": 0.95},
                {"category": "relationship", "subject": "张律", "predicate": "结盟",
                 "object": "周德", "source": "两人相视一笑", "confidence": 0.8},
                {"category": "resource", "subject": "张律", "predicate": "获得",
                 "object": "一枚金丹", "source": "张律获得了金丹", "confidence": 0.9},
            ])

        observer = Observer(llm_client=mock_llm)
        updater = TruthUpdater(tf, observer)

        text = "张律走进天衡宗大殿，与周德相视一笑，随后获得了金丹。"
        result = updater.update(chapter=3, text=text)

        assert result.facts_extracted == 3
        assert result.snapshot_created is True

        # Check state
        state = tf.get(TF.CURRENT_STATE)
        assert state.get("张律_location") == "天衡宗大殿"

        # Check relationship
        cm = tf.get(TF.CHARACTER_MATRIX)
        assert any("周德" in str(v) for v in cm.values())

        # Check resource
        ledger = tf.get(TF.PARTICLE_LEDGER)
        assert any("金丹" in str(v) for v in ledger.values())

    def test_llm_failure_fallback(self, tmp_path):
        """If LLM fails, falls back to rule-based extraction."""
        story_dir = tmp_path / "story"
        story_dir.mkdir()
        tf = TruthFiles(story_dir)
        tf.load()

        def failing_llm(prompt, max_tokens=1000):
            raise RuntimeError("API down")

        observer = Observer(llm_client=failing_llm)
        updater = TruthUpdater(tf, observer)

        text = "张律往密林深处奔去。"
        result = updater.update(chapter=1, text=text)

        # Should still extract via rules
        assert result.facts_extracted >= 1
        assert len(result.errors) == 0
