"""Persistent project selection shared by CLI and TUI, ready for project memory."""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, replace
from pathlib import Path

from synapse.projects import (
    ProjectError, ProjectProfile, ProjectState, _new_profile, _timestamp,
    active_project, read_project_state,
)


def registry_path() -> Path:
    override = os.environ.get('SYNAPSE_PROJECTS_FILE')
    return Path(override).expanduser() if override else Path.home() / '.synapse' / 'projects.json'


def load_registry() -> ProjectState:
    path = registry_path()
    if not path.exists():
        return ProjectState('', [])
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
        profiles = [ProjectProfile(**p) for p in payload['profiles']]
        active = payload['active_project_id']
        ids = set()
        roots = set()
        namespaces = set()
        for p in profiles:
            if not all(isinstance(v, str) and v.strip() for v in
                       (p.id, p.name, p.workspace_root, p.memory_namespace, p.created_at, p.last_used_at)):
                raise ValueError()
            if not Path(p.workspace_root).is_absolute() or type(p.memory_revision) is not int or p.memory_revision < 0:
                raise ValueError()
            root = str(Path(p.workspace_root).resolve())
            if p.id in ids or root in roots or p.memory_namespace in namespaces:
                raise ValueError()
            ids.add(p.id)
            roots.add(root)
            namespaces.add(p.memory_namespace)
        if (profiles and active not in ids) or (not profiles and active != ''):
            raise ValueError()
        return ProjectState(active, profiles)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ProjectError(f'Cannot read project registry: {path}. Check or restore the file.') from error


def save_registry(state: ProjectState, *, removed_workspaces: list[str] | None = None) -> None:
    path = registry_path()
    temporary = None
    try:
        if removed_workspaces is None:
            removed_workspaces = json.loads(path.read_text()).get('removed_workspaces', []) if path.exists() else []
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            json.dump({'active_project_id': state.active_project_id, 'removed_workspaces': removed_workspaces,
                       'profiles': [asdict(p) for p in state.profiles]}, file, indent=2)
            file.write('\n')
        temporary.replace(path)
    except OSError as error:
        raise ProjectError(f'Cannot save project registry: {path}.') from error
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def _workspace(path: Path) -> Path:
    path = path.expanduser().resolve()
    if not path.is_dir():
        raise ProjectError(f'Workspace directory does not exist: {path}')
    return path


def register_project(path: Path, name: str | None = None, *, activate: bool = True) -> ProjectState:
    root = _workspace(path)
    if name is not None and not name.strip():
        raise ProjectError('Project name must not be empty.')
    state = load_registry()
    if any(Path(p.workspace_root).resolve() == root for p in state.profiles):
        raise ProjectError(f'Workspace already registered: {root}. Select its existing project.')
    legacy = root / '.synapse' / 'projects.json'
    if legacy.exists() and legacy.resolve() != registry_path().resolve():
        try:
            imported = read_project_state(root)
        except (OSError, ValueError, TypeError, AttributeError) as error:
            raise ProjectError(f'Cannot import legacy profiles from {legacy}.') from error
        profile = active_project(imported)
        # Import every legacy profile; reject ambiguity instead of discarding identities.
        profiles = imported.profiles
        if any(not Path(p.workspace_root).is_dir() for p in profiles):
            raise ProjectError('A legacy project workspace is missing. Restore it before importing.')
    else:
        profile = _new_profile(root, name)
        profiles = [profile]
    for p in profiles:
        if any(old.id == p.id or old.memory_namespace == p.memory_namespace
               or Path(old.workspace_root).resolve() == Path(p.workspace_root).resolve()
               for old in state.profiles):
            raise ProjectError('Legacy project conflicts with an existing registry entry.')
    combined = state.profiles + profiles
    if (len({str(Path(p.workspace_root).resolve()) for p in combined}) != len(combined)
            or len({p.id for p in combined}) != len(combined)
            or len({p.memory_namespace for p in combined}) != len(combined)):
        raise ProjectError('Legacy profiles have conflicting workspaces or identities.')
    if name is not None:
        combined = [replace(p, name=name.strip()) if p.id == profile.id else p for p in combined]
    selected = profile.id if activate or not state.profiles else state.active_project_id
    result = ProjectState(selected, combined)
    save_registry(result)
    return result


