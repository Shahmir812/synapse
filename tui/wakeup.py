from __future__ import annotations

from rich.console import Console
from rich.panel import Panel


async def run_wakeup() -> None:
    console = Console()
    console.print(
        Panel.fit(
            "Synapse is awake.",
            title="synapse",
            border_style="cyan",
        )
    )
