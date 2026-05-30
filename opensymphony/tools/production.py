"""Pre-approved production tools — registered at kernel start, bypassing Workshop sandbox.

These tools are trusted code (not agent-generated), so they can use urllib/subprocess.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger("symphony.tools")

# ── Jimeng Image Generation ──────────────────────────────────────────

class JimengImageTool:
    """Image generation via Jimeng (dreamina) CLI."""

    name = "jimeng_image"
    description = "Image generation via Jimeng CLI (dreamina)"

    MAX_RETRIES = 3
    RETRY_DELAYS = [3, 6, 12]

    def execute(self, params: dict) -> dict:
        prompt = params.get("prompt", "")
        if not prompt:
            return {"success": False, "error": "prompt is required"}

        # Auto-enhance for realism (skip if illustration keywords)
        skip_kws = ("illustration", "cartoon", "anime", "icon", "logo", "diagram", "chart", "3D")
        if not any(kw in prompt.lower() for kw in skip_kws):
            prompt += ", realistic skin texture, natural lighting, 8k photography"

        ratio = params.get("ratio", "1:1")
        resolution = params.get("resolution", "2k")
        session = params.get("session", "")
        cli_path = os.environ.get("DREAMINA_CLI", r"C:\Users\Administrator\.dreamina_cli\bin\dreamina.exe")

        if not os.path.exists(cli_path):
            return {"success": False, "error": f"CLI not found: {cli_path}"}

        res_map = {"1k": "1", "2k": "2", "4k": "3"}
        res_type = res_map.get(resolution, "2")

        cmd = [cli_path, "text2image", f"--prompt={prompt}", f"--ratio={ratio}",
               "--model_version=5.0", f"--resolution_type={res_type}", "--poll=120"]
        if session:
            cmd.append(f"--session={session}")

        last_error = None
        for attempt in range(self.MAX_RETRIES):
            try:
                result = subprocess.run(cmd, capture_output=True, text=True,
                                        encoding="utf-8", errors="replace", timeout=180)
                output = result.stdout + result.stderr
                url = self._parse_url(output)
                if url:
                    local_path = self._download(url, prompt, attempt)
                    return {"success": True, "result": {"url": url, "local_path": local_path,
                            "prompt": prompt, "backend": "jimeng"}}
                last_error = "No URL in output"
            except subprocess.TimeoutExpired:
                last_error = "CLI timeout (180s)"
            except Exception as e:
                last_error = str(e)
            if attempt < self.MAX_RETRIES - 1:
                time.sleep(self.RETRY_DELAYS[attempt])

        return {"success": False, "error": f"Failed after {self.MAX_RETRIES} retries: {last_error}"}

    def healthcheck(self) -> dict:
        cli_path = os.environ.get("DREAMINA_CLI", r"C:\Users\Administrator\.dreamina_cli\bin\dreamina.exe")
        return {"healthy": os.path.exists(cli_path), "details": f"CLI: {cli_path}"}

    @staticmethod
    def _parse_url(output: str) -> str | None:
        # Strategy 0: Full JSON with result_json.images
        try:
            obj = json.loads(output)
            images = obj.get("result_json", {}).get("images", [])
            if images and images[0].get("image_url"):
                return images[0]["image_url"]
        except (json.JSONDecodeError, ValueError, IndexError, KeyError):
            pass
        # Strategy 1: Regex URL
        urls = re.findall(r'https?://[^\s"\'\]>)},]+\.(?:jpg|jpeg|png|webp)[^\s"\'\]>)},]*', output, re.IGNORECASE)
        if urls:
            return urls[-1].rstrip(".,;:)")
        # Strategy 2: Any byteimg URL
        urls = re.findall(r'https?://[^\s"\'\]>)},]+byteimg[^\s"\'\]>)},]+', output)
        if urls:
            return urls[-1].rstrip(".,;:)")
        return None

    def _download(self, url: str, prompt: str, attempt: int) -> str | None:
        try:
            download_dir = Path(os.getenv("SYMPHONY_TASKS", "C:/Users/Administrator/symphony/tasks")) / "images"
            download_dir.mkdir(parents=True, exist_ok=True)
            safe_name = re.sub(r'[^\w]', '_', prompt[:30])[:30]
            filename = f"img_{safe_name}_{attempt}.jpg"
            out_path = download_dir / filename
            urllib.request.urlretrieve(url, str(out_path))
            return str(out_path)
        except Exception:
            return None


# ── Mimo Text Generation (standalone) ─────────────────────────────────

class MimoTextGenTool:
    """Text generation via Mimo-V2.5 API with retry."""

    name = "mimo_textgen"
    description = "Text generation via Mimo-V2.5 API"

    MIMO_URL = "https://token-plan-cn.xiaomimimo.com/v1/chat/completions"
    MAX_RETRIES = 3

    def execute(self, params: dict) -> dict:
        prompt = params.get("prompt", "")
        if not prompt:
            return {"success": False, "error": "prompt is required"}

        system_prompt = params.get("system_prompt", "")
        max_tokens = params.get("max_tokens", 4096)
        temperature = params.get("temperature", 0.8)
        api_key = os.environ.get("MIMO_API_KEY", "")

        messages = [{"role": "system", "content": "Respond directly. Do not show thinking. Output only the final answer."}]
        if system_prompt:
            messages[0]["content"] += "\n\n" + system_prompt
        messages.append({"role": "user", "content": prompt})

        payload = json.dumps({"model": "mimo-v2.5", "messages": messages,
                              "max_tokens": max_tokens, "temperature": temperature}).encode("utf-8")
        last_error = None
        for attempt in range(self.MAX_RETRIES):
            try:
                req = urllib.request.Request(self.MIMO_URL, data=payload, headers={
                    "Content-Type": "application/json", "Authorization": f"Bearer {api_key}"})
                with urllib.request.urlopen(req, timeout=120) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    if not content:
                        content = data.get("choices", [{}])[0].get("message", {}).get("reasoning_content", "")
                    # Strip thinking tags
                    if "</think" in content:
                        idx = content.find("</think")
                        content = content[idx + len("</think"):]
                        gt = content.find(">")
                        if gt >= 0:
                            content = content[gt + 1:].strip()
                    if content:
                        return {"success": True, "result": {"text": content}}
                    last_error = "Empty response"
            except Exception as e:
                last_error = str(e)
            if attempt < self.MAX_RETRIES - 1:
                time.sleep(2 ** attempt)

        return {"success": False, "error": f"Failed after {self.MAX_RETRIES} retries: {last_error}"}


# ── Legal Review ──────────────────────────────────────────────────────

class LegalReviewTool:
    """Extract legal citations and append disclaimer."""

    name = "legal_review"
    description = "Extract legal citations and append disclaimer"

    LEGAL_PATTERNS = [
        r"第[一二三四五六七八九十百千万零\d]+条", r"《[^》]+法》",
        r"《[^》]+条例》", r"《[^》]+规定》", r"《[^》]+办法》",
    ]

    def execute(self, params: dict) -> dict:
        text = params.get("text", "")
        if not text:
            return {"success": False, "error": "text is required"}
        refs = []
        for p in self.LEGAL_PATTERNS:
            refs.extend(re.findall(p, text))
        refs = list(dict.fromkeys(refs))  # dedupe
        disclaimer = f"\n\n---\n免责声明：本文由AI辅助生成，不构成法律意见。具体法律问题请咨询执业律师。\n法律条文引用截至{time.strftime('%Y年%m月')}。"
        return {"success": True, "result": {"text_with_disclaimer": text + disclaimer,
                "legal_refs": refs, "ref_count": len(refs), "needs_manual_review": len(refs) > 0}}


# ── Quality Check ─────────────────────────────────────────────────────

class QualityCheckTool:
    """Rule-based quality gate (5 deterministic checks)."""

    name = "quality_check"
    description = "5-point quality gate: length, format, cliches, similarity, compliance"

    def execute(self, params: dict) -> dict:
        text = params.get("text", "")
        soul = params.get("soul", "default")
        if not text:
            return {"success": False, "error": "text is required"}

        checks = []

        # 1. Length
        char_count = len(text)
        ranges = {"social_copy": (300, 600), "tech_blogger": (2000, 6000), "default": (200, 5000)}
        mn, mx = ranges.get(soul, ranges["default"])
        checks.append({"check": "length", "passed": mn <= char_count <= mx,
                        "value": char_count, "range": f"{mn}-{mx}"})

        # 2. Format
        if soul == "social_copy":
            has_tags = bool(re.search(r'#\S+', text))
            has_hook = bool(re.search(r'【[^】]+】', text))
            checks.append({"check": "format", "passed": has_tags and has_hook,
                            "details": {"has_tags": has_tags, "has_hook": has_hook}})
        elif soul == "tech_blogger":
            has_code = bool(re.search(r'```', text))
            has_headings = bool(re.search(r'^#{1,3}\s', text, re.MULTILINE))
            checks.append({"check": "format", "passed": has_code or has_headings,
                            "details": {"has_code": has_code, "has_headings": has_headings}})
        else:
            checks.append({"check": "format", "passed": True})

        # 3. Compliance
        has_compliance = bool(re.search(r'AI\s*辅助|AI\s*assisted|使用AI', text))
        checks.append({"check": "compliance", "passed": has_compliance})

        # 4. AI-speak detection
        ai_phrases = ["值得注意的是", "让我们来看看", "正如我们所知", "总而言之", "不可否认"]
        ai_hits = [p for p in ai_phrases if p in text]
        checks.append({"check": "ai_speak", "passed": len(ai_hits) == 0, "hits": ai_hits})

        # 5. Structure (not too many consecutive short lines)
        lines = text.split("\n")
        short_runs = 0
        max_short_run = 0
        for line in lines:
            if len(line.strip()) < 10 and line.strip():
                short_runs += 1
                max_short_run = max(max_short_run, short_runs)
            else:
                short_runs = 0
        checks.append({"check": "structure", "passed": max_short_run <= 3, "max_consecutive_short": max_short_run})

        passed = all(c["passed"] for c in checks)
        score = sum(1 for c in checks if c["passed"]) / len(checks) * 100
        return {"success": True, "result": {"passed": passed, "score": round(score, 1), "checks": checks}}


# ── Prompt Extract ────────────────────────────────────────────────────

class PromptExtractTool:
    """Extract [IMAGE: ...] markers from text."""

    name = "prompt_extract"
    description = "Extract image prompt markers from text"

    def execute(self, params: dict) -> dict:
        text = params.get("text", "")
        prompts = re.findall(r'\[IMAGE:\s*(.+?)\]', text)
        return {"success": True, "result": {"prompts": prompts, "count": len(prompts)}}


# ── File Tools (for agent tool-use) ───────────────────────────────────

class FileReadTool:
    """Read a file's content."""
    name = "file_read"
    description = "Read a file and return its content. Params: {path: str}"

    def execute(self, params: dict) -> dict:
        path = params.get("path", "")
        if not path:
            return {"success": False, "error": "path is required"}
        try:
            p = Path(path)
            if not p.exists():
                return {"success": False, "error": f"File not found: {path}"}
            if p.stat().st_size > 500_000:
                return {"success": False, "error": f"File too large ({p.stat().st_size} bytes, max 500KB)"}
            content = p.read_text(encoding="utf-8", errors="replace")
            return {"success": True, "result": {"content": content[:50_000], "path": str(p), "size": len(content)}}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def healthcheck(self) -> dict:
        return {"healthy": True, "details": "file_read ready"}


