import asyncio
from io import StringIO
from unittest.mock import AsyncMock

import pytest
from rich.console import Console

from synapse import project_registry as registry
from tui import projects


def prompts(monkeypatch, method, values):
    prompt = AsyncMock()
    prompt.ask_async.side_effect = values
    factory = lambda *args, **kwargs: prompt
    monkeypatch.setattr(projects.questionary, method, factory)
    return prompt


def workspace(tmp_path, name):
    root = tmp_path / name
    root.mkdir()
    return root


def test_open_existing_registers_exact_folder_without_creating_managed_copy(tmp_path, monkeypatch):
    root = workspace(tmp_path, 'Existing code')
    source = root / 'app.py'
    source.write_text('print("hello")\n')
    prompts(monkeypatch, 'path', [str(root)])
    output = StringIO()

    assert asyncio.run(projects.open_existing_project(Console(file=output, width=240)))

    assert registry.current_project().workspace_root == str(root.resolve())
    assert source.read_text() == 'print("hello")\n'
    assert not (tmp_path / 'core' / 'Projects').exists()
    assert str(root) in output.getvalue()


def test_open_registered_alias_reuses_project_and_memory_identity(tmp_path, monkeypatch):
    root = workspace(tmp_path, 'existing')
    registry.register_project(root, 'Original name')
    original = registry.current_project()
    registry.register_project(workspace(tmp_path, 'other'))
    alias = tmp_path / 'alias'
    alias.symlink_to(root, target_is_directory=True)
    prompts(monkeypatch, 'path', [str(alias)])

    assert asyncio.run(projects.open_existing_project(Console(file=StringIO())))

    selected = registry.current_project()
    assert (selected.id, selected.name, selected.memory_namespace, selected.memory_revision) == (
        original.id, original.name, original.memory_namespace, original.memory_revision,
    )
    assert len(registry.load_registry().profiles) == 2


@pytest.mark.parametrize('value', [None, '', 'missing', 'file'])
def test_invalid_or_cancelled_open_preserves_registry(tmp_path, monkeypatch, value):
    registry.register_project(workspace(tmp_path, 'original'))
    before = registry.registry_path().read_bytes()
    if value in ('missing', 'file'):
        path = tmp_path / value
        if value == 'file':
            path.write_text('not a directory')
        value = str(path)
    prompts(monkeypatch, 'path', [value])

    assert not asyncio.run(projects.open_existing_project(Console(file=StringIO())))
    assert registry.registry_path().read_bytes() == before


def test_choose_displays_scope_before_prompt_and_does_not_open_memory(tmp_path, monkeypatch):
    root = workspace(tmp_path, 'code')
    registry.register_project(root, 'Code project')
    output = StringIO()
    prompt = AsyncMock()
    prompt.ask_async.return_value = 'use'

    def select(*args, **kwargs):
        assert 'Project: Code project' in output.getvalue()
        assert str(root) in output.getvalue()
        return prompt

    monkeypatch.setattr(projects.questionary, 'select', select)
    monkeypatch.setattr('synapse.memory.open_conversation', lambda *args, **kwargs: pytest.fail('Memory opened before choosing workspace'))
    before = registry.registry_path().read_bytes()

    assert asyncio.run(projects.choose_workspace(Console(file=output, width=240)))
    assert registry.registry_path().read_bytes() == before


@pytest.mark.parametrize('choice', [None, 'back'])
def test_choose_cancel_preserves_active_project(tmp_path, monkeypatch, choice):
    registry.register_project(workspace(tmp_path, 'original'))
    before = registry.registry_path().read_bytes()
    prompts(monkeypatch, 'select', [choice])

    assert not asyncio.run(projects.choose_workspace(Console(file=StringIO())))
    assert registry.registry_path().read_bytes() == before


def test_choose_switches_registered_project(tmp_path, monkeypatch):
    first = workspace(tmp_path, 'first')
    registry.register_project(first)
    project = registry.current_project()
    registry.register_project(workspace(tmp_path, 'second'))
    prompts(monkeypatch, 'select', ['switch', project.id])

    assert asyncio.run(projects.choose_workspace(Console(file=StringIO())))
    assert registry.current_project().id == project.id


def test_missing_active_folder_can_recover_by_opening_another(tmp_path, monkeypatch):
    missing = workspace(tmp_path, 'missing')
    registry.register_project(missing)
    missing.rmdir()
    valid = workspace(tmp_path, 'valid')
    prompts(monkeypatch, 'select', ['use', 'open'])
    prompts(monkeypatch, 'path', [str(valid)])
    output = StringIO()

    assert asyncio.run(projects.choose_workspace(Console(file=output, width=240)))
    assert registry.current_project().workspace_root == str(valid)
    assert 'Workspace folder is missing' in output.getvalue()


def test_invalid_open_returns_to_workspace_picker(tmp_path, monkeypatch):
    registry.register_project(workspace(tmp_path, 'original'))
    before = registry.registry_path().read_bytes()
    selection = prompts(monkeypatch, 'select', ['open', 'use'])
    prompts(monkeypatch, 'path', [str(tmp_path / 'not-real')])
    output = StringIO()

    assert asyncio.run(projects.choose_workspace(Console(file=output, width=240)))
    assert selection.ask_async.await_count == 2
    assert 'Folder does not exist' in output.getvalue()
    assert registry.registry_path().read_bytes() == before


def test_choose_without_saved_project_can_use_current_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    prompts(monkeypatch, 'select', ['use'])
    output = StringIO()

    assert asyncio.run(projects.choose_workspace(Console(file=output, width=240)))
    assert str(tmp_path) in output.getvalue()
    assert not registry.registry_path().exists()


def test_manage_projects_offers_existing_folder(tmp_path, monkeypatch):
    root = workspace(tmp_path, 'existing')
    prompt = AsyncMock()
    prompt.ask_async.side_effect = ['open', 'back']

    def select(*args, **kwargs):
        assert any(choice.title == 'Open existing folder' for choice in kwargs['choices'])
        return prompt

    monkeypatch.setattr(projects.questionary, 'select', select)
    prompts(monkeypatch, 'path', [str(root)])

    asyncio.run(projects.manage_projects(Console(file=StringIO())))
    assert registry.current_project().workspace_root == str(root)


def test_create_explains_new_managed_folder_is_empty(tmp_path, monkeypatch):
    prompts(monkeypatch, 'text', ['New project'])
    output = StringIO()

    asyncio.run(projects.manage_project('create', Console(file=output, width=240)))

    assert registry.current_project().workspace_root == str(tmp_path / 'core' / 'Projects' / 'New_project')
    assert str(tmp_path / 'core' / 'Projects' / 'New_project') in output.getvalue()
    assert 'new, empty folder' in output.getvalue()