def import_current_project(path: Path | None = None) -> None:
    root = (path or Path.cwd()).resolve()
    state = load_registry()
    removed = json.loads(registry_path().read_text()).get('removed_workspaces', []) if registry_path().exists() else []
    if str(root) in removed:
        return
    if (root / '.synapse' / 'projects.json').exists() and not any(Path(p.workspace_root).resolve() == root for p in state.profiles):
        register_project(root, activate=False)


def switch_project(project_id: str) -> ProjectState:
    state = load_registry()
    match = next((p for p in state.profiles if p.id == project_id), None)
    if match is None:
        raise ProjectError(f'Unknown project: {project_id}')
    _workspace(Path(match.workspace_root))
    result = ProjectState(project_id, [replace(p, last_used_at=_timestamp()) if p.id == project_id else p for p in state.profiles])
    save_registry(result)
    return result


def rename_project(name: str) -> ProjectState:
    if not name.strip():
        raise ProjectError('Project name must not be empty.')
    state = load_registry()
    if not state.profiles:
        raise ProjectError('No active project. Create a project first.')
    result = ProjectState(state.active_project_id, [replace(p, name=name.strip()) if p.id == state.active_project_id else p for p in state.profiles])
    save_registry(result)
    return result


def current_project() -> ProjectProfile | None:
    state = load_registry()
    return active_project(state) if state.profiles else None


def active_workspace() -> Path:
    project = current_project()
    return _workspace(Path(project.workspace_root)) if project else Path.cwd().resolve()


def projects_directory() -> Path:
    core = Path(os.environ.get('SYNAPSE_CORE_DIR') or Path(__file__).resolve().parents[1]).expanduser().resolve()
    directory = core / 'Projects'
    if directory.is_symlink():
        raise ProjectError('Projects directory must not be a symlink.')
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ProjectError(f'Cannot create projects directory: {directory}') from error
    return directory


def create_project(name: str) -> ProjectState:
    name = name.strip()
    if not name or any(not (c.isalnum() or c.isspace() or c in '._-') for c in name):
        raise ProjectError('Use letters, numbers, spaces, dots, underscores, or hyphens in project names.')
    folder = '_'.join(name.split())
    if folder.startswith('.') or not any(c.isalnum() for c in folder):
        raise ProjectError('Project names must contain a letter or number and cannot start with a dot.')
    root = projects_directory() / folder
    state = load_registry()
    if any(Path(p.workspace_root).resolve() == root for p in state.profiles):
        raise ProjectError(f'Workspace already registered: {root}')
    if root.exists() or root.is_symlink():
        raise ProjectError(f'Folder already exists: {root}. Register it with project init, or choose another name.')
    try:
        root.mkdir()
    except OSError as error:
        raise ProjectError(f'Cannot create project folder: {root}') from error
    return register_project(root, name)


def remove_project(project_id: str) -> ProjectState:
    state = load_registry()
    project = next((p for p in state.profiles if p.id == project_id), None)
    if project is None:
        raise ProjectError(f'Unknown project: {project_id}')
    remaining = [p for p in state.profiles if p.id != project_id]
    selected = state.active_project_id
    if selected == project_id:
        selected = next((p.id for p in remaining if Path(p.workspace_root).is_dir()), '')
        if not selected and remaining:
            selected = remaining[0].id
    removed = json.loads(registry_path().read_text()).get('removed_workspaces', [])
    removed = sorted(set(removed + [str(Path(project.workspace_root).resolve())]))
    result = ProjectState(selected, remaining)
    save_registry(result, removed_workspaces=removed)
    return result
