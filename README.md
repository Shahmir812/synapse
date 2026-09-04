# synapse

Synapse is a Python CLI for building an AI coding-agent workflow.

The first milestone is intentionally small: a runnable command-line spine that
can load local environment settings and launch a wakeup screen.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and fill in local keys as features need them.

## Run

```bash
python -m synapse wakeup
```

## Test

```bash
python -m pytest -q
```
