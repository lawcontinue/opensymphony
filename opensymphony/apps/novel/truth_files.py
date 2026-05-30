"""Truth Files — 7 truth files as single source of truth for novel continuity.

Ported from InkOS concept. Each file tracks a different aspect of the novel's state.
All writes go through apply_delta() which validates before committing.
Snapshots enable rollback to any chapter.
"""
from __future__ import annotations

import copy
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger("symphony.apps.novel.truth")


class TruthFile(str, Enum):
    """The 7 truth files."""
    CURRENT_STATE = "current_state"
    CHARACTER_MATRIX = "character_matrix"
    PARTICLE_LEDGER = "particle_ledger"
    PENDING_HOOKS = "pending_hooks"
    CHAPTER_SUMMARIES = "chapter_summaries"
    SUBPLOT_BOARD = "subplot_board"
    EMOTIONAL_ARCS = "emotional_arcs"


@dataclass
class Delta:
    """A change to truth files — only contains what changed."""
    chapter: int
    changes: dict[str, Any]  # {file_name: {field: new_value}}
    timestamp: float = field(default_factory=time.time)


@dataclass
class Snapshot:
    """A point-in-time snapshot of all truth files."""
    chapter: int
    data: dict[str, Any]  # {file_name: parsed_content}
    timestamp: float = field(default_factory=time.time)


