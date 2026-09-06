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


async def run_ask_mode(console: Console) -> None:
    console.print(Panel(
        "Ask a question and get an answer in your terminal.\n"
        "Each question is independent. Type /back to return to the menu.",
        title="Ask Mode", border_style=ACCENT, padding=(1, 2),
    ))
    while True:
        question = await questionary.text("Ask a question:", style=PROMPT_STYLE).ask_async()
        if question is None or question.strip().lower() == "/back":
            return
        if not question.strip():
            console.print("Please enter a question.", style="yellow")
            continue
        try:
            with console.status("Waiting for model response…", spinner="dots", spinner_style=ACCENT):
                answer = await asyncio.to_thread(
                    ask_question, question.strip(),
                    on_status=lambda message: console.print(message, style="dim", markup=False),
                )
        except (AskError, ModelConfigError) as error:
            console.print(f"Error: {error}", style="red", markup=False)
            continue
        console.print()
        console.print(Panel(Markdown(answer), title="Answer", border_style=ACCENT, padding=(1, 2)))
        console.print()
