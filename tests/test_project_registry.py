from dataclasses import asdict
from io import StringIO
from pathlib import Path
import asyncio
import json
import subprocess
import sys
from unittest.mock import AsyncMock

import pytest
from rich.console import Console

from synapse import project_registry as registry
from synapse.projects import ProjectError, initialize_project, active_project
from synapse.file_context import FileContextError
from synapse.ask import ask_question
from tui import projects


def workspace(tmp_path, name):
    path = tmp_path / name
    path.mkdir()
    return path


def test_create_switch_rename_and_restart(tmp_path):
    a, b = workspace(tmp_path, 'a'), workspace(tmp_path, 'b')
    first = active_project(registry.register_project(a, 'First'))
    second = active_project(registry.register_project(b, 'Second'))
    assert first.memory_namespace != second.memory_namespace
    assert registry.active_workspace() == b
    registry.switch_project(first.id)
    before = registry.current_project()
    registry.rename_project('Renamed')
    after = registry.current_project()
    assert after.name == 'Renamed'
    for field in ('id', 'memory_namespace', 'memory_revision', 'created_at', 'workspace_root'):
        assert getattr(before, field) == getattr(after, field)
    result = subprocess.run([sys.executable, '-c', 'from synapse.project_registry import current_project; print(current_project().name)'], capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout.strip() == 'Renamed'


def test_import_preserves_all_identity_fields(tmp_path, monkeypatch):
    root = workspace(tmp_path, 'legacy')
    original = active_project(initialize_project(root, 'Legacy'))
    monkeypatch.chdir(root)
    registry.import_current_project()
    assert asdict(registry.current_project()) == asdict(original)
    registry.import_current_project()
    assert len(registry.load_registry().profiles) == 1
    assert (root / '.synapse' / 'projects.json').exists()


def test_import_does_not_switch_existing_selection(tmp_path, monkeypatch):
    a, b = workspace(tmp_path, 'a'), workspace(tmp_path, 'b')
    original = active_project(registry.register_project(a))
    initialize_project(b)
    monkeypatch.chdir(b)
    registry.import_current_project()
    assert registry.current_project().id == original.id
    assert len(registry.load_registry().profiles) == 2


def test_duplicate_workspace_and_blank_name(tmp_path):
    root = workspace(tmp_path, 'a')
    registry.register_project(root)
    with pytest.raises(ProjectError, match='already registered'):
        registry.register_project(root / '.')
    with pytest.raises(ProjectError, match='empty'):
        registry.rename_project(' ')
    with pytest.raises(ProjectError, match='empty'):
        registry.register_project(workspace(tmp_path, 'b'), ' ')


def test_missing_workspace_does_not_change_selection(tmp_path):
    a, b = workspace(tmp_path, 'a'), workspace(tmp_path, 'b')
    first = active_project(registry.register_project(a))
    second = active_project(registry.register_project(b))
    a.rmdir()
    with pytest.raises(ProjectError, match='does not exist'):
        registry.switch_project(first.id)
    assert registry.current_project().id == second.id
    b.rmdir()
    with pytest.raises(ProjectError, match='does not exist'):
        registry.active_workspace()
    with pytest.raises(ProjectError, match='Unknown'):
        registry.switch_project('unknown')


@pytest.mark.parametrize('payload', ['broken', '{}', '{"profiles": [], "active_project_id": "bad"}'])
def test_corrupt_registry_is_not_overwritten(tmp_path, payload):
    path = registry.registry_path()
    path.parent.mkdir()
    path.write_text(payload)
    with pytest.raises(ProjectError, match='Cannot read'):
        registry.register_project(workspace(tmp_path, 'a'))
    assert path.read_text() == payload


def test_ask_uses_active_workspace_and_blocks_other_project(tmp_path, monkeypatch):
    from synapse import ask
    a, b = workspace(tmp_path, 'a'), workspace(tmp_path, 'b')
    (a / 'module.py').write_text('PROJECT_A')
    (b / 'module.py').write_text('PROJECT_B')
    first = active_project(registry.register_project(a))
    registry.register_project(b)
    monkeypatch.chdir(a)
    calls = []
    def request(question, *args):
        calls.append(question)
        return 'Answer'
    monkeypatch.setattr(ask, '_request', request)
    ask_question('Explain', files=['module.py'])
    assert 'PROJECT_B' in calls[-1] and 'PROJECT_A' not in calls[-1]
    with pytest.raises(FileContextError):
        ask_question('Explain', files=[a / 'module.py'])
    assert len(calls) == 1
    registry.switch_project(first.id)
    ask_question('Explain', files=['module.py'])
    assert 'PROJECT_A' in calls[-1]


def prompt(monkeypatch, method, value):
    mock = AsyncMock()
    mock.ask_async.return_value = value
    monkeypatch.setattr(projects.questionary, method, lambda *args, **kwargs: mock)


def test_tui_create_rename_details_and_switch(tmp_path, monkeypatch):
    a, b = workspace(tmp_path, 'a'), workspace(tmp_path, 'b')
    output = StringIO()
    console = Console(file=output)
    prompt(monkeypatch, 'text', 'First')
    prompt(monkeypatch, 'path', str(a))
    asyncio.run(projects.manage_project('create', console))
    first = registry.current_project()
    registry.register_project(b, 'Second')
    prompt(monkeypatch, 'select', first.id)
    asyncio.run(projects.manage_project('switch', console))
    prompt(monkeypatch, 'text', 'Renamed')
    asyncio.run(projects.manage_project('rename', console))
    asyncio.run(projects.manage_project('details', console))
    assert registry.current_project().name == 'Renamed'
    assert first.memory_namespace in output.getvalue()
    assert 'Honcho memory is scoped to this project' in output.getvalue()


def test_tui_cancel_create_no_write(monkeypatch):
    prompt(monkeypatch, 'text', None)
    asyncio.run(projects.manage_project('create', Console(file=StringIO())))
    assert not registry.registry_path().exists()


def test_tui_picker_uses_selected_workspace(tmp_path, monkeypatch):
    from tui import ask
    a = workspace(tmp_path, 'a')
    registry.register_project(a)
    text = AsyncMock()
    text.ask_async.side_effect = ['Question', '/back']
    monkeypatch.setattr(ask.questionary, 'text', lambda *args, **kwargs: text)
    confirm = AsyncMock()
    confirm.ask_async.return_value = True
    monkeypatch.setattr(ask.questionary, 'confirm', lambda *args, **kwargs: confirm)
    seen = []
    def candidates(root):
        seen.append(root)
        return [], False
    monkeypatch.setattr(ask, 'selectable_files', candidates)
    asyncio.run(ask.run_ask_mode(Console(file=StringIO())))
    assert seen == [a]


def test_failed_save_preserves_previous_registry(tmp_path, monkeypatch):
    root = workspace(tmp_path, 'a')
    registry.register_project(root, 'Original')
    path = registry.registry_path()
    before = path.read_bytes()
    def denied(*args, **kwargs):
        raise PermissionError('denied')
    monkeypatch.setattr(Path, 'replace', denied)
    with pytest.raises(ProjectError, match='Cannot save'):
        registry.rename_project('New')
    assert path.read_bytes() == before
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize('action, method', [('switch', 'select'), ('rename', 'text')])
def test_cancel_preserves_registry(tmp_path, monkeypatch, action, method):
    registry.register_project(workspace(tmp_path, 'a'), 'Original')
    before = registry.registry_path().read_bytes()
    prompt(monkeypatch, method, None)
    asyncio.run(projects.manage_project(action, Console(file=StringIO())))
    assert registry.registry_path().read_bytes() == before


def test_duplicate_symlink_workspace(tmp_path):
    root = workspace(tmp_path, 'a')
    registry.register_project(root)
    alias = tmp_path / 'alias'
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(ProjectError, match='already registered'):
        registry.register_project(alias)


def test_launcher_routes_project_management(monkeypatch):
    from tui import wakeup
    prompt_mock = AsyncMock()
    prompt_mock.ask_async.side_effect = ['manage', 'exit']
    monkeypatch.setattr(wakeup.questionary, 'select', lambda *args, **kwargs: prompt_mock)
    monkeypatch.setattr(wakeup, 'Console', lambda: Console(file=StringIO(), force_terminal=True))
    monkeypatch.setattr(wakeup, 'show_banner', AsyncMock())
    monkeypatch.setattr(wakeup, 'import_current_project', lambda: None)
    manager = AsyncMock()
    monkeypatch.setattr(wakeup, 'manage_projects', manager)
    asyncio.run(wakeup.run_wakeup())
    manager.assert_awaited_once()


@pytest.mark.parametrize('name, folder', [('My Project', 'My_Project'), ('My.Project', 'My.Project'), ('My_Project', 'My_Project'), ('  Two   Words ', 'Two_Words')])
def test_managed_project_folder(tmp_path, name, folder):
    state = registry.create_project(name)
    project = active_project(state)
    assert Path(project.workspace_root) == tmp_path / 'core' / 'Projects' / folder
    assert Path(project.workspace_root).is_dir()
    assert project.name == name.strip()


@pytest.mark.parametrize('name', ['', ' ', '../escape', '/tmp/escape', 'a/b', 'a\\b', '..', '.hidden', '---'])
def test_invalid_project_names(name):
    with pytest.raises(ProjectError):
        registry.create_project(name)


def test_existing_folder_not_overwritten():
    root = registry.projects_directory() / 'Existing'
    root.mkdir()
    (root / 'keep.txt').write_text('keep')
    with pytest.raises(ProjectError, match='already exists'):
        registry.create_project('Existing')
    assert (root / 'keep.txt').read_text() == 'keep'


def test_remove_active_then_last_keeps_files():
    first = active_project(registry.create_project('First'))
    second = active_project(registry.create_project('Second'))
    file = Path(second.workspace_root) / 'keep.txt'
    file.write_text('keep')
    registry.remove_project(second.id)
    assert registry.current_project().id == first.id
    assert file.read_text() == 'keep'
    registry.remove_project(first.id)
    assert registry.current_project() is None
    assert Path(first.workspace_root).is_dir()


def test_removed_legacy_not_reimported(tmp_path, monkeypatch):
    root = workspace(tmp_path, 'old')
    initialize_project(root)
    monkeypatch.chdir(root)
    registry.import_current_project()
    registry.remove_project(registry.current_project().id)
    registry.import_current_project()
    assert registry.load_registry().profiles == []
    assert (root / '.synapse' / 'projects.json').exists()
    registry.register_project(root)
    assert registry.current_project() is not None


@pytest.mark.parametrize('confirmed', [False, None, True])
def test_tui_delete_confirmation(monkeypatch, confirmed):
    project = active_project(registry.create_project('Keep Files'))
    prompt(monkeypatch, 'select', project.id)
    prompt(monkeypatch, 'confirm', confirmed)
    asyncio.run(projects.manage_project('delete', Console(file=StringIO())))
    assert Path(project.workspace_root).is_dir()
    assert (registry.current_project() is None) == (confirmed is True)


def test_submenu_returns(monkeypatch):
    selection = AsyncMock()
    selection.ask_async.side_effect = ['details', 'back']
    monkeypatch.setattr(projects.questionary, 'select', lambda *args, **kwargs: selection)
    handler = AsyncMock()
    monkeypatch.setattr(projects, 'manage_project', handler)
    console = Console(file=StringIO())
    asyncio.run(projects.manage_projects(console))
    handler.assert_awaited_once_with('details', console)
