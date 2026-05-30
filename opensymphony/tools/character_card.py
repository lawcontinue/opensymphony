"""Character card generator — 3-view turnaround sheet via Jimeng.

Generates a character turnaround (front / side / back) as a single image,
then stores it with metadata for reuse across sessions.

Output structure:
    characters/
        <character_slug>/
            card.png              — the 3-view image
            meta.json             — name, description, session, prompt
"""
import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path


def _parse_url(output: str) -> str | None:
    """Parse image URL from jimeng CLI output."""
    try:
        obj = json.loads(output)
        images = obj.get("result_json", {}).get("images", [])
        if images and images[0].get("image_url"):
            return images[0]["image_url"]
    except (json.JSONDecodeError, ValueError, IndexError, KeyError):
        pass
    urls = re.findall(r'https?://[^\s"\'\]>)},]+\.(?:jpg|jpeg|png|webp)[^\s"\'\]>)},]*', output, re.IGNORECASE)
    if urls:
        return urls[-1].rstrip(".,;:)")
    urls = re.findall(r'https?://[^\s"\'\]>)},]+byteimg[^\s"\'\]>)},]+', output)
    if urls:
        return urls[-1].rstrip(".,;:)")
    return None





# Proven prompt template (from prompts_character_cards.json, verified working)
CARD_PROMPT_TEMPLATE = (
    "参考站位，纯白背景，高清画质，极致细节，超高分辨率，"
    "全身三视图及面部特写，布局：左边三分之一为超大面部特写，"
    "右边三分之二放置正视图、侧视图45度、后视图，"
    "右上角空白处文字\"{name_label}\"。"
    "{style}。{description}。{extra_details}"
)

# Style presets (with full rendering directives)
STYLES = {
    "xianxia_3d": "国漫3D动画风格，PBR材质渲染，电影级CG画质，光影层次分明，明暗对比柔和，风格锚定《仙逆》《凡人修仙传》",
    "xianxia_real": "写实仙侠风格，电影级光影，真实皮肤质感，自然光线，风格参考《长月烬明》《苍兰诀》",
    "modern_3d": "国漫3D动画风格，PBR材质渲染，电影级CG画质，现代都市光影，霓虹与自然光混合",
    "historical": "中国古装正剧风格，写实质感，电影级布光，丝绸与金属材质真实还原",
}