class FileWriteTool:
    """Write content to a file."""
    name = "file_write"
    description = "Write content to a file. Params: {path: str, content: str}"

    def execute(self, params: dict) -> dict:
        path = params.get("path", "")
        content = params.get("content", "")
        if not path:
            return {"success": False, "error": "path is required"}
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return {"success": True, "result": {"path": str(p), "size": len(content)}}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def healthcheck(self) -> dict:
        return {"healthy": True, "details": "file_write ready"}


class FileEditTool:
    """Replace exact text in a file."""
    name = "file_edit"
    description = "Replace exact text in a file. Params: {path: str, old_text: str, new_text: str}"

    def execute(self, params: dict) -> dict:
        path = params.get("path", "")
        old_text = params.get("old_text", "")
        new_text = params.get("new_text", "")
        if not path or not old_text:
            return {"success": False, "error": "path and old_text are required"}
        try:
            p = Path(path)
            if not p.exists():
                return {"success": False, "error": f"File not found: {path}"}
            content = p.read_text(encoding="utf-8", errors="replace")
            if old_text not in content:
                return {"success": False, "error": "old_text not found in file"}
            new_content = content.replace(old_text, new_text, 1)
            p.write_text(new_content, encoding="utf-8")
            return {"success": True, "result": {"path": str(p), "old_size": len(content), "new_size": len(new_content)}}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def healthcheck(self) -> dict:
        return {"healthy": True, "details": "file_edit ready"}


