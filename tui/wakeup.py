from __future__ import annotations

import os
from pathlib import Path

import questionary
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from synapse.projects import ProjectError
from synapse.project_registry import current_project, import_current_project, projects_directory
from tui.projects import manage_projects
from tui.theme import ACCENT, PROMPT_STYLE, show_banner


def show_workspace(console: Console) -> None:
    details = Table.grid(padding=(0, 2), expand=True)
    details.add_column(style="dim", no_wrap=True)
    details.add_column(overflow="fold")
    try:
        project = current_project()
        details.add_row("Project", Text(project.name if project else "No project selected"))
        details.add_row("Workspace", Text(project.workspace_root if project else str(Path.cwd())))
        if project:
            details.add_row("Memory namespace", Text(project.memory_namespace))
    except ProjectError as error:
        details.add_row("Project error", Text(str(error)))
    from synapse.memory import configured
    details.add_row("Memory", "Honcho configured (connects when opening Ask)" if configured() else "SQLite local; Honcho not configured")
    primary = os.environ.get("OPENROUTER_DEFAULT_MODEL", "").strip()
    google = os.environ.get("GEMINI_MODEL", "").strip()
    details.add_row("OpenRouter", Text(primary or "Model not configured"))
    details.add_row("Google fallback", Text(google or "Model not configured"))
    console.print(Panel(details, title="Workspace", border_style=ACCENT, padding=(1, 2)))


async def run_wakeup() -> None:
    console = Console()
    if not console.is_terminal:
        console.print("Synapse is awake.")
        return
    await show_banner(console)
    console.print("Synapse is awake.", style="dim", justify="center")
    console.print()
    try:
        projects_directory()
        import_current_project()
    except ProjectError as error:
        console.print(str(error), style="red", markup=False)
    show_workspace(console)
    console.print("↑ ↓ navigate  ·  Enter select  ·  Ctrl+C exit", style="dim")
    console.print()
    while True:
        choice = await questionary.select(
            "Project launcher",
            choices=[
                questionary.Choice("Ask Mode", value="ask"),
                questionary.Choice("Manage projects", value="manage"),
                questionary.Choice("Exit", value="exit"),
            ],
            style=PROMPT_STYLE,
        ).ask_async()
        if choice is None or choice == "exit":
            console.print("\nGoodbye.\n", style="dim")
            return
        if choice == "ask":
            from tui.ask import run_ask_mode
            await run_ask_mode(console)

        elif choice == "manage":
            await manage_projects(console)
            show_workspace(console)
