"""Interactive terminal interface for single-question Ask requests."""

from __future__ import annotations

import asyncio
from threading import Event

import questionary
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from tui.theme import ACCENT, PROMPT_STYLE

from synapse.ask import AskError, ask_question
from synapse.model import ModelConfigError
from synapse.memory import MemoryError
from synapse.local_memory import LocalMemoryError
from synapse.exploration import ExplorationError, inspect_workspace
from tui.conversations import choose_conversation
from tui.projects import choose_workspace
from synapse.projects import ProjectError
from synapse.project_registry import active_workspace, current_project
from synapse.file_context import FileContextError, selectable_files


async def cancellable_ask(function, *args, **kwargs):
    cancellation = kwargs.pop('cancel_event', None) or Event()
    try:
        return await asyncio.to_thread(function, *args, **kwargs, cancel_event=cancellation)
    except BaseException:
        cancellation.set()
        raise


async def select_ask_workspace(console: Console, *, explore: bool):
    while await choose_workspace(console):
        try:
            workspace = active_workspace()
            if explore:
                with console.status('Checking workspace files…', spinner_style=ACCENT):
                    listing = await asyncio.to_thread(inspect_workspace, workspace)
                paths = listing['files']
                console.print(
                    f"Workspace preview: {len(paths)} file(s) listed"
                    + (' (scan limited)' if listing['truncated'] else ''), style='dim',
                )
                for path in paths[:5]:
                    console.print(f'  {path}', style='dim', markup=False)
                console.print('Explore finds and reads relevant files automatically; attachments are optional.', style='dim')
            return workspace
        except (ExplorationError, ProjectError) as error:
            console.print(str(error), style='yellow', markup=False)
            console.print('No request sent. Select a different workspace below.', style='dim')
    return None


async def run_ask_mode(console: Console, *, explore: bool = False) -> None:
    console.print(Panel(
        ("Explore the code in the project you select.\n" if explore else "Ask a question and get an answer in your terminal.\n")
        + "Type /project to change workspace, /conversation to change conversation, or /back to return.",
        title="Ask · Explore workspace" if explore else "Ask Mode", border_style=ACCENT, padding=(1, 2),
    ))
    if explore:
        console.print("Read-only tools: list_files · read_file · search_files. Tool activity appears below.", style="dim")
        workspace = await select_ask_workspace(console, explore=True)
        if workspace is None:
            return
    else:
        try:
            workspace = active_workspace()
            project = current_project()
            console.print(f"Project: {project.name if project else 'No saved project'}", markup=False)
            console.print(f'Workspace: {workspace}', style='dim', markup=False)
        except ProjectError as error:
            console.print(str(error), style='yellow', markup=False)
            workspace = await select_ask_workspace(console, explore=False)
            if workspace is None:
                return
    conversation = await choose_conversation(console)
    if conversation is None:
        return
    console.print(f"Conversation: {conversation.id}", style="dim", markup=False)
    while True:
        question = await questionary.text("Ask a question:", style=PROMPT_STYLE).ask_async()
        if question is None or question.strip().lower() == "/back":
            return
        if question.strip().lower() == '/project':
            selected_workspace = await select_ask_workspace(console, explore=explore)
            if selected_workspace is None:
                return
            workspace = selected_workspace
            # Even temporary history belongs to the workspace where it was collected.
            conversation = await choose_conversation(console)
            if conversation is None:
                return
            console.print(f"Conversation: {conversation.id}", style='dim', markup=False)
            continue
        if question.strip().lower() == '/conversation':
            selected = await choose_conversation(console)
            if selected is not None:
                conversation = selected
                console.print(f"Conversation: {conversation.id}", style="dim", markup=False)
            continue
        if not question.strip():
            console.print("Please enter a question.", style="yellow")
            continue
        console.print(f"Workspace: {workspace}", style="dim", markup=False)
        attach = await questionary.confirm(
            "Attach workspace files?", default=False, style=PROMPT_STYLE,
        ).ask_async()
        if attach is None:
            continue
        files = []
        if attach:
            with console.status("Finding text files…", spinner_style=ACCENT):
                candidates, truncated = await asyncio.to_thread(selectable_files, workspace)
            if truncated:
                console.print("File list limited. Use CLI --file for files not shown.", style="yellow")
            if not candidates:
                console.print("No eligible text files found. Continuing with your question without attachments.", style="yellow")
            else:
                files = await questionary.checkbox(
                    "Select files (Space to toggle, Enter to continue)",
                    choices=candidates, style=PROMPT_STYLE,
                ).ask_async()
                if files is None:
                    continue
        try:
            with console.status("Waiting for model response…", spinner="dots", spinner_style=ACCENT):
                answer = await cancellable_ask(
                    ask_question, question.strip(), files=files, conversation=conversation, explore=explore,
                    expected_workspace=workspace,
                    on_status=lambda message: console.print(message, style="dim", markup=False),
                )
        except (AskError, ModelConfigError, FileContextError, ProjectError, MemoryError, LocalMemoryError) as error:
            console.print(f"Error: {error}", style="red", markup=False)
            continue
        console.print()
        console.print(Panel(Markdown(answer), title="Answer", border_style=ACCENT, padding=(1, 2)))
        console.print()
