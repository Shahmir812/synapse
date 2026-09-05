from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from synapse.projects import (
    ProjectError,
    active_project,
    initialize_project,
    read_project_state,
    save_project_state,
    select_project,
)
from tui.wakeup import run_wakeup

VERSION = "0.2.0"


def _path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(
        prog="synapse",
        description="Synapse - AI-powered coding agent CLI",
    )
    parser.add_argument("-v", "--version", action="version", version=VERSION)

    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("wakeup", help="Show the Synapse wakeup screen")

    project_parser = subparsers.add_parser("project", help="Manage Synapse project profiles")
    project_subparsers = project_parser.add_subparsers(dest="project_command")

    init_parser = project_subparsers.add_parser("init", help="Initialize a project in a folder")
    init_parser.add_argument("path", nargs="?", type=_path, default=_path("."))
    init_parser.add_argument("--name", help="Human-readable project name")

    status_parser = project_subparsers.add_parser("status", help="Show the active project profile")
    status_parser.add_argument("path", nargs="?", type=_path, default=_path("."))

    list_parser = project_subparsers.add_parser("list", help="List project profiles")
    list_parser.add_argument("path", nargs="?", type=_path, default=_path("."))

    use_parser = project_subparsers.add_parser("use", help="Select the active project profile")
    use_parser.add_argument("project_id")
    use_parser.add_argument("--path", type=_path, default=_path("."))

    args = parser.parse_args()

    if args.command == "wakeup":
        asyncio.run(run_wakeup())
        return

    if args.command == "project":
        console = Console()
        try:
            if args.project_command == "init":
                state = initialize_project(args.path, args.name)
                project = active_project(state)
                console.print(f"Initialized [cyan]{project.name}[/cyan] ({project.id})")
                return

            if args.project_command == "status":
                project = active_project(read_project_state(args.path))
                console.print(f"Project: [cyan]{project.name}[/cyan]")
                console.print(f"ID:      {project.id}")
                console.print(f"Path:    [dim]{project.workspace_root}[/dim]")
                console.print(f"Memory:  {project.memory_namespace}")
                return

            if args.project_command == "list":
                state = read_project_state(args.path)
                table = Table(title="Synapse projects")
                table.add_column("Active")
                table.add_column("Name")
                table.add_column("ID")
                table.add_column("Workspace")
                for project in state.profiles:
                    table.add_row(
                        "*" if project.id == state.active_project_id else "",
                        project.name,
                        project.id,
                        project.workspace_root,
                    )
                console.print(table)
                return

            if args.project_command == "use":
                state = select_project(read_project_state(args.path), args.project_id)
                save_project_state(args.path, state)
                console.print(f"Active project: [cyan]{active_project(state).name}[/cyan]")
                return
        except ProjectError as error:
            console.print(f"[red]Error:[/red] {error}")
            raise SystemExit(1) from error

        project_parser.print_help()
        return

    parser.print_help()


if __name__ == "__main__":
    main()
