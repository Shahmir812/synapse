from pathlib import Path

import questionary
from rich.text import Text

from synapse.project_registry import current_project, load_registry, create_project, remove_project, rename_project, switch_project, projects_directory
from synapse.projects import ProjectError
from tui.theme import PROMPT_STYLE


async def manage_project(action, console):
    try:
        if action == 'create':
            name = await questionary.text('Project name', style=PROMPT_STYLE).ask_async()
            if name is None:
                return
            create_project(name)
            console.print('Project created and selected.', style='green')
        elif action in ('switch', 'delete'):
            state = load_registry()
            if not state.profiles:
                console.print('No saved projects. Create one first.', style='yellow')
                return
            selected = await questionary.select('Choose a project', choices=[
                questionary.Choice(f"{'● ' if p.id == state.active_project_id else ''}{p.name} — {p.workspace_root}", value=p.id)
                for p in state.profiles], style=PROMPT_STYLE).ask_async()
            if selected is not None:
                if action == 'delete':
                    confirmed = await questionary.confirm(
                        'Remove this project from the registry? Its folder and files will remain.',
                        default=False, style=PROMPT_STYLE,
                    ).ask_async()
                    if confirmed:
                        remove_project(selected)
                        console.print('Project removed from registry. Files kept.', style='green')
                else:
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


async def manage_projects(console):
    while True:
        action = await questionary.select('Manage projects', choices=[
            questionary.Choice('Create project', value='create'),
            questionary.Choice('Switch project', value='switch'),
            questionary.Choice('Project details', value='details'),
            questionary.Choice('Rename project', value='rename'),
            questionary.Choice('Delete project from registry', value='delete'),
            questionary.Choice('Back', value='back'),
        ], style=PROMPT_STYLE).ask_async()
        if action in (None, 'back'):
            return
        await manage_project(action, console)
