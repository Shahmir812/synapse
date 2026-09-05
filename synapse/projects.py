from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

PROJECT_DIRECTORY = ".synapse"
PROJECT_FILE = "projects.json"


class ProjectError(Exception):
    """Raised when Synapse project state cannot be read or updated."""


@dataclass(frozen=True)
class ProjectProfile:
    id: str
    name: str
    workspace_root: str
    memory_namespace: str
    memory_revision: int
    created_at: str
    last_used_at: str


@dataclass(frozen=True)
class ProjectState:
    active_project_id: str
    profiles: list[ProjectProfile]


def _state_path(workspace_root: Path) -> Path:
    return workspace_root / PROJECT_DIRECTORY / PROJECT_FILE


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _slug(value: str) -> str:
    normalized = "".join(
        character if character.isalnum() else "-" for character in value.lower().strip()
    )
    return normalized.strip("-") or "workspace"


def _new_profile(workspace_root: Path, name: str | None = None) -> ProjectProfile:
    display_name = name.strip() if name else workspace_root.name
    profile_id = f"{_slug(display_name)}-{hashlib.sha1(str(workspace_root).encode()).hexdigest()[:8]}"
    created_at = _timestamp()
    return ProjectProfile(
        id=profile_id,
        name=display_name,
        workspace_root=str(workspace_root),
        memory_namespace=f"project:{profile_id}",
        memory_revision=0,
        created_at=created_at,
        last_used_at=created_at,
    )


def initialize_project(workspace_root: Path, name: str | None = None) -> ProjectState:
    workspace_root = workspace_root.expanduser().resolve()
    if not workspace_root.is_dir():
        raise ProjectError(f"Directory does not exist: {workspace_root}")
    if _state_path(workspace_root).exists():
        raise ProjectError(f"Synapse project already exists: {_state_path(workspace_root)}")

    profile = _new_profile(workspace_root, name)
    state = ProjectState(active_project_id=profile.id, profiles=[profile])
    save_project_state(workspace_root, state)
    return state


def read_project_state(workspace_root: Path) -> ProjectState:
    workspace_root = workspace_root.expanduser().resolve()
    state_path = _state_path(workspace_root)
    if not state_path.exists():
        raise ProjectError(f"No Synapse project found in {workspace_root}. Run 'synapse project init'.")

    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        profiles = [ProjectProfile(**profile) for profile in payload["profiles"]]
        active_project_id = str(payload["active_project_id"])
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ProjectError(f"Invalid project state: {state_path}") from error

    if not profiles or active_project_id not in {profile.id for profile in profiles}:
        raise ProjectError(f"Invalid project state: {state_path}")
    return ProjectState(active_project_id=active_project_id, profiles=profiles)


def save_project_state(workspace_root: Path, state: ProjectState) -> None:
    state_path = _state_path(workspace_root)
    state_path.parent.mkdir(exist_ok=True)
    payload = {
        "active_project_id": state.active_project_id,
        "profiles": [asdict(profile) for profile in state.profiles],
    }
    state_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def active_project(state: ProjectState) -> ProjectProfile:
    return next(profile for profile in state.profiles if profile.id == state.active_project_id)


def select_project(state: ProjectState, project_id: str) -> ProjectState:
    if project_id not in {profile.id for profile in state.profiles}:
        raise ProjectError(f"Unknown project: {project_id}")
    return ProjectState(active_project_id=project_id, profiles=state.profiles)
