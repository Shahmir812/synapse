import pytest


@pytest.fixture(autouse=True)
def isolated_project_registry(monkeypatch, tmp_path):
    monkeypatch.setenv("SYNAPSE_MEMORY_DB", str(tmp_path / "memory.sqlite3"))
    monkeypatch.delenv("HONCHO_API_KEY", raising=False)
    monkeypatch.delenv("HONCHO_URL", raising=False)
    monkeypatch.setenv("SYNAPSE_CORE_DIR", str(tmp_path / "core"))
    monkeypatch.setenv("SYNAPSE_PROJECTS_FILE", str(tmp_path / "registry" / "projects.json"))


@pytest.fixture(autouse=True)
def existing_ask_tests_use_temporary_memory(monkeypatch):
    from unittest.mock import AsyncMock
    from synapse.memory import open_conversation
    monkeypatch.setattr('tui.ask.choose_conversation', AsyncMock(return_value=open_conversation(temporary=True)))
