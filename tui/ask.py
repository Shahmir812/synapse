"""Interactive terminal interface for single-question Ask requests."""

from __future__ import annotations

import asyncio

import questionary
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from tui.theme import ACCENT, PROMPT_STYLE

from synapse.ask import AskError, ask_question
from synapse.model import ModelConfigError
from synapse.memory import MemoryError
from synapse.local_memory import LocalMemoryError
from tui.conversations import choose_conversation
from synapse.projects import ProjectError
from synapse.project_registry import active_workspace
from synapse.file_context import FileContextError, selectable_files


async def run_ask_mode(console: Console) -> None:
    console.print(Panel(
        "Ask a question and get an answer in your terminal.\n"
        "Type /conversation to start or resume a conversation; /back returns to the menu.",
        title="Ask Mode", border_style=ACCENT, padding=(1, 2),
    ))
    conversation = await choose_conversation(console)
    if conversation is None:
        return
    console.print(f"Conversation: {conversation.id}", style="dim", markup=False)
    while True:
        question = await questionary.text("Ask a question:", style=PROMPT_STYLE).ask_async()
        if question is None or question.strip().lower() == "/back":
            return
        if question.strip().lower() == '/conversation':
            selected = await choose_conversation(console)
            if selected is not None:
                conversation = selected
                console.print(f"Conversation: {conversation.id}", style="dim", markup=False)
            continue
        if not question.strip():
            console.print("Please enter a question.", style="yellow")
            continue
        try:
            workspace = active_workspace()
        except ProjectError as error:
            console.print(str(error), style="red", markup=False)
            return
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
                console.print("No eligible text files found. Ask again without attachments.", style="yellow")
                continue
            files = await questionary.checkbox(
                "Select files (Space to toggle, Enter to continue)",
                choices=candidates, style=PROMPT_STYLE,
            ).ask_async()
            if files is None:
                continue
        try:
            with console.status("Waiting for model response…", spinner="dots", spinner_style=ACCENT):
                answer = await asyncio.to_thread(
                    ask_question, question.strip(), files=files, conversation=conversation,
                    on_status=lambda message: console.print(message, style="dim", markup=False),
                )
        except (AskError, ModelConfigError, FileContextError, ProjectError, MemoryError, LocalMemoryError) as error:
            console.print(f"Error: {error}", style="red", markup=False)
            continue
        console.print()
        console.print(Panel(Markdown(answer), title="Answer", border_style=ACCENT, padding=(1, 2)))
        console.print()
