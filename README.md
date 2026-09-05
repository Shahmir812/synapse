# synapse

Synapse is a Python CLI for building an AI coding-agent workflow.

Synapse starts with a runnable command-line spine and local project profiles.
Project profiles establish the workspace and memory namespace that later agent
modes will use.

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

Initialize the current folder as a Synapse project:

```bash
python -m synapse project init
```

Inspect or list saved profiles:

```bash
python -m synapse project status
python -m synapse project list
```

Project state is stored locally in `.synapse/projects.json` and is not committed.

## Test

```bash
python -m pytest -q
```
