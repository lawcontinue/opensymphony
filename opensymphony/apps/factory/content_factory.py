"""Content Factory — Main orchestration engine.

Orchestrates: Task Queue → LLM Generation → Quality Gate → Output
Continuously processes tasks until queue is empty.
Supports genre-based Soul selection and LLM router fallback.

Usage (on 5060Ti):
    python -m symphony.apps.factory.content_factory --queue tasks/factory_tasks.json
    python -m symphony.apps.factory.content_factory --seed "帮信罪辩护要点"
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from .quality_gate import QualityGate
from .task_queue import Task, TaskQueue, TaskState, TaskTier, TaskType

logger = logging.getLogger("symphony.apps.factory.content_factory")


# ── Genre → Soul mapping ────────────────────────────────────────

GENRE_SOUL_MAP = {
    "legal": "social_copy",       # 法律科普
    "tech": "tech_blogger",       # 技术博客
    "novel": "screenwriter",      # 网文
    "xianxia": "screenwriter",    # 修仙小说
    "social": "social_copy",      # 社交媒体
    "general": "social_copy",     # 通用
}

GENRE_PROMPT_TEMPLATES = {
    "legal": "请写一篇关于「{topic}」的法律科普文章。要求：案例真实可信、法条引用准确、语言通俗易懂（非学术风格）、字数 {target_length} 字左右。",
    "tech": "请写一篇关于「{topic}」的技术博客。要求：有实际代码示例、解释清楚原理、语言简洁不啰嗦、字数 {target_length} 字左右。",
    "novel": "请撰写网络小说章节。场景：{prompt}。要求：对话自然、感官描写丰富、避免 AI 常见词汇、章末留悬念、字数 {target_length} 字左右。",
    "xianxia": "请撰写修仙小说章节。{prompt}。要求：修仙体系严谨、人物性格鲜明、战斗场面有画面感、避免 AI 常见词汇、字数 {target_length} 字左右。",
    "general": "请写一篇关于「{topic}」的文章。要求：观点清晰、论据充分、语言流畅、字数 {target_length} 字左右。",
}

# AI content label (mandatory per TOOLS.md #47)
AI_LABEL = "\n\n---\n*本文由 AI 辅助生成，经人工审核。*"


# ── LLM Client ──────────────────────────────────────────────────

class FactoryLLM:
    """LLM client with router fallback (Mimo → DeepSeek → Kimi)."""

    def __init__(self, router=None):
        self.router = router
        self._direct_clients = []
        self._init_direct_clients()

    def _init_direct_clients(self):
        """Init direct API clients as fallback."""
        api_key = os.environ.get("MIMO_API_KEY", "")
        base_url = os.environ.get("MIMO_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1")
        if api_key:
            self._direct_clients.append(("mimo", api_key, base_url))

        ds_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if ds_key:
            self._direct_clients.append(("deepseek", ds_key, "https://api.deepseek.com/v1"))

        mk_key = os.environ.get("MOONSHOT_API_KEY", "")
        if mk_key:
            self._direct_clients.append(("kimi", mk_key, "https://api.moonshot.cn/v1"))

    def chat(self, prompt: str, max_tokens: int = 4096,
             temperature: float = 0.8, timeout: float = 120) -> str:
        """Chat with LLM, trying each provider in order until one succeeds."""
        import urllib.error
        import urllib.request

        for provider_name, api_key, base_url in self._direct_clients:
            payload = json.dumps({
                "model": "mimo-v2.5" if "mimo" in provider_name else "deepseek-chat" if "deepseek" in provider_name else "moonshot-v1-8k",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": temperature if "kimi" not in provider_name else 1,
            }).encode("utf-8")

            req = urllib.request.Request(
                f"{base_url}/chat/completions",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                method="POST",
            )

            try:
                import threading
                result = [None, None]

                def _call():
                    try:
                        with urllib.request.urlopen(req, timeout=timeout) as resp:
                            data = json.loads(resp.read().decode("utf-8"))
                            choice = data.get("choices", [{}])[0]
                            msg = choice.get("message", {})
                            content = msg.get("content", "") or msg.get("reasoning_content", "")
                            result[0] = content
                    except Exception as e:
                        result[1] = e

                t = threading.Thread(target=_call, daemon=True)
                t.start()
                t.join(timeout=timeout + 5)

                if result[1]:
                    logger.warning(f"  {provider_name} failed: {result[1]}")
                    continue

                if result[0]:
                    logger.info(f"  LLM response from {provider_name}: {len(result[0])} chars")
                    return result[0]
                else:
                    logger.warning(f"  {provider_name} returned empty")
                    continue

            except Exception as e:
                logger.warning(f"  {provider_name} error: {e}")
                continue

        logger.error("All LLM providers failed")
        return ""


# ── Media Generator ─────────────────────────────────────────────

class MediaGenerator:
    """Generate images via Jimeng, videos via Seedance."""

    DREAMINA_PATH = r"C:\Users\Administrator\.dreamina_cli\bin\dreamina.exe"

    def __init__(self, media_root: str = r"E:\novel_output"):
        self.media_root = Path(media_root)
        self.media_root.mkdir(parents=True, exist_ok=True)

    def generate_image(self, prompt: str, task_id: str, scene_id: str = "01",
                       session_id: int = 0) -> dict:
        """Generate a single image via Jimeng CLI."""
        import subprocess

        out_dir = self.media_root / task_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{task_id}_{scene_id}.png"

        cmd = [self.DREAMINA_PATH, "text2image",
               "--prompt", prompt[:500],
               "--ratio", "16:9",
               "--model_version", "5.0",
               "--resolution_type", "2k",
               "--poll", "120",
               "--session", str(session_id)]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            output = result.stdout or result.stderr

            image_url = ""
            try:
                data = json.loads(output)
                rj = data.get("result_json", {})
                if isinstance(rj, dict) and rj.get("images"):
                    image_url = rj["images"][0].get("image_url", "")
            except json.JSONDecodeError:
                pass

            if image_url and out_dir:
                import urllib.request
                urllib.request.urlretrieve(image_url, str(out_path))
                return {"url": image_url, "path": str(out_path), "scene_id": scene_id}
            elif image_url:
                return {"url": image_url, "path": "", "scene_id": scene_id}
            else:
                logger.warning(f"No image URL for {task_id}/{scene_id}")
                return {"url": "", "path": "", "scene_id": scene_id, "error": "no_url"}

        except Exception as e:
            return {"url": "", "path": "", "scene_id": scene_id, "error": str(e)}

    def generate_images_for_text(self, text: str, task_id: str,
                                  max_images: int = 3,
                                  session_id: int = 0) -> list[dict]:
        """Extract key scenes and generate images."""
        paragraphs = [p.strip() for p in text.split("\n")
                      if p.strip() and not p.startswith("#") and len(p.strip()) > 30]
        if not paragraphs:
            return []

        # Select evenly spaced paragraphs as scene prompts
        if len(paragraphs) <= max_images:
            selected = paragraphs
        else:
            step = len(paragraphs) / max_images
            indices = [int(i * step) for i in range(max_images)]
            selected = [paragraphs[i] for i in indices]

        results = []
        for i, para in enumerate(selected):
            scene_id = f"{i+1:02d}"
            prompt = (
                f"Illustration for article. Scene: {para[:200]}. "
                f"Style: cinematic lighting, detailed, professional. "
                f"Aspect ratio 16:9."
            )
            r = self.generate_image(prompt, task_id, scene_id, session_id)
            results.append(r)

        return results


# ── Content Factory ─────────────────────────────────────────────

class ContentFactory:
    """Main factory orchestrator. Processes task queue continuously."""

    def __init__(self, queue_file: str | Path,
                 media_root: str = r"E:\novel_output",
                 output_root: str = r"E:\novel_output",
                 max_output_gb: float = 5.0):
        self.queue = TaskQueue(Path(queue_file))
        self.llm = FactoryLLM()
        self.quality = QualityGate()
        self.media = MediaGenerator(media_root=media_root)
        self.output_root = Path(output_root)
        self.max_output_bytes = int(max_output_gb * 1024**3)
        self._running = False

    def _check_disk_limit(self) -> bool:
        """Check if output directory exceeds size limit."""
        if not self.output_root.exists():
            return True
        total = sum(f.stat().st_size for f in self.output_root.rglob("*") if f.is_file())
        if total > self.max_output_bytes:
            logger.warning(f"Output dir {total/1024**3:.1f}GB exceeds limit {self.max_output_bytes/1024**3:.1f}GB")
            return False
        return True

    def add_seed(self, topic: str, genre: str = "general",
                 tier: str = "A", prompt: str = "",
                 seed_material: str = "", task_type: str = "article",
                 **kwargs) -> Task:
        """Create a task from a seed topic."""
        task_id = f"{genre}_{int(time.time())}_{abs(hash(topic)) % 10000:04d}"
        task = Task(
            id=task_id, topic=topic, genre=genre,
            task_type=TaskType(task_type),
            tier=TaskTier(tier), prompt=prompt,
            seed_material=seed_material,
            target_length=kwargs.get("target_length", 2000),
            media_root=str(self.media.media_root),
            output_dir=str(self.output_root / task_id),
            download_media=kwargs.get("download_media", True),
            keywords=kwargs.get("keywords", []),
            priority=kwargs.get("priority", 1),
            max_retries=kwargs.get("max_retries", 2),
        )
        self.queue.add(task)
        return task

    def _build_prompt(self, task: Task) -> str:
        """Build LLM prompt from task definition."""
        template = GENRE_PROMPT_TEMPLATES.get(task.genre, GENRE_PROMPT_TEMPLATES["general"])

        if task.seed_material:
            prompt = f"基于以下素材写作：\n{task.seed_material[:1000]}\n\n{template.format(topic=task.topic, prompt=task.prompt, target_length=task.target_length)}"
        elif task.prompt:
            prompt = template.format(topic=task.topic, prompt=task.prompt, target_length=task.target_length)
        else:
            prompt = template.format(topic=task.topic, prompt="", target_length=task.target_length)

        return prompt

    def _process_task(self, task: Task) -> Task:
        """Process a single task through the 3-step pipeline."""
        task.state = TaskState.EXECUTING
        task.started_at = time.time()
        self.queue.update(task)

        # ── Step 1: Generate text ──
        logger.info(f"[{task.id}] Step 1: Generating text ({task.genre}, tier {task.tier.value})")
        prompt = self._build_prompt(task)

        # Retry logic for LLM
        text = ""
        for attempt in range(3):
            text = self.llm.chat(prompt, max_tokens=6000, temperature=0.8)
            if text and len(text) > 100:
                break
            logger.warning(f"[{task.id}] LLM attempt {attempt+1} returned {len(text)} chars, retrying...")

        if not text:
            task.state = TaskState.FAILED
            task.error = "LLM returned empty after 3 attempts"
            task.finished_at = time.time()
            self.queue.update(task)
            return task

        task.progress["text_generated"] = True
        task.progress["text_length"] = len(text)

        # ── Step 2: Quality gate ──
        logger.info(f"[{task.id}] Step 2: Quality check")
        qr = self.quality.check(
            text=text,
            tier=task.tier.value,
            min_length=max(200, task.target_length // 2),
            max_length=task.target_length * 3,
        )
        task.score = qr.score
        task.progress["quality_passed"] = qr.passed
        task.progress["quality_issues"] = qr.issues

        logger.info(f"[{task.id}] Quality: {qr.score:.0f}/100 ({'PASS' if qr.passed else 'FAIL'})")

        if not qr.passed and task.retry_count < task.max_retries:
            # Revise: add quality feedback to prompt
            task.retry_count += 1
            feedback = "\n".join(f"- {i}" for i in qr.issues + qr.warnings)
            prompt = f"以下是上一稿的问题：\n{feedback}\n\n请修改以下文章，解决上述问题：\n\n{text}\n\n修改后的完整文章："
            text = self.llm.chat(prompt, max_tokens=6000, temperature=0.7)

            # Re-check
            qr = self.quality.check(text=text, tier=task.tier.value,
                                     min_length=max(200, task.target_length // 2))
            task.score = qr.score
            task.progress["revised"] = True

        if not qr.passed:
            # Final fail or pause
            if task.tier == TaskTier.S:
                task.state = TaskState.PAUSED  # S-tier always needs human
            elif qr.score >= 60:
                task.state = TaskState.PAUSED  # Borderline, pause
            else:
                task.state = TaskState.FAILED
            task.error = f"Quality {qr.score:.0f} < threshold"
            task.finished_at = time.time()
            self.queue.update(task)
            return task

        # ── Step 3: Post-process + media ──
        logger.info(f"[{task.id}] Step 3: Post-processing")

        # Add AI label
        final_text = text + AI_LABEL

        # Save text
        out_dir = Path(task.output_dir or self.output_root / task.id)
        out_dir.mkdir(parents=True, exist_ok=True)
        text_path = out_dir / "article.md"
        text_path.write_text(final_text, encoding="utf-8")

        task.result["text_path"] = str(text_path)
        task.result["text_length"] = len(final_text)
        task.result["score"] = task.score

        # Generate images (if not article-only)
        if task.task_type in (TaskType.FULL_PACKAGE, TaskType.IMAGE_SET):
            images = self.media.generate_images_for_text(
                text, task.id, max_images=3, session_id=hash(task.topic) % 10000
            )
            task.result["images"] = images
            task.progress["images_generated"] = len(images)

        # Done
        task.state = TaskState.DONE
        task.finished_at = time.time()
        self.queue.update(task)

        elapsed = task.finished_at - task.started_at
        logger.info(f"[{task.id}] Done in {elapsed:.0f}s: score={task.score:.0f}, "
                     f"text={len(final_text)} chars")

        return task

    def run_once(self) -> int:
        """Process one task from the queue. Returns tasks processed."""
        if not self._check_disk_limit():
            logger.warning("Disk limit reached, pausing factory")
            return 0

        # Try pending first, then retryable
        task = self.queue.pop_next()
        if not task:
            task = self.queue.get_retryable()
            if task:
                task.state = TaskState.PENDING  # Reset for retry

        if not task:
            return 0

        self._process_task(task)
        return 1

    def run_continuous(self, poll_interval: float = 5.0, max_tasks: int = 0):
        """Run continuously until queue is empty.

        Args:
            poll_interval: Seconds between queue checks.
            max_tasks: Max tasks to process (0 = unlimited).
        """
        self._running = True
        processed = 0

        logger.info(f"Factory started: {self.queue.stats()}")
        logger.info(f"Media root: {self.media.media_root}")
        logger.info(f"Output root: {self.output_root}")

        try:
            while self._running:
                stats = self.queue.stats()
                pending = stats.get("pending", 0)
                if pending == 0 and not self.queue.get_retryable():
                    logger.info("Queue empty, factory stopping")
                    break

                if max_tasks > 0 and processed >= max_tasks:
                    logger.info(f"Reached max tasks ({max_tasks}), stopping")
                    break

                n = self.run_once()
                processed += n

                if n == 0:
                    time.sleep(poll_interval)

        except KeyboardInterrupt:
            logger.info("Factory interrupted by user")
        finally:
            self._running = False

        stats = self.queue.stats()
        logger.info(f"Factory stopped: processed={processed}, queue={stats}")
        return processed

    def stop(self):
        self._running = False


# ── CLI ──────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Symphony Content Factory")
    parser.add_argument("--queue", type=Path, default=Path("tasks/factory_tasks.json"))
    parser.add_argument("--media-root", default=r"E:\novel_output")
    parser.add_argument("--output-root", default=r"E:\novel_output")
    parser.add_argument("--max-tasks", type=int, default=0)
    parser.add_argument("--seed", type=str, help="Quick seed: topic to create task")
    parser.add_argument("--genre", default="general")
    parser.add_argument("--tier", default="A", choices=["S", "A", "B"])
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    # Load .env
    env_file = Path(__file__).parent.parent.parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())

    factory = ContentFactory(
        queue_file=args.queue,
        media_root=args.media_root,
        output_root=args.output_root,
    )

    if args.seed:
        factory.add_seed(topic=args.seed, genre=args.genre, tier=args.tier)

    factory.run_continuous(max_tasks=args.max_tasks)


if __name__ == "__main__":
    main()
