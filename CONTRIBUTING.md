# Contributing to Symphony

## Development Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Running Tests

```bash
pytest                              # All tests
pytest tests/ --ignore=tests/test_mvp_intent_bridge.py  # Unit only (no LLM calls)
```

## Code Style

We use [Ruff](https://docs.astral.sh/ruff/):

```bash
ruff check .
ruff format .
```

## Pull Request Process

1. Fork → Branch → PR
2. All tests must pass
3. New features need tests
4. Keep it simple — 200→50 rule (if you can halve it, you should)
