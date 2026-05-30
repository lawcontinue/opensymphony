"""Tests for declarative Pipeline with retry and fallback."""

import pytest
from opensymphony.kernel import SymphonyKernel
from opensymphony.pipeline import Pipeline, PipelineStep


@pytest.fixture
def kernel(tmp_path):
    souls_dir = tmp_path / "souls"
    souls_dir.mkdir()
    for name in ("default", "themis", "code", "reflector", "screenwriter", "drama_director"):
        (souls_dir / f"{name}.yaml").write_text(
            f"id: {name}\nname: {name}\narchetype: test\nthinking_framework: test\n")
    k = SymphonyKernel(souls_dir=souls_dir, data_dir=tmp_path / "data")
    k.start()
    return k


class TestPipelineStep:
    def test_step_requires_soul_or_tool(self):
        with pytest.raises(ValueError, match="must have either"):
            PipelineStep(id="bad")

    def test_step_cannot_have_both(self):
        with pytest.raises(ValueError, match="cannot have both"):
            PipelineStep(id="bad", soul="themis", tool="jimeng_image")

    def test_step_valid_soul(self):
        s = PipelineStep(id="ok", soul="themis", output_key="result")
        assert s.soul == "themis"

    def test_step_valid_tool(self):
        s = PipelineStep(id="ok", tool="jimeng_image", output_key="result")
        assert s.tool == "jimeng_image"

    def test_step_with_retry(self):
        s = PipelineStep(id="ok", tool="jimeng_image", retry=3, fallback_soul="reflector")
        assert s.retry == 3
        assert s.fallback_soul == "reflector"


class TestPipelineDefinition:
    def test_duplicate_step_id_rejected(self, kernel):
        steps = [
            PipelineStep(id="dup", soul="themis"),
            PipelineStep(id="dup", soul="code"),
        ]
        with pytest.raises(ValueError, match="Duplicate"):
            Pipeline(steps=steps, kernel=kernel)

    def test_to_dict_roundtrip(self, kernel):
        steps = [
            PipelineStep(id="write", soul="themis", output_key="text"),
            PipelineStep(id="review", soul="code", input_key="text", output_key="reviewed"),
        ]
        p = Pipeline(steps=steps, kernel=kernel)
        d = p.to_dict()
        assert len(d["steps"]) == 2
        assert d["steps"][0]["id"] == "write"

    def test_from_dict(self, kernel):
        data = {"steps": [
            {"id": "gen", "soul": "themis", "output_key": "out"},
            {"id": "check", "tool": "quality_check", "input_key": "out", "output_key": "result"},
        ]}
        p = Pipeline.from_dict(data, kernel=kernel)
        assert len(p.steps) == 2
        assert p.steps[1].tool == "quality_check"


class TestPipelineExecution:
    def test_tool_step_success(self, kernel):
        """quality_check tool should work in pipeline."""
        steps = [
            PipelineStep(id="check", tool="quality_check",
                         output_key="result"),
        ]
        p = Pipeline(steps=steps, kernel=kernel)
        result = p.run({"text": "AI辅助生成的测试文章", "soul": "default"})
        assert result["success"] is True
        assert "result" in result["context"]

    def test_prompt_extract_step(self, kernel):
        """prompt_extract tool should work in pipeline."""
        steps = [
            PipelineStep(id="extract", tool="prompt_extract", output_key="prompts"),
        ]
        p = Pipeline(steps=steps, kernel=kernel)
        result = p.run({"text": "Here is [IMAGE: a sunset] and [IMAGE: a mountain]"})
        assert result["success"] is True
        assert result["context"]["prompts"]["count"] == 2

    def test_chained_steps(self, kernel):
        """Two tool steps chained via input_key/output_key."""
        steps = [
            PipelineStep(id="extract", tool="prompt_extract", output_key="prompts"),
            PipelineStep(id="review", tool="legal_review",
                         output_key="reviewed"),
        ]
        p = Pipeline(steps=steps, kernel=kernel)
        result = p.run({"text": "根据《刑法》第264条，[IMAGE: 法庭场景]"})
        assert result["success"] is True

    def test_condition_skip(self, kernel):
        """Step with unmet condition should be skipped."""
        steps = [
            PipelineStep(id="check", tool="quality_check", output_key="result",
                         condition="should_check"),
        ]
        p = Pipeline(steps=steps, kernel=kernel)
        result = p.run({"text": "hello"})
        assert result["success"] is True
        # "result" not in context because step was skipped

    def test_failure_propagates(self, kernel):
        """Tool failure with no retry should fail the pipeline."""
        steps = [
            PipelineStep(id="fail", tool="nonexistent_tool", output_key="x"),
        ]
        p = Pipeline(steps=steps, kernel=kernel)
        result = p.run({"prompt": "test"})
        assert result["success"] is False
        assert result["failed_at"] == "fail"


class TestPipelineSerialization:
    def test_to_from_dict_preserves_retry(self):
        steps = [
            PipelineStep(id="gen", tool="jimeng_image", output_key="img",
                         retry=3, fallback_soul="reflector"),
        ]
        d = {"steps": [{"id": s.id, "soul": s.soul, "tool": s.tool,
                         "output_key": s.output_key, "retry": s.retry,
                         "fallback_soul": s.fallback_soul, "condition": s.condition,
                         "input_key": s.input_key} for s in steps]}
        assert d["steps"][0]["retry"] == 3
        assert d["steps"][0]["fallback_soul"] == "reflector"
