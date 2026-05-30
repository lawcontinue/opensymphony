"""Symphony Content Factory — Batch content production with L2 autonomy.

Architecture:
  Task Queue (JSON, atomic write) → Pipeline (max 3 steps) → Quality Gate → Output

Quality tiers:
  S: Human-provided material, 85+ score, human review required
  A: Search-assisted, 75+ auto-pass
  B: Pure AI, 70+ auto-pass, stockpile only

L2 Autonomy:
  Whitelist (auto): write, audit, generate images, download, quality score
  Blacklist (need human): publish externally, delete files, modify config, spend over threshold
"""
