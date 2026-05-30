"""Tests for ChapterPipeline — end-to-end chapter generation."""

from opensymphony.apps.novel.chapter_pipeline import ChapterPipeline
from opensymphony.apps.novel.truth_files import TruthFiles


class MockLLM:
    """Mock LLM that returns chapter-like content."""
    def __init__(self, responses=None):
        self.responses = responses or []
        self._idx = 0

    def __call__(self, prompt, max_tokens=4096, temperature=0.7):
        if self._idx < len(self.responses):
            r = self.responses[self._idx]
            self._idx += 1
            return r
        # Default: generate plausible content
        return self._default_response(prompt)

    def _default_response(self, prompt):
        if "场景" in prompt and "节拍" in prompt:
            return "1. 土地庙：张律在破庙里处理伤口\n2. 苏晚归来：采药少女发现了他\n3. 信息交换：苏晚讲述落云镇的故事\n4. 分析案子：张律用律师思维分析\n5. 符文发热：张律手腕上的符文再次亮起"
        return (
            "张律靠在土地庙的墙角，用撕下的衣袖缠住手臂上的伤口。血已经止住了一半，"
            "但每动一下都像被人用刀剜。\n\n"
            "「你是谁？」\n\n"
            "一个背竹篓的姑娘站在门口，手里攥着一把草药。她看上去不过十七八岁，"
            "一双眼睛警惕地盯着他。\n\n"
            "「路过的。被东西咬了。」张律说。\n\n"
            "姑娘犹豫了一下，从竹篓里拿出一把草药，走过来蹲在他面前。"
            "「这不是虫咬的，是剑伤。」她说着，把草药敷在伤口上，动作利落得不像是第一次。\n\n"
            "张律看着她处理伤口的手法，忽然想起了自己在律所实习时的法医课。"
            "眼前这个姑娘，动作沉稳，指节上有常年采药留下的薄茧。\n\n"
            "「你叫什么？」\n\n"
            "「苏晚。落云镇药铺的。」\n\n"
            "张律忽然注意到她手腕上有一道浅浅的淤痕，像是被人用力握过。"
            "他职业本能地多看了一眼，苏晚却把手缩了回去。"
        )


class TestChapterPipelineStandalone:
    """Test pipeline without LLM (truth update + anti-AI + audit on existing draft)."""

    def test_standalone_basic(self, tmp_path):
        """Standalone mode processes a pre-written draft."""
        story_dir = tmp_path / "story"
        story_dir.mkdir()
        tf = TruthFiles(story_dir)
        tf.load()

        pipe = ChapterPipeline(tf)
        draft = "张律往密林深处奔去。苏晚走到了青云城门口。他获得了一颗灵石。"

        result = pipe.run_standalone(chapter=1, draft=draft, intent="第1章测试")

        assert result.chapter == 1
        assert result.facts_extracted > 0
        assert len(result.final) > 0
        assert result.elapsed_s > 0

    def test_standalone_truth_updated(self, tmp_path):
        """Truth files are updated after standalone processing."""
        story_dir = tmp_path / "story"
        story_dir.mkdir()
        tf = TruthFiles(story_dir)
        tf.load()

        pipe = ChapterPipeline(tf)
        draft = "张律来到了青云城门口。"

        result = pipe.run_standalone(chapter=1, draft=draft)

        assert result.truth_updated is True

    def test_standalone_audit(self, tmp_path):
        """Audit score is populated."""
        story_dir = tmp_path / "story"
        story_dir.mkdir()
        tf = TruthFiles(story_dir)
        tf.load()

        pipe = ChapterPipeline(tf)
        # Short text should get a low score
        result = pipe.run_standalone(chapter=1, draft="短文本")

        assert result.audit_score >= 0  # Should run without error

    def test_standalone_no_llm_needed(self, tmp_path):
        """Standalone mode works without any LLM."""
        story_dir = tmp_path / "story"
        story_dir.mkdir()
        tf = TruthFiles(story_dir)
        tf.load()

        pipe = ChapterPipeline(tf)  # No llm_fn
        result = pipe.run_standalone(chapter=1, draft="张律往密林深处奔去。")

        assert len(result.errors) == 0


class TestChapterPipelineFull:
    """Test full pipeline with mock LLM."""

    def test_full_run_basic(self, tmp_path):
        """Full pipeline generates outline → scenes → draft → truth → audit."""
        story_dir = tmp_path / "story"
        story_dir.mkdir()
        tf = TruthFiles(story_dir)
        tf.load()

        llm = MockLLM()
        pipe = ChapterPipeline(tf, llm_fn=llm)

        result = pipe.run(chapter=1, intent="张律在土地庙遇到苏晚")

        assert result.chapter == 1
        assert len(result.outline) > 0
        assert len(result.draft) > 0
        assert len(result.final) > 0
        assert result.facts_extracted >= 0
        assert result.elapsed_s > 0

    def test_full_run_scenes_generated(self, tmp_path):
        """Multiple scenes are generated from outline beats."""
        story_dir = tmp_path / "story"
        story_dir.mkdir()
        tf = TruthFiles(story_dir)
        tf.load()

        llm = MockLLM()
        pipe = ChapterPipeline(tf, llm_fn=llm)

        result = pipe.run(chapter=1, intent="测试场景生成")

        assert len(result.scenes) > 0

    def test_full_run_no_llm_error(self, tmp_path):
        """Full run without LLM returns error gracefully."""
        story_dir = tmp_path / "story"
        story_dir.mkdir()
        tf = TruthFiles(story_dir)
        tf.load()

        pipe = ChapterPipeline(tf)  # No llm_fn
        result = pipe.run(chapter=1, intent="测试")

        assert len(result.errors) > 0
        assert "No LLM" in result.errors[0]

    def test_full_run_multi_chapter(self, tmp_path):
        """Multiple chapters build up truth state."""
        story_dir = tmp_path / "story"
        story_dir.mkdir()
        tf = TruthFiles(story_dir)
        tf.load()

        llm = MockLLM()
        pipe = ChapterPipeline(tf, llm_fn=llm)

        r1 = pipe.run(chapter=1, intent="张律来到青云城")
        r2 = pipe.run(chapter=2, intent="张律在土地庙遇到苏晚")

        assert r1.success or len(r1.final) > 0
        assert r2.facts_extracted >= 0
        assert tf.list_snapshots() == [1, 2]


class TestChapterPipelineEdge:
    """Edge cases and error handling."""

    def test_empty_draft(self, tmp_path):
        """Empty draft is handled gracefully in standalone."""
        story_dir = tmp_path / "story"
        story_dir.mkdir()
        tf = TruthFiles(story_dir)
        tf.load()

        pipe = ChapterPipeline(tf)
        result = pipe.run_standalone(chapter=1, draft="")

        # Should not crash
        assert result.chapter == 1

    def test_llm_returns_empty(self, tmp_path):
        """LLM returning empty string is handled."""
        story_dir = tmp_path / "story"
        story_dir.mkdir()
        tf = TruthFiles(story_dir)
        tf.load()

        def empty_llm(prompt, **kwargs):
            return ""

        pipe = ChapterPipeline(tf, llm_fn=empty_llm)
        result = pipe.run(chapter=1, intent="测试")

        # Should report error but not crash
        assert result.chapter == 1
        assert len(result.errors) > 0 or not result.success
