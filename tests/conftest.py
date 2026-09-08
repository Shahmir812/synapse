import pytest


@pytest.fixture(autouse=True)
def isolated_project_registry(monkeypatch, tmp_path):
    monkeypatch.setenv("SYNAPSE_PROJECTS_FILE", str(tmp_path / "registry" / "projects.json"))