class TruthFiles:
    """Manages 7 truth files with snapshot, delta, and rollback support.

    Usage:
        tf = TruthFiles(Path("story"))
        tf.load()

        # Write chapter 3
        tf.snapshot(3)
        tf.apply_delta(3, {"current_state": {"location": "青云城"}})

        # Rollback if needed
        tf.rollback(2)
    """

    def __init__(self, story_dir: Path):
        self.story_dir = story_dir
        self.state_dir = story_dir / "state"
        self.snapshot_dir = story_dir / "snapshots"
        self._data: dict[str, Any] = {}
        self._snapshots: dict[int, Snapshot] = {}

    # ── Load / Save ──────────────────────────────────────────────

    def load(self) -> None:
        """Load all truth files from disk."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._data = {}
        for tf in TruthFile:
            path = self.state_dir / f"{tf.value}.json"
            md_path = self.story_dir / f"{tf.value}.md"
            if path.exists():
                self._data[tf.value] = json.loads(path.read_text("utf-8"))
            elif md_path.exists():
                # Legacy markdown — store as {"_markdown": content}
                self._data[tf.value] = {"_markdown": md_path.read_text("utf-8")}
            else:
                self._data[tf.value] = {}
        # Load snapshot index
        self._load_snapshot_index()

    def save(self) -> None:
        """Persist all truth files to disk."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        for tf in TruthFile:
            if tf.value in self._data:
                path = self.state_dir / f"{tf.value}.json"
                path.write_text(
                    json.dumps(self._data[tf.value], ensure_ascii=False, indent=2),
                    "utf-8",
                )

    # ── Read ─────────────────────────────────────────────────────

    def get(self, file: TruthFile) -> dict:
        """Get truth file content."""
        return self._data.get(file.value, {})

    def get_field(self, file: TruthFile, key: str, default: Any = None) -> Any:
        """Get a specific field from a truth file."""
        return self._data.get(file.value, {}).get(key, default)

    def all_files(self) -> dict[str, dict]:
        """Get all truth file contents."""
        return dict(self._data)

    # ── Delta (validated write) ──────────────────────────────────

    def apply_delta(self, chapter: int, delta: dict[str, Any]) -> Delta:
        """Apply a validated delta to truth files.

        Args:
            chapter: The chapter this delta belongs to.
            delta: {file_name: {field: new_value}} or {file_name: {"_markdown": content}}

        Returns:
            The applied Delta object.

        Raises:
            ValueError: If delta contains invalid truth file names or data.
        """
        # Validate file names
        valid_names = {tf.value for tf in TruthFile}
        for name in delta:
            if name not in valid_names:
                raise ValueError(f"Invalid truth file: {name}. Valid: {valid_names}")

        # Validate data types
        for name, changes in delta.items():
            if not isinstance(changes, dict):
                raise ValueError(f"Delta for {name} must be a dict, got {type(changes)}")

        # Deep copy current state for rollback
        copy.deepcopy(self._data)

        # Apply changes (merge, not replace)
        for name, changes in delta.items():
            if name not in self._data:
                self._data[name] = {}
            if "_markdown" in changes:
                # Full markdown replacement
                self._data[name] = {"_markdown": changes["_markdown"]}
            else:
                self._data[name].update(changes)

        d = Delta(chapter=chapter, changes=delta)
        logger.info(f"Applied delta for chapter {chapter}: {list(delta.keys())}")
        return d

    # ── Snapshots ────────────────────────────────────────────────

    def snapshot(self, chapter: int) -> Snapshot:
        """Create a snapshot of all truth files for a given chapter."""
        snap = Snapshot(
            chapter=chapter,
            data=copy.deepcopy(self._data),
        )
        self._snapshots[chapter] = snap

        # Persist to disk
        snap_dir = self.snapshot_dir / str(chapter)
        snap_dir.mkdir(parents=True, exist_ok=True)
        for tf in TruthFile:
            if tf.value in self._data:
                (snap_dir / f"{tf.value}.json").write_text(
                    json.dumps(self._data[tf.value], ensure_ascii=False, indent=2),
                    "utf-8",
                )

        logger.info(f"Created snapshot for chapter {chapter}")
        return snap

    def _load_snapshot_index(self) -> None:
        """Load existing snapshots from disk."""
        if not self.snapshot_dir.exists():
            return
        for d in self.snapshot_dir.iterdir():
            if d.is_dir() and d.name.isdigit():
                ch = int(d.name)
                data = {}
                for tf in TruthFile:
                    p = d / f"{tf.value}.json"
                    if p.exists():
                        data[tf.value] = json.loads(p.read_text("utf-8"))
                if data:
                    self._snapshots[ch] = Snapshot(chapter=ch, data=data)

    def rollback(self, chapter: int) -> None:
        """Rollback truth files to a specific chapter snapshot."""
        if chapter not in self._snapshots:
            raise ValueError(f"No snapshot for chapter {chapter}. Available: {list(self._snapshots.keys())}")

        snap = self._snapshots[chapter]
        self._data = copy.deepcopy(snap.data)

        # Delete later snapshots
        for ch in list(self._snapshots.keys()):
            if ch > chapter:
                del self._snapshots[ch]
                snap_dir = self.snapshot_dir / str(ch)
                if snap_dir.exists():
                    for f in snap_dir.glob("*.json"):
                        f.unlink()

        self.save()
        logger.info(f"Rolled back to chapter {chapter}")

    def list_snapshots(self) -> list[int]:
        """List available snapshot chapter numbers."""
        return sorted(self._snapshots.keys())

    # ── Context compilation ──────────────────────────────────────

    def context_for_chapter(self, chapter: int, max_chars: int = 4000) -> str:
        """Compile relevant truth file context for the writer agent.

        Selects the most relevant parts based on chapter number,
        keeping total under max_chars.
        """
        parts = []

        # Always include current_state (most important)
        cs = self.get(TruthFile.CURRENT_STATE)
        if cs:
            parts.append(f"## 当前世界状态\n{self._format_for_context(cs)}")

        # Character matrix — always relevant
        cm = self.get(TruthFile.CHARACTER_MATRIX)
        if cm:
            parts.append(f"## 角色矩阵\n{self._format_for_context(cm)}")

        # Pending hooks — check for active hooks
        ph = self.get(TruthFile.PENDING_HOOKS)
        if ph and "hooks" in ph:
            active = [h for h in ph["hooks"]
                      if h.get("status") in ("open", "progressing")]
            if active:
                parts.append(f"## 活跃伏笔\n{json.dumps(active, ensure_ascii=False, indent=2)}")

        # Chapter summaries — last 3 chapters
        cs_sum = self.get(TruthFile.CHAPTER_SUMMARIES)
        if cs_sum and "rows" in cs_sum:
            recent = cs_sum["rows"][-3:]
            parts.append(f"## 近期章节摘要\n{json.dumps(recent, ensure_ascii=False, indent=2)}")

        # Resource ledger
        pl = self.get(TruthFile.PARTICLE_LEDGER)
        if pl:
            parts.append(f"## 资源账本\n{self._format_for_context(pl)}")

        # Emotional arcs
        ea = self.get(TruthFile.EMOTIONAL_ARCS)
        if ea:
            parts.append(f"## 情感弧线\n{self._format_for_context(ea)}")

        # Subplot board
        sb = self.get(TruthFile.SUBPLOT_BOARD)
        if sb and "subplots" in sb:
            active_subs = [s for s in sb["subplots"]
                           if s.get("status") != "resolved"]
            if active_subs:
                parts.append(f"## 活跃支线\n{json.dumps(active_subs, ensure_ascii=False, indent=2)}")

        result = "\n\n".join(parts)

        # Truncate if too long
        if len(result) > max_chars:
            result = result[:max_chars] + "\n\n[上下文已截断]"

        return result

    def _format_for_context(self, data: dict) -> str:
        """Format truth file data for inclusion in agent context."""
        if "_markdown" in data:
            return data["_markdown"]
        return json.dumps(data, ensure_ascii=False, indent=2)
