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
)
from tui.wakeup import run_wakeup
from synapse.project_registry import (register_project, load_registry, switch_project, rename_project, import_current_project, create_project, remove_project)

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
    ask_parser = subparsers.add_parser("ask", help="Ask the configured AI model a question")
    ask_parser.add_argument("question", help="Question to ask (wrap it in quotes)")

    ask_parser.add_argument("--file", action="append", default=[], metavar="PATH", help="Attach a workspace text file; repeat for multiple files")

    project_parser = subparsers.add_parser("project", help="Manage Synapse project profiles")
    project_subparsers = project_parser.add_subparsers(dest="project_command")

    init_parser = project_subparsers.add_parser("init", help="Register a workspace and select it as the active project")
    init_parser.add_argument("path", nargs="?", type=_path, default=_path("."))
    init_parser.add_argument("--name", help="Human-readable project name")

    status_parser = project_subparsers.add_parser("status", help="Show the active project profile")
    status_parser.add_argument("path", nargs="?", type=_path, default=_path("."))

    list_parser = project_subparsers.add_parser("list", help="List project profiles")
    list_parser.add_argument("path", nargs="?", type=_path, default=_path("."))

    use_parser = project_subparsers.add_parser("use", help="Select the active project profile")
    use_parser.add_argument("project_id")
    use_parser.add_argument("--path", type=_path, default=_path("."))

    rename_parser = project_subparsers.add_parser("rename", help="Rename the active project")
    rename_parser.add_argument("name")

    create_parser = project_subparsers.add_parser("create", help="Create a workspace under CORE_DIR/Projects")
    create_parser.add_argument("name")
    remove_parser = project_subparsers.add_parser("remove", help="Remove a profile without deleting its folder")
    remove_parser.add_argument("project_id")

    args = parser.parse_args()

    if args.command == "ask":
        from rich.markdown import Markdown

        from synapse.ask import AskError, ask_question
        from synapse.model import ModelConfigError
        from synapse.file_context import FileContextError

        try:
            answer = ask_question(
                args.question, files=args.file,
                on_status=lambda message: Console(stderr=True).print(message, style="dim", markup=False),
            )
        except (AskError, ModelConfigError, FileContextError, ProjectError) as error:
            Console(stderr=True).print(f"Error: {error}", style="red", markup=False)
            raise SystemExit(1) from error
        Console().print(Markdown(answer))
        return

    if args.command == "wakeup":
        asyncio.run(run_wakeup())
        return

    if args.command == "project":
        console = Console()
        try:
            if args.project_command == "create":
                state = create_project(args.name)
                console.print(f"Created project: {active_project(state).workspace_root}", markup=False)
                return
            if args.project_command == "remove":
                remove_project(args.project_id)
                console.print("Project removed from registry. Files kept.")
                return
            if args.project_command == "rename":
                rename_project(args.name)
                console.print("Project renamed.")
                return
            if args.project_command != "init":
                import_current_project(args.path if hasattr(args, "path") else None)
            if args.project_command == "status" and not load_registry().profiles:
                raise ProjectError("No active project. Run synapse project init.")
            if args.project_command == "init":
                state = register_project(args.path, args.name)
                project = active_project(state)
                console.print(f"Initialized [cyan]{project.name}[/cyan] ({project.id})")
                return

            if args.project_command == "status":
                project = active_project(load_registry())
                console.print(f"Project: [cyan]{project.name}[/cyan]")
                console.print(f"ID:      {project.id}")
                console.print(f"Path:    [dim]{project.workspace_root}[/dim]")
                console.print(f"Memory:  {project.memory_namespace}")
                return

            if args.project_command == "list":
                state = load_registry()
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
                state = switch_project(args.project_id)
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
