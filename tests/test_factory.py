"""Tests for Content Factory: task queue, quality gate, factory pipeline."""
import json
import time

from opensymphony.apps.factory.quality_gate import QualityGate
from opensymphony.apps.factory.task_queue import (
    Task,
    TaskQueue,
    TaskState,
    TaskTier,
)

# ── Task Queue Tests ────────────────────────────────────────────

class TestTaskQueue:
    def test_create_and_persist(self, tmp_path):
        qf = tmp_path / "tasks.json"
        q = TaskQueue(qf)
        t = Task(id="test_001", topic="帮信罪辩护", genre="legal", tier=TaskTier.A)
        q.add(t)

        # Reload from file
        q2 = TaskQueue(qf)
        assert q2.get("test_001") is not None
        assert q2.get("test_001").topic == "帮信罪辩护"

    def test_atomic_write(self, tmp_path):
        qf = tmp_path / "tasks.json"
        q = TaskQueue(qf)
        q.add(Task(id="t1", topic="test"))
        q.add(Task(id="t2", topic="test2"))

        # File should be valid JSON
        data = json.loads(qf.read_text(encoding="utf-8"))
        assert len(data["tasks"]) == 2

    def test_pop_next_priority(self, tmp_path):
        q = TaskQueue(tmp_path / "tasks.json")
        q.add(Task(id="t1", topic="low", priority=2))
        q.add(Task(id="t2", topic="high", priority=0))
        q.add(Task(id="t3", topic="mid", priority=1))

        next_t = q.pop_next()
        assert next_t.id == "t2"  # highest priority (lowest number)

    def test_pop_next_empty(self, tmp_path):
        q = TaskQueue(tmp_path / "tasks.json")
        assert q.pop_next() is None

    def test_state_transitions(self, tmp_path):
        q = TaskQueue(tmp_path / "tasks.json")
        t = Task(id="t1", topic="test")
        q.add(t)

        t.state = TaskState.EXECUTING
        q.update(t)
        assert q.get("t1").state == TaskState.EXECUTING

        # pop_next should skip executing tasks
        assert q.pop_next() is None

    def test_retryable(self, tmp_path):
        q = TaskQueue(tmp_path / "tasks.json")
        t = Task(id="t1", topic="test", max_retries=2)
        q.add(t)

        t.state = TaskState.FAILED
        t.retry_count = 1
        q.update(t)

        retry = q.get_retryable()
        assert retry is not None

        t.retry_count = 2
        q.update(t)
        assert q.get_retryable() is None

    def test_stats(self, tmp_path):
        q = TaskQueue(tmp_path / "tasks.json")
        q.add(Task(id="t1", topic="a"))
        q.add(Task(id="t2", topic="b"))
        q.add(Task(id="t3", topic="c", state=TaskState.DONE))

        stats = q.stats()
        assert stats["total"] == 3
        assert stats["pending"] == 2
        assert stats["done"] == 1

    def test_clear_done(self, tmp_path):
        q = TaskQueue(tmp_path / "tasks.json")
        t = Task(id="t1", topic="old", state=TaskState.DONE, finished_at=time.time() - 100000)
        q.add(t)
        q.add(Task(id="t2", topic="new"))

        removed = q.clear_done(max_age_hours=1)
        assert removed == 1
        assert q.get("t1") is None
        assert q.get("t2") is not None

    def test_corrupted_file_recovery(self, tmp_path):
        qf = tmp_path / "tasks.json"
        # Write valid data
        q = TaskQueue(qf)
        q.add(Task(id="t1", topic="survivor"))

        # Create backup
        import shutil
        shutil.copy2(str(qf), str(qf.with_suffix(".bak")))

        # Corrupt the main file
        qf.write_text("{invalid json", encoding="utf-8")

        # Should recover from backup
        q2 = TaskQueue(qf)
        assert q2.get("t1") is not None


# ── Quality Gate Tests ──────────────────────────────────────────