class ListDirTool:
    """List files in a directory."""
    name = "list_dir"
    description = "List files in a directory. Params: {path: str}"

    def execute(self, params: dict) -> dict:
        path = params.get("path", "")
        if not path:
            return {"success": False, "error": "path is required"}
        try:
            p = Path(path)
            if not p.exists():
                return {"success": False, "error": f"Directory not found: {path}"}
            entries = []
            for item in sorted(p.iterdir()):
                entries.append({"name": item.name, "type": "dir" if item.is_dir() else "file",
                                "size": item.stat().st_size if item.is_file() else 0})
            return {"success": True, "result": {"entries": entries[:100], "count": len(entries)}}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def healthcheck(self) -> dict:
        return {"healthy": True, "details": "list_dir ready"}


# ── Registry ──────────────────────────────────────────────────────────

# All pre-approved tool instances
PRODUCTION_TOOLS: dict[str, Any] = {}


def register_all() -> dict[str, str]:
    """Register all production tools. Returns {name: description}."""
    tools = [JimengImageTool(), MimoTextGenTool(), LegalReviewTool(),
             QualityCheckTool(), PromptExtractTool(), CharacterCardTool(),
             FileReadTool(), FileWriteTool(), FileEditTool(), ListDirTool()]
    for t in tools:
        PRODUCTION_TOOLS[t.name] = t
        logger.info(f"Registered production tool: {t.name}")
    return {t.name: t.description for t in tools}


