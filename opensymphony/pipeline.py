"""Pipeline — Declarative multi-step agent workflows with governance.

Steps are defined as a list of dicts (data-driven, not hardcoded).
Each step specifies a soul or tool, input/output mapping, and failure handling.

Usage:
    from opensymphony.pipeline import Pipeline, PipelineStep

    steps = [
        PipelineStep(id="write", soul="screenwriter", output_key="script"),
        PipelineStep(id="direct", soul="drama_director", input_key="script", output_key="prompts"),
        PipelineStep(id="render", tool="jimeng_image", input_key="prompts", output_key="images",
                     retry=3, fallback_soul="reflector"),
    ]

    pipe = Pipeline(steps, kernel=kernel)
    result = await pipe.run({"prompt": "A story about..."})
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("symphony.pipeline")


@dataclass
class PipelineStep:
    """A single step in a pipeline."""
    id: str
    # Either soul or tool (exactly one)
    soul: str | None = None
    tool: str | None = None
    # Input/output
    input_key: str | None = None  # Key from context to use as input
    output_key: str = "output"
    # LLM params (for soul steps)
    max_tokens: int = 4096
    temperature: float = 0.7
    # Failure handling
    retry: int = 0  # Max retries on failure
    fallback_soul: str | None = None  # Fallback agent on failure
    # Condition
    condition: str | None = None  # Optional: only run if this key exists in context

    def __post_init__(self):
        if not self.soul and not self.tool:
            raise ValueError(f"Step '{self.id}' must have either soul or tool")
        if self.soul and self.tool:
            raise ValueError(f"Step '{self.id}' cannot have both soul and tool")


@dataclass
class FanOutStep:
    """A fan-out step that runs multiple branches in parallel.

    Each branch is a list of PipelineSteps. All branches receive the same input.
    Results are collected into a dict keyed by branch id.

    Usage:
        fan = FanOutStep(
            id="parallel_review",
            branches={
                "audit": [PipelineStep(id="audit", tool="novel_auditor", ...)],
                "style": [PipelineStep(id="style", tool="style_imitator", ...)],
            }
        )
    """
    id: str
    branches: dict[str, list[PipelineStep]]  # branch_id -> steps
    input_key: str | None = None
    output_key: str = "output"
    max_workers: int = 4  # Max parallel branches
    fail_fast: bool = False  # Stop all branches if one fails

    def __post_init__(self):
        if not self.branches:
            raise ValueError(f"FanOutStep '{self.id}' must have at least one branch")


@dataclass
class StepResult:
    """Result of executing a pipeline step."""
    step_id: str
    success: bool
    output: Any = None
    error: str = ""
    attempts: int = 1
    latency_ms: float = 0.0
    used_fallback: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class Pipeline:
    """Declarative multi-step workflow engine."""

    def __init__(self, steps: list[PipelineStep | FanOutStep], kernel: Any):
        self.steps = steps
        self.kernel = kernel
        self._validate()

    def _validate(self) -> None:
        """Validate pipeline definition."""
        ids = set()
        for step in self.steps:
            if isinstance(step, FanOutStep):
                if step.id in ids:
                    raise ValueError(f"Duplicate step id: {step.id}")
                ids.add(step.id)
                for branch_id, branch_steps in step.branches.items():
                    for bs in branch_steps:
                        if bs.id in ids:
                            raise ValueError(f"Duplicate step id: {bs.id}")
                        ids.add(bs.id)
                continue
            if step.id in ids:
                raise ValueError(f"Duplicate step id: {step.id}")
            ids.add(step.id)
            # Check input_key references a previous output_key or initial context
            # (we can't fully validate until runtime, but warn for obvious issues)

    def run(self, initial_context: dict[str, Any]) -> dict[str, Any]:
        """Execute the pipeline synchronously.

        Supports both sequential PipelineSteps and parallel FanOutSteps.
        """
        context = dict(initial_context)
        results: list[StepResult] = []

        for step in self.steps:
            # FanOutStep: parallel execution
            if isinstance(step, FanOutStep):
                fan_results = self._execute_fanout(step, context)
                results.extend(fan_results["step_results"])

                if fan_results["failed_branches"] and step.fail_fast:
                    return {"success": False, "results": results, "context": context,
                            "failed_at": step.id}

                # Merge all branch outputs into context
                context[step.output_key] = fan_results["branch_outputs"]
                # Also merge individual branch outputs
                for bid, boutput in fan_results["branch_outputs"].items():
                    context[f"{step.id}.{bid}"] = boutput
                continue

            # Regular PipelineStep
            if step.condition and step.condition not in context:
                logger.info(f"Skipping step '{step.id}': condition '{step.condition}' not met")
                results.append(StepResult(step_id=step.id, success=True, output=None))
                continue

            if step.input_key:
                input_data = context.get(step.input_key, "")
            elif results:
                input_data = ""
                for prev in reversed(results):
                    if prev.success and prev.output is not None:
                        input_data = prev.output
                        break
            else:
                input_data = context

            sr = self._execute_step(step, input_data, context)
            results.append(sr)

            if sr.success:
                context[step.output_key] = sr.output
                logger.info(f"Step '{step.id}' done ({sr.latency_ms:.0f}ms, {sr.attempts} attempts)")
            else:
                if step.fallback_soul:
                    sr = self._execute_fallback(step, input_data, context)
                    results[-1] = sr
                    if sr.success:
                        context[step.output_key] = sr.output
                        logger.info(f"Step '{step.id}' fallback succeeded via '{step.fallback_soul}'")
                    else:
                        logger.error(f"Step '{step.id}' failed (including fallback)")
                        return {"success": False, "results": results, "context": context,
                                "failed_at": step.id}
                else:
                    logger.error(f"Step '{step.id}' failed: {sr.error}")
                    return {"success": False, "results": results, "context": context,
                            "failed_at": step.id}

        return {"success": True, "results": results, "context": context, "failed_at": None}

    def _execute_fanout(self, step: FanOutStep, context: dict) -> dict:
        """Execute a FanOutStep: run all branches in parallel."""
        # Get input
        if step.input_key:
            input_data = context.get(step.input_key, "")
        else:
            input_data = context

        branch_outputs: dict[str, Any] = {}
        step_results: list[StepResult] = []
        failed_branches: list[str] = []

        def _run_branch(branch_id: str, branch_steps: list[PipelineStep]):
            """Run a single branch and return its results."""
            branch_ctx = dict(context)
            if isinstance(input_data, str):
                branch_ctx["_fan_input"] = input_data
            elif isinstance(input_data, dict):
                branch_ctx.update(input_data)
            else:
                branch_ctx["_fan_input"] = str(input_data)

            branch_results = []
            for bs in branch_steps:
                # Get input for this branch step
                if bs.input_key:
                    bs_input = branch_ctx.get(bs.input_key, input_data)
                elif branch_results:
                    bs_input = ""
                    for prev in reversed(branch_results):
                        if prev.success and prev.output is not None:
                            bs_input = prev.output
                            break
                else:
                    bs_input = input_data

                sr = self._execute_step(bs, bs_input, branch_ctx)
                branch_results.append(sr)
                if sr.success:
                    branch_ctx[bs.output_key] = sr.output
                else:
                    if bs.fallback_soul:
                        sr = self._execute_fallback(bs, bs_input, branch_ctx)
                        branch_results[-1] = sr
                        if sr.success:
                            branch_ctx[bs.output_key] = sr.output
                    break

            # Return last output as branch result
            last_output = None
            for r in reversed(branch_results):
                if r.success and r.output is not None:
                    last_output = r.output
                    break

            return branch_id, last_output, branch_results, \
                any(not r.success for r in branch_results)

        # Execute branches in parallel
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=step.max_workers) as executor:
            futures = {
                executor.submit(_run_branch, bid, bsteps): bid
                for bid, bsteps in step.branches.items()
            }
            for future in as_completed(futures):
                bid, output, branch_srs, failed = future.result()
                branch_outputs[bid] = output
                step_results.extend(branch_srs)
                if failed:
                    failed_branches.append(bid)
                    logger.warning(f"FanOut branch '{bid}' failed")
                else:
                    logger.info(f"FanOut branch '{bid}' done")

        total_ms = (time.time() - t0) * 1000
        logger.info(f"FanOut '{step.id}' completed in {total_ms:.0f}ms "
                     f"({len(step.branches)} branches, {len(failed_branches)} failed)")

        return {
            "branch_outputs": branch_outputs,
            "step_results": step_results,
            "failed_branches": failed_branches,
            "latency_ms": total_ms,
        }

    def _execute_step(self, step: PipelineStep, input_data: Any, context: dict) -> StepResult:
        """Execute a single step with retries."""
        last_error = ""
        for attempt in range(1 + step.retry):
            t0 = time.time()
            try:
                if step.tool:
                    output = self._run_tool(step, input_data, context)
                else:
                    output = self._run_soul(step, input_data, context)
                latency = (time.time() - t0) * 1000
                return StepResult(step_id=step.id, success=True, output=output,
                                  attempts=attempt + 1, latency_ms=latency)
            except Exception as e:
                last_error = str(e)
                latency = (time.time() - t0) * 1000
                if attempt < step.retry:
                    logger.warning(f"Step '{step.id}' attempt {attempt+1} failed: {e}, retrying...")
                    time.sleep(1)

        return StepResult(step_id=step.id, success=False, error=last_error,
                          attempts=1 + step.retry, latency_ms=latency)

    def _execute_fallback(self, step: PipelineStep, input_data: Any, context: dict) -> StepResult:
        """Execute fallback soul for a failed step."""
        t0 = time.time()
        try:
            agent = self.kernel.create_agent(soul_id=step.fallback_soul)
            # Build prompt for fallback
            prompt = f"The previous step '{step.id}' failed. Original input:\n{input_data}\n\nPlease handle this."
            response = agent.chat(prompt, max_tokens=step.max_tokens, temperature=step.temperature)
            latency = (time.time() - t0) * 1000
            return StepResult(step_id=step.id, success=True, output=response.content,
                              attempts=1, latency_ms=latency, used_fallback=True,
                              metadata={"fallback_soul": step.fallback_soul})
        except Exception as e:
            latency = (time.time() - t0) * 1000
            return StepResult(step_id=step.id, success=False, error=f"Fallback failed: {e}",
                              attempts=1, latency_ms=latency, used_fallback=True)

    def _run_tool(self, step: PipelineStep, input_data: Any, context: dict) -> Any:
        """Execute a tool step."""
        from .tools.production import call_tool
        # Build params: always include text and prompt from input
        params = dict(context)  # Start with full context
        if isinstance(input_data, str):
            params["text"] = input_data
            params["prompt"] = input_data
        elif isinstance(input_data, dict):
            params.update(input_data)
        else:
            params["text"] = str(input_data)
            params["prompt"] = str(input_data)
        result = call_tool(step.tool, params)
        if not result.get("success"):
            raise RuntimeError(result.get("error", "Tool failed"))
        return result.get("result", result)

    def _run_soul(self, step: PipelineStep, input_data: Any, context: dict) -> Any:
        """Execute a soul (LLM) step."""
        agent = self.kernel.create_agent(soul_id=step.soul)
        prompt = input_data if isinstance(input_data, str) else str(input_data)
        response = agent.chat(prompt, max_tokens=step.max_tokens, temperature=step.temperature)
        return response.content

    def to_dict(self) -> dict:
        """Serialize pipeline definition."""
        return {
            "steps": [
                {"id": s.id, "soul": s.soul, "tool": s.tool, "input_key": s.input_key,
                 "output_key": s.output_key, "retry": s.retry, "fallback_soul": s.fallback_soul,
                 "condition": s.condition}
                for s in self.steps
            ]
        }

    @classmethod
    def from_dict(cls, data: dict, kernel: Any) -> Pipeline:
        """Deserialize pipeline from dict."""
        steps = [PipelineStep(**s) for s in data["steps"]]
        return cls(steps=steps, kernel=kernel)
