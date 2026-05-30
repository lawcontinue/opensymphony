"""SoulCompiler — compile Soul definitions into LLM system prompts."""

from __future__ import annotations

from .soul import Soul


def compile_soul(soul: Soul, output_mode: str = "agent") -> str:
    """Compile a Soul into a system prompt string.

    Args:
        soul: Soul definition.
        output_mode: "agent" for structured/technical output, "human" for
                     natural conversational output. Both share the same
                     identity, thinking framework, values and veto conditions
                     (behavioral consistency guarantee). Only communication
                     style differs.

    Structure:
    1. Identity (archetype) — shared
    2. Thinking framework (core) — shared
    3. Communication style — mode-dependent
    4. Values (as guiding principles) — shared
    5. Veto conditions (as hard prohibitions) — shared
    6. Output format — mode-dependent
    """
    parts: list[str] = []

    # 1. Identity — shared
    if soul.name or soul.archetype:
        identity = soul.archetype or soul.name
        parts.append(f"You are {soul.name}, {identity}.")

    # 2. Thinking framework (the core) — shared
    if soul.thinking_framework:
        parts.append(soul.thinking_framework)

    # 3. Communication style — mode-dependent
    if output_mode == "human":
        # Human mode: conversational, empathetic, natural
        if soul.communication_style:
            parts.append(
                f"You are talking to a human. Use your natural communication style: {soul.communication_style}. "
                "Be conversational, warm, and clear. Use natural language, not structured output."
            )
        else:
            parts.append(
                "You are talking to a human. Be conversational, warm, and clear. "
                "Use natural language, not structured output."
            )
        # Ambiguity strategy
        strategy = soul.extra.get("ambiguity_strategy", "balanced")
        if strategy == "conservative":
            parts.append(
                "When the user's request is unclear, always ask for clarification before acting. "
                "Never assume or guess the user's intent."
            )
        elif strategy == "aggressive":
            parts.append(
                "When the user's request is unclear, make your best guess and propose a solution. "
                "The user can always say 'no, I meant something else'."
            )
        else:  # balanced
            parts.append(
                "When the user's request is unclear, make a reasonable guess but mention your "
                "assumption so the user can correct you."
            )
    else:
        # Agent mode: structured, technical
        if soul.communication_style:
            parts.append(f"Communication style: {soul.communication_style}")

    # 4. Values — shared
    if soul.values:
        parts.append("Core values:")
        for v in soul.values:
            parts.append(f"- {v}")

    # 5. Veto conditions (hard rules) — shared
    if soul.veto_conditions:
        parts.append("Absolute prohibitions (never violate):")
        for vc in soul.veto_conditions:
            parts.append(f"- {vc}")

    return "\n\n".join(parts)


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English, ~2 for CJK."""
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other = len(text) - cjk
    return int(cjk / 2 + other / 4)


def compile_and_check(soul: Soul, max_tokens: int = 4000, output_mode: str = "agent") -> tuple[str, int]:
    """Compile soul and check token budget. Returns (prompt, token_count)."""
    prompt = compile_soul(soul, output_mode=output_mode)
    tokens = estimate_tokens(prompt)
    if tokens > max_tokens:
        # Truncate thinking framework proportionally
        budget = max_tokens - 500  # Reserve for other sections
        tf = soul.thinking_framework
        if len(tf) > budget * 2:
            soul_copy = Soul(
                id=soul.id, name=soul.name, archetype=soul.archetype,
                thinking_framework=tf[:budget * 2] + "\n\n[truncated for token budget]",
                communication_style=soul.communication_style,
                values=soul.values, veto_conditions=soul.veto_conditions,
                tools_whitelist=soul.tools_whitelist, extra=soul.extra,
            )
            prompt = compile_soul(soul_copy, output_mode=output_mode)
            tokens = estimate_tokens(prompt)
    return prompt, tokens
