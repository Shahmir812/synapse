from __future__ import annotations

import subprocess
import sys

import pytest

from synapse.projects import ProjectError, active_project, initialize_project, read_project_state


def test_initialize_project_writes_projects_json(tmp_path) -> None:
    state = initialize_project(tmp_path, name="Demo project")

    assert (tmp_path / ".synapse" / "projects.json").exists()
    assert active_project(read_project_state(tmp_path)).name == "Demo project"
    assert state.active_project_id == active_project(state).id


def test_initialize_project_rejects_an_existing_project(tmp_path) -> None:
    initialize_project(tmp_path)

    with pytest.raises(ProjectError, match="already exists"):
        initialize_project(tmp_path)


def test_project_status_command_reads_initialized_project(tmp_path) -> None:
    initialized = subprocess.run(
        [
            sys.executable,
            "-m",
            "synapse",
            "project",
            "init",
            str(tmp_path),
            "--name",
            "CLI demo",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    status = subprocess.run(
        [sys.executable, "-m", "synapse", "project", "status", str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert initialized.returncode == 0
    assert status.returncode == 0
    assert "CLI demo" in status.stdout
