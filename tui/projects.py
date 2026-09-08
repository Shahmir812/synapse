from pathlib import Path

import questionary
from rich.text import Text

from synapse.project_registry import current_project, load_registry, register_project, rename_project, switch_project
from synapse.projects import ProjectError
from tui.theme import PROMPT_STYLE


async def manage_project(action, console):
    try:
        if action == 'create':
            name = await questionary.text('Project name', style=PROMPT_STYLE).ask_async()
            if name is None:
                return
            path = await questionary.path('Workspace folder', default=str(Path.cwd()), only_directories=True, style=PROMPT_STYLE).ask_async()
            if path is None:
                return
            register_project(Path(path), name)
            console.print('Project created and selected.', style='green')
        elif action == 'switch':
            state = load_registry()
            if not state.profiles:
                console.print('No saved projects. Create one first.', style='yellow')
                return
            selected = await questionary.select('Choose a project', choices=[
                questionary.Choice(f"{'● ' if p.id == state.active_project_id else ''}{p.name} — {p.workspace_root}", value=p.id)
                for p in state.profiles], style=PROMPT_STYLE).ask_async()
            if selected is not None:
                switch_project(selected)
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
            console.print('Memory storage is not enabled yet.', style='dim')
    except ProjectError as error:
        console.print(f'Error: {error}', style='red', markup=False)
