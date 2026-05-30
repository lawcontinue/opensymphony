"""Tests for AbilityUpdater + RelationshipUpdater."""

from opensymphony.apps.novel.ability_updater import AbilityUpdater
from opensymphony.apps.novel.observer import Observer
from opensymphony.apps.novel.relationship_updater import RelationshipUpdater
from opensymphony.apps.novel.truth_files import TruthFile, TruthFiles


def _observe(chapter, text):
    return Observer().observe(chapter, text)


class TestAbilityUpdater:
    def test_detect_awakened_ability(self, tmp_path):
        tf = TruthFiles(tmp_path / "s"); tf.load()
        text = "张律发现手腕上的符文发光发热。"
        result = AbilityUpdater(tf).update(_observe(1, text), text=text)
        assert len(result.changes) >= 1
        assert any(c.change_type == "awakened" for c in result.changes)

    def test_detect_new_ability(self, tmp_path):
        tf = TruthFiles(tmp_path / "s"); tf.load()
        text = "张律领悟了一门新的功法。"
        result = AbilityUpdater(tf).update(_observe(2, text), text=text)
        assert len(result.changes) >= 1
        assert any(c.change_type == "new" for c in result.changes)

    def test_no_false_positives(self, tmp_path):
        tf = TruthFiles(tmp_path / "s"); tf.load()
        text = "张律走到了城门口。苏晚来到了河边。"
        result = AbilityUpdater(tf).update(_observe(1, text), text=text)
        assert all(c.change_type not in ("new", "level_up", "awakened") for c in result.changes)

    def test_persists_to_truth_files(self, tmp_path):
        tf = TruthFiles(tmp_path / "s"); tf.load()
        text = "张律的手腕印记突然亮起。"
        result = AbilityUpdater(tf).update(_observe(1, text), text=text)
        if result.updated:
            abilities = tf.get_field(TruthFile.CHARACTER_MATRIX, "abilities", {})
            assert len(abilities) > 0

    def test_detect_level_up(self, tmp_path):
        tf = TruthFiles(tmp_path / "s"); tf.load()
        text = "张律修为突破，从练气三层进练气四层。"
        result = AbilityUpdater(tf).update(_observe(2, text), text=text)
        assert any(c.change_type == "level_up" for c in result.changes)

    def test_no_false_positive_from_generic_text(self, tmp_path):
        tf = TruthFiles(tmp_path / "s"); tf.load()
        text = "他在刑事案件中见过的一百多份现场勘查报告描述的完全一样。"
        result = AbilityUpdater(tf).update(_observe(1, text), text=text)
        assert len(result.changes) == 0


class TestRelationshipUpdater:
    def test_detect_new_interaction(self, tmp_path):
        tf = TruthFiles(tmp_path / "s"); tf.load()
        text = "苏晚帮助张律包扎伤口。"
        result = RelationshipUpdater(tf).update(_observe(1, text), text=text)
        assert len(result.changes) >= 1

    def test_detect_positive_trust(self, tmp_path):
        tf = TruthFiles(tmp_path / "s"); tf.load()
        text = "苏晚帮助张律包扎了伤口。苏晚告诉张律关于灵根的信息。"
        result = RelationshipUpdater(tf).update(_observe(1, text), text=text)
        assert any(c.change_type == "trust_up" for c in result.changes)

    def test_detect_negative_trust(self, tmp_path):
        tf = TruthFiles(tmp_path / "s"); tf.load()
        text = "周德扣押了苏晚的父亲。"
        result = RelationshipUpdater(tf).update(_observe(1, text), text=text)
        change_types = [c.change_type for c in result.changes]
        assert any(t in ("trust_down", "conflict") for t in change_types)

    def test_deduplication(self, tmp_path):
        tf = TruthFiles(tmp_path / "s"); tf.load()
        text = "苏晚帮助张律包扎。苏晚帮助张律处理伤口。"
        result = RelationshipUpdater(tf).update(_observe(1, text), text=text)
        # Same pair + same change_type should be deduped
        assert len(result.changes) <= 3

    def test_persists_to_truth_files(self, tmp_path):
        tf = TruthFiles(tmp_path / "s"); tf.load()
        text = "苏晚帮助张律包扎了伤口。"
        result = RelationshipUpdater(tf).update(_observe(1, text), text=text)
        if result.updated:
            rels = tf.get_field(TruthFile.CHARACTER_MATRIX, "relationships", {})
            assert len(rels) > 0

    def test_blacklist_generic_names(self, tmp_path):
        """追杀者/对方 etc should not be treated as character names."""
        tf = TruthFiles(tmp_path / "s"); tf.load()
        text = "追杀者追杀张律。对方很厉害。"
        result = RelationshipUpdater(tf).update(_observe(1, text), text=text)
        names = [c.character_a for c in result.changes] + [c.character_b for c in result.changes]
        assert "追杀者" not in names
        assert "对方" not in names


class TestIntegratedUpdaters:
    def test_both_on_chapter(self, tmp_path):
        tf = TruthFiles(tmp_path / "s"); tf.load()
        text = "苏晚帮助张律包扎伤口。张律的手腕符文发光发热。苏晚告诉张律关于灵根的信息。"
        obs = _observe(1, text)
        ab = AbilityUpdater(tf).update(obs, text=text)
        rel = RelationshipUpdater(tf).update(obs, text=text)
        assert ab.changes or rel.changes