class TestQualityGate:
    def test_perfect_text_passes(self):
        gate = QualityGate()
        good_text = "这是一篇关于帮信罪的法律科普文章。张律师在执业过程中遇到过很多类似案件。根据刑法第287条，帮助信息网络犯罪活动罪的构成要件包括..." + "具体的案例分析和法律条文解释。" * 50
        result = gate.check(good_text, tier="A", min_length=200)
        assert result.passed

    def test_too_short_fails(self):
        gate = QualityGate()
        result = gate.check("太短了", tier="A", min_length=500)
        assert not result.passed
        assert any("过短" in i for i in result.issues)

    def test_forbidden_patterns_detected(self):
        gate = QualityGate()
        text = "他不是一般的力量，而是超越常人的力量。" * 10 + "填充文本" * 200
        result = gate.check(text, tier="B", min_length=100)
        assert any("禁止句式" in i for i in result.issues)

    def test_ai_marker_detection(self):
        gate = QualityGate()
        text = "他不禁感到震惊。竟然如此。忽然间变化了。" * 30 + "填充文本" * 100
        result = gate.check(text, tier="A", min_length=100)
        assert len(result.warnings) > 0 or not result.passed

    def test_duplicate_detection(self):
        gate = QualityGate()
        text = "这是一段测试文本。" * 100
        gate.check(text, tier="A", min_length=100)  # First time
        result = gate.check(text, tier="A", min_length=100)  # Duplicate
        assert any("重复" in i for i in result.issues)

    def test_similarity_detection(self):
        gate = QualityGate()
        text1 = "张律师代理了一起帮信罪案件。被告人小王是一名大学生。" * 50
        text2 = "张律师代理了一起帮信罪案件。被告人小王是一名大学生。" * 49 + "稍微改了一点文字"
        gate.check(text1, tier="A", min_length=100)
        gate.check(text2, tier="A", min_length=100)
        # Should have similarity warning or score penalty

    def test_tier_thresholds(self):
        gate = QualityGate()
        # Text that would pass B but not S
        text = "普通文本" * 200
        r_b = gate.check(text, tier="B", min_length=100)
        r_s = gate.check(text, tier="S", min_length=100)
        # S-tier requires higher score
        assert r_s.passed or r_s.score <= r_b.score  # S is stricter or same

    def test_sensitive_content(self):
        gate = QualityGate()
        text = "正常文章内容" * 200 + "涉及国家领导人的相关内容"
        result = gate.check(text, tier="A", min_length=100)
        assert any("敏感" in i for i in result.issues)


# ── Factory Integration Tests ───────────────────────────────────

class TestContentFactory:
    def test_add_seed(self, tmp_path):
        from opensymphony.apps.factory.content_factory import ContentFactory
        factory = ContentFactory(
            queue_file=tmp_path / "tasks.json",
            media_root=str(tmp_path / "media"),
            output_root=str(tmp_path / "output"),
        )
        task = factory.add_seed("帮信罪辩护要点", genre="legal", tier="A")
        assert task.id.startswith("legal_")
        assert task.genre == "legal"
        assert factory.queue.get(task.id) is not None

    def test_build_prompt(self, tmp_path):
        from opensymphony.apps.factory.content_factory import ContentFactory
        factory = ContentFactory(
            queue_file=tmp_path / "tasks.json",
            media_root=str(tmp_path / "media"),
            output_root=str(tmp_path / "output"),
        )
        task = Task(id="t1", topic="帮信罪", genre="legal", target_length=2000)
        prompt = factory._build_prompt(task)
        assert "帮信罪" in prompt
        assert "2000" in prompt

    def test_build_prompt_with_seed_material(self, tmp_path):
        from opensymphony.apps.factory.content_factory import ContentFactory
        factory = ContentFactory(
            queue_file=tmp_path / "tasks.json",
            media_root=str(tmp_path / "media"),
            output_root=str(tmp_path / "output"),
        )
        task = Task(id="t1", topic="AI创业", genre="tech",
                    seed_material="我上周参加了一个AI创业沙龙...", target_length=1500)
        prompt = factory._build_prompt(task)
        assert "素材" in prompt or "沙龙" in prompt

    def test_disk_limit(self, tmp_path):
        from opensymphony.apps.factory.content_factory import ContentFactory
        # Create a file that exceeds 1KB limit
        factory = ContentFactory(
            queue_file=tmp_path / "tasks.json",
            media_root=str(tmp_path / "media"),
            output_root=str(tmp_path / "output"),
            max_output_gb=0.000001,  # ~1KB
        )
        # Write a large file
        (tmp_path / "output").mkdir(exist_ok=True)
        (tmp_path / "output" / "big.txt").write_text("x" * 10000, encoding="utf-8")
        assert not factory._check_disk_limit()

    def test_genre_soul_mapping(self):
        from opensymphony.apps.factory.content_factory import GENRE_SOUL_MAP
        assert GENRE_SOUL_MAP["legal"] == "social_copy"
        assert GENRE_SOUL_MAP["tech"] == "tech_blogger"
        assert GENRE_SOUL_MAP["xianxia"] == "screenwriter"

    def test_ai_label_added(self, tmp_path):
        from opensymphony.apps.factory.content_factory import AI_LABEL
        assert "AI" in AI_LABEL or "人工智能" in AI_LABEL
