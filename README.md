# OpenSymphony

> Let your agents have character, follow rules, and grow. An open-source AI agent framework with soul, governance, and self-evolution.

Symphony is a local-first AI agent framework built around three pillars:

- **Soul** — Define agent personalities as YAML, not just tools. Same framework, different minds.
- **Governance** — Voting, precedent, and defense layers. Agents make collective decisions, not chaos.
- **Self-evolution** — Agents can build their own tools through the tool workshop.

Designed to run on consumer hardware (tested on Mac Mini M4 + RTX 5060Ti 16GB).

## Architecture

```
Request → Gateway → [Intent Bridge] → [Governance] → Runtime → Kernel → Response
                                              ↑
                                    Voting / Precedent / Defense
```

**Onion model**: every request passes through governance layers before execution.

## Quick Start

```bash
# Install
pip install opensymphony

# Run tests
pytest

# Start the server
python -m opensymphony.main
```

## Core Concepts

### Soul System

Agents are defined by YAML soul files:

```yaml
name: themis
role: "Foresight Architect"
mission: "Foresee risks before opportunities"
rules:
  - "Risk before reward"
  - "Measure before trusting"
  - "Consult Crit before major decisions"
```

13 built-in souls included in `souls/`.

### Governance Layer

- **Voting**: Agents vote on decisions with configurable timeout
- **Precedent**: Past decisions become reusable precedents
- **Defense**: Risk assessment before dangerous operations
- **HITL**: Human-in-the-loop for high-risk actions

### Tool Workshop

Agents can create their own tools at runtime — write code, test it, and deploy it without human intervention.

## Project Structure

```
symphony/
├── agents/        # Agent core + Soul compiler
├── apps/          # Application modules (novel, factory)
├── gateway/       # HTTP + Human adapters
├── governance/    # Voting, precedent, defense, HITL
├── kernel.py      # Core orchestration
├── llm/           # LLM router (cloud + local)
├── memory/        # Three-tier memory (L1/L2/L3)
├── pipeline.py    # Declarative pipeline engine
├── runtime/       # Agent pool + scheduler + sandbox
├── session.py     # Session management
├── skill_registry.py  # Dynamic skill loading
├── telemetry.py   # Observability
└── tools/         # Production + workshop tools
```

## Requirements

- Python 3.11+
- 16GB+ RAM (for local inference)
- Optional: NVIDIA GPU for local LLM

## License

MIT
