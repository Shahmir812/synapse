"""Shared terminal styling and a short, terminal-only startup animation."""
from __future__ import annotations

import asyncio
import os

import pyfiglet
import questionary
from rich.align import Align
from rich.console import Console
from rich.live import Live
from rich.text import Text

FACE_COLOR = "#e8dcf8"
SHADOW_COLOR = "#5b4d9e"
ACCENT = "#b8a0ef"
PROMPT_STYLE = questionary.Style([
    ("qmark", "fg:#b8a0ef bold"),
    ("question", "fg:#e8dcf8 bold"),
    ("answer", "fg:#b8a0ef bold"),
    ("pointer", "fg:#b8a0ef bold"),
    ("highlighted", "fg:#e8dcf8 bold"),
    ("selected", "fg:#b8a0ef"),
    ("instruction", "fg:#888888"),
])


def banner(width: int) -> Text:
    art = pyfiglet.figlet_format("synapse", font="ansi_shadow").rstrip("\n")
    lines = art.splitlines()
    if max(map(len, lines), default=0) + 2 > width:
        return Text("S Y N A P S E" if width >= 13 else "SYNAPSE", style=f"bold {FACE_COLOR}")
    # Composite the offset shadow without raw cursor movement or overwriting spaces.
    canvas = Text()
    for row in range(len(lines) + 1):
        face = lines[row] if row < len(lines) else ""
        shadow = "  " + lines[row - 1] if row else ""
        for column in range(max(len(face), len(shadow))):
            front = face[column] if column < len(face) else " "
            back = shadow[column] if column < len(shadow) else " "
            canvas.append(front if front != " " else back,
                          style=f"bold {FACE_COLOR}" if front != " " else SHADOW_COLOR)
        canvas.append("\n")
    return canvas


async def show_banner(console: Console) -> None:
    art = banner(console.width)
    animate = (console.is_terminal and not console.is_dumb_terminal
               and not console.no_color and not os.environ.get("SYNAPSE_NO_ANIMATION"))
    if animate:
        with Live(console=console, auto_refresh=False, transient=True) as live:
            for color in (SHADOW_COLOR, "#8470ba", "#b8a0ef"):
                live.update(Align.center(Text(art.plain, style=color)), refresh=True)
                await asyncio.sleep(0.08)
    console.print(Align.center(art))