class CharacterCard:
    name = "character_card"
    description = "Generate 3-view character turnaround card via Jimeng"

    MAX_RETRIES = 3
    RETRY_DELAYS = [5, 10, 20]

    # Base dir for character cards
    BASE_DIR = Path(os.getenv("SYMPHONY_CHARS", "C:/Users/Administrator/symphony/characters"))

    def execute(self, params: dict) -> dict:
        """
        Params:
            name (str): Character name (used for directory)
            description (str): Detailed character appearance description
            style (str): Style preset — xianxia/modern/historical/casual (default: xianxia)
            extra_details (str): Additional prompt details (optional)
            session (str): Jimeng session ID for consistency (optional, auto-created if missing)
            ratio (str): Aspect ratio (default "1:1" — turnaround sheets work best square)
            resolution (str): "2k" or "4k" (default "2k")
        """
        errors = self.validate(params)
        if errors:
            return {"success": False, "result": None, "error": "; ".join(errors)}

        name = params["name"]
        name_label = params.get("name_label", name)
        description = params["description"]
        style_key = params.get("style", "xianxia_3d")
        style = STYLES.get(style_key, style_key)
        extra = params.get("extra_details", "")
        session = params.get("session", "")
        ratio = params.get("ratio", "1:1")
        resolution = params.get("resolution", "2k")

        # Build prompt
        prompt = CARD_PROMPT_TEMPLATE.format(
            style=style, description=description, extra_details=extra, name_label=name_label
        )
        # Skip image_enhance realism boosters for character sheets (illustration-like)
        # Call jimeng directly with raw prompt

        # Setup output dir
        slug = re.sub(r'[^\w]', '_', name)[:40]
        out_dir = self.BASE_DIR / slug
        out_dir.mkdir(parents=True, exist_ok=True)

        # Generate via jimeng CLI
        cli_path = os.environ.get(
            "DREAMINA_CLI",
            r"C:\Users\Administrator\.dreamina_cli\bin\dreamina.exe"
        )
        if not os.path.exists(cli_path):
            return {"success": False, "result": None, "error": f"CLI not found: {cli_path}"}

        res_map = {"1k": "1", "2k": "2", "4k": "3"}
        res_type = res_map.get(resolution, "2")

        cmd = [
            cli_path, "text2image",
            f"--prompt={prompt}",
            f"--ratio={ratio}",
            "--model_version=5.0",
            f"--resolution_type={res_type}",
            "--poll=120",
        ]
        if session:
            cmd.append(f"--session={session}")

        last_error = None
        for attempt in range(self.MAX_RETRIES):
            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=180
                )
                output = result.stdout + result.stderr
                url = _parse_url(output)

                if url:
                    # Download to character dir
                    card_path = out_dir / f"card_{attempt}.jpg"
                    try:
                        urllib.request.urlretrieve(url, str(card_path))
                    except Exception:
                        card_path = None

                    # Save metadata
                    meta = {
                        "name": name,
                        "description": description,
                        "style": style_key,
                        "prompt": prompt,
                        "session": session,
                        "url": url,
                        "local_path": str(card_path) if card_path else None,
                        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "attempt": attempt,
                    }
                    meta_path = out_dir / "meta.json"
                    with open(meta_path, "w", encoding="utf-8") as f:
                        json.dump(meta, f, ensure_ascii=False, indent=2)

                    return {
                        "success": True,
                        "result": {
                            "name": name,
                            "slug": slug,
                            "url": url,
                            "local_path": str(card_path) if card_path else None,
                            "meta_path": str(meta_path),
                            "session": session,
                            "prompt": prompt,
                        },
                        "error": None,
                    }
                last_error = f"No URL in output (last 300): {output[-300:]}"
            except subprocess.TimeoutExpired:
                last_error = "CLI timeout (180s)"
            except Exception as e:
                last_error = str(e)

            if attempt < self.MAX_RETRIES - 1:
                time.sleep(self.RETRY_DELAYS[attempt])

        return {
            "success": False,
            "result": None,
            "error": f"Character card failed after {self.MAX_RETRIES} retries: {last_error}"
        }

    def healthcheck(self) -> dict:
        cli_path = os.environ.get(
            "DREAMINA_CLI",
            r"C:\Users\Administrator\.dreamina_cli\bin\dreamina.exe"
        )
        if not os.path.exists(cli_path):
            return {"healthy": False, "details": f"CLI not found: {cli_path}"}
        return {"healthy": True, "details": "Ready"}

    def validate(self, params: dict) -> list[str]:
        errors = []
        if not params.get("name"):
            errors.append("name is required")
        if not params.get("description"):
            errors.append("description is required")
        return errors

    # --- Utility: list existing character cards ---
    @classmethod
    def list_cards(cls) -> list[dict]:
        """List all saved character cards."""
        cards = []
        if not cls.BASE_DIR.exists():
            return cards
        for d in sorted(cls.BASE_DIR.iterdir()):
            if d.is_dir():
                meta_path = d / "meta.json"
                if meta_path.exists():
                    try:
                        with open(meta_path, encoding="utf-8") as f:
                            cards.append(json.load(f))
                    except Exception:
                        cards.append({"slug": d.name, "error": "unreadable meta"})
        return cards

    # --- Utility: batch generate cards for a cast ---
    @classmethod
    def batch_generate(cls, characters: list[dict]) -> list[dict]:
        """
        Generate cards for multiple characters sequentially.
        Each item: {"name": ..., "description": ..., "style": ..., "session": ...}
        All characters in same session share style consistency.
        Returns list of results.
        """
        tool = cls()
        results = []
        # Use shared session for cast consistency
        shared_session = characters[0].get("session", "") if characters else ""
        for char in characters:
            if shared_session:
                char["session"] = shared_session
            r = tool.execute(char)
            results.append({"name": char["name"], **r})
            # Small delay between generations
            if r.get("success"):
                time.sleep(2)
        return results