def call_tool(name: str, params: dict) -> dict:
    """Execute a production tool by name."""
    tool = PRODUCTION_TOOLS.get(name)
    if not tool:
        return {"success": False, "error": f"Tool '{name}' not found. Available: {list(PRODUCTION_TOOLS.keys())}"}
    try:
        return tool.execute(params)
    except Exception as e:
        return {"success": False, "error": str(e)}


def list_tools() -> list[dict]:
    """List all registered tools with health status."""
    result = []
    for name, tool in PRODUCTION_TOOLS.items():
        hc = tool.healthcheck() if hasattr(tool, 'healthcheck') else {"healthy": True}
        result.append({"name": name, "description": tool.description, "healthy": hc.get("healthy", True)})
    return result


# ── Character Card (3-view turnaround) ──────────────────────────────

class CharacterCardTool:
    """Generate 3-view character turnaround card via Jimeng CLI."""
    name = "character_card"
    description = "Generate 3-view character turnaround card via Jimeng CLI"

    def execute(self, params: dict) -> dict:
        from .character_card import CharacterCard
        tool = CharacterCard()
        return tool.execute(params)

    def healthcheck(self) -> dict:
        cli_path = os.environ.get("DREAMINA_CLI", r"C:\Users\Administrator\.dreamina_cli\bin\dreamina.exe")
        return {"healthy": os.path.exists(cli_path), "details": f"CLI: {cli_path}"}
