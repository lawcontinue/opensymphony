"""Soul — Agent personality and thinking framework."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Soul:
    """Defines how an Agent thinks, not what it does."""

    id: str
    name: str
    archetype: str = ""
    thinking_framework: str = ""
    communication_style: str = ""
    values: list[str] = field(default_factory=list)
    veto_conditions: list[str] = field(default_factory=list)
    tools_whitelist: list[str] = field(default_factory=list)
    ambiguity_strategy: str = "balanced"  # conservative | aggressive | balanced
    extra: dict[str, Any] = field(default_factory=dict)


def load_soul_from_text(path: Path) -> Soul:
    """Load a soul from a plain text file (legacy format)."""
    text = path.read_text(encoding="utf-8").strip()
    name = path.stem
    return Soul(
        id=name,
        name=name.replace("_", " ").title(),
        thinking_framework=text,
    )


def load_soul_from_yaml(path: Path) -> Soul:
    """Load a soul from a YAML file (new format)."""
    try:
        import yaml
    except ImportError:
        # Fallback: parse minimal YAML manually
        return _parse_yaml_minimal(path)

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Soul(
        id=data.get("id", path.stem),
        name=data.get("name", path.stem),
        archetype=data.get("archetype", ""),
        thinking_framework=data.get("thinking_framework", ""),
        communication_style=data.get("communication_style", ""),
        values=data.get("values", []),
        veto_conditions=data.get("veto_conditions", []),
        tools_whitelist=data.get("tools_whitelist", []),
        ambiguity_strategy=data.get("ambiguity_strategy", "balanced"),
        extra={k: v for k, v in data.items()
               if k not in {"id", "name", "archetype", "thinking_framework",
                            "communication_style", "values", "veto_conditions",
                            "tools_whitelist", "ambiguity_strategy"}},
    )


def _parse_yaml_minimal(path: Path) -> Soul:
    """Minimal YAML parser for soul files when PyYAML is not installed."""
    data: dict[str, Any] = {}
    current_key = None
    current_list: list[str] = []

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line.startswith("  - ") and current_key:
            current_list.append(stripped[2:])
        elif ":" in line and not line.startswith(" "):
            if current_key and current_list:
                data[current_key] = current_list
                current_list = []
            key, _, val = stripped.partition(":")
            current_key = key.strip()
            val = val.strip()
            if val:
                # Handle inline list like [a, b, c]
                if val.startswith("[") and val.endswith("]"):
                    items = [v.strip().strip("'\"") for v in val[1:-1].split(",") if v.strip()]
                    data[current_key] = items
                    current_key = None
                    current_list = []
                else:
                    data[current_key] = val
                    current_key = None
                    current_list = []
    if current_key and current_list:
        data[current_key] = current_list

    return Soul(
        id=data.get("id", path.stem),
        name=data.get("name", path.stem),
        archetype=data.get("archetype", ""),
        thinking_framework=data.get("thinking_framework", ""),
        communication_style=data.get("communication_style", ""),
        values=data.get("values", []) if isinstance(data.get("values"), list) else [],
        veto_conditions=data.get("veto_conditions", []) if isinstance(data.get("veto_conditions"), list) else [],
        tools_whitelist=data.get("tools_whitelist", []) if isinstance(data.get("tools_whitelist"), list) else [],
        ambiguity_strategy=data.get("ambiguity_strategy", "balanced"),
    )


def load_soul(path: Path) -> Soul:
    """Load soul from .yaml or .txt file."""
    if path.suffix in (".yaml", ".yml"):
        return load_soul_from_yaml(path)
    return load_soul_from_text(path)


def load_souls_dir(directory: Path) -> dict[str, Soul]:
    """Load all soul files from a directory."""
    souls: dict[str, Soul] = {}
    for f in sorted(directory.iterdir()):
        if f.suffix in (".yaml", ".yml", ".txt"):
            soul = load_soul(f)
            souls[soul.id] = soul
    return souls
