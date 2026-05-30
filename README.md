# OpenSymphony

> Let your agents have character, follow rules, and grow.

An open-source AI agent framework with **soul**, **governance**, and **self-evolution**. Designed to run on consumer hardware.

[![PyPI](https://img.shields.io/pypi/v/opensymphony)](https://pypi.org/project/opensymphony/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-377%20passing-green)](tests/)

## Why OpenSymphony?

Most agent frameworks focus on **orchestration** — chaining API calls. OpenSymphony focuses on **who your agents are** and **how they behave**:

- 🎭 **Soul** — Define agent personalities as YAML. Not prompts — persistent behavioral frameworks.
- ⚖️ **Governance** — Voting, precedent, defense. Agents make collective decisions, not chaos.
- 🔧 **Self-evolution** — Agents build their own tools at runtime.

## Architecture

```
Request → Gateway → [Intent Bridge] → [Governance] → Runtime → Kernel → Response
                                              ↑
                                    Voting / Precedent / Defense
```

**Onion model**: every request passes through governance layers before execution.

```
┌─────────────────────────────────────────────────┐
│  Gateway (HTTP / WebSocket / CLI)                │
│    └─ HumanAdapter — Intent Bridge (NL→struct)   │
├─────────────────────────────────────────────────┤
│  Governance                                      │
│    ├─ VotingMechanism — multi-agent decisions     │
│    ├─ PrecedentStore — reusable past decisions    │
│    ├─ DefenseLayer — risk assessment              │
│    └─ HITLManager — human-in-the-loop             │
├─────────────────────────────────────────────────┤
│  Runtime                                         │
│    ├─ AgentPool — concurrent agent management     │
│    ├─ TaskScheduler — priority queue              │
│    └─ AgentSandbox — resource limits              │
├─────────────────────────────────────────────────┤
│  Kernel                                          │
│    ├─ Soul Compiler — YAML → behavioral rules     │
│    ├─ LLM Router — cloud + local providers        │
│    ├─ Memory (L1/L2/L3) — three-tier storage      │
│    └─ Tool Workshop — agents create tools         │
└─────────────────────────────────────────────────┘
```

## Quick Start

```bash
# Install
pip install opensymphony

# Or from source
git clone https://github.com/lawcontinue/opensymphony.git
cd opensymphony
pip install -e ".[dev]"

# Run tests (377 passing)
pytest

# Start the API server
python -m opensymphony.gateway.http
```

### Example: Define a Soul

```yaml
# souls/my_agent.yaml
id: my_agent
name: MyAgent
archetype: Code Reviewer

thinking_framework: |
  You are a code reviewer focused on security and correctness.
  Rules:
  1. Flag any unvalidated user input
  2. Check for race conditions in concurrent code
  3. Prefer readability over cleverness

values:
  - Security first
  - Evidence-based review
  - Constructive feedback
```

### Example: Use the API

```python
from opensymphony.agents.soul import Soul
from opensymphony.agents.soul_compiler import compile_soul
from opensymphony.kernel import SymphonyKernel

# Load a soul
soul = Soul.from_yaml("souls/my_agent.yaml")
prompt = compile_soul(soul, output_mode="agent")

# Create kernel with governance
kernel = SymphonyKernel()
kernel.load_souls("souls/")

# Chat with an agent
response = kernel.chat("my_agent", "Review this function for security issues...")
print(response)
```

## Core Concepts

### 🎭 Soul System

Agents are defined by YAML soul files with identity, thinking framework, values, and veto conditions. Souls compile into behavioral constraints that persist across conversations.

**13 built-in souls**: themis, athena, crit, shield, code, novelist, screenwriter, tech_blogger, legal_writer, social_copy, drama_director, reflector, default.

### ⚖️ Governance Layer

| Mechanism | Description |
|-----------|-------------|
| **Voting** | Multi-agent voting with configurable timeout and majority rules |
| **Precedent** | Past decisions become searchable, reusable precedents |
| **Defense** | Risk assessment classifies actions (safe/risky/dangerous) |
| **HITL** | Human-in-the-loop confirmation for high-risk operations |

### 🔧 Tool Workshop

Agents can create, test, and deploy their own Python tools at runtime — no human intervention needed.

### 💾 Three-Tier Memory

| Tier | Storage | Use Case |
|------|---------|----------|
| L1 | In-memory | Current conversation context |
| L2 | SQLite | Experience database with search |
| L3 | Cloud API | Long-term persistent memory |

## Requirements

- Python 3.11+
- Optional: 16GB+ RAM for local LLM inference
- Optional: NVIDIA GPU for local models

## Project Structure

```
opensymphony/
├── agents/            # Agent core + Soul compiler
├── apps/              # Application modules (novel pipeline, content factory)
├── gateway/           # HTTP + WebSocket + Human adapters
├── governance/        # Voting, precedent, defense, HITL
├── kernel.py          # Core orchestration
├── llm/               # LLM router (cloud + local)
├── memory/            # Three-tier memory (L1/L2/L3)
├── pipeline.py        # Declarative pipeline engine
├── runtime/           # Agent pool + scheduler + sandbox
├── session.py         # Session management
├── skill_registry.py  # Dynamic skill loading
├── telemetry.py       # Observability
└── tools/             # Production tools + workshop
```

## License

MIT
