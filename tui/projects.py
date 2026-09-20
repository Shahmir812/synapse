from pathlib import Path

import questionary
from rich.text import Text

from synapse.project_registry import current_project, load_registry, create_project, remove_project, rename_project, switch_project, register_project
from synapse.projects import ProjectError
from tui.theme import PROMPT_STYLE


def _show_selected_project(console):
    project = current_project()
    if project is not None:
        console.print(f'Project: {project.name}', markup=False)
        console.print(f'Workspace: {project.workspace_root}', markup=False)
    else:
        console.print('No saved project selected. The current directory will be used.', style='yellow')
        console.print(f'Workspace: {Path.cwd().resolve()}', markup=False)


async def open_existing_project(console) -> bool:
    """Select an existing folder without copying files or changing its identity."""
    value = await questionary.path(
        'Existing project folder', only_directories=True, style=PROMPT_STYLE,
    ).ask_async()
    if value is None:
        return False
    try:
        if not value.strip():
            raise ProjectError('Enter the path to an existing project folder.')
        root = Path(value).expanduser().resolve()
        if not root.is_dir():
            raise ProjectError(f'Folder does not exist: {root}. Choose the folder containing your project files.')
        state = load_registry()
        existing = next((p for p in state.profiles if Path(p.workspace_root).resolve() == root), None)
        if existing is not None:
            switch_project(existing.id)
        else:
            register_project(root)
        console.print('Existing project selected. Its files stay in their current folder.', style='green')
        _show_selected_project(console)
        return True
    except (ProjectError, OSError, RuntimeError, ValueError) as error:
        console.print(f'Error: {error}', style='red', markup=False)
        return False


async def _switch_registered_project(console) -> bool:
    state = load_registry()
    if not state.profiles:
        console.print('No saved projects. Open an existing folder or create a project.', style='yellow')
        return False
    selected = await questionary.select('Choose a project', choices=[
        questionary.Choice(f"{'● ' if p.id == state.active_project_id else ''}{p.name} — {p.workspace_root}", value=p.id)
        for p in state.profiles], style=PROMPT_STYLE).ask_async()
    if selected is None:
        return False
    switch_project(selected)
    _show_selected_project(console)
    return True


async def choose_workspace(console) -> bool:
    """Choose the file and memory scope before opening an Ask conversation."""
    while True:
        try:
            _show_selected_project(console)
            action = await questionary.select('Workspace for this Ask session', choices=[
                questionary.Choice('Use this workspace', value='use'),
                questionary.Choice('Switch saved project', value='switch'),
                questionary.Choice('Open existing folder', value='open'),
                questionary.Choice('Back', value='back'),
            ], style=PROMPT_STYLE).ask_async()
            if action in (None, 'back'):
                return False
            if action == 'use':
                project = current_project()
                root = Path(project.workspace_root) if project else Path.cwd()
                if not root.is_dir():
                    raise ProjectError(f'Workspace folder is missing: {root}. Switch projects or open an existing folder.')
                return True
            if action == 'switch' and await _switch_registered_project(console):
                return True
            if action == 'open' and await open_existing_project(console):
                return True
        except (ProjectError, OSError, RuntimeError, ValueError) as error:
            console.print(f'Error: {error}', style='red', markup=False)
            # Invalid registry state cannot be repaired through selection.
            try:
                load_registry()
            except ProjectError:
                return False


async def manage_project(action, console):
    try:
        if action == 'create':
            name = await questionary.text('Project name', style=PROMPT_STYLE).ask_async()
            if name is None:
                return
            create_project(name)
            console.print('Project created and selected.', style='green')
            _show_selected_project(console)
            console.print('This is a new, empty folder. Add your files here, or open an existing project folder.', style='dim')
        elif action == 'open':
            await open_existing_project(console)
        elif action == 'switch':
            await _switch_registered_project(console)
        elif action == 'delete':
            state = load_registry()
            if not state.profiles:
                console.print('No saved projects. Create one first.', style='yellow')
                return
            selected = await questionary.select('Choose a project', choices=[
                questionary.Choice(f"{'● ' if p.id == state.active_project_id else ''}{p.name} — {p.workspace_root}", value=p.id)
                for p in state.profiles], style=PROMPT_STYLE).ask_async()
            if selected is not None:
                confirmed = await questionary.confirm(
                    'Remove this project from the registry? Its folder and files will remain.',
                    default=False, style=PROMPT_STYLE,
                ).ask_async()
                if confirmed:
                    remove_project(selected)
                    console.print('Project removed from registry. Files kept.', style='green')
        elif action == 'rename':
            project = current_project()
            if project is None:
                raise ProjectError('No active project. Create one first.')
            name = await questionary.text('New project name', default=project.name, style=PROMPT_STYLE).ask_async()
            if name is not None:
                rename_project(name)
        elif action == 'details':
            project = current_project()
            if project is None:
                raise ProjectError('No active project. Create one first.')
            for label, value in [('Project', project.name), ('ID', project.id), ('Workspace', project.workspace_root),
                                 ('Memory namespace', project.memory_namespace), ('Created', project.created_at), ('Last used', project.last_used_at)]:
                console.print(Text(f'{label}: {value}'))
            console.print('Honcho memory is scoped to this project when configured.', style='dim')
    except ProjectError as error:
        console.print(f'Error: {error}', style='red', markup=False)


async def manage_projects(console):
    while True:
        action = await questionary.select('Manage projects', choices=[
            questionary.Choice('Create project', value='create'),
            questionary.Choice('Open existing folder', value='open'),
            questionary.Choice('Switch project', value='switch'),
            questionary.Choice('Project details', value='details'),
            questionary.Choice('Rename project', value='rename'),
            questionary.Choice('Delete project from registry', value='delete'),
            questionary.Choice('Back', value='back'),
        ], style=PROMPT_STYLE).ask_async()
        if action in (None, 'back'):
            return
        await manage_project(action, console)
